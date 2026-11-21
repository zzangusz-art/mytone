"""STT — 통화 녹음을 글로 옮긴다.

엔진: ffmpeg 8 에 내장된 whisper 필터(whisper.cpp). 별도 파이썬 네이티브 모듈이 없어
Windows 앱 제어 정책(Smart App Control)에 걸리지 않고, 음성이 PC 밖으로 나가지 않는다.

통화 전체를 한 번에 넘기는 방식(queue = 통화 길이)을 쓴다. 실측에서 VAD 분할보다
20배 이상 빠르고(2분 통화 ≈ 6초) 문장 경계도 더 자연스러웠다.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import uuid
from pathlib import Path

from . import config

_LOCK = threading.Lock()  # GPU/CPU 경합 방지: 한 번에 한 건만 전사

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".amr", ".3gp", ".ogg", ".opus", ".flac",
              ".mp4", ".webm", ".wma", ".mov", ".3ga", ".caf"}

# 무음·잡음 구간에서 Whisper 가 지어내는 대표 문구(한국어) — 통화에 나올 리 없는 것만
_HALLUCINATION = re.compile(
    r"(시청해\s*주셔서\s*감사|구독과?\s*좋아요|자막\s*(제공|제작)|MBC\s*뉴스|KBS\s*뉴스|SBS\s*뉴스|"
    r"다음\s*영상에서\s*만나|한글자막|영상편집)", re.I)


class STTError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 3600) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout)
    except FileNotFoundError as e:
        raise STTError("ffmpeg 를 찾을 수 없습니다. ffmpeg 8 이상을 설치해 주세요 (winget install ffmpeg).") from e
    except subprocess.TimeoutExpired as e:
        raise STTError("전사 시간이 너무 오래 걸려 중단했습니다.") from e


def engine_status() -> dict:
    """화면·헬스체크용: 엔진이 실제로 쓸 수 있는 상태인지."""
    model = config.MODELS_DIR / config.WHISPER_MODEL
    ffmpeg_ok = whisper_filter = False
    version = ""
    try:
        p = _run([config.FFMPEG, "-hide_banner", "-filters"], timeout=30)
        ffmpeg_ok = p.returncode == 0
        whisper_filter = b" whisper " in p.stdout
        v = _run([config.FFMPEG, "-hide_banner", "-version"], timeout=30)
        version = v.stdout.decode("utf-8", "replace").splitlines()[0][:60] if v.stdout else ""
    except STTError:
        pass
    return {
        "provider": config.STT_PROVIDER,
        "ffmpeg": ffmpeg_ok,
        "ffmpeg_version": version,
        "whisper_filter": whisper_filter,
        "model": config.WHISPER_MODEL,
        "model_ready": model.exists() and model.stat().st_size > 50_000_000,
        "ready": ffmpeg_ok and whisper_filter and model.exists(),
    }


def probe_duration(path: Path) -> float:
    p = _run([config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
              "-of", "csv=p=0", str(path)], timeout=60)
    try:
        return float(p.stdout.decode().strip())
    except ValueError as e:
        raise STTError("음성 파일을 읽을 수 없습니다. 형식을 확인해 주세요.") from e


def _clean_segments(rows: list[dict], offset_ms: int = 0) -> list[dict]:
    out: list[dict] = []
    prev = ""
    repeat = 0
    for r in rows:
        text = re.sub(r"\s+", " ", str(r.get("text", ""))).strip()
        if not text or _HALLUCINATION.search(text):
            continue
        if text == prev:           # 같은 문장이 연달아 반복 = Whisper 루프
            repeat += 1
            if repeat >= 1:
                continue
        else:
            repeat = 0
        prev = text
        out.append({
            "start": round((int(r.get("start", 0)) + offset_ms) / 1000, 2),
            "end": round((int(r.get("end", 0)) + offset_ms) / 1000, 2),
            "text": text,
        })
    return out


def _transcribe_piece(src: Path, start: float, length: float) -> list[dict]:
    work = config.MODELS_DIR / "_work"
    work.mkdir(parents=True, exist_ok=True)
    job = uuid.uuid4().hex[:12]
    dest = work / f"{job}.json"
    # 필터 옵션에 드라이브 문자(:)·한글 경로가 들어가지 않도록 models 폴더를 cwd 로 두고 상대경로만 쓴다.
    af = ("aresample=16000,aformat=sample_fmts=fltp:channel_layouts=mono,"
          f"whisper=model={config.WHISPER_MODEL}:language={config.WHISPER_LANGUAGE}"
          f":queue={int(length) + 3}:use_gpu={'true' if config.WHISPER_USE_GPU else 'false'}"
          f":destination=_work/{job}.json:format=json")
    cmd = [config.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-y"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-t", f"{length:.3f}", "-i", str(src.resolve()), "-vn", "-af", af, "-f", "null", "-"]
    try:
        p = _run(cmd, cwd=config.MODELS_DIR)
        if p.returncode != 0:
            err = p.stderr.decode("utf-8", "replace")[-400:]
            raise STTError(f"전사 실패: {err.strip() or 'ffmpeg 오류'}")
        if not dest.exists():
            return []
        rows = []
        for line in dest.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return _clean_segments(rows, offset_ms=int(start * 1000))
    finally:
        dest.unlink(missing_ok=True)


def transcribe(path: Path) -> dict:
    """음성 파일 → {'duration', 'segments':[{start,end,text}], 'provider', 'model'}."""
    path = Path(path)
    if not path.exists():
        raise STTError("파일이 없습니다.")
    st = engine_status()
    if not st["ffmpeg"] or not st["whisper_filter"]:
        raise STTError("이 PC의 ffmpeg 에 whisper 필터가 없습니다. ffmpeg 8 이상(full build)이 필요합니다.")
    if not st["model_ready"]:
        raise STTError(f"음성 모델이 없습니다: {config.MODELS_DIR / config.WHISPER_MODEL} (0_SETUP.bat 실행)")
    duration = probe_duration(path)
    if duration < 1:
        raise STTError("음성이 너무 짧습니다.")
    segments: list[dict] = []
    with _LOCK:
        start = 0.0
        while start < duration:
            length = min(config.STT_MAX_CHUNK_SEC, duration - start)
            segments += _transcribe_piece(path, start, length)
            start += length
    return {"duration": round(duration, 1), "segments": segments,
            "provider": "local-whisper", "model": config.WHISPER_MODEL}
