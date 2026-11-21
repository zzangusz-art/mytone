"""생성된 글 점검 — (1) AI 티 표현 찾기 (2) '내 말투 일치도' 재기.

둘 다 규칙·측정 기반이라 같은 글이면 항상 같은 결과가 나온다. 일치도는 참고 지표다:
말투 측정값(style_stats)과 같은 자로 생성 글을 다시 재서 얼마나 벗어났는지를 본다.
"""
from __future__ import annotations

import re

from . import style_stats
from .textutil import eojeol_count, split_sentences

# (이름, 정규식, 무게)  무게 2 = 거의 확실한 AI/공문 투, 1 = 사람도 쓰지만 겹치면 티 나는 것
AI_TELLS: list[tuple[str, str, int]] = [
    ("소중한/귀한 자녀", r"(소중한|귀한)\s*(자녀|아이|학생)", 2),
    ("따뜻한 관심·격려", r"따뜻한\s*(관심|격려|응원|사랑|마음)", 2),
    ("함께 ~해 나가겠습니다", r"함께\s*.{0,12}(나가겠습니다|나가도록|노력하겠습니다|만들어\s*가)", 2),
    ("최선을 다하겠습니다", r"최선을\s*다하", 2),
    ("아낌없는", r"아낌없(는|이)", 2),
    ("진심으로 ~", r"진심으로\s*(감사|공감|응원|축하|사과)", 2),
    ("깊이 공감/이해", r"깊이\s*(공감|이해|감사|사과)", 2),
    ("~하고자 합니다", r"(하|드리|리|보)고자\s*(합니다|해요|했)", 2),
    ("~에 대해/관하여", r"(에\s*대해|에\s*대하여|에\s*관하여|[와과]\s*관련하여|[와과]\s*관련해)", 1),
    ("~을 통해", r"[을를]\s*통(해|하여)", 1),
    ("~에 있어", r"에\s*있어(서)?\s", 2),
    ("긍정적인 변화·성장", r"긍정적(인|으로)\s*(변화|성장|영향|방향|모습)", 2),
    ("성장할 수 있도록", r"(성장|발전|향상)(할|될)\s*수\s*있도록", 2),
    ("지속적인 관심", r"지속적(인|으로)\s*(관심|지도|노력|소통|협력)", 2),
    ("세심한 지도·배려", r"세심(한|하게|히)\s*(지도|관심|배려|살피|관찰)", 2),
    ("언제든지 연락 주시기 바랍니다", r"언제든(지)?\s*.{0,8}(연락|문의|말씀)\s*(주시기|주십시오|바랍니다)", 2),
    ("~하시기 바랍니다", r"(주시기|하시기|되시기|부탁드리기)\s*바랍니다", 1),
    ("늘 응원", r"(늘|항상|언제나)\s*응원", 2),
    ("다시 한번 감사", r"다시\s*한\s*번\s*(감사|말씀)", 1),
    ("도움이 되셨기를", r"도움이\s*되(셨|시)(기를|길)", 2),
    ("문어체 접속어(또한·더불어·아울러)", r"(?<![가-힣])(또한|더불어|아울러|뿐만\s*아니라|나아가|한편)(?![가-힣])", 2),
    ("무엇보다", r"무엇보다(도)?\s", 1),
    ("가정과 학교가", r"가정(과|에서도)\s*(학교|함께|지속)", 1),
    ("협력·협조 부탁", r"(협력|협조|동참)(을|를)?\s*(부탁|요청|바랍)", 1),
    ("첫째/둘째", r"(첫째|둘째|셋째|첫\s*번째로|두\s*번째로)", 2),
    ("목록·번호 서식", r"^\s*(\d+[.)]|[-•*·▶■□▪])\s", 2),
    ("굵은 글씨·소제목", r"\*\*|^#{1,3}\s|__", 2),
    ("과한 공감 선언", r"(얼마나|많이)\s*.{0,14}(크셨을지|되셨을지|하셨을지|놀라셨을지).{0,14}(이해|공감|짐작|헤아)", 2),
    ("안녕하십니까", r"안녕하십니까", 1),
    ("~드리는 바입니다", r"(드리는|하는)\s*바입니다", 2),
    ("귀하/귀 가정", r"(?<![가-힣])귀(하|\s*가정|\s*댁)", 2),
]
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿❤️]")
_RX = [(name, re.compile(rx, re.M), w) for name, rx, w in AI_TELLS]


def find_tells(text: str, teacher_corpus: str = "", allow_emoji: bool = False) -> list[dict]:
    """AI 티 표현 목록. 선생님이 실제로 쓰는 표현(teacher_corpus 에 있는 것)은 제외한다."""
    out = []
    for name, rx, w in _RX:
        m = rx.search(text)
        if not m:
            continue
        if teacher_corpus and rx.search(teacher_corpus):
            continue
        out.append({"label": name, "match": m.group(0).strip()[:30], "weight": w})
    if not allow_emoji and _EMOJI.search(text) and not _EMOJI.search(teacher_corpus or ""):
        out.append({"label": "이모지", "match": _EMOJI.search(text).group(0), "weight": 1})
    return out


