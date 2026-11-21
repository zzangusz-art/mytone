"""한국어 구어 텍스트 공통 유틸 — 형태소 분석기 없이(순수 파이썬) 동작한다."""
from __future__ import annotations

import re

_PUNCT_SPLIT = re.compile(r"(?<=[.?!…])\s+")
# 구두점이 거의 없는 전사에서 문장 경계로 볼 종결형(뒤에 공백이 올 때만)
_ENDING_SPLIT = re.compile(
    r"(니다|니까|어요|아요|에요|예요|세요|네요|데요|고요|구요|든요|라고요|"
    r"게요|래요|대요|까요|나요|가요|해요|돼요|려요|워요|와요|줘요|봐요|서요)\s+")
_STRIP = " \t\r\n\"'“”‘’()[]<>~·,"
_TAIL_PUNCT = ".?!…~,;:"


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def punct_density(text: str) -> float:
    words = max(1, len(text.split()))
    return len(re.findall(r"[.?!…]", text)) / words


def split_sentences(text: str) -> list[str]:
    """문장 단위 분리. 구두점이 있으면 구두점 기준, 없으면 종결어미 기준."""
    text = normalize_space(text)
    if not text:
        return []
    parts = _PUNCT_SPLIT.split(text)
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 구두점 없이 길게 이어진 덩어리는 종결어미에서 한 번 더 자른다
        if len(part.split()) > 14 and punct_density(part) < 0.04:
            out += [s.strip() for s in _ENDING_SPLIT.sub(r"\1\n", part).split("\n") if s.strip()]
        else:
            out.append(part)
    return out


def eojeol_count(text: str) -> int:
    return len([w for w in text.split() if re.search(r"[가-힣A-Za-z0-9]", w)])


def strip_tail(text: str) -> str:
    """문장 끝 구두점·따옴표 제거 (종결어미 판정용)."""
    return text.strip().rstrip(_TAIL_PUNCT + _STRIP).strip()


def words(text: str) -> list[str]:
    """어절 목록(구두점 제거)."""
    out = []
    for w in text.split():
        w = w.strip(_STRIP + _TAIL_PUNCT)
        if w:
            out.append(w)
    return out


def is_question(sentence: str) -> bool:
    s = sentence.strip()
    if s.endswith("?"):
        return True
    core = strip_tail(s)
    return bool(re.search(r"(까요|나요|[은는인건떤던]가요|습니까|ㅂ니까|니까|으세요\?|을까요|ㄹ까요)$", core)) \
        and not core.endswith("니까요")


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)
