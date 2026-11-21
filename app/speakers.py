"""화자 구분 — 전사된 통화에서 '교사(T)'와 '학부모(P)' 발화를 가른다.

STT 가 화자를 알려주지 않으므로 글의 단서로 추정한다:
  · 호칭: "어머님/아버님"이라 부르는 쪽이 교사, "선생님"이라 부르는 쪽이 학부모
  · 내용: 학교·수업 쪽 어휘 vs 집·아이 쪽 어휘
  · 흐름: 질문 다음엔 화자가 바뀌고, 같은 STT 구간 안의 문장은 대개 같은 화자
자동 추정이므로 화면에서 교사가 확인·수정하는 것을 전제로 한다(각 문장에 확신도 표시).
"""
from __future__ import annotations

import re

from .textutil import is_question, split_sentences, strip_tail, words

_SUBJECT_TEACHER = r"(담임|과학|수학|영어|국어|사회|역사|체육|음악|미술|기술|가정|도덕|정보|보건|상담|교과|부장|교감|교장|학원|원장|다른|옆\s*반)"

_T_CUES: list[tuple[str, float]] = [
    (r"(어머님|아버님|어머니|아버지|학부모님|보호자분|보호자님)", 3.0),
    (r"담임(이에요|입니다|이고요|인데요|이구요|\s*교사)", 4.0),
    (r"(선생|쌤|강사|원장)(이에요|입니다|인데요)", 3.0),
    (r"(전화|연락|말씀|안내|부탁)\s*드(렸|리|릴|려|립)", 2.0),
    (r"(수업|숙제|과제|수행평가|중간고사|기말고사|시험\s*범위|모둠|발표|반에서|교실|학교에서|상담\s*주간|가정통신문|"
     r"생활기록부|생기부|출결|조퇴|결석|지각|진도|단원|레벨\s*테스트|보충)", 1.0),
    (r"제가\s*.{0,12}(지켜보|챙겨|확인해\s*보|살펴|지도|얘기를?\s*해\s*봤|들어\s*봤|불러서)", 1.5),
    (r"(물어봐|칭찬해|확인해|챙겨|보내|봐)\s*주시면", 2.5),
    (r"(걱정|놀라|속상해)\s*하실(까|\s*수)", 2.0),
    (r"(친구|학생)(잖아요|라서|인데|예요|이에요|거든요)", 1.2),
]
_P_CUES: list[tuple[str, float]] = [
    # "선생님" 호칭 단서는 _score 에서 따로 계산한다(교사가 다른 교사를 가리키는 경우 제외)
    (r"(우리|저희)\s*(애|아이|아들|딸|집)", 2.5),
    (r"(애|아이)\s*(아빠|엄마|아버지|할머니)", 2.5),
    (r"집에서는?\s*.{0,14}(던데|하던|했다고|안\s*하|그런\s*얘기)", 2.2),
    (r"신경\s*써\s*주셔서", 3.0),
    (r"(감사합니다|감사해요|고맙습니다)", 0.8),
    (r"(알겠습니다|알겠어요|그럴게요|확인할게요)", 0.9),
    (r"(학원을?\s*.{0,8}(늘렸|보내|다니)|집에\s*와서|집에서\s*(는|도)\s)", 1.5),
    (r"(어떤가요|괜찮은가요|잘\s*하나요|잘못했나요|되나요|싸웠습니까|건가요)", 0.9),
]


def _teacher_vocative_guard(sentence: str) -> bool:
    """'과학 선생님도 칭찬을…'처럼 교사가 다른 교사를 가리키는 경우."""
    return bool(re.search(rf"{_SUBJECT_TEACHER}\s*(선생님|쌤)", sentence))


def _score(sentence: str) -> tuple[float, float]:
    """(교사 단서 점수, 학부모 단서 점수)."""
    t = sum(w for pat, w in _T_CUES if re.search(pat, sentence))
    p = sum(w for pat, w in _P_CUES if re.search(pat, sentence))
    if re.search(r"(선생님|쌤)", sentence):
        if _teacher_vocative_guard(sentence):
            t += 1.0          # 다른 교사 언급 = 학교 쪽 화자
        else:
            p += 2.8          # 상대를 "선생님"이라 부른다 = 학부모
    return t, p


def _is_reaction(sentence: str) -> bool:
    """'아 그래요?', '어머 정말요?', '네네.' 같은 짧은 맞장구·반응."""
    core = strip_tail(sentence)
    return len(core.split()) <= 3 and bool(
        re.match(r"^(아|어|어머|아이고|아유|오|와|정말|진짜|다행|그래요|그렇군요|그쵸|그죠|그렇죠|네|예|음)", core))


