"""샘플 통화로 STT 정확도와 화자 구분 정확도를 잰다 (정답 = samples/*.truth.json).

사용: python scripts/eval_samples.py         → samples/_stt_cache/ 에 전사 캐시 저장
"""
from __future__ import annotations

import difflib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import speakers, stt  # noqa: E402

SAMPLES = ROOT / "samples"
CACHE = SAMPLES / "_stt_cache"


def norm(s: str) -> str:
    return re.sub(r"[^가-힣0-9a-zA-Z]", "", s)


def transcribe_cached(audio: Path) -> dict:
    CACHE.mkdir(exist_ok=True)
    f = CACHE / (audio.stem + ".json")
    if f.exists() and f.stat().st_mtime >= audio.stat().st_mtime:
        return json.loads(f.read_text(encoding="utf-8"))
    t0 = time.time()
    res = stt.transcribe(audio)
    res["elapsed"] = round(time.time() - t0, 1)
    f.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def gold_speakers(stem: str, units: list[dict]) -> tuple[list[str | None], float]:
    """정답 대본과 글자 단위로 정렬해 문장마다 정답 화자를 구한다. 반환: (문장별 정답 화자, 글자 일치율)."""
    truth = json.loads((SAMPLES / f"{stem}.truth.json").read_text(encoding="utf-8"))
    ref = norm("".join(t["text"] for t in truth["turns"]))
    hyp = norm("".join(u["text"] for u in units))
    sm = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    # 정답 글자마다 화자 표시 → 가설 글자와 정렬해 문장별 정답 화자를 다수결로 구한다
    ref_spk = "".join(t["speaker"] * len(norm(t["text"])) for t in truth["turns"])
    hyp_true = ["?"] * len(hyp)
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            hyp_true[b + k] = ref_spk[a + k]
    gold: list[str | None] = []
    pos = 0
    for u in units:
        n = len(norm(u["text"]))
        votes = hyp_true[pos:pos + n]
        pos += n
        t, p = votes.count("T"), votes.count("P")
        gold.append(None if t + p == 0 else ("T" if t >= p else "P"))
    return gold, sm.ratio()


def evaluate(audio: Path, res: dict, units: list[dict]) -> dict:
    gold, char_acc = gold_speakers(audio.stem, units)
    right, total, wrong = 0, 0, []
    for u, g in zip(units, gold):
        if g is None:
            continue
        total += 1
        if g == u["speaker"]:
            right += 1
        else:
            wrong.append(f"  정답 {g} / 추정 {u['speaker']} (확신 {u['conf']}): {u['text']}")
    return {"name": audio.stem, "duration": res["duration"], "elapsed": res.get("elapsed"),
            "char_acc": char_acc, "spk_acc": right / max(1, total), "units": total, "wrong": wrong}


def main() -> None:
    print("STT 엔진:", json.dumps(stt.engine_status(), ensure_ascii=False))
    audios = sorted(SAMPLES.glob("*.m4a"))
    results = [transcribe_cached(a) for a in audios]
    all_units = [speakers.assign_speakers(speakers.segments_to_units(r["segments"])) for r in results]
    habit = speakers.refine_all(all_units)       # 세 통화를 합쳐 교사 말버릇을 배운 뒤 재추정
    print("자기학습으로 배운 화자별 말:", {k: round(v, 2) for k, v in sorted(habit.items(), key=lambda kv: -kv[1])})
    rows = [evaluate(a, r, u) for a, r, u in zip(audios, results, all_units)]
    for r in rows:
        sp = f"{r['duration'] / r['elapsed']:.0f}배속" if r.get("elapsed") else "캐시"
        print(f"{r['name']}: {r['duration']}초 / 전사 {r.get('elapsed', '-')}초({sp}) · "
              f"글자 일치 {r['char_acc']:.1%} · 화자 구분 {r['spk_acc']:.1%} ({r['units']}문장)")
        for w in r["wrong"]:
            print(w)
    if rows:
        print(f"평균: 글자 일치 {sum(r['char_acc'] for r in rows) / len(rows):.1%} · "
              f"화자 구분 {sum(r['spk_acc'] * r['units'] for r in rows) / sum(r['units'] for r in rows):.1%}")


if __name__ == "__main__":
    main()
