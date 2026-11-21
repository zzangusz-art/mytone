"""검증용 가상 통화 녹음 3개 생성 (실제 인물·학생 아님, 전부 지어낸 대본).

교사 대사에 의도적으로 말버릇을 심어 두고(‑거든요, ‑더라고요, 일단은, 사실, 네네, 그쵸 …)
STT → 화자 구분 → 말투 측정이 그 버릇을 실제로 잡아내는지 확인하는 데 쓴다.

사용:  python scripts/make_sample_calls.py            (samples/ 에 m4a 3개 + 정답 json)
필요:  edge-tts(인터넷), ffmpeg
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples"

TEACHER_VOICE = "ko-KR-InJoonNeural"
PARENT_VOICES = {"mother": "ko-KR-SunHiNeural", "father": "ko-KR-HyunsuMultilingualNeural"}

CALLS = [
    {
        "name": "sample_call_1_숙제",
        "parent": "mother",
        "student": "서준",
        "turns": [
            ("T", "어머님 안녕하세요, 서준이 담임이에요. 지금 잠깐 통화 괜찮으세요?"),
            ("P", "아 네 선생님 안녕하세요. 네 괜찮아요."),
            ("T", "네네, 다름이 아니라 서준이 요즘 학교생활 좀 말씀드리려고 전화드렸거든요. 일단은 큰일은 아니고요, 어머님 놀라실까 봐 먼저 말씀드려요."),
            ("P", "아 네, 무슨 일 있나요 선생님?"),
            ("T", "사실 서준이가 원래 수업 태도가 되게 좋은 친구잖아요. 근데 요 이 주 정도 숙제를 좀 안 해오더라고요. 수학이랑 영어 쪽에서 세 번 정도 빠졌거든요."),
            ("P", "아 그래요? 집에서는 다 했다고 하던데요."),
            ("T", "아 그쵸, 애들이 보통 그렇게 말하거든요. 제가 서준이랑 어제 잠깐 얘기를 해봤는데요, 요즘 학원 숙제가 많아져서 학교 거를 좀 미뤘다고 하더라고요."),
            ("P", "아이고, 학원을 하나 더 늘렸거든요. 그게 좀 무리였나 봐요."),
            ("T", "네네, 아무래도 그런 것 같아요. 일단은 제가 이번 주까지는 숙제 검사할 때 서준이를 좀 더 챙겨볼게요. 그리고 어머님께서도 집에서 저녁에 한 번만 물어봐 주시면 좋을 것 같아요."),
            ("P", "네 알겠습니다. 제가 확인할게요. 성적은 좀 어떤가요?"),
            ("T", "성적은요, 사실 지난 중간고사 때는 반에서 중상위권이었거든요. 근데 이번 수행평가 점수가 조금 내려가긴 했어요. 그래도 아직 걱정하실 정도는 아니고요, 지금 잡으면 충분히 올라와요."),
            ("P", "다행이네요. 감사합니다 선생님."),
            ("T", "아니에요 어머님. 서준이가 워낙 성실한 친구라서 금방 돌아올 거예요. 제가 다음 주에 한 번 더 보고 다시 연락드릴게요."),
            ("P", "네 선생님, 신경 써 주셔서 감사해요."),
            ("T", "네 어머님, 그럼 또 연락드릴게요. 들어가세요."),
        ],
    },
    {
        "name": "sample_call_2_교우",
        "parent": "father",
        "student": "하윤",
        "turns": [
            ("T", "아버님 안녕하세요, 하윤이 담임이에요. 지금 잠깐 통화 괜찮으세요?"),
            ("P", "네 선생님, 말씀하세요."),
            ("T", "네네, 다름이 아니라 오늘 학교에서 하윤이가 친구랑 좀 다툼이 있었거든요. 일단은 다친 데는 없고요, 아버님 걱정하실까 봐 먼저 말씀드려요."),
            ("P", "아 그래요? 누구랑 싸웠습니까?"),
            ("T", "같은 반 친구인데요, 사실 둘이 원래 되게 친한 사이잖아요. 근데 점심시간에 모둠 활동 얘기하다가 말이 좀 세게 나왔더라고요. 서로 밀치거나 그런 건 전혀 없었고요."),
            ("P", "하윤이가 먼저 그런 건가요?"),
            ("T", "음, 그게요, 제가 양쪽 얘기를 다 들어봤는데 누가 먼저라고 하기는 좀 어렵더라고요. 둘 다 서운한 게 좀 쌓여 있었던 것 같아요. 일단은 오늘 방과 후에 둘이 같이 얘기하고 서로 사과는 했거든요."),
            ("P", "아 네, 그럼 다행이네요."),
            ("T", "네네, 그쵸. 근데 하윤이가 집에 가서 좀 속상해할 수도 있거든요. 그래서 아버님께서 오늘 저녁에 무슨 일 있었냐고 한 번만 편하게 물어봐 주시면 좋을 것 같아요."),
            ("P", "알겠습니다. 제가 얘기해 보겠습니다."),
            ("T", "네 감사해요. 사실 하윤이가 평소에 친구들을 되게 잘 챙기는 친구라서요, 저는 크게 걱정은 안 하거든요. 제가 이번 주는 둘이 지내는 거 좀 더 지켜보고요, 혹시 또 무슨 일 있으면 바로 연락드릴게요."),
            ("P", "네 선생님 감사합니다."),
            ("T", "네 아버님, 그럼 또 연락드릴게요. 들어가세요."),
        ],
    },
    {
        "name": "sample_call_3_칭찬",
        "parent": "mother",
        "student": "지호",
        "turns": [
            ("T", "어머님 안녕하세요, 지호 담임이에요. 지금 잠깐 통화 괜찮으세요?"),
            ("P", "네 선생님 안녕하세요. 혹시 지호가 무슨 잘못했나요?"),
            ("T", "아 아니에요 어머님, 오늘은 좋은 일로 전화드렸거든요. 일단은 안심하셔도 돼요."),
            ("P", "아 그래요? 다행이다."),
            ("T", "네네, 지호가 이번에 과학 발표를 했는데요, 준비를 되게 꼼꼼하게 해왔더라고요. 친구들 앞에서 설명도 차분하게 잘했고요. 과학 선생님도 따로 칭찬을 하시더라고요."),
            ("P", "어머 정말요? 집에서는 그런 얘기 전혀 안 하던데요."),
            ("T", "그쵸, 지호가 원래 자기 자랑을 잘 안 하는 친구잖아요. 그래서 제가 어머님께 꼭 말씀드리고 싶었거든요. 집에서 한 번만 칭찬해 주시면 지호가 되게 좋아할 것 같아요."),
            ("P", "네 꼭 그럴게요. 감사합니다 선생님."),
            ("T", "아니에요. 그리고 하나만 더 말씀드리면요, 다음 달 둘째 주에 학부모 상담 주간이 있거든요. 사실 지호는 특별히 걱정되는 건 없는데요, 진로 얘기를 한번 나눠보면 좋을 것 같아서요."),
            ("P", "아 네, 저도 그게 좀 궁금했어요. 어떻게 신청하면 되나요?"),
            ("T", "일단은 다음 주에 가정통신문이 나가거든요. 거기에 가능한 시간을 적어서 보내 주시면 돼요. 혹시 직접 오시기 어려우면 전화 상담도 되니까요, 편하신 쪽으로 하시면 돼요."),
            ("P", "네 알겠습니다. 확인하고 신청할게요."),
            ("T", "네 어머님, 그럼 또 연락드릴게요. 들어가세요."),
        ],
    },
]


async def _tts(text: str, voice: str, path: Path) -> None:
    import edge_tts

    last: Exception | None = None
    for attempt in range(4):  # 서비스가 가끔 빈 응답을 준다 → 재시도, 마지막엔 속도 옵션 없이
        try:
            kwargs = {"rate": "+8%"} if attempt < 2 else {}
            await edge_tts.Communicate(text, voice, **kwargs).save(str(path))
            if path.exists() and path.stat().st_size > 1000:
                return
        except Exception as e:  # noqa: BLE001
            last = e
        await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"TTS 실패({voice}): {last}")


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {p.stderr[-600:]}")


async def build_call(call: dict, ffmpeg: str = "ffmpeg") -> Path:
    OUT.mkdir(exist_ok=True)
    out = OUT / f"{call['name']}.m4a"
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        parts = []
        for i, (who, text) in enumerate(call["turns"]):
            voice = TEACHER_VOICE if who == "T" else PARENT_VOICES[call["parent"]]
            mp3 = tdp / f"{i:02d}.mp3"
            await _tts(text, voice, mp3)
            parts.append(mp3)
        # 턴 사이 0.35초 쉼 + 전화 음질(8kHz 대역·모노)로 낮춰 실제 통화 녹음에 가깝게
        inputs: list[str] = []
        for p in parts:
            inputs += ["-i", str(p)]
        n = len(parts)
        chain = "".join(f"[{i}:a]aresample=16000,apad=pad_dur=0.35[a{i}];" for i in range(n))
        chain += "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[cat];"
        chain += "[cat]highpass=f=300,lowpass=f=3400,volume=0.9[out]"
        _run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *inputs,
              "-filter_complex", chain, "-map", "[out]", "-ac", "1", "-ar", "16000",
              "-c:a", "aac", "-b:a", "48k", str(out)])
    truth = {"name": call["name"], "student": call["student"],
             "turns": [{"speaker": w, "text": t} for w, t in call["turns"]]}
    (OUT / f"{call['name']}.truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


async def main() -> None:
    for call in CALLS:
        path = await build_call(call)
        print("생성:", path.name, f"{path.stat().st_size/1024:.0f}KB")


if __name__ == "__main__":
    asyncio.run(main())
