"""말투 프로필 만들기 — 녹음 전사(화자 구분 완료) → 측정 + 실제 발화 예시 + (키 있으면) 정성 분석."""
from __future__ import annotations

import re
import time

from . import llm, pii, prompts, speakers, style_stats
from .textutil import eojeol_count

_FUNC_RULES: list[tuple[str, str]] = [
    ("praise", r"(잘했|잘하|칭찬|꼼꼼|성실|좋아졌|늘었|기특|대견|열심히|차분하게|잘\s*챙기)"),
    ("reassure", r"(큰\s*일은?\s*아니|걱정하실\s*(정도|필요)|안심|금방|충분히|다친\s*데는?\s*없|괜찮(아요|습니다))"),
    ("concern", r"(안\s*해\s*오|안\s*해오|떨어|내려가|부족|힘들|지각|결석|다툼|싸우|졸|미뤘|빠졌|속상|어렵더라)"),
    ("request", r"(주시면|부탁|해\s*주세요|봐\s*주세요|챙겨\s*주)"),
    ("plan", r"(제가\s*.{0,24}(볼게요|드릴게요|할게요|하겠습니다|보고요|보겠습니다)|지켜보|챙겨\s*볼)"),
    ("guide", r"(일정|상담\s*주간|가정통신문|신청|준비물|날짜|다음\s*(주|달)에|까지\s*(보내|제출))"),
]


def _tag(text: str) -> list[str]:
    tags = [name for name, rx in _FUNC_RULES if re.search(rx, text)]
    return tags or ["explain"]


def _habit_score(text: str, stats: dict) -> int:
    table = dict(style_stats.MARKERS + style_stats.CUSHIONS + style_stats.BACKCHANNELS)
    score = 0
    for key in ("markers", "cushions", "backchannels"):
        for row in stats.get(key, [])[:8]:
            if row["word"] in table and re.search(table[row["word"]], text):
                score += 1
    return score


def pick_exemplars(turns_by_rec: list[list[str]], stats: dict, limit: int = 22, char_budget: int = 3800) -> list[dict]:
    """실제 교사 발화 중 예시로 쓸 것을 고른다: 기능이 고루 섞이고, 말버릇이 담긴 것 우선."""
    cands: list[dict] = []
    for ri, turns in enumerate(turns_by_rec):
        usable = [(ti, t) for ti, t in enumerate(turns) if eojeol_count(t) >= 4]
        for pos, (ti, text) in enumerate(usable):
            n = eojeol_count(text)
            if n > 70:
                continue
            tags = _tag(text)
            if pos == 0:
                tags = ["opener"] + tags
            elif pos == len(usable) - 1:
                tags = ["closer"] + tags
            cands.append({"rec": ri, "turn": ti, "text": text, "tags": tags, "n": n,
                          "habit": _habit_score(text, stats)})
    picked: list[dict] = []
    used_chars = 0
    covered: dict[str, int] = {}

    def take(c: dict, func: str) -> None:
        nonlocal used_chars
        picked.append({"id": f"r{c['rec']}t{c['turn']}", "rec": c["rec"], "text": c["text"], "function": func})
        used_chars += len(c["text"])
        covered[func] = covered.get(func, 0) + 1
        cands.remove(c)

    # 1) 통화마다 시작·마무리는 반드시(문자의 인사·끝맺음 기준이 된다)
    for func in ("opener", "closer"):
        for c in [c for c in cands if c["tags"][0] == func][:3]:
            take(c, func)
    # 2) 기능별로 돌아가며, 말버릇이 많이 담긴 발화부터
    order = ["concern", "reassure", "request", "plan", "praise", "guide", "explain"]
    while cands and len(picked) < limit and used_chars < char_budget:
        progressed = False
        for func in order:
            pool = [c for c in cands if func in c["tags"]]
            if not pool or covered.get(func, 0) >= 4:
                continue
            best = max(pool, key=lambda c: (c["habit"], min(c["n"], 30)))
            take(best, func)
            progressed = True
            if len(picked) >= limit or used_chars >= char_budget:
                break
        if not progressed:
            break
    order_key = {f: i for i, f in enumerate(["opener", "reassure", "concern", "praise", "explain", "plan",
                                             "request", "guide", "closer"])}
    picked.sort(key=lambda e: (order_key.get(e["function"], 9), e["rec"]))
    return picked


def collect(recordings: list[dict], extra_names: list[str] | None = None) -> dict:
    """녹음들에서 교사 문장·턴을 모으고 이름을 가린다."""
    units_lists = [r.get("units", []) for r in recordings if r.get("status") == "done"]
    raw_sents = [[u["text"] for u in units if u["speaker"] == "T" and not u.get("deleted")] for units in units_lists]
    names = list(dict.fromkeys((extra_names or []) + pii.detect_student_names([s for r in raw_sents for s in r])))
    sent_by_rec = [[pii.mask_all(s, names) for s in r] for r in raw_sents]
    turns_by_rec = [[pii.mask_all(" ".join(u["text"] for u in turn), names) for turn in speakers.teacher_turns(units)]
                    for units in units_lists]
    return {"names": names, "sent_by_rec": sent_by_rec, "turns_by_rec": turns_by_rec}


def build(recordings: list[dict], extra_names: list[str] | None = None, prev: dict | None = None,
          use_llm: bool = True) -> dict:
    """프로필 생성. LLM 이 없거나 실패해도 측정+예시만으로 완성된다(정성 분석은 빈칸)."""
    data = collect(recordings, extra_names)
    stats = style_stats.measure(data["sent_by_rec"])
    if not stats.get("n_sentences"):
        raise ValueError("교사 발화가 없습니다. 전사 화면에서 '교사'로 표시된 문장이 있는지 확인해 주세요.")
    profile = {
        "version": 1,
        "built_at": time.strftime("%Y-%m-%d %H:%M"),
        "names_masked": data["names"],
        "stats": stats,
        "reliability": style_stats.reliability(stats),
        "exemplars": pick_exemplars(data["turns_by_rec"], stats),
        "corpus_text": "\n".join(s for r in data["sent_by_rec"] for s in r),
        "qualitative": None,
        "qualitative_error": None,
        "custom": (prev or {}).get("custom") or {"always": [], "banned": [], "notes": "", "emoji": False},
    }
    if use_llm and llm.is_live():
        try:
            profile["qualitative"] = llm.complete_json(
                prompts.ANALYSIS_SYSTEM, prompts.build_analysis_user(stats, data["turns_by_rec"]),
                prompts.ANALYSIS_SCHEMA, max_tokens=6000)
        except llm.LLMError as e:
            profile["qualitative_error"] = str(e)
    return profile


def refine_speakers_with_llm(units: list[dict]) -> int:
    """LLM 으로 화자 추정을 보정한다. 교사가 직접 고친 문장은 그대로 둔다. 반환: 바뀐 문장 수."""
    live = [u for u in units if not u.get("deleted")]
    if not live or not llm.is_live():
        return 0
    res = llm.complete_json(prompts.SPEAKER_SYSTEM, prompts.build_speaker_user(live), prompts.SPEAKER_SCHEMA,
                            max_tokens=min(16000, 1500 + 14 * len(live)), effort="low")
    by_i = {u["i"]: u for u in live}
    changed = 0
    for row in res.get("labels", []):
        u = by_i.get(row.get("i"))
        if not u or u.get("manual") or row.get("s") not in ("T", "P"):
            continue
        if u["speaker"] != row["s"]:
            u["speaker"] = row["s"]
            changed += 1
        u["conf"] = max(float(u.get("conf", 0.5)), 0.85)
    return changed
