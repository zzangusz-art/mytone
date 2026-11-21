"""내말투 상담 — API 서버 + 화면.

실행:  python -m app.main   (기본 http://127.0.0.1:8700)
"""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import aicheck, auth, config, generate as gen, llm, pii, profile as prof, prompts, speakers, store, stt, style_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mytone")
for _noisy in ("httpx", "httpx2", "anthropic"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

app = FastAPI(title="내말투 상담", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS or [],     # 같은 주소에서 화면을 내려주므로 기본은 교차 출처 차단
    allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def _headers(request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ── 인증 ─────────────────────────────────────────────────────────────────────
def current_teacher(authorization: str = Header(default="")) -> dict:
    token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    tid = auth.read_token(token) if token else None
    teacher = store.get_teacher(tid) if tid else None
    if not teacher:
        raise HTTPException(401, "로그인이 필요합니다.")
    return teacher


def _public(teacher: dict) -> dict:
    return {k: v for k, v in teacher.items() if k not in ("pin_hash", "pin_salt")}


class SignupBody(BaseModel):
    login_id: str = Field(min_length=3, max_length=20)
    pin: str = Field(min_length=4, max_length=8)
    name: str = Field(min_length=1, max_length=20)
    role: str = "homeroom"
    org: str = Field(default="", max_length=40)
    grade: str = Field(default="", max_length=30)
    invite: str = ""


class LoginBody(BaseModel):
    login_id: str
    pin: str


@app.post("/api/signup")
def signup(body: SignupBody):
    if config.INVITE_CODE and body.invite.strip() != config.INVITE_CODE:
        raise HTTPException(403, "초대 코드가 맞지 않습니다.")
    if not re.fullmatch(r"[A-Za-z0-9가-힣_.\-]{3,20}", body.login_id.strip()):
        raise HTTPException(400, "아이디는 한글·영문·숫자 3~20자로 정해 주세요.")
    if not body.pin.isdigit():
        raise HTTPException(400, "비밀번호(PIN)는 숫자 4~8자리입니다.")
    salt, digest = auth.hash_pin(body.pin)
    try:
        teacher = store.create_teacher(body.login_id, {
            "name": body.name.strip(), "role": body.role if body.role in prompts.ROLES else "other",
            "org": body.org.strip(), "grade": body.grade.strip(), "pin_salt": salt, "pin_hash": digest})
    except KeyError:
        raise HTTPException(409, "이미 쓰고 있는 아이디입니다.") from None
    return {"token": auth.make_token(teacher["id"]), "teacher": _public(teacher)}


@app.post("/api/login")
def login(body: LoginBody):
    key = body.login_id.strip().lower()
    wait = auth.locked(key)
    if wait:
        raise HTTPException(429, f"여러 번 틀려 잠겼습니다. {wait // 60 + 1}분 뒤 다시 시도해 주세요.")
    tid = store.find_teacher_id(key)
    teacher = store.get_teacher(tid) if tid else None
    if not teacher or not auth.verify_pin(body.pin, teacher["pin_salt"], teacher["pin_hash"]):
        auth.note_fail(key)
        raise HTTPException(401, "아이디 또는 비밀번호가 맞지 않습니다.")
    auth.clear_fails(key)
    return {"token": auth.make_token(teacher["id"]), "teacher": _public(teacher)}


class MeBody(BaseModel):
    name: str | None = Field(default=None, max_length=20)
    role: str | None = None
    org: str | None = Field(default=None, max_length=40)
    grade: str | None = Field(default=None, max_length=30)


@app.get("/api/me")
def me(teacher: dict = Depends(current_teacher)):
    return _public(teacher)


@app.patch("/api/me")
def update_me(body: MeBody, teacher: dict = Depends(current_teacher)):
    for k, v in body.model_dump(exclude_none=True).items():
        if k == "role" and v not in prompts.ROLES:
            continue
        teacher[k] = v.strip()
    store.save_teacher(teacher)
    return _public(teacher)


@app.delete("/api/me")
def delete_me(teacher: dict = Depends(current_teacher)):
    store.delete_teacher(teacher["id"])       # 통화 전사·프로필·확정 글까지 전부 삭제
    return {"ok": True}


@app.get("/api/health")
def health():
    st = stt.engine_status()
    return {
        "stt": {k: st[k] for k in ("provider", "ready", "ffmpeg", "whisper_filter", "model", "model_ready")},
        "llm": {"provider": llm.provider(), "model": config.CLAUDE_MODEL if llm.is_live() else None},
        "invite_required": bool(config.INVITE_CODE),
        "keep_audio": config.KEEP_AUDIO,
        "options": {"roles": prompts.ROLES, "channels": prompts.CHANNELS, "modes": prompts.MODES,
                    "situations": prompts.SITUATIONS, "lengths": prompts.LENGTHS},
    }


# ── 녹음 올리기·전사 ─────────────────────────────────────────────────────────
def _summary(rec: dict) -> dict:
    units = rec.get("units") or []
    live = [u for u in units if not u.get("deleted")]
    return {k: rec.get(k) for k in ("id", "title", "source", "status", "error", "duration", "created_at",
                                    "student_names")} | {
        "n_units": len(live), "n_teacher": sum(1 for u in live if u["speaker"] == "T"),
        "n_unsure": sum(1 for u in live if u.get("conf", 1) <= 0.5 and not u.get("manual")),
    }


def _names(raw: str) -> list[str]:
    return [n for n in (x.strip() for x in re.split(r"[,\s/]+", raw or "")) if 1 < len(n) <= 5][:10]


def _finish_units(tid: str, rid: str, units: list[dict]) -> list[dict]:
    """같은 교사의 다른 녹음에서 배운 말버릇으로 화자 추정을 한 번 더 다듬는다."""
    others = [r.get("units", []) for r in store.list_recordings(tid) if r.get("status") == "done" and r["id"] != rid]
    habit = speakers.learn_habits(others + [units])
    if habit:
        speakers.assign_speakers(units, habit=habit)
    return units


def _transcribe_job(tid: str, rid: str, audio: Path) -> None:
    rec = store.get_recording(tid, rid)
    if not rec:
        return
    rec["status"] = "transcribing"
    store.save_recording(tid, rec)
    t0 = time.time()
    try:
        res = stt.transcribe(audio)
        units = speakers.assign_speakers(speakers.segments_to_units(res["segments"]))
        if not units:
            raise stt.STTError("말소리를 찾지 못했습니다. 녹음 파일을 확인해 주세요.")
        rec.update(status="done", duration=res["duration"], units=_finish_units(tid, rid, units),
                   stt={"provider": res["provider"], "model": res["model"], "seconds": round(time.time() - t0, 1)})
    except stt.STTError as e:
        rec.update(status="error", error=str(e))
    except Exception:  # noqa: BLE001
        log.exception("전사 실패 %s", rid)
        rec.update(status="error", error="전사 중 오류가 났습니다. 파일 형식을 확인하고 다시 올려 주세요.")
    finally:
        if not config.KEEP_AUDIO:
            audio.unlink(missing_ok=True)      # 통화 원본은 전사가 끝나면 지운다
    store.save_recording(tid, rec)


@app.post("/api/recordings")
async def upload_recording(file: UploadFile = File(...), student_names: str = Form(default=""),
                           teacher: dict = Depends(current_teacher)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in stt.AUDIO_EXTS:
        raise HTTPException(400, f"지원하지 않는 형식입니다({ext or '확장자 없음'}). m4a·mp3·wav 등 음성 파일을 올려 주세요.")
    tid, rid = teacher["id"], store.new_id("r_")
    dest = store.audio_dir(tid) / f"{rid}{ext}"
    size, limit = 0, config.MAX_UPLOAD_MB * 1024 * 1024
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"파일이 너무 큽니다(최대 {config.MAX_UPLOAD_MB}MB).")
            out.write(chunk)
    # 통화 녹음 앱은 파일명에 상대 전화번호를 넣는다("통화 녹음 010-1234-5678_…") → 제목에서 가린다
    title = pii.mask_basic(Path(file.filename or "녹음").stem)[:60] or "녹음"
    rec = {"id": rid, "title": title, "source": "audio", "status": "queued",
           "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "student_names": _names(student_names), "units": []}
    store.save_recording(tid, rec)
    threading.Thread(target=_transcribe_job, args=(tid, rid, dest), daemon=True).start()
    return _summary(rec)


class TranscriptBody(BaseModel):
    title: str = Field(default="붙여넣은 대화", max_length=60)
    text: str = Field(min_length=20, max_length=200_000)
    student_names: str = ""


@app.post("/api/transcripts")
def add_transcript(body: TranscriptBody, teacher: dict = Depends(current_teacher)):
    """이미 글로 된 통화 기록(클로바노트·통화 녹음 앱의 텍스트 등)을 붙여 넣는 경로."""
    units = speakers.parse_pasted_transcript(body.text)
    if not units:
        raise HTTPException(400, "문장을 찾지 못했습니다.")
    tid, rid = teacher["id"], store.new_id("r_")
    rec = {"id": rid, "title": body.title.strip() or "붙여넣은 대화", "source": "text", "status": "done",
           "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "student_names": _names(body.student_names),
           "duration": None, "units": units}
    store.save_recording(tid, rec)
    return _summary(rec)


@app.get("/api/recordings")
def list_recordings(teacher: dict = Depends(current_teacher)):
    return [_summary(r) for r in store.list_recordings(teacher["id"])]


def _rec_or_404(tid: str, rid: str) -> dict:
    try:
        rec = store.get_recording(tid, rid)
    except ValueError:
        rec = None
    if not rec:
        raise HTTPException(404, "녹음을 찾을 수 없습니다.")
    return rec


@app.get("/api/recordings/{rid}")
def get_recording(rid: str, teacher: dict = Depends(current_teacher)):
    rec = _rec_or_404(teacher["id"], rid)
    return _summary(rec) | {"units": rec.get("units", [])}


class UnitChange(BaseModel):
    i: int
    speaker: str | None = None
    deleted: bool | None = None
    text: str | None = Field(default=None, max_length=600)


class UnitsBody(BaseModel):
    changes: list[UnitChange] = []
    swap_all: bool = False
    student_names: str | None = None


@app.patch("/api/recordings/{rid}/units")
def patch_units(rid: str, body: UnitsBody, teacher: dict = Depends(current_teacher)):
    rec = _rec_or_404(teacher["id"], rid)
    by_i = {u["i"]: u for u in rec.get("units", [])}
    if body.swap_all:                        # 교사·학부모가 통째로 뒤바뀐 경우
        for u in by_i.values():
            u["speaker"] = "P" if u["speaker"] == "T" else "T"
            u["manual"] = True
    for ch in body.changes:
        u = by_i.get(ch.i)
        if not u:
            continue
        if ch.speaker in ("T", "P"):
            u["speaker"], u["manual"], u["conf"] = ch.speaker, True, 1.0
        if ch.deleted is not None:
            u["deleted"] = ch.deleted
        if ch.text is not None and ch.text.strip():
            u["text"] = ch.text.strip()
    if body.student_names is not None:
        rec["student_names"] = _names(body.student_names)
    store.save_recording(teacher["id"], rec)
    return _summary(rec) | {"units": rec.get("units", [])}


@app.post("/api/recordings/{rid}/refine")
def refine_recording(rid: str, teacher: dict = Depends(current_teacher)):
    """Claude 로 화자 구분을 한 번 더 확인(키 있을 때만)."""
    rec = _rec_or_404(teacher["id"], rid)
    if not llm.is_live():
        raise HTTPException(400, "Claude API 키가 없어 자동 보정을 쓸 수 없습니다. 화면에서 직접 고쳐 주세요.")
    try:
        changed = prof.refine_speakers_with_llm(rec.get("units", []))
    except llm.LLMError as e:
        raise HTTPException(502, str(e)) from e
    store.save_recording(teacher["id"], rec)
    return _summary(rec) | {"units": rec.get("units", []), "changed": changed}


@app.delete("/api/recordings/{rid}")
def delete_recording(rid: str, teacher: dict = Depends(current_teacher)):
    _rec_or_404(teacher["id"], rid)
    store.delete_recording(teacher["id"], rid)
    return {"ok": True}


# ── 말투 프로필 ──────────────────────────────────────────────────────────────
def _profile_view(profile: dict) -> dict:
    view = {k: v for k, v in profile.items() if k != "corpus_text"}
    view["summary"] = style_stats.summary_lines(profile.get("stats") or {})
    return view


class BuildBody(BaseModel):
    use_llm: bool = True


@app.post("/api/profile/build")
def build_profile(body: BuildBody, teacher: dict = Depends(current_teacher)):
    recs = [r for r in store.list_recordings(teacher["id"]) if r.get("status") == "done"]
    if not recs:
        raise HTTPException(400, "먼저 통화 녹음을 1개 이상 올려 주세요.")
    names = [n for r in recs for n in (r.get("student_names") or [])]
    try:
        profile = prof.build(recs, names, prev=store.get_profile(teacher["id"]), use_llm=body.use_llm)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    store.save_profile(teacher["id"], profile)
    return _profile_view(profile)


@app.get("/api/profile")
def get_profile(teacher: dict = Depends(current_teacher)):
    profile = store.get_profile(teacher["id"])
    if not profile:
        raise HTTPException(404, "아직 말투 프로필이 없습니다.")
    return _profile_view(profile)


class CustomBody(BaseModel):
    always: list[str] = Field(default=[], max_length=20)
    banned: list[str] = Field(default=[], max_length=30)
    notes: str = Field(default="", max_length=500)
    emoji: bool = False


@app.patch("/api/profile/custom")
def patch_custom(body: CustomBody, teacher: dict = Depends(current_teacher)):
    profile = store.get_profile(teacher["id"])
    if not profile:
        raise HTTPException(404, "아직 말투 프로필이 없습니다.")
    profile["custom"] = {"always": [s.strip()[:40] for s in body.always if s.strip()],
                         "banned": [s.strip()[:40] for s in body.banned if s.strip()],
                         "notes": body.notes.strip(), "emoji": body.emoji}
    store.save_profile(teacher["id"], profile)
    return _profile_view(profile)


@app.get("/api/profile/prompt")
def export_prompt(teacher: dict = Depends(current_teacher)):
    profile = store.get_profile(teacher["id"])
    if not profile:
        raise HTTPException(404, "아직 말투 프로필이 없습니다.")
    return {"prompt": prompts.build_export_prompt(teacher, profile, store.get_examples(teacher["id"]))}


# ── 상담 답변 만들기 ─────────────────────────────────────────────────────────
class GenerateBody(BaseModel):
    mode: str = "initiate"
    channel: str = "sms"
    situation: str = "etc"
    length: str = "normal"
    intensity: int = Field(default=2, ge=1, le=3)
    variants: int = Field(default=2, ge=1, le=3)
    student: str = Field(default="", max_length=10)
    parent_title: str = Field(default="", max_length=12)
    parent_message: str = Field(default="", max_length=4000)
    facts: str = Field(default="", max_length=4000)


def _with_examples(profile: dict, examples: list[dict]) -> dict:
    """확정한 글에 나온 표현도 '선생님이 쓰는 말'로 인정해 AI 티 판정에서 뺀다."""
    p = dict(profile)
    p["corpus_text"] = (profile.get("corpus_text") or "") + "\n" + "\n".join(e["text"] for e in examples)
    return p


@app.post("/api/generate")
def generate(body: GenerateBody, teacher: dict = Depends(current_teacher)):
    profile = store.get_profile(teacher["id"])
    if not profile:
        raise HTTPException(400, "먼저 '내 말투 등록'에서 말투 프로필을 만들어 주세요.")
    if not body.facts.strip() and not body.parent_message.strip():
        raise HTTPException(400, "전할 내용 메모나 학부모가 보낸 글 중 하나는 적어 주세요.")
    examples = store.get_examples(teacher["id"])
    try:
        return gen.generate(teacher, _with_examples(profile, examples), examples, body.model_dump())
    except llm.LLMError as e:
        raise HTTPException(502, str(e)) from e


class CheckBody(BaseModel):
    text: str = Field(min_length=1, max_length=6000)
    parent_title: str = ""


@app.post("/api/check")
def check_text(body: CheckBody, teacher: dict = Depends(current_teacher)):
    """교사가 고친 글(또는 다른 곳에서 쓴 글)의 일치도·AI 티를 다시 잰다."""
    profile = store.get_profile(teacher["id"])
    if not profile:
        raise HTTPException(400, "말투 프로필이 없습니다.")
    return aicheck.style_match(body.text, _with_examples(profile, store.get_examples(teacher["id"])), body.parent_title)


class ExampleBody(BaseModel):
    text: str = Field(min_length=5, max_length=4000)
    channel: str = "sms"
    situation: str = "etc"


@app.get("/api/examples")
def list_examples(teacher: dict = Depends(current_teacher)):
    return store.get_examples(teacher["id"])


@app.post("/api/examples")
def add_example(body: ExampleBody, teacher: dict = Depends(current_teacher)):
    """교사가 손봐서 '이게 내 말투'라고 확정한 글 → 다음 생성부터 기준으로 쓴다."""
    examples = store.get_examples(teacher["id"])
    profile = store.get_profile(teacher["id"]) or {}
    text = pii.mask_all(body.text.strip(), profile.get("names_masked") or [])
    examples.append({"id": store.new_id("e_"), "text": text, "channel": body.channel, "situation": body.situation,
                     "created_at": time.strftime("%Y-%m-%d %H:%M")})
    store.save_examples(teacher["id"], examples)
    return {"ok": True, "count": len(examples)}


@app.delete("/api/examples/{eid}")
def delete_example(eid: str, teacher: dict = Depends(current_teacher)):
    examples = [e for e in store.get_examples(teacher["id"]) if e["id"] != eid]
    store.save_examples(teacher["id"], examples)
    return {"ok": True, "count": len(examples)}


# ── 화면 ─────────────────────────────────────────────────────────────────────
@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html", media_type="text/html; charset=utf-8")


def main() -> None:
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="내말투 상담 서버")
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--data", default="", help="데이터 폴더(기본: 프로그램 폴더의 data/)")
    args = ap.parse_args()
    config.PORT, config.HOST = args.port, args.host
    if args.data:
        config.DATA_DIR = Path(args.data)

    print(f"\n  내말투 상담  →  http://{config.HOST}:{config.PORT}\n"
          f"  STT: {'준비됨' if stt.engine_status()['ready'] else '미준비(0_SETUP.bat 실행)'}"
          f" / LLM: {llm.provider()}{' (' + config.CLAUDE_MODEL + ')' if llm.is_live() else ' — 키 없음, 데모'}\n")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
