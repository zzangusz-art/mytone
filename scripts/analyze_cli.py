"""명령줄로 한 번에: 녹음 파일들 → STT → 화자 구분 → 말투 프로필 → 말투 카드(.md).

화면(1_RUN.bat)에서 하는 일과 같은 코드를 쓴다. 결과는 같은 data/ 폴더에 저장되므로
끝난 뒤 화면에서 같은 아이디로 로그인하면 전사 확인·화자 수정·답변 생성을 이어서 할 수 있다.

사용:
  python scripts/analyze_cli.py --login 아이디 --name 이름 --pin 1234 통화1.m4a 통화2.m4a 통화3.m4a
  옵션: --role academy|homeroom|subject|director|kinder|afterschool|counselor|other
        --names "민준,서연"   (가릴 학생 이름을 직접 지정. 안 주면 자동 추정)
        --out 말투카드.md      --dump-prompt 프롬프트.txt
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import auth, llm, profile as prof, prompts, speakers, store, stt, style_stats  # noqa: E402


def card_markdown(teacher: dict, profile: dict, recs: list[dict]) -> str:
    st = profile["stats"]
    rel = profile["reliability"]
    L = [f"# {teacher['name']} 선생님 말투 카드", "",
         f"- 만든 날: {profile['built_at']} · 통화 {st['n_recordings']}건 · 교사 발화 {st['n_sentences']}문장({st['n_eojeol']}어절)",
         f"- 자료량: **{rel['level']}** — {rel['note']}",
         f"- 가린 학생 이름: {', '.join(profile['names_masked']) or '없음'}", "",
         "## 세어 본 습관", ""]
    L += [f"- {ln}" for ln in style_stats.summary_lines(st)]
    q = profile.get("qualitative")
    L += ["", "## 말하는 방식(정성 분석)", ""]
    if q:
        for key, title in [("one_line", "한 줄 인상"), ("delivery_order", "소식을 꺼내는 순서"),
                           ("bad_news", "걱정되는 일을 전할 때"), ("praise", "칭찬할 때"), ("request", "부탁할 때"),
                           ("empathy", "공감·안심"), ("explain", "설명하는 방식"), ("written_tips", "글로 옮길 때")]:
            if q.get(key):
                L.append(f"- **{title}**: {q[key]}")
        if q.get("signature"):
            L.append("- **이 선생님다운 표현**: " + " / ".join(f"\"{s['phrase']}\"({s['usage']})" for s in q["signature"]))
        if q.get("never"):
            L.append("- **쓰지 않는 말투**: " + " / ".join(q["never"]))
    else:
        L.append("- (Claude API 키가 없어 생략 — 측정값과 실제 발화만으로도 답변 생성은 됩니다)")
    L += ["", "## 예시로 쓰는 실제 발화", ""]
    L += [f"- [{prompts.FUNCTION_LABELS.get(e['function'], '설명')}] {e['text']}" for e in profile["exemplars"]]
    L += ["", "## 녹음별 화자 구분", ""]
    for r in recs:
        units = [u for u in r["units"] if not u.get("deleted")]
        unsure = sum(1 for u in units if u.get("conf", 1) <= 0.5 and not u.get("manual"))
        L.append(f"- {r['title']}: {len(units)}문장 중 교사 {sum(1 for u in units if u['speaker'] == 'T')}문장"
                 f" · 확신 낮은 문장 {unsure}개(화면에서 확인 권장)")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="녹음 → 말투 프로필")
    ap.add_argument("audio", nargs="+", help="통화 녹음 파일들 (m4a/mp3/wav …)")
    ap.add_argument("--login", required=True, help="화면 로그인에 쓸 아이디")
    ap.add_argument("--name", required=True, help="교사 이름")
    ap.add_argument("--pin", required=True, help="숫자 4~8자리 (새 계정일 때 설정, 기존 계정이면 확인)")
    ap.add_argument("--role", default="other", choices=list(prompts.ROLES))
    ap.add_argument("--org", default="")
    ap.add_argument("--names", default="", help="가릴 학생 이름(쉼표 구분)")
    ap.add_argument("--out", default="", help="말투 카드 저장 경로(.md)")
    ap.add_argument("--dump-prompt", default="", help="생성용 시스템 프롬프트를 이 파일로 저장")
    ap.add_argument("--no-llm", action="store_true", help="키가 있어도 정성 분석을 건너뜀")
    args = ap.parse_args()

    files = [Path(a) for a in args.audio]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        sys.exit("파일이 없습니다: " + ", ".join(missing))
    if not args.pin.isdigit() or not 4 <= len(args.pin) <= 8:
        sys.exit("--pin 은 숫자 4~8자리입니다.")

    tid = store.find_teacher_id(args.login)
    if tid:
        teacher = store.get_teacher(tid)
        if not auth.verify_pin(args.pin, teacher["pin_salt"], teacher["pin_hash"]):
            sys.exit("이미 있는 아이디인데 PIN 이 다릅니다.")
        print(f"기존 계정에 추가합니다: {args.login}")
    else:
        salt, digest = auth.hash_pin(args.pin)
        teacher = store.create_teacher(args.login, {"name": args.name, "role": args.role, "org": args.org, "grade": "",
                                                    "pin_salt": salt, "pin_hash": digest})
        tid = teacher["id"]
        print(f"새 계정: {args.login}")

    names = [n.strip() for n in args.names.split(",") if n.strip()]
    for f in files:
        t0 = time.time()
        print(f"전사 중: {f.name} …", end=" ", flush=True)
        try:
            res = stt.transcribe(f)
        except stt.STTError as e:
            print(f"실패 — {e}")
            continue
        units = speakers.assign_speakers(speakers.segments_to_units(res["segments"]))
        rec = {"id": store.new_id("r_"), "title": f.stem[:60], "source": "audio", "status": "done",
               "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "student_names": names, "duration": res["duration"],
               "units": units, "stt": {"provider": res["provider"], "model": res["model"],
                                       "seconds": round(time.time() - t0, 1)}}
        store.save_recording(tid, rec)
        print(f"{res['duration']:.0f}초 분량 → {time.time() - t0:.0f}초, {len(units)}문장")

    recs = [r for r in store.list_recordings(tid) if r.get("status") == "done"]
    if not recs:
        sys.exit("전사된 녹음이 없습니다.")
    lists = [r["units"] for r in recs]
    if speakers.refine_all(lists):                # 여러 통화를 합쳐 화자 추정을 한 번 더 다듬는다
        for r in recs:
            store.save_recording(tid, r)
    all_names = list(dict.fromkeys(names + [n for r in recs for n in (r.get("student_names") or [])]))
    profile = prof.build(recs, all_names, prev=store.get_profile(tid), use_llm=not args.no_llm)
    store.save_profile(tid, profile)

    md = card_markdown(teacher, profile, recs)
    out = Path(args.out) if args.out else ROOT / "data" / f"말투카드_{args.login}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print("\n" + md)
    print(f"말투 카드 저장: {out}")
    if args.dump_prompt:
        Path(args.dump_prompt).write_text(
            prompts.build_system_prompt(teacher, profile, store.get_examples(tid)), encoding="utf-8")
        print(f"시스템 프롬프트 저장: {args.dump_prompt}")
    if not llm.is_live():
        print("참고: Claude API 키가 없어 정성 분석은 생략했습니다(측정값·실제 발화는 완성).")


if __name__ == "__main__":
    main()
