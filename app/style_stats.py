"""말투 측정 — 교사 발화에서 '세어서 확인할 수 있는' 습관을 뽑는다.

LLM 의 인상 비평이 아니라 실제 빈도다. 그래서 (1) 키 없이도 돌고 (2) 숫자로 검증되고
(3) 생성 결과가 이 분포에서 얼마나 벗어났는지 다시 잴 수 있다(aicheck.style_match).
"""
from __future__ import annotations

import re
from collections import Counter
from statistics import mean, median

from .pii import PLACEHOLDER
from .textutil import eojeol_count, is_question, percentile, strip_tail, words

# ── 문장 끝(종결) 분류: 위에서부터 먼저 맞는 것 ──────────────────────────────
ENDING_CLASSES: list[tuple[str, str]] = [
    ("~것 같아요", r"(것|거|듯)\s*같(아요|습니다|네요|더라고요|은데요|아서요)$"),
    ("~거든요", r"거든요$"),
    ("~더라고요", r"더라(고|구)요$"),
    ("~잖아요", r"잖아요$"),
    ("~(으)ㄹ게요", r"게요$"),
    ("~(으)ㄹ까요", r"까요$"),
    ("~는데요", r"데요$"),
    ("~고요", r"(고|구)요$"),
    ("~네요", r"네요$"),
    ("~죠", r"(죠|쵸|지요)$"),
    ("~나요/가요", r"(나요|[은는인건떤던런]가요)$"),
    ("~세요", r"(세요|셔요)$"),
    ("~(아)서요/니까요", r"(서요|니까요|면요|[은는]요)$"),
    ("~거예요", r"(거예요|거에요|것이에요)$"),
    ("~이에요/예요", r"(이에요|예요|에요)$"),
    ("~습니다", r"니다$"),
    ("~습니까", r"니까$"),
    ("~어요/아요", r"요$"),
    ("호칭으로 끝맺음(네, 어머님)", r"(어머님|아버님|어머니|아버지|학부모님)$"),
    ("~다(반말·혼잣말)", r"(다|지|네|군|구나|야|어|아)$"),
]
HAEYO = {lab for lab, _ in ENDING_CLASSES} - {"~습니다", "~습니까", "~다(반말·혼잣말)",
                                               "호칭으로 끝맺음(네, 어머님)"}
HAPSYO = {"~습니다", "~습니까"}
_ENDING_RX = [(lab, re.compile(rx)) for lab, rx in ENDING_CLASSES]


def classify_ending(sentence: str) -> str:
    core = strip_tail(sentence)
    for lab, rx in _ENDING_RX:
        if rx.search(core):
            return lab
    return "기타"


# ── 습관 어휘 ────────────────────────────────────────────────────────────────
def _w(word: str) -> str:
    return rf"(?<![가-힣]){word}(?![가-힣])"


MARKERS: list[tuple[str, str]] = [
    ("일단은", _w("일단은")), ("일단", _w("일단")), ("사실", r"(?<![가-힣])사실(은)?(?![가-힣])"),
    ("근데", _w("근데")), ("그런데", _w("그런데")), ("그래서", _w("그래서")), ("그리고", _w("그리고")),
    ("그러니까", r"(?<![가-힣])(그러니까|그니까)(요)?(?![가-힣])"), ("아무래도", _w("아무래도")),
    ("솔직히", r"(?<![가-힣])솔직히"), ("다름이 아니라", r"다름이\s*아니라"),
    ("어쨌든", r"(?<![가-힣])(어쨌든|아무튼|암튼)"), ("우선", _w("우선")), ("그래도", _w("그래도")),
    ("다만", _w("다만")), ("물론", _w("물론")), ("그게요", r"(?<![가-힣])그게요"),
    ("제가 보기에는", r"제가\s*보기(에는|엔|에)"), ("이제", _w("이제")), ("뭐랄까", r"뭐랄까"),
    ("예를 들면", r"예를\s*들(면|어)"), ("하나만 더", r"하나만\s*더"), ("그럼", r"(?<![가-힣])(그러면|그럼)(?![가-힣요])"),
]
BACKCHANNELS: list[tuple[str, str]] = [
    ("네네", r"(?<![가-힣])네네"), ("그쵸", r"(?<![가-힣])(그쵸|그죠|그렇죠)"),
    ("맞아요", r"(?<![가-힣])맞아요"), ("그럼요", r"(?<![가-힣])그럼요"), ("아니에요", r"(?<![가-힣])아니에요"),
    ("아이고", r"(?<![가-힣])(아이고|아유|어휴)"), ("음", _w("음")), ("어", _w("어")),
    ("아(말머리)", r"(?:^|[.?!]\s)아[,\s]"), ("네(말머리)", r"(?:^|[.?!]\s)네[,\s]"),
]
INTENSIFIERS: list[tuple[str, str]] = [(w, _w(w)) for w in [
    "되게", "너무", "진짜", "정말", "엄청", "굉장히", "많이", "좀", "조금", "약간", "살짝", "꽤", "워낙",
    "아주", "딱", "꼭", "충분히", "전혀", "완전", "특히", "크게", "금방", "잠깐", "한번"]]
