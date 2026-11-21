"""환경설정 — .env 또는 OS 환경변수에서 읽는다.

기본값은 키 없이도 도는 구성(STT=내 PC의 ffmpeg Whisper, LLM=데모)이고,
ANTHROPIC_API_KEY 가 있으면 자동으로 Claude 를 쓴다.
비밀값은 코드/깃에 넣지 않는다(.env 는 .gitignore 대상, 서버는 환경변수로 주입).
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """python-dotenv 없이 .env 간단 로드. 이미 설정된 환경변수가 우선."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(Path(os.environ.get("ENV_FILE", ROOT / ".env")))


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _default_home() -> Path:
    # 프로그램 폴더를 옮겨도 다시 받지 않도록, 모델·가상환경은 사용자 폴더의 짧은 경로에 둔다.
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "MyTone" if base else Path.home() / ".mytone"


HOME_DIR = Path(_get("MYTONE_HOME") or _default_home())
DATA_DIR = Path(_get("DATA_DIR") or ROOT / "data")
MODELS_DIR = Path(_get("MODELS_DIR") or HOME_DIR / "models")
WEB_DIR = ROOT / "web"

# ----- STT (음성 → 글) -----
STT_PROVIDER = _get("STT_PROVIDER", "local")                   # local (ffmpeg 내장 whisper.cpp)
FFMPEG = _get("FFMPEG_PATH", "ffmpeg")
FFPROBE = _get("FFPROBE_PATH", "ffprobe")
WHISPER_MODEL = _get("WHISPER_MODEL", "ggml-large-v3-turbo-q5_0.bin")
WHISPER_MODEL_URL = _get(
    "WHISPER_MODEL_URL",
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
)
WHISPER_LANGUAGE = _get("WHISPER_LANGUAGE", "ko")
WHISPER_USE_GPU = _get("WHISPER_USE_GPU", "1") != "0"
STT_MAX_CHUNK_SEC = int(_get("STT_MAX_CHUNK_SEC", "1800"))      # 한 번에 넘기는 최대 길이(30분)
KEEP_AUDIO = _get("KEEP_AUDIO", "0") == "1"                     # 기본: 전사 후 원본 음성 삭제
MAX_UPLOAD_MB = int(_get("MAX_UPLOAD_MB", "200"))

# ----- LLM -----
ANTHROPIC_KEY_PRESENT = bool(_get("ANTHROPIC_API_KEY") or _get("ANTHROPIC_AUTH_TOKEN"))
LLM_PROVIDER = _get("MYTONE_LLM", "auto").lower()             # auto | claude | demo
if LLM_PROVIDER == "auto":
    LLM_PROVIDER = "claude" if ANTHROPIC_KEY_PRESENT else "demo"
# 이름에 MYTONE_ 를 붙인다: CLAUDE_EFFORT 같은 이름은 Claude Code 등 다른 도구의 환경변수와 겹친다
CLAUDE_MODEL = _get("MYTONE_CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = _get("MYTONE_CLAUDE_EFFORT", "medium")                 # low | medium | high
CLAUDE_FALLBACKS = _get("MYTONE_CLAUDE_FALLBACKS", "default").lower()  # default | off (거절 시 대체 모델)

# ----- 서버/보안 -----
PORT = int(_get("PORT", "8700"))
HOST = _get("HOST", "127.0.0.1")
ALLOWED_ORIGINS = [o.strip() for o in _get("ALLOWED_ORIGINS").split(",") if o.strip()]
INVITE_CODE = _get("INVITE_CODE")                               # 설정하면 가입 시 초대코드 필요
TOKEN_TTL_HOURS = int(_get("TOKEN_TTL_HOURS", "720"))


def secret_key() -> bytes:
    """토큰 서명 키. 환경변수 없으면 data/ 에 1회 생성해 재사용."""
    env = _get("SECRET_KEY")
    if env:
        return env.encode("utf-8")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = DATA_DIR / ".secret_key"
    if not f.exists():
        f.write_text(secrets.token_hex(32), encoding="utf-8")
    return f.read_text(encoding="utf-8").strip().encode("utf-8")
