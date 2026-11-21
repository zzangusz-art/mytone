"""LLM 레이어 — Claude(공식 anthropic SDK) / 데모.

키는 서버 환경변수(ANTHROPIC_API_KEY)에서만 읽는다. 브라우저에는 절대 내려가지 않는다.
거절(refusal) 대비 서버측 fallbacks 를 기본으로 켠다(MYTONE_CLAUDE_FALLBACKS=off 로 끌 수 있음).
"""
from __future__ import annotations

import json
import logging
import threading

from . import config

log = logging.getLogger("mytone.llm")
_client = None
_lock = threading.Lock()
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """사용자에게 그대로 보여 줄 수 있는 한국어 메시지를 담는다."""


def provider() -> str:
    return config.LLM_PROVIDER


def is_live() -> bool:
    return config.LLM_PROVIDER == "claude"


def client():
    global _client
    with _lock:
        if _client is None:
            import anthropic
            _client = anthropic.Anthropic()   # 환경변수/프로필에서 자격 증명을 스스로 찾는다
        return _client


def build_request(system: str, user: str, *, max_tokens: int = 8000, json_schema: dict | None = None,
                  effort: str | None = None, history: list[dict] | None = None) -> dict:
    """Claude Messages API 요청 본문. (테스트에서 SDK 시그니처와 대조할 수 있게 따로 둔다.)"""
    output_config: dict = {"effort": effort or config.CLAUDE_EFFORT}
    if json_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": json_schema}
    return {
        "model": config.CLAUDE_MODEL,
        "max_tokens": max_tokens,
        # 말투 프로필이 든 시스템 프롬프트는 교사별로 고정 → 캐시해 두면 같은 교사의 반복 호출이 싸진다
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": (history or []) + [{"role": "user", "content": user}],
        "thinking": {"type": "adaptive"},
        "output_config": output_config,
    }


def _send(req: dict):
    import anthropic

    c = client()
    try:
        if config.CLAUDE_FALLBACKS != "off":
            try:
                return c.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **req)
            except anthropic.BadRequestError as e:
                # 계정·플랫폼이 fallbacks 를 받지 않는 경우에만 한 번 일반 호출로 재시도
                if "fallback" not in str(e).lower() and "beta" not in str(e).lower():
                    raise
                log.warning("fallbacks 미지원 → 일반 호출로 재시도: %s", e)
        return c.messages.create(**req)
    except anthropic.AuthenticationError as e:
        raise LLMError("Claude API 키가 올바르지 않습니다. 서버의 ANTHROPIC_API_KEY 를 확인해 주세요.") from e
    except anthropic.PermissionDeniedError as e:
        raise LLMError("이 API 키로는 해당 모델을 쓸 수 없습니다(권한).") from e
    except anthropic.NotFoundError as e:
        raise LLMError(f"모델 이름을 찾을 수 없습니다: {config.CLAUDE_MODEL}") from e
    except anthropic.RateLimitError as e:
        raise LLMError("요청이 몰려 잠시 제한되었습니다. 1분 뒤 다시 시도해 주세요.") from e
    except anthropic.BadRequestError as e:
        raise LLMError(f"요청 형식 오류: {getattr(e, 'message', str(e))[:200]}") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude 서버 오류({e.status_code}). 잠시 뒤 다시 시도해 주세요.") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Claude 서버에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요.") from e


def complete(system: str, user: str, **kwargs) -> dict:
    """텍스트 응답. 반환: {'text', 'model', 'usage', 'assistant_content'}"""
    if not is_live():
        raise LLMError("Claude API 키가 설정되지 않았습니다(데모 모드).")
    resp = _send(build_request(system, user, **kwargs))
    if getattr(resp, "stop_reason", None) == "refusal":
        raise LLMError("모델이 이 요청을 처리하지 않았습니다. 메모 내용을 바꿔 다시 시도해 주세요.")
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if getattr(resp, "stop_reason", None) == "max_tokens" and not text:
        raise LLMError("응답이 길이 제한에 걸렸습니다. 다시 시도해 주세요.")
    usage = getattr(resp, "usage", None)
    return {
        "text": text,
        "model": getattr(resp, "model", config.CLAUDE_MODEL),
        "usage": {
            "input": getattr(usage, "input_tokens", 0) or 0,
            "output": getattr(usage, "output_tokens", 0) or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        },
        "assistant_content": resp.content,   # 이어지는 턴에 그대로 되돌려 보내야 한다(생각 블록 포함)
    }


def complete_json(system: str, user: str, schema: dict, **kwargs) -> dict:
    res = complete(system, user, json_schema=schema, **kwargs)
    try:
        return json.loads(res["text"])
    except json.JSONDecodeError as e:
        raise LLMError("모델 응답을 해석하지 못했습니다. 다시 시도해 주세요.") from e
