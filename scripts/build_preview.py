"""체험판(정적 미리보기) 만들기 — GitHub Pages 처럼 서버 없이 여는 곳에 올릴 한 장짜리 페이지.

실제 화면(web/index.html)을 그대로 쓰고, 서버 응답 자리에 '가상 통화 3건으로 실제 파이프라인을 돌린 결과'를
심는다. 그래서 체험판의 말투 카드·전사·일치도 점수는 모두 진짜 코드가 계산한 값이다.
글쓰기 예시와 '말하는 방식' 분석은 같은 프롬프트로 Claude 가 미리 써 둔 것을 samples/ 에서 읽는다.

  python scripts/build_preview.py                      → preview/index.html
  python scripts/build_preview.py --dump-analysis DIR  → 말투 분석 프롬프트를 DIR 에 저장
  python scripts/build_preview.py --dump-prompts DIR   → 글쓰기 프롬프트(system.txt, user_*.txt)를 DIR 에 저장
  python scripts/build_preview.py --import DIR         → DIR 의 out_*.txt / out_Q.json 을 samples/ 예시로 가져오기
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import aicheck, generate, profile as prof, prompts, speakers, stt, style_stats  # noqa: E402

SAMPLES = ROOT / "samples"
OUT = ROOT / "preview"
GEN_FILE = SAMPLES / "demo_generations.json"
QUAL_FILE = SAMPLES / "demo_qualitative.json"

TEACHER = {"id": "t_demo", "login_id": "demo", "name": "김도윤", "role": "homeroom", "org": "가상중학교", "grade": "2학년"}
TITLES = {"sample_call_1_숙제": "샘플 통화 1 · 숙제", "sample_call_2_교우": "샘플 통화 2 · 친구와 다툼",
          "sample_call_3_칭찬": "샘플 통화 3 · 칭찬과 상담 안내"}
PRESETS = [
    {"id": "late", "label": "지각 안내 문자", "req": {
        "mode": "initiate", "channel": "sms", "situation": "attendance", "length": "normal", "intensity": 2,
        "variants": 2, "student": "민준", "parent_title": "어머님", "parent_message": "",
        "facts": "이번 주 화·목 두 번 지각(8시 50분쯤 등교). 본인은 늦잠이라고 함. 다음 주부터 아침에 제가 교문에서 확인 예정. "
                 "집에서 기상 시간 한 번 챙겨봐 달라고 부탁."}},
    {"id": "grade", "label": "성적 문의에 답장(카톡)", "req": {
        "mode": "reply", "channel": "kakao", "situation": "grade", "length": "normal", "intensity": 2,
        "variants": 2, "student": "수아", "parent_title": "어머님",
        "parent_message": "선생님 안녕하세요. 수아 엄마예요. 이번 기말 수학 점수가 많이 떨어져서 너무 속상하네요. "
                          "학원을 바꿔야 할지 고민인데 학교에서는 어떤지 궁금합니다.",
        "facts": "수아 기말 수학 68점(중간 82점). 서술형에서 많이 틀림. 수업 태도는 변함없이 좋음. "
                 "학원 바꾸는 건 제가 판단할 부분 아님. 방학 전에 서술형 오답 같이 볼 예정."}},
    {"id": "glasses", "label": "안경 파손 통화 대본", "req": {
        "mode": "call_script", "channel": "call", "situation": "friend", "length": "normal", "intensity": 3,
        "variants": 1, "student": "도현", "parent_title": "아버님", "parent_message": "",
        "facts": "오늘 체육 시간에 도현이가 공을 세게 던져서 같은 반 친구 안경이 부러짐. 고의는 아닌 걸로 보임. "
                 "상대 학생 다친 곳은 없음. 상대 부모님께는 제가 오늘 저녁에 먼저 연락드릴 예정. "
                 "안경 수리비 관련해서 두 가정이 통화하실 수 있게 연락처 전달해도 되는지 여쭤봐야 함."}},
]


def load_recordings() -> list[dict]:
    """샘플 녹음 → (캐시된) STT → 화자 구분. 실제 서버가 저장하는 것과 같은 모양."""
    cache = SAMPLES / "_stt_cache"
    cache.mkdir(exist_ok=True)
    recs = []
    for audio in sorted(SAMPLES.glob("*.m4a")):
        f = cache / f"{audio.stem}.json"
        if f.exists():
            res = json.loads(f.read_text(encoding="utf-8"))
        else:
            res = stt.transcribe(audio)
            f.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        units = speakers.assign_speakers(speakers.segments_to_units(res["segments"]))
        recs.append({"id": f"r_demo{len(recs) + 1}", "title": TITLES.get(audio.stem, audio.stem), "source": "audio",
                     "status": "done", "error": None, "duration": res["duration"], "student_names": [],
                     "created_at": "2026-11-20 10:00:00", "units": units})
    if not recs:
        sys.exit("samples/*.m4a 가 없습니다. 먼저 scripts/make_sample_calls.py 를 실행하세요.")
    speakers.refine_all([r["units"] for r in recs])
    # 실제 사용 흐름대로: 자동 추정 뒤 교사가 화면에서 틀린 화자를 바로잡은 상태로 만든다(정답 대본 기준)
    from eval_samples import gold_speakers
    fixed = 0
    for audio, rec in zip(sorted(SAMPLES.glob("*.m4a")), recs):
        gold, _ = gold_speakers(audio.stem, rec["units"])
        for u, g in zip(rec["units"], gold):
            if g and g != u["speaker"]:
                u["speaker"], u["manual"], u["conf"] = g, True, 1.0
                fixed += 1
    load_recordings.fixed = fixed
    return recs


def load_profile(recs: list[dict]) -> dict:
    profile = prof.build(recs, use_llm=False)
    profile["built_at"] = "2026-11-20 10:30"      # 고정: 다시 만들 때마다 시각만 바뀌어 커밋이 생기지 않게
    if QUAL_FILE.exists():
        profile["qualitative"] = json.loads(QUAL_FILE.read_text(encoding="utf-8"))
    return profile


def dump_analysis(dest: Path) -> None:
    recs = load_recordings()
    data = prof.collect(recs)
    stats = style_stats.measure(data["sent_by_rec"])
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "analysis_system.txt").write_text(prompts.ANALYSIS_SYSTEM, encoding="utf-8")
    (dest / "analysis_user.txt").write_text(prompts.build_analysis_user(stats, data["turns_by_rec"]), encoding="utf-8")
    (dest / "analysis_schema.json").write_text(json.dumps(prompts.ANALYSIS_SCHEMA, ensure_ascii=False, indent=1), encoding="utf-8")
    print("분석 프롬프트 저장:", dest)


def dump_prompts(dest: Path) -> None:
    profile = load_profile(load_recordings())
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "system.txt").write_text(prompts.build_system_prompt(TEACHER, profile, []), encoding="utf-8")
    for p in PRESETS:
        (dest / f"user_{p['id']}.txt").write_text(prompts.build_user_prompt(p["req"]), encoding="utf-8")
    print("글쓰기 프롬프트 저장:", dest, "| 정성 분석 포함:", bool(profile.get("qualitative")))


def import_outputs(src: Path) -> None:
    q = src / "out_Q.json"
    if q.exists():
        data = json.loads(q.read_text(encoding="utf-8"))
        missing = [k for k in prompts.ANALYSIS_SCHEMA["required"] if k not in data]
        if missing:
            sys.exit(f"out_Q.json 에 빠진 항목: {missing}")
        QUAL_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print("가져옴:", QUAL_FILE.name)
    items = {p["id"]: (src / f"out_{p['id']}.txt").read_text(encoding="utf-8").strip()
             for p in PRESETS if (src / f"out_{p['id']}.txt").exists()}
    if items:
        GEN_FILE.write_text(json.dumps({"note": "build_preview.py 의 PRESETS 와 같은 프롬프트로 Claude(Opus 계열)가 쓴 글",
                                        "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
        print("가져옴:", GEN_FILE.name, list(items))


def summary(rec: dict) -> dict:
    live = [u for u in rec["units"] if not u.get("deleted")]
    return {k: rec.get(k) for k in ("id", "title", "source", "status", "error", "duration", "created_at", "student_names")} | {
        "n_units": len(live), "n_teacher": sum(1 for u in live if u["speaker"] == "T"),
        "n_unsure": sum(1 for u in live if u.get("conf", 1) <= 0.5 and not u.get("manual"))}


def public_prompt(teacher: dict, profile: dict) -> str:
    """공개 체험판에는 프롬프트 앞부분(말투 요약·실제 발화)만 싣는다. 작성 원칙 전문은 설치본에서 복사된다."""
    full = prompts.build_export_prompt(teacher, profile, [])
    cut = full.find("# 쓰는 원칙")
    return (full[:cut].rstrip() + "\n\n# 쓰는 원칙\n(체험판에서는 여기까지만 보여 줍니다. 설치본에서는 글 쓰는 원칙 8가지와 출력 형식까지 전체가 복사됩니다.)"
            if cut > 0 else full)


def build() -> Path:
    if not GEN_FILE.exists():
        sys.exit(f"{GEN_FILE.name} 이 없습니다. --dump-prompts 로 프롬프트를 뽑아 글을 만든 뒤 --import 하세요.")
    recs = load_recordings()
    profile = load_profile(recs)
    gens = json.loads(GEN_FILE.read_text(encoding="utf-8"))["items"]
    presets = []
    for p in PRESETS:
        if p["id"] not in gens:
            continue
        variants = [{"text": v, "check": aicheck.style_match(v, profile, p["req"]["parent_title"])}
                    for v in generate.split_variants(gens[p["id"]])]
        presets.append({"id": p["id"], "label": p["label"], "req": p["req"], "result": {
            "provider": "claude", "variants": variants, "revised": False,
            "notices": ["체험판: 이 글은 화면에 적힌 메모와 이 프로그램의 실제 프롬프트로 Claude가 미리 써 둔 결과입니다. "
                        "설치본에서는 적은 메모로 그 자리에서 씁니다."]}})
    view = {k: v for k, v in profile.items() if k != "corpus_text"}
    view["summary"] = style_stats.summary_lines(profile["stats"])
    data = {
        "me": TEACHER,
        "health": {"stt": {"provider": "local", "ready": True, "ffmpeg": True, "whisper_filter": True,
                           "model": "(체험판)", "model_ready": True},
                   "llm": {"provider": "demo", "model": None}, "invite_required": False, "keep_audio": False,
                   "options": {"roles": prompts.ROLES, "channels": prompts.CHANNELS, "modes": prompts.MODES,
                               "situations": prompts.SITUATIONS, "lengths": prompts.LENGTHS}},
        "recordings": recs, "profile": view, "prompt": public_prompt(TEACHER, profile), "presets": presets,
    }
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    marker = '<script>\n"use strict";'
    if marker not in html:
        sys.exit("web/index.html 구조가 바뀌어 체험판 스크립트를 넣을 자리를 찾지 못했습니다.")
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    demo = "<script>\n" + DEMO_JS.replace("__DATA__", blob) + "\n</script>\n"
    html = html.replace(marker, demo + marker, 1)
    html = html.replace("<title>내말투 상담</title>",
                        "<title>내말투 상담 — 체험판</title>\n<meta name=\"robots\" content=\"noindex\">\n"
                        "<meta name=\"description\" content=\"통화 말투를 측정해 그 말투로 학부모 상담 글을 쓰는 프로그램의 화면 체험판(샘플 데이터)\">", 1)
    OUT.mkdir(exist_ok=True)
    # 줄바꿈을 LF 로 고정한다(윈도우 기본 CRLF 로 쓰면 깃에 올라간 파일과 바이트 수가 달라져 대조가 어렵다)
    (OUT / "index.html").write_text(html, encoding="utf-8", newline="\n")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    (OUT / "README.md").write_text(README, encoding="utf-8", newline="\n")
    n_var = sum(len(p["result"]["variants"]) for p in presets)
    scores = [v["check"]["score"] for p in presets for v in p["result"]["variants"]]
    print(f"체험판 생성: {OUT / 'index.html'} ({len(html.encode('utf-8')) / 1024:.0f}KB) · 통화 {len(recs)}건 · "
          f"예시 {len(presets)}개({n_var}안, 일치도 {scores}) · 정성 분석 {'포함' if profile.get('qualitative') else '없음'}"
          f" · 바로잡은 화자 {getattr(load_recordings, 'fixed', 0)}문장")
    return OUT / "index.html"


README = """# 내말투 상담 — 체험판

