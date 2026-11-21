"""저장소 — 교사별 폴더에 JSON 으로 저장한다(DB 없이 시작, 구조는 그대로 DB 로 옮기기 쉬움).

data/
  teachers/<교사ID>/teacher.json      계정·기본 정보(PIN 은 해시만)
                    recordings/<녹음ID>.json   전사·화자 구분 결과
                    profile.json      말투 프로필
                    examples.json     교사가 확정한 글(학습용)
                    audio/            업로드 원본(전사 후 삭제가 기본)
  index.json                          로그인ID → 교사ID
교사 데이터는 서로 완전히 분리된다(다른 교사의 통화·프로필에 접근할 길이 없다).
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import config

_lock = threading.RLock()


def _root() -> Path:
    p = config.DATA_DIR / "teachers"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)       # 쓰다 끊겨도 파일이 깨지지 않게 원자적 교체


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


def teacher_dir(tid: str) -> Path:
    if not tid or not tid.replace("_", "").isalnum():
        raise ValueError("bad id")
    return _root() / tid


# ── 계정 ─────────────────────────────────────────────────────────────────────
def _index_path() -> Path:
    return config.DATA_DIR / "index.json"


def find_teacher_id(login_id: str) -> str | None:
    return (_read(_index_path(), {}) or {}).get(login_id.strip().lower())


def create_teacher(login_id: str, record: dict) -> dict:
    with _lock:
        idx = _read(_index_path(), {}) or {}
        key = login_id.strip().lower()
        if key in idx:
            raise KeyError("exists")
        tid = new_id("t_")
        record = {**record, "id": tid, "login_id": login_id.strip(), "created_at": time.strftime("%Y-%m-%d %H:%M")}
        _write(teacher_dir(tid) / "teacher.json", record)
        idx[key] = tid
        _write(_index_path(), idx)
        return record


def get_teacher(tid: str) -> dict | None:
    return _read(teacher_dir(tid) / "teacher.json")


def save_teacher(record: dict) -> None:
    with _lock:
        _write(teacher_dir(record["id"]) / "teacher.json", record)


def delete_teacher(tid: str) -> None:
    with _lock:
        t = get_teacher(tid)
        idx = _read(_index_path(), {}) or {}
        if t:
            idx.pop(t.get("login_id", "").strip().lower(), None)
            _write(_index_path(), idx)
        shutil.rmtree(teacher_dir(tid), ignore_errors=True)


# ── 녹음·전사 ────────────────────────────────────────────────────────────────
def rec_path(tid: str, rid: str) -> Path:
    if not rid.replace("_", "").isalnum():
        raise ValueError("bad id")
    return teacher_dir(tid) / "recordings" / f"{rid}.json"


def save_recording(tid: str, rec: dict) -> None:
    with _lock:
        _write(rec_path(tid, rec["id"]), rec)


def get_recording(tid: str, rid: str) -> dict | None:
    return _read(rec_path(tid, rid))


def list_recordings(tid: str) -> list[dict]:
    d = teacher_dir(tid) / "recordings"
    if not d.exists():
        return []
    recs = [r for r in (_read(p) for p in d.glob("*.json")) if r]
    return sorted(recs, key=lambda r: r.get("created_at", ""))


def delete_recording(tid: str, rid: str) -> None:
    with _lock:
        rec_path(tid, rid).unlink(missing_ok=True)


def audio_dir(tid: str) -> Path:
    p = teacher_dir(tid) / "audio"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── 프로필·확정 글 ───────────────────────────────────────────────────────────
def get_profile(tid: str) -> dict | None:
    return _read(teacher_dir(tid) / "profile.json")


def save_profile(tid: str, profile: dict) -> None:
    with _lock:
        _write(teacher_dir(tid) / "profile.json", profile)


def get_examples(tid: str) -> list[dict]:
    return _read(teacher_dir(tid) / "examples.json", []) or []


def save_examples(tid: str, examples: list[dict]) -> None:
    with _lock:
        _write(teacher_dir(tid) / "examples.json", examples[-40:])