def segments_to_units(segments: list[dict]) -> list[dict]:
    """STT 구간 → 문장 단위(unit). 시간은 글자 수 비례로 나눈다."""
    units: list[dict] = []
    for si, seg in enumerate(segments):
        sents = split_sentences(seg["text"])
        if not sents:
            continue
        total = sum(len(s) for s in sents) or 1
        t0, t1 = float(seg.get("start", 0)), float(seg.get("end", 0))
        acc = 0
        for s in sents:
            a = t0 + (t1 - t0) * acc / total
            acc += len(s)
            b = t0 + (t1 - t0) * acc / total
            units.append({"i": len(units), "seg": si, "start": round(a, 2), "end": round(b, 2),
                          "text": s, "speaker": "?", "conf": 0.0})
    return units


def learn_habits(units_lists: list[list[dict]]) -> dict[str, float]:
    """확신이 높은(또는 교사가 직접 고친) 문장만으로 '교사만 쓰는 말 / 학부모만 쓰는 말'을 배운다.

    실측: 확신 낮은 문장까지 넣으면 오히려 정확도가 떨어져(95.1% → 92.7%) 확신 0.7 이상만 쓴다.
    교사가 화면에서 고친 문장이 늘수록 이 학습이 강해진다.

    교사는 모든 녹음에 공통으로 등장하므로 말버릇('일단은', '사실', '네네' 등)이 일정하다.
    여러 녹음을 합쳐 배운 가중치를 단서 없는 문장을 가르는 데 쓴다. 반환: 어절 → 가중치(+교사, -학부모).
    """
    import math
    from collections import Counter

    ct: Counter[str] = Counter()
    cp: Counter[str] = Counter()
    for units in units_lists:
        for u in units:
            if u.get("deleted") or (u.get("conf", 0) < 0.7 and not u.get("manual")):
                continue
            toks = set(words(u["text"]))
            (ct if u["speaker"] == "T" else cp).update(toks)
    nt, np_ = sum(ct.values()) or 1, sum(cp.values()) or 1
    weights: dict[str, float] = {}
    for w in set(ct) | set(cp):
        if ct[w] + cp[w] < 3 or len(w) < 2:
            continue
        lo = math.log(((ct[w] + 0.5) / nt) / ((cp[w] + 0.5) / np_))
        if abs(lo) >= 0.9:
            weights[w] = max(-2.0, min(2.0, lo)) * 0.5
    return weights


def refine_all(units_lists: list[list[dict]]) -> dict[str, float]:
    """여러 녹음을 함께 보고 화자 추정을 한 번 더 다듬는다. 교사가 손으로 고친 문장은 건드리지 않는다."""
    habit = learn_habits(units_lists)
    if habit:
        for units in units_lists:
            assign_speakers(units, habit=habit)
    return habit


def assign_speakers(units: list[dict], habit: dict[str, float] | None = None) -> list[dict]:
    """비터비로 T/P 라벨을 정한다. units 를 제자리에서 고쳐 반환.

    단서: 호칭·내용(_score) + 대화 흐름(trans) + (있으면) 화자별 말버릇 가중치(habit).
    교사가 직접 지정한 문장(manual)은 그대로 고정한다.
    """
    n = len(units)
    if n == 0:
        return units
    emis: list[float] = []  # +면 교사, -면 학부모
    for u in units:
        if u.get("manual"):
            emis.append(8.0 if u["speaker"] == "T" else -8.0)
            continue
        t, p = _score(u["text"])
        e = t - p
        if habit:
            e += max(-2.5, min(2.5, sum(habit.get(w, 0.0) for w in set(words(u["text"])))))
        emis.append(max(-5.0, min(5.0, e)))

    def trans(i: int, same: bool) -> float:
        prev, cur = units[i - 1], units[i]
        score = 0.0
        if cur["seg"] == prev["seg"]:
            score += 1.0 if same else -0.6      # 같은 STT 구간 = 한 호흡("아 그래요? 집에서는…")
        elif is_question(prev["text"]):
            score += -1.2 if same else 1.6      # 질문 뒤 새 구간 = 상대가 답한다
        else:
            score += 0.3 if same else 0.0
            if _is_reaction(cur["text"]):       # 새 구간을 여는 짧은 반응 = 듣고 있던 쪽
                score += -0.5 if same else 0.7
        gap = cur["start"] - prev["end"]
        if gap >= 0.8 and not same:
            score += 0.3
        return score

    best = [[0.0, 0.0] for _ in range(n)]       # [T, P]
    back = [[0, 0] for _ in range(n)]
    best[0] = [emis[0], -emis[0]]
    for i in range(1, n):
        for s in (0, 1):
            e = emis[i] if s == 0 else -emis[i]
            stay = best[i - 1][s] + trans(i, True)
            switch = best[i - 1][1 - s] + trans(i, False)
            ps = s if stay >= switch else 1 - s   # 동점이면 화자 유지
            best[i][s] = max(stay, switch) + e
            back[i][s] = ps
    s = 0 if best[-1][0] >= best[-1][1] else 1
    labels = [0] * n
    for i in range(n - 1, -1, -1):
        labels[i] = s
        s = back[i][s]
    for i, u in enumerate(units):
        if u.get("manual"):
            continue
        u["speaker"] = "T" if labels[i] == 0 else "P"
        own = emis[i] if labels[i] == 0 else -emis[i]
        u["conf"] = round(max(0.0, min(1.0, 0.5 + own / 6)), 2)   # 단서가 없으면 0.5(흐름으로만 추정)
    # 단서 없이 흐름으로만 정해진 문장: 같은 STT 구간에 확실한 문장이 있거나 앞뒤가 같은 화자면 믿을 만하다
    base = [u["conf"] for u in units]
    for i, u in enumerate(units):
        if u.get("manual") or base[i] > 0.5:
            continue
        boost = 0.0
        for j in range(max(0, i - 3), min(n, i + 4)):
            v = units[j]
            if j != i and v["seg"] == u["seg"] and v["speaker"] == u["speaker"] and (base[j] >= 0.65 or v.get("manual")):
                boost = 0.2
                break
        if 0 < i < n - 1 and units[i - 1]["speaker"] == u["speaker"] == units[i + 1]["speaker"]:
            boost += 0.12
        u["conf"] = round(min(0.8, base[i] + boost), 2)
    return units


