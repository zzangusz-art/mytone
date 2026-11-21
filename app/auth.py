"""로그인 — 로그인ID + 숫자 PIN. PIN 은 PBKDF2 해시로만 저장, 토큰은 HMAC 서명."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from . import config

_fails: dict[str, list[float]] = {}     # 로그인ID → 최근 실패 시각 (무차별 대입 방지)
MAX_FAILS, LOCK_SEC = 5, 300


def hash_pin(pin: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return salt, digest.hex()


def verify_pin(pin: str, salt: str, digest: str) -> bool:
    return hmac.compare_digest(hash_pin(pin, salt)[1], digest)


def locked(login_id: str) -> int:
    """잠겨 있으면 남은 초, 아니면 0."""
    now = time.time()
    recent = [t for t in _fails.get(login_id, []) if now - t < LOCK_SEC]
    _fails[login_id] = recent
    return int(LOCK_SEC - (now - recent[0])) if len(recent) >= MAX_FAILS else 0


def note_fail(login_id: str) -> None:
    _fails.setdefault(login_id, []).append(time.time())


def clear_fails(login_id: str) -> None:
    _fails.pop(login_id, None)


def _sign(payload: str) -> str:
    return hmac.new(config.secret_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_token(teacher_id: str) -> str:
    exp = int(time.time()) + config.TOKEN_TTL_HOURS * 3600
    payload = f"{teacher_id}.{exp}"
    return base64.urlsafe_b64encode(f"{payload}.{_sign(payload)}".encode()).decode()


def read_token(token: str) -> str | None:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        teacher_id, exp, sig = raw.rsplit(".", 2)
        if not hmac.compare_digest(sig, _sign(f"{teacher_id}.{exp}")) or int(exp) < time.time():
            return None
        return teacher_id
    except Exception:  # noqa: BLE001 — 형식이 깨진 토큰은 전부 무효
        return None