def _habit_items(stats: dict, min_count: int = 3) -> list[tuple[str, re.Pattern]]:
    """선생님의 대표 말버릇(프로필에서 일정 횟수 이상 나온 것)."""
    items: list[tuple[str, re.Pattern]] = []
    table = dict(style_stats.MARKERS + style_stats.BACKCHANNELS + style_stats.CUSHIONS + style_stats.INTENSIFIERS)
    for key in ("markers", "cushions", "intensifiers"):
        for row in stats.get(key, []):
            if row["count"] >= min_count and row["word"] in table and row["word"] not in ("좀", "많이", "조금"):
                items.append((row["word"], re.compile(table[row["word"]])))
    for ph in stats.get("phrases", [])[:8]:
        if ph["count"] >= min_count and len(ph["text"]) >= 4:
            pat = r"\s*".join(re.escape(w) for w in ph["text"].split()).replace(re.escape("{학생}"), "[가-힣]{2,4}")
            items.append((ph["text"], re.compile(pat)))
    return items


def style_match(text: str, profile: dict, parent_title: str = "") -> dict:
    """생성 글이 선생님 말투 측정값과 얼마나 맞는지 0~100."""
    stats = profile.get("stats") or {}
    # 줄바꿈은 문장 경계로 보고, 대본의 "(학부모 반응)" 같은 괄호 지시문은 재지 않는다
    body = re.sub(r"\([^)\n]{0,30}\)", " ", text)
    sents = [s for line in body.splitlines() for s in split_sentences(line) if eojeol_count(s) >= 2]
    if not sents or not stats.get("n_sentences"):
        return {"score": None, "label": "측정 불가", "parts": {}, "notes": []}
    notes: list[str] = []

    share = {e["label"]: e["share"] for e in stats.get("endings", [])}
    labels = [style_stats.classify_ending(s) for s in sents]
    ok = sum(1 for lab in labels if share.get(lab, 0) >= 0.04)
    s_end = ok / len(labels)
    odd = sorted({lab for lab in labels if share.get(lab, 0) < 0.04 and lab != "기타"})
    if odd:
        notes.append("선생님이 잘 안 쓰는 문장 끝: " + ", ".join(odd))

    haeyo_t = stats.get("formality", {}).get("haeyo", 0.9)
    haeyo_x = sum(1 for lab in labels if lab in style_stats.HAEYO) / len(labels)
    s_form = max(0.0, 1 - abs(haeyo_t - haeyo_x) * 1.5)
    if abs(haeyo_t - haeyo_x) > 0.3:
        notes.append(f"격식 차이: 선생님은 해요체 {haeyo_t:.0%}, 이 글은 {haeyo_x:.0%}")

    forms_t = {p["form"] for p in stats.get("address", {}).get("parent", [])} | ({parent_title} if parent_title else set())
    used = {f for f in style_stats.PARENT_FORMS if re.search(rf"(?<![가-힣]){f}", text)}
    s_addr = 1.0 if (not used or not forms_t or used & forms_t) else 0.0
    if s_addr == 0:
        notes.append(f"호칭이 다름: 선생님은 {', '.join(sorted(forms_t))}")

    habits = _habit_items(stats)
    hits = [name for name, rx in habits if rx.search(text)]
    s_sig = {0: 0.25, 1: 0.65}.get(len(hits), 1.0) if habits else 0.7

    penalty = 0.0
    # 흉내가 지나친 경우: 한 가지 종결이 선생님 비율보다 훨씬 많이 반복
    for lab in set(labels):
        x = labels.count(lab) / len(labels)
        if len(labels) >= 4 and lab not in ("~어요/아요", "기타") and x - share.get(lab, 0) > 0.34 and labels.count(lab) >= 3:
            penalty += 10
            notes.append(f"'{lab}'가 너무 자주 나옴(이 글 {x:.0%}, 선생님 평소 {share.get(lab, 0):.0%})")
    tells = find_tells(text, profile.get("corpus_text", ""), bool(profile.get("custom", {}).get("emoji")))
    penalty += min(32, sum(8 if t["weight"] == 2 else 4 for t in tells))

    raw = 100 * (0.40 * s_end + 0.20 * s_form + 0.15 * s_addr + 0.25 * s_sig)
    score = int(round(max(0, min(100, raw - penalty))))
    label = "내 말투에 가까움" if score >= 80 else ("대체로 비슷함" if score >= 62 else "다듬기 필요")
    return {
        "score": score, "label": label,
        "parts": {"문장 끝": round(s_end, 2), "격식": round(s_form, 2), "호칭": s_addr, "말버릇": round(s_sig, 2)},
        "habits_found": hits[:6], "tells": tells, "notes": notes,
    }