학부모 통화 녹음으로 선생님 본인의 말투를 측정해 두고, 그 말투로 상담 글(문자·카톡·통화 대본)을 쓰는 프로그램의 **화면 체험판**입니다.

- 가상의 교사·학생으로 만든 샘플 데이터입니다. 실제 인물이나 통화가 아닙니다.
- 말투 카드, 전사 화면, 일치도 점수는 샘플 통화 3건을 실제 프로그램으로 처리한 결과입니다. 화자 구분은 자동 추정 뒤 틀린 문장을 화면에서 바로잡은 상태입니다(실제 사용 순서와 같습니다).
- 녹음을 올려 글로 옮기는 기능과 직접 적은 메모로 글을 쓰는 기능은 서버가 필요해서 체험판에서는 동작하지 않습니다. 예시 3개로 결과 화면을 볼 수 있습니다.

이 페이지는 `scripts/build_preview.py`가 만든 한 장짜리 정적 파일입니다. 직접 고치지 말고 다시 빌드하세요.
"""

DEMO_JS = r"""/* 체험판: 서버 대신 이 데이터가 응답한다. scripts/build_preview.py 가 생성 — 직접 고치지 말 것 */
(() => {
  const D = __DATA__;
  D.examples = [];
  const NO = (what) => { throw new Error("체험판에서는 " + what + " 설치본에서 동작합니다."); };
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const summary = (r) => {
    const live = r.units.filter((u) => !u.deleted);
    return { id: r.id, title: r.title, source: r.source, status: r.status, error: r.error, duration: r.duration,
      created_at: r.created_at, student_names: r.student_names, n_units: live.length,
      n_teacher: live.filter((u) => u.speaker === "T").length,
      n_unsure: live.filter((u) => u.conf <= 0.5 && !u.manual).length };
  };
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  window.MYTONE_DEMO = async (path, opts = {}) => {
    const method = (opts.method || "GET").toUpperCase(), body = opts.body || {};
    await wait(120);
    let m;
    if (path === "/api/health") return D.health;
    if (path === "/api/me") { if (method === "DELETE") NO("계정을 지울 수 없습니다."); if (method === "PATCH") Object.assign(D.me, body); return D.me; }
    if (path === "/api/recordings") { if (method === "POST") NO("녹음을 올릴 수 없습니다."); return D.recordings.map(summary); }
    if (path === "/api/transcripts") NO("대화록을 추가할 수 없습니다.");
    if ((m = path.match(/^\/api\/recordings\/([^/]+)\/units$/))) {
      const r = D.recordings.find((x) => x.id === m[1]);
      if (body.swap_all) r.units.forEach((u) => { u.speaker = u.speaker === "T" ? "P" : "T"; u.manual = true; });
      for (const c of body.changes || []) {
        const u = r.units.find((x) => x.i === c.i); if (!u) continue;
        if (c.speaker) { u.speaker = c.speaker; u.manual = true; u.conf = 1; }
        if (c.deleted != null) u.deleted = c.deleted;
      }
      return Object.assign(summary(r), { units: r.units });
    }
    if ((m = path.match(/^\/api\/recordings\/([^/]+)\/refine$/))) NO("자동 보정을 쓸 수 없습니다.");
    if ((m = path.match(/^\/api\/recordings\/([^/]+)$/))) {
      if (method === "DELETE") NO("샘플 통화를 지울 수 없습니다.");
      const r = D.recordings.find((x) => x.id === m[1]);
      return Object.assign(summary(r), { units: r.units });
    }
    if (path === "/api/profile/build") { await wait(700); return D.profile; }
    if (path === "/api/profile/custom") { D.profile.custom = body; return D.profile; }
    if (path === "/api/profile/prompt") return { prompt: D.prompt };
    if (path === "/api/profile") return D.profile;
    if (path === "/api/generate") {
      await wait(900);
      const p = D.presets.find((x) => norm(x.req.facts) === norm(body.facts));
      return p ? p.result : { provider: "demo", variants: [], notices: [
        "체험판은 위의 예시 " + D.presets.length + "개만 보여 줍니다. 예시 버튼을 눌러 주세요. 설치본에서는 적은 메모로 바로 씁니다."] };
    }
    if (path === "/api/check") NO("고친 글을 다시 잴 수 없습니다.");
    if (path === "/api/examples") {
      if (method === "POST") { D.examples.push({ id: "e" + Date.now(), text: body.text, created_at: "방금" }); return { ok: true, count: D.examples.length }; }
      return D.examples;
    }
    if ((m = path.match(/^\/api\/examples\/(.+)$/))) { D.examples = D.examples.filter((e) => e.id !== m[1]); return { ok: true, count: D.examples.length }; }
    NO("이 기능을 쓸 수 없습니다.");
  };
  window.MYTONE_DEMO_INIT = () => {
    const mk = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; };
    const bar = mk("div", "note warn", "체험판입니다. 가상의 교사·학생으로 만든 샘플 데이터로 화면을 보여 줍니다. 녹음을 올려 글로 옮기고 직접 적은 메모로 글을 쓰는 기능은 설치본(서버)에서 동작합니다.");
    bar.style.margin = "0 0 14px";
    document.querySelector("main").prepend(bar);
    document.getElementById("btnLogout").classList.add("hide");
    const s = document.getElementById("chipStt"), l = document.getElementById("chipLlm");
    s.textContent = "음성 인식: 체험판"; s.className = "chip warn"; s.title = "";
    l.textContent = "글쓰기: 체험판(예시)"; l.className = "chip warn"; l.title = "";
    const box = mk("div", "note");
    box.append(mk("b", "", "체험판 예시 — 눌러 보세요  "));
    for (const p of D.presets) {
      const b = mk("button", "btn small", p.label); b.type = "button"; b.style.margin = "4px 6px 0 0";
      b.onclick = () => {
        const r = p.req, set = (id, v) => { document.getElementById(id).value = v; };
        setMode(r.mode); set("wChannel", r.channel); set("wSituation", r.situation); set("wLength", r.length);
        set("wIntensity", String(r.intensity)); set("wVariants", String(r.variants)); set("wStudent", r.student);
        set("wParent", r.parent_title); set("wParentMsg", r.parent_message || ""); set("wFacts", r.facts);
        document.getElementById("btnGen").click();
      };
      box.append(b);
    }
    const card = document.querySelector("#tab-write .card");
    card.insertBefore(box, card.children[1]);
  };
})();"""


def main() -> None:
    ap = argparse.ArgumentParser(description="체험판 만들기")
    ap.add_argument("--dump-analysis", metavar="DIR")
    ap.add_argument("--dump-prompts", metavar="DIR")
    ap.add_argument("--import", dest="import_dir", metavar="DIR")
    args = ap.parse_args()
    if args.dump_analysis:
        dump_analysis(Path(args.dump_analysis))
    elif args.dump_prompts:
        dump_prompts(Path(args.dump_prompts))
    elif args.import_dir:
        import_outputs(Path(args.import_dir))
    else:
        build()


if __name__ == "__main__":
    main()
