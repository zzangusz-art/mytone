"""전체 흐름 자체 점검 — 임시 데이터 폴더에서 돌리므로 실제 데이터는 건드리지 않는다.

  1) 가입·로그인·권한 분리   2) 샘플 녹음 3개 업로드 → 실제 STT → 화자 구분
  3) 말투 프로필(측정)       4) 답변 생성(데모) + 일치도·AI 티 점검
  5) Claude 경로(모의 응답): 요청 본문이 설치된 SDK 시그니처에 맞는지까지 확인
  6) 계정 삭제 시 데이터가 실제로 지워지는지

사용: python scripts/selftest.py     (samples/*.m4a 가 없으면 먼저 make_sample_calls.py)
주의: 5)는 모의 응답이다. 실제 Claude 호출은 API 키가 있어야 검증된다.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="mytone_selftest_"))
os.environ["DATA_DIR"] = str(TMP)
os.environ["MYTONE_LLM"] = "demo"
os.environ.pop("MYTONE_CLAUDE_EFFORT", None)
os.environ.pop("INVITE_CODE", None)

from fastapi.testclient import TestClient  # noqa: E402

from app import aicheck, config, llm, main, prompts  # noqa: E402

client = TestClient(main.app)
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + (f"  — {detail}" if detail else ""))


def hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main_test() -> None:
    print("[1] 가입·로그인·권한")
    r = client.post("/api/signup", json={"login_id": "kimdoyun", "pin": "4821", "name": "김도윤", "role": "homeroom",
                                         "org": "가상중학교", "grade": "2학년"})
    check("가입", r.status_code == 200, str(r.status_code))
    tok = r.json()["token"]
    check("응답에 PIN 해시가 없음", "pin_hash" not in r.json()["teacher"])
    check("같은 아이디 재가입 거절", client.post("/api/signup", json={"login_id": "KimDoyun", "pin": "1111",
                                                                  "name": "x"}).status_code == 409)
    check("틀린 PIN 거절", client.post("/api/login", json={"login_id": "kimdoyun", "pin": "0000"}).status_code == 401)
    check("맞는 PIN 로그인", client.post("/api/login", json={"login_id": "kimdoyun", "pin": "4821"}).status_code == 200)
    check("토큰 없이 접근 차단", client.get("/api/recordings").status_code == 401)
    r2 = client.post("/api/signup", json={"login_id": "other", "pin": "9999", "name": "다른교사"})
    tok2 = r2.json()["token"]

    print("[2] 녹음 업로드 → STT → 화자 구분")
    health = client.get("/api/health").json()
    check("STT 엔진 준비", health["stt"]["ready"], json.dumps(health["stt"], ensure_ascii=False))
    samples = sorted((ROOT / "samples").glob("*.m4a"))
    check("샘플 녹음 3개 있음", len(samples) == 3)
    rids = []
    t0 = time.time()
    for s in samples:
        with s.open("rb") as f:
            r = client.post("/api/recordings", headers=hdr(tok), files={"file": (s.name, f, "audio/mp4")})
        check(f"업로드 {s.name}", r.status_code == 200, r.text[:80] if r.status_code != 200 else "")
        rids.append(r.json()["id"])
    deadline = time.time() + 300
    while time.time() < deadline:
        recs = client.get("/api/recordings", headers=hdr(tok)).json()
        if all(x["status"] in ("done", "error") for x in recs):
            break
        time.sleep(1)
    total_audio = sum(x.get("duration") or 0 for x in recs)
    check("3개 모두 전사 완료", all(x["status"] == "done" for x in recs),
          f"통화 {total_audio:.0f}초를 {time.time() - t0:.0f}초에 처리 / " + ", ".join(f"{x['n_teacher']}/{x['n_units']}문장" for x in recs))
    check("전사 후 원본 음성 삭제됨", not any((TMP / "teachers").rglob("*.m4a")))
    check("다른 교사는 내 녹음을 못 봄", client.get(f"/api/recordings/{rids[0]}", headers=hdr(tok2)).status_code == 404)
    check("다른 교사 목록은 비어 있음", client.get("/api/recordings", headers=hdr(tok2)).json() == [])
    check("오디오 아닌 파일 거절", client.post("/api/recordings", headers=hdr(tok),
                                         files={"file": ("a.exe", b"MZ", "application/octet-stream")}).status_code == 400)
    with samples[0].open("rb") as f:   # 통화 녹음 앱 식 파일명: 한글·공백·상대 전화번호 포함
        r = client.post("/api/recordings", headers=hdr(tok2),
                        files={"file": ("통화 녹음 010-1234-5678_261120_153012.m4a", f, "audio/mp4")})
    check("파일명 속 전화번호를 제목에서 가림", r.status_code == 200 and "010" not in r.json()["title"]
          and "[전화번호]" in r.json()["title"], r.json().get("title", ""))
    for _ in range(120):
        if client.get(f"/api/recordings/{r.json()['id']}", headers=hdr(tok2)).json()["status"] in ("done", "error"):
            break
        time.sleep(1)
    client.delete(f"/api/recordings/{r.json()['id']}", headers=hdr(tok2))
    check("삭제한 녹음은 목록에서 사라짐", client.get("/api/recordings", headers=hdr(tok2)).json() == [])

    rec = client.get(f"/api/recordings/{rids[0]}", headers=hdr(tok)).json()
    u0 = rec["units"][0]
    flipped = "P" if u0["speaker"] == "T" else "T"
    r = client.patch(f"/api/recordings/{rids[0]}/units", headers=hdr(tok),
                     json={"changes": [{"i": u0["i"], "speaker": flipped}]})
    check("화자 수동 수정 저장", r.json()["units"][0]["speaker"] == flipped and r.json()["units"][0].get("manual"))
    client.patch(f"/api/recordings/{rids[0]}/units", headers=hdr(tok), json={"changes": [{"i": u0["i"], "speaker": u0["speaker"]}]})

    pasted = "참석자 1 00:01\n어머님 안녕하세요 민지 담임이에요.\n참석자 2 00:05\n네 선생님 안녕하세요.\n참석자 1 00:09\n민지가 요즘 숙제를 잘 해오거든요."
    r = client.post("/api/transcripts", headers=hdr(tok2), json={"title": "붙여넣기", "text": pasted})
    pr = client.get(f"/api/recordings/{r.json()['id']}", headers=hdr(tok2)).json()
    check("붙여넣은 대화록 화자 인식(참석자 1=교사)", [u["speaker"] for u in pr["units"]] == ["T", "P", "T"],
          str([u["speaker"] for u in pr["units"]]))

    print("[3] 말투 프로필")
    r = client.post("/api/profile/build", headers=hdr(tok), json={"use_llm": True})
    check("프로필 생성", r.status_code == 200, r.text[:120] if r.status_code != 200 else "")
    p = r.json()
    st = p["stats"]
    top = [e["label"] for e in st["endings"][:3]]
    check("심어둔 종결 '~거든요'가 상위", "~거든요" in top, f"상위 종결: {top}")
    words = {m["word"]: m["count"] for m in st["markers"]}
    check("말머리 '일단은'·'사실' 검출", words.get("일단은", 0) >= 4 and words.get("사실", 0) >= 3, str(words))
    check("학부모 호칭 '어머님' 1위", st["address"]["parent"][0]["form"] == "어머님")
    check("학생 이름 3명 자동 가림", set(p["names_masked"]) >= {"서준", "하윤", "지호"}, str(p["names_masked"]))
    blob = json.dumps(p["exemplars"], ensure_ascii=False)
    check("예시 발화에 실명이 남지 않음", not any(n in blob for n in ("서준", "하윤", "지호")))
    funcs = {e["function"] for e in p["exemplars"]}
    check("예시에 시작·마무리·걱정·부탁 포함", {"opener", "closer", "concern", "request"} <= funcs, str(sorted(funcs)))
    check("응답에 원문 말뭉치(corpus_text) 미포함", "corpus_text" not in p)
    for ln in p["summary"]:
        print("       ·", ln)

    print("[4] 답변 생성(데모) + 점검")
    body = {"mode": "initiate", "channel": "sms", "situation": "homework", "student": "민준", "parent_title": "어머님",
            "facts": "이번 주 수학 숙제 2번 안 해옴. 어제 본인과 얘기함. 다음 주까지 지켜보고 다시 연락 예정."}
    r = client.post("/api/generate", headers=hdr(tok), json=body)
    check("데모 생성 응답", r.status_code == 200 and r.json()["provider"] == "demo")
    check("데모임을 화면에 알림", any("데모" in n for n in r.json()["notices"]))
    check("메모 없으면 거절", client.post("/api/generate", headers=hdr(tok), json={"facts": ""}).status_code == 400)
    check("프로필 없는 교사는 생성 불가", client.post("/api/generate", headers=hdr(tok2), json=body).status_code == 400)

    ai_text = ("안녕하세요, 학부모님. 소중한 자녀의 학습 태도에 대해 말씀드리고자 합니다. 또한 가정에서도 지속적인 관심을 "
               "부탁드립니다. 함께 노력하겠습니다. 언제든지 편하게 연락 주시기 바랍니다.")
    my_text = ("어머님 안녕하세요, 민준이 담임이에요.\n다름이 아니라 민준이가 이번 주에 수학 숙제를 두 번 안 해왔거든요. "
               "일단은 큰일은 아니고요, 어제 민준이랑 잠깐 얘기는 해봤어요.\n제가 다음 주까지 좀 더 지켜볼게요. "
               "집에서 저녁에 한 번만 물어봐 주시면 좋을 것 같아요.\n그럼 또 연락드릴게요.")
    a = client.post("/api/check", headers=hdr(tok), json={"text": ai_text, "parent_title": "어머님"}).json()
    m = client.post("/api/check", headers=hdr(tok), json={"text": my_text, "parent_title": "어머님"}).json()
    check("AI투 글은 점수가 낮고 티 표현이 잡힘", a["score"] < 50 and len(a["tells"]) >= 4,
          f"{a['score']}점, {[t['label'] for t in a['tells']]}")
    check("선생님 말투 글은 점수가 높음", m["score"] >= 80 and not m["tells"], f"{m['score']}점 {m['parts']}")
    check("두 글의 점수 차가 뚜렷함", m["score"] - a["score"] >= 40, f"{m['score']} vs {a['score']}")

    exp = client.get("/api/profile/prompt", headers=hdr(tok)).json()["prompt"]
    check("내보내기 프롬프트에 실제 발화·측정값 포함", "<실제_발화>" in exp and "~거든요" in exp and "김도윤" in exp,
          f"{len(exp)}자")
    r = client.post("/api/examples", headers=hdr(tok), json={"text": my_text.replace("민준", "서준"), "channel": "sms"})
    ex = client.get("/api/examples", headers=hdr(tok)).json()
    check("확정 글 저장 + 이름 가림", r.status_code == 200 and "서준" not in ex[0]["text"] and "{학생}" in ex[0]["text"])
    check("확정 글이 프롬프트에 반영", "<확정한_글>" in client.get("/api/profile/prompt", headers=hdr(tok)).json()["prompt"])

    print("[5] Claude 경로 (모의 응답 — 실제 호출 아님)")
    import anthropic
    real = anthropic.Anthropic(api_key="selftest-not-a-real-key")
    sig_beta = inspect.signature(real.beta.messages.create)
    sig_std = inspect.signature(real.messages.create)
    calls: list[dict] = []

    def fake_create(sig):
        def _create(**kw):
            sig.bind(**kw)                                   # SDK 가 모르는 인자가 있으면 여기서 TypeError
            calls.append(kw)
            fmt = (kw.get("output_config") or {}).get("format")
            if fmt:
                props = fmt["schema"]["properties"]
                if "labels" in props:
                    text = json.dumps({"labels": []})
                else:
                    text = json.dumps({k: ([] if v["type"] == "array" else "발화에서 확인되지 않음") for k, v in props.items()},
                                      ensure_ascii=False)
            elif "<초안>" in str(kw["messages"][-1]["content"]):
                text = my_text
            else:
                text = ai_text + "\n=====\n" + my_text
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                                   model=kw["model"], usage=SimpleNamespace(input_tokens=3000, output_tokens=400,
                                                                            cache_read_input_tokens=0,
                                                                            cache_creation_input_tokens=2800))
        return _create

    fake = SimpleNamespace(messages=SimpleNamespace(create=fake_create(sig_std)),
                           beta=SimpleNamespace(messages=SimpleNamespace(create=fake_create(sig_beta))))
    config.LLM_PROVIDER = "claude"
    llm._client = fake
    try:
        r = client.post("/api/generate", headers=hdr(tok), json=body)
        j = r.json()
        check("Claude 경로 생성 응답", r.status_code == 200 and j["provider"] == "claude", r.text[:150])
        check("두 안으로 분리", len(j["variants"]) == 2)
        check("AI투 초안을 감지해 1회 고쳐 씀", j.get("revised") is True and j["variants"][0]["check"]["score"] >= 80,
              f"고친 뒤 {j['variants'][0]['check']['score']}점")
        first = calls[0]
        check("요청: 모델·적응형 사고·effort", first["model"] == config.CLAUDE_MODEL and first["thinking"] == {"type": "adaptive"}
              and first["output_config"]["effort"] == config.CLAUDE_EFFORT, f"{first['model']}, effort={first['output_config']['effort']}")
        check("요청: 거절 대비 fallbacks 사용", first.get("fallbacks") == "default" and first.get("betas") == [llm.FALLBACK_BETA])
        check("요청: 시스템 프롬프트 캐시 지정", first["system"][0]["cache_control"] == {"type": "ephemeral"})
        check("요청: 샘플링 인자(temperature 등) 없음", not ({"temperature", "top_p", "top_k"} & set(first)))
        check("고쳐 쓰기 요청이 이전 대화를 이어감", len(calls[1]["messages"]) == 3 and calls[1]["messages"][1]["role"] == "assistant")
        r = client.post("/api/profile/build", headers=hdr(tok), json={"use_llm": True})
        check("정성 분석(JSON 스키마) 경로", r.status_code == 200 and r.json()["qualitative"] is not None)
        sys_prompt = calls[0]["system"][0]["text"]
        check("시스템 프롬프트 길이 적정", 1500 < len(sys_prompt) < 12000, f"{len(sys_prompt)}자")
    finally:
        config.LLM_PROVIDER = "demo"
        llm._client = None

    print("[6] 계정 삭제")
    tdir = TMP / "teachers" / client.get("/api/me", headers=hdr(tok)).json()["id"]
    check("삭제 전 교사 폴더 존재", (tdir / "profile.json").exists())
    check("계정 삭제 응답", client.delete("/api/me", headers=hdr(tok)).status_code == 200)
    check("삭제 후 토큰 무효", client.get("/api/me", headers=hdr(tok)).status_code == 401)
    check("교사 폴더(전사·프로필·확정 글)가 실제로 지워짐", not tdir.exists())
    check("삭제 후 같은 아이디로 로그인 불가", client.post("/api/login", json={"login_id": "kimdoyun", "pin": "4821"}).status_code == 401)
    check("다른 교사 데이터는 그대로", client.get("/api/me", headers=hdr(tok2)).status_code == 200)

    print(f"\n결과: 통과 {len(PASS)} / 실패 {len(FAIL)}")
    if FAIL:
        print("실패 항목:", ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    try:
        main_test()
    finally:
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)
