"""개인정보 가리기 — 전화번호·주민번호·학생 이름.

학생 이름은 `{학생}` 자리표시로 바꾼다. 이름 뒤에 '이'를 붙여 부르는 습관("서준이가")은
말투의 일부라서 "{학생}이가" 형태로 남긴다.
"""
from __future__ import annotations

import re
from collections import Counter

PLACEHOLDER = "{학생}"

_PHONE = re.compile(r"(?<!\d)(?:01[016789]|0\d{1,2})[-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)")
_RRN = re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)")

_STOP = {
    "아이", "학생", "친구", "선생", "어머", "아버", "우리", "저희", "본인", "자기", "애들", "아들", "따님",
    "자녀", "담임", "학부모", "부모", "동생", "누나", "언니", "오빠", "엄마", "아빠", "그쪽", "이쪽", "상대",
    "서로", "반장", "짝꿍", "같은", "다른", "어떤", "무슨", "이번", "저번", "지난", "다음", "오늘", "내일",
    "어제", "지금", "요즘", "원래", "사실", "일단", "혹시", "그럼", "그래", "아니", "감사", "죄송", "안녕",
    "아이들", "친구들", "학생들", "선생님", "어머님", "아버님", "어머니", "아버지", "그럼요", "맞아요",
    "네네", "저는", "제가", "둘이", "혼자", "모두", "전부", "누구", "여기", "거기", "이거", "그거", "어린",
    "사람", "남자", "여자", "가끔", "항상", "보통", "아마", "먼저", "나중", "새로", "따로", "같이", "교과",
    "과학", "수학", "영어", "국어", "체육", "음악", "미술", "학원", "학교", "교실", "숙제", "성적", "이야기",
    "그래서", "그리고", "그런데", "근데", "그러면", "그러니까", "일단은", "사실은", "아니에요", "감사해요",
    "그래도", "하지만", "그러게", "그렇죠", "그쵸", "맞아요", "아이고", "어머", "정말", "진짜", "이제", "저도",
}
_BAD_TAIL = "요다죠까네서고며면지만도는은를을가이"

_A = re.compile(r"(?<![가-힣])([가-힣]{2,3}?)이?\s*담임")
_A2 = re.compile(r"(?<![가-힣])([가-힣]{2,3}?)이?\s*(?:어머님|어머니|아버님|아버지)(?![가-힣])")
_B = re.compile(r"(?<![가-힣])([가-힣]{2})이(?:가|는|를|랑|한테|도|의|하고|에게|만)(?![가-힣])")
_C = re.compile(r"(?<![가-힣])([가-힣]{2,3})\s*학생(?:이|은|는|을|도|의)?(?![가-힣])")


def mask_basic(text: str) -> str:
    text = _RRN.sub("[주민번호]", text)
    return _PHONE.sub("[전화번호]", text)


def _ok(name: str) -> bool:
    return name not in _STOP and name[-1] not in _BAD_TAIL[:5] and not re.search(r"(님|쌤|선생)", name)


def detect_student_names(sentences: list[str]) -> list[str]:
    """교사 발화에서 학생 이름 후보를 찾는다(자동 추정 — 화면에서 수정 가능)."""
    text = "\n".join(sentences)
    strong: Counter[str] = Counter()
    weak: Counter[str] = Counter()
    for m in _A.finditer(text):
        if _ok(m.group(1)):
            strong[m.group(1)] += 1
    for m in _C.finditer(text):
        if _ok(m.group(1)):
            strong[m.group(1)] += 1
    for m in _A2.finditer(text):          # "서준 어머님" — "그래서 어머님" 같은 오탐이 있어 약한 증거로만
        if _ok(m.group(1)):
            weak[m.group(1)] += 1
    for m in _B.finditer(text):
        if _ok(m.group(1)):
            weak[m.group(1)] += 1
    names = set(strong) | {n for n, c in weak.items() if c >= 2}
    # "서준"과 "서준이"가 둘 다 잡히면 짧은 쪽만 남긴다
    out = [n for n in names if not (n.endswith("이") and n[:-1] in names)]
    return sorted(out, key=lambda n: (-(strong[n] * 2 + weak[n]), n))


def mask_names(text: str, names: list[str]) -> str:
    for name in sorted({n.strip() for n in names if n and n.strip()}, key=len, reverse=True):
        n = re.escape(name)
        # 받침 있는 이름 + 접미 '이' ("서준이가", "서준이 담임") → "{학생}이…"
        text = re.sub(rf"(?<![가-힣]){n}이(?=(?:가|는|를|랑|한테|도|의|하고|에게|만|요)?(?:[\s,.?!]|$))",
                      PLACEHOLDER + "이", text)
        text = re.sub(rf"(?<![가-힣]){n}", PLACEHOLDER, text)
    return text


def mask_all(text: str, names: list[str]) -> str:
    return mask_names(mask_basic(text), names)