_LABEL_T = r"(교사|선생님?|담임|쌤|나|본인|T|강사|원장)"
_LABEL_P = r"(학부모|어머님?|아버님?|엄마|아빠|부모|보호자|상대|P)"
_LABELED = re.compile(r"^\s*(?:\[?\(?\d{1,2}:\d{2}(?::\d{2})?\)?\]?\s*)?"
                      r"(?P<label>[^\s:：\]]{1,12}(?:\s?\d)?)\s*[:：]\s*(?P<text>.+)$")
_CLOVA = re.compile(r"^\s*(?P<label>(참석자|화자|Speaker|발화자)\s*\d+)\s*(?:\d{1,2}:\d{2}(?::\d{2})?)?\s*$", re.I)


def parse_pasted_transcript(text: str) -> list[dict]:
    """붙여넣은 대화록 → 문장 단위. '교사: …' / '참석자 1 00:12' 형식이면 화자를 그대로 쓴다."""
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    turns: list[tuple[str, str]] = []
    pending_label: str | None = None
    labeled = 0
    for ln in lines:
        if not ln.strip():
            continue
        m2 = _CLOVA.match(ln)
        if m2:
            pending_label = m2.group("label")
            continue
        m = _LABELED.match(ln)
        if m and len(m.group("label")) <= 12:
            turns.append((m.group("label").strip(), m.group("text").strip()))
            labeled += 1
            pending_label = None
        elif pending_label:
            turns.append((pending_label, ln.strip()))
            labeled += 1
        else:
            turns.append(("", ln.strip()))
    units: list[dict] = []
    have_labels = labeled >= max(2, len(turns) * 0.6)
    raw_labels: list[str] = []
    for ti, (label, body) in enumerate(turns):
        for s in split_sentences(body):
            units.append({"i": len(units), "seg": ti, "start": 0.0, "end": 0.0,
                          "text": s, "speaker": "?", "conf": 0.0})
            raw_labels.append(label)
    if not units:
        return units
    if not have_labels:
        return assign_speakers(units)
    # 라벨이 있으면: 이름이 명확하면 그대로, '참석자 1/2'처럼 중립이면 내용 단서로 어느 쪽이 교사인지 정한다
    names = sorted(set(raw_labels))
    mapping: dict[str, str] = {}
    for nm in names:
        if re.fullmatch(_LABEL_T, nm, re.I):
            mapping[nm] = "T"
        elif re.fullmatch(_LABEL_P, nm, re.I):
            mapping[nm] = "P"
    unknown = [nm for nm in names if nm not in mapping]
    if unknown:
        tally = {nm: 0.0 for nm in unknown}
        for u, lab in zip(units, raw_labels):
            if lab in tally:
                t, p = _score(u["text"])
                tally[lab] += t - p
        teacher = max(tally, key=lambda k: tally[k]) if "T" not in mapping.values() else None
        for nm in unknown:
            mapping[nm] = "T" if nm == teacher else "P"
    for u, lab in zip(units, raw_labels):
        u["speaker"] = mapping.get(lab, "P")
        u["conf"] = 0.95
    return units


def teacher_turns(units: list[dict]) -> list[list[dict]]:
    """연속된 교사 문장을 한 '턴'으로 묶는다(삭제 표시된 문장 제외)."""
    turns: list[list[dict]] = []
    cur: list[dict] = []
    for u in units:
        if u.get("deleted"):
            continue
        if u["speaker"] == "T":
            cur.append(u)
        elif cur:
            turns.append(cur)
            cur = []
    if cur:
        turns.append(cur)
    return turns


def is_backchannel(sentence: str) -> bool:
    core = strip_tail(sentence)
    return len(core) <= 6 and bool(re.fullmatch(r"(아\s*)?(네|예|음|어|아|응)+(\s*(네|예))*|그쵸|그죠|그렇죠|맞아요|그럼요", core))
