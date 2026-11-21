"""상담 답변 생성 — 교사 메모 + 말투 프로필 → 보낼 글(여러 안) + 일치도·AI 티 점검."""
from __future__ import annotations

import re

from . import aicheck, llm, prompts
from .pii import PLACEHOLDER

SENSITIVE = re.compile(r"(학폭|학교\s*폭력|폭행|때렸|맞았|자해|자살|죽고\s*싶|학대|성희롱|성추행|고소|소송|변호사|"
                       r"교육청|신고|경찰|녹음|내용증명|아동\s*보호)")
SENSITIVE_NOTE = ("민감한 사안으로 보입니다. 보내기 전에 학교(기관) 절차와 관리자 보고 여부를 먼저 확인하고, "
                  "확인된 사실만 담겼는지 한 번 더 읽어 주세요.")


def split_variants(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"(?m)^\s*={3,}\s*$", text) if p.strip()]
    cleaned = []
    for p in parts:
        p = re.sub(r"(?m)^\s*(\[?(안|버전|시안)\s*\d+\]?|\d+\s*안|첫\s*번째\s*안|두\s*번째\s*안|세\s*번째\s*안)\s*[:：.]?\s*$",
                   "", p).strip()
        if p:
            cleaned.append(p.strip("\"“”"))
    return cleaned or [text.strip()]


def _fill(text: str, student: str) -> str:
    if not student:
        return text.replace(PLACEHOLDER, "[학생 이름]")
    last = student[-1]
    has_final = (ord(last) - 0xAC00) % 28 != 0 if "가" <= last <= "힣" else False
    # "{학생}이가"는 받침 있는 이름에만 자연스럽다
    text = text.replace(PLACEHOLDER + "이", student + ("이" if has_final else ""))
    return text.replace(PLACEHOLDER, student)


def demo_draft(profile: dict, req: dict) -> str:
    """키가 없을 때의 임시 결과: 선생님 실제 인사·끝맺음 사이에 메모를 그대로 끼운다(말투 변환 아님)."""
    ex = profile.get("exemplars") or []
    opener = next((e["text"] for e in ex if e["function"] == "opener"), "")
    closer = next((e["text"] for e in ex if e["function"] == "closer"), "")
    student = (req.get("student") or "").strip()
    body = (req.get("facts") or "").strip() or "(전할 내용 메모를 적어 주세요)"
    if req.get("channel") != "call":
        opener = re.sub(r"\s*(지금\s*)?(잠깐\s*)?통화\s*괜찮으세요\??", "", opener).strip()
        closer = re.sub(r"\s*들어가세요\.?", "", closer).strip()
    return "\n\n".join(p for p in [_fill(opener, student), body, _fill(closer, student)] if p)


def _issues(check: dict) -> list[str]:
    issues = [f"AI 티가 나는 표현 \"{t['match']}\"({t['label']})을 선생님이 평소 쓰는 말로 바꾸기"
              for t in check.get("tells", []) if t["weight"] == 2]
    issues += [n for n in check.get("notes", []) if "너무 자주" in n or "호칭이 다름" in n or "격식 차이" in n]
    return issues


def generate(teacher: dict, profile: dict, examples: list[dict], req: dict) -> dict:
    parent_title = (req.get("parent_title") or "").strip()
    notices: list[str] = []
    blob = " ".join(str(req.get(k) or "") for k in ("facts", "parent_message"))
    if SENSITIVE.search(blob):
        notices.append(SENSITIVE_NOTE)

    if not llm.is_live():
        text = demo_draft(profile, req)
        notices.insert(0, "지금은 Claude API 키가 없어 '데모' 결과입니다. 선생님의 실제 인사·끝맺음 사이에 메모를 그대로 "
                          "넣었을 뿐, 말투 변환은 하지 않았습니다. [내 말투 프롬프트 복사]를 Claude·ChatGPT에 붙이면 "
                          "키 없이도 지금 바로 쓸 수 있습니다.")
        return {"provider": "demo", "variants": [{"text": text, "check": aicheck.style_match(text, profile, parent_title)}],
                "notices": notices, "usage": None}

    system = prompts.build_system_prompt(teacher, profile, examples)
    user = prompts.build_user_prompt(req)
    res = llm.complete(system, user, max_tokens=8000)
    variants = [{"text": v, "check": aicheck.style_match(v, profile, parent_title)} for v in split_variants(res["text"])]
    usage = dict(res["usage"])
    revised = False

    # 측정값과 어긋난 점이 뚜렷하면 한 번만 고쳐 쓰게 한다(비용 때문에 1회 제한)
    worst = min(variants, key=lambda v: v["check"]["score"] if v["check"]["score"] is not None else 100)
    issues = _issues(worst["check"])
    if issues:
        try:
            history = [{"role": "user", "content": user}, {"role": "assistant", "content": res["assistant_content"]}]
            fix = llm.complete(system, prompts.build_revision_prompt(worst["text"], issues), max_tokens=6000,
                               history=history)
            new_text = split_variants(fix["text"])[0]
            new_check = aicheck.style_match(new_text, profile, parent_title)
            if (new_check["score"] or 0) >= (worst["check"]["score"] or 0):
                worst["text"], worst["check"] = new_text, new_check
                revised = True
            for k in usage:
                usage[k] += fix["usage"][k]
        except llm.LLMError:
            pass
    return {"provider": "claude", "model": res["model"], "variants": variants, "notices": notices,
            "usage": usage, "revised": revised}