CUSHIONS: list[tuple[str, str]] = [
    ("~면 좋을 것 같아요", r"면\s*좋을\s*(것|거)\s*같"), ("한 번만", r"한\s*번만"),
    ("~실까 봐", r"실까\s*봐"), ("혹시", _w("혹시")), ("괜찮으세요", r"괜찮으(세요|시면|실까요|신가요)"),
    ("죄송하지만", r"죄송(하지만|한데|합니다만)"), ("바쁘실 텐데", r"바쁘(실|신|시)"),
    ("편하게/편하신", r"편하(게|신|실|시)"), ("걱정하실 정도는", r"걱정하실\s*(정도|필요|일)"),
    ("말씀드리~", r"말씀\s*드(리|려|릴|렸)"), ("~주시면", r"주시면"), ("부탁드려요", r"부탁\s*드(려|립|릴)"),
    ("큰일은 아니고요", r"큰\s*일은?\s*아니"), ("먼저 말씀", r"먼저\s*말씀"),
]
PARENT_FORMS = ["어머님", "어머니", "아버님", "아버지", "학부모님", "부모님", "보호자"]
STUDENT_FORMS: list[tuple[str, str]] = [
    (f"{PLACEHOLDER}이(이름+이)", re.escape(PLACEHOLDER) + r"이(?:가|는|를|랑|한테|도|의|\s)"),
    (f"우리 {PLACEHOLDER}", r"우리\s*" + re.escape(PLACEHOLDER)),
    (f"{PLACEHOLDER} 학생", re.escape(PLACEHOLDER) + r"\s*학생"),
    ("친구(학생을 '친구'라 부름)", r"친구(잖아요|라서|인데|예요|이에요|거든요|가|는|들)"),
    ("아이", r"(?<![가-힣])아이(가|는|를|도|들)"), ("애", r"(?<![가-힣])애(가|는|를|도|들)"),
]


def _count(items: list[tuple[str, str]], text: str) -> list[dict]:
    out = []
    for label, rx in items:
        c = len(re.findall(rx, text, flags=re.M))
        if c:
            out.append({"word": label, "count": c})
    out.sort(key=lambda d: -d["count"])
    return out


def _with_rate(rows: list[dict], n_sent: int) -> list[dict]:
    for r in rows:
        r["per100"] = round(r["count"] * 100 / max(1, n_sent), 1)
    return rows


# ── 습관 구절(n-gram) ────────────────────────────────────────────────────────
def habitual_phrases(sent_by_rec: list[list[str]], top: int = 14) -> list[dict]:
    n_rec = len([s for s in sent_by_rec if s])
    total_sent = sum(len(s) for s in sent_by_rec)
    min_count = 3 if total_sent >= 60 else 2
    counts: Counter[tuple[str, ...]] = Counter()
    recs: dict[tuple[str, ...], set[int]] = {}
    example: dict[tuple[str, ...], str] = {}
    for ri, sents in enumerate(sent_by_rec):
        for s in sents:
            ws = words(s)
            for n in (2, 3, 4, 5):
                for i in range(len(ws) - n + 1):
                    g = tuple(ws[i:i + n])
                    counts[g] += 1
                    recs.setdefault(g, set()).add(ri)
                    example.setdefault(g, s)
    cands = []
    for g, c in counts.items():
        if c < min_count:
            continue
        if n_rec >= 2 and len(recs[g]) < 2:      # 한 통화에서만 나온 말은 그 날의 화제일 뿐
            continue
        if all(w.strip("{}이가는을를") in ("학생", "") for w in g):
            continue
        cands.append((g, c))
    # 더 긴 구절에 완전히 포함되고 횟수가 같은 짧은 구절은 버린다
    cands.sort(key=lambda gc: (-len(gc[0]), -gc[1]))
    kept: list[tuple[tuple[str, ...], int]] = []
    for g, c in cands:
        joined = " ".join(g)
        if any(joined in " ".join(k) and kc >= c for k, kc in kept):
            continue
        kept.append((g, c))
    kept.sort(key=lambda gc: -(gc[1] * (1 + 0.35 * (len(gc[0]) - 2))))
    return [{"text": " ".join(g), "count": c, "example": example[g]} for g, c in kept[:top]]


# ── 전체 측정 ────────────────────────────────────────────────────────────────
def measure(sent_by_rec: list[list[str]]) -> dict:
    """sent_by_rec: 녹음별 교사 문장 목록(이름·번호는 이미 가려진 상태)."""
    sentences = [s for rec in sent_by_rec for s in rec]
    n = len(sentences)
    text = "\n".join(sentences)
    if n == 0:
        return {"n_sentences": 0, "n_eojeol": 0}

    ending_counts = Counter(classify_ending(s) for s in sentences if eojeol_count(s) >= 2)
    n_end = sum(ending_counts.values()) or 1
    endings = [{"label": lab, "count": c, "share": round(c / n_end, 3)}
               for lab, c in ending_counts.most_common()]
    haeyo = sum(c for lab, c in ending_counts.items() if lab in HAEYO) / n_end
    hapsyo = sum(c for lab, c in ending_counts.items() if lab in HAPSYO) / n_end

    all_lens = [eojeol_count(s) for s in sentences if eojeol_count(s) >= 1]
    lens = [x for x in all_lens if x >= 3] or all_lens
    parent = [{"form": f, "count": len(re.findall(rf"(?<![가-힣]){f}", text))} for f in PARENT_FORMS]
    parent = sorted([p for p in parent if p["count"]], key=lambda d: -d["count"])
    student = [{"form": lab, "count": len(re.findall(rx, text))} for lab, rx in STUDENT_FORMS]
    student = sorted([s for s in student if s["count"]], key=lambda d: -d["count"])

    return {
        "n_recordings": len([r for r in sent_by_rec if r]),
        "n_sentences": n,
        "n_eojeol": sum(all_lens),
        "endings": endings,
        "formality": {"haeyo": round(haeyo, 3), "hapsyo": round(hapsyo, 3),
                      "other": round(max(0.0, 1 - haeyo - hapsyo), 3)},
        "sentence_len": {
            "mean": round(mean(lens), 1), "median": median(lens),
            "p25": round(percentile(lens, 0.25), 1), "p75": round(percentile(lens, 0.75), 1),
            "short_share": round(sum(1 for x in all_lens if x <= 2) / len(all_lens), 3),
            "long_share": round(sum(1 for x in lens if x >= 12) / len(lens), 3),
        },
        "question_share": round(sum(1 for s in sentences if is_question(s)) / n, 3),
        "markers": _with_rate(_count(MARKERS, text), n),
        "backchannels": _with_rate(_count(BACKCHANNELS, text), n),
        "intensifiers": _with_rate(_count(INTENSIFIERS, text), n),
        "cushions": _with_rate(_count(CUSHIONS, text), n),
        "address": {"parent": parent, "student": student},
        "phrases": habitual_phrases(sent_by_rec),
    }


def _fmt(rows: list[dict], key: str, limit: int, min_count: int = 2) -> str:
    picked = [r for r in rows if r["count"] >= min_count][:limit]
    return " · ".join(f"\"{r[key]}\"({r['count']}회)" for r in picked)


def reliability(stats: dict) -> dict:
    """자료가 충분한지. 문장이 적으면 비율이 출렁이므로 화면·프롬프트에 그대로 알린다."""
    n = stats.get("n_sentences", 0)
    if n >= 150:
        level, note = "충분", "교사 발화가 충분해 측정값을 믿을 만합니다."
    elif n >= 60:
        level, note = "보통", "쓸 만하지만, 통화를 1~2개 더 넣으면 습관 구절이 더 정확해집니다."
    else:
        level, note = "부족", "교사 발화가 적어 비율이 출렁일 수 있습니다. 5분 이상 통화 3개를 권장합니다."
    return {"level": level, "note": note, "n_sentences": n}


def summary_lines(stats: dict) -> list[str]:
    """사람이 읽는 요약(화면 표시 + 프롬프트 주입 공용)."""
    if not stats.get("n_sentences"):
        return []
    L: list[str] = []
    ends = [e for e in stats["endings"] if e["share"] >= 0.04 and e["label"] != "기타"][:7]
    f = stats["formality"]
    L.append("문장 끝: " + " · ".join(f"{e['label']} {e['share']:.0%}" for e in ends)
             + f"  (해요체 {f['haeyo']:.0%}, 합쇼체 '~습니다' {f['hapsyo']:.0%})")
    sl = stats["sentence_len"]
    L.append(f"한 호흡(끊어 말하는 단위) 길이: 평균 {sl['mean']}어절, 대부분 {sl['p25']:.0f}~{sl['p75']:.0f}어절"
             f" · \"네네\" 같은 한두 마디 반응 {sl['short_share']:.0%} · 12어절 넘게 길게 잇는 말 {sl['long_share']:.0%}")
    for title, key, limit in [("말머리·연결", "markers", 8), ("맞장구·반응", "backchannels", 6),
                              ("정도·강조 부사", "intensifiers", 8), ("부탁·완충 표현", "cushions", 7)]:
        s = _fmt(stats[key], "word", limit)
        if s:
            L.append(f"{title}: {s}")
    par = " · ".join(f"\"{p['form']}\"({p['count']}회)" for p in stats["address"]["parent"][:3])
    stu = " · ".join(f"{s['form']}({s['count']}회)" for s in stats["address"]["student"][:4])
    if par:
        L.append(f"학부모 호칭: {par}")
    if stu:
        L.append(f"학생 지칭: {stu}")
    ph = " · ".join(f"\"{p['text']}\"({p['count']}회)" for p in stats["phrases"][:10])
    if ph:
        L.append(f"입에 붙은 구절: {ph}")
    L.append(f"질문으로 끝나는 문장: {stats['question_share']:.0%}")
    return L
