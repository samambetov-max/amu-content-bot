"""Russian voice-over for reels: Microsoft neural voices through edge-tts (free, no key), with word timings for subtitles."""
import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path

import edge_tts

VOICE = "ru-RU-DmitryNeural"
RATE = "+8%"  # a touch faster than default: reels are watched impatiently
TAIL = 0.25  # pause after each phrase, seconds


@dataclass(frozen=True)
class Phrase:
    audio: Path
    duration: float
    words: list  # [(start, end, word)] in seconds from the phrase start


def _probe_seconds(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


async def _speak(text: str, target: Path, voice: str) -> list:
    words = []
    with target.open("wb") as f:
        async for chunk in edge_tts.Communicate(text, voice, rate=RATE, boundary="WordBoundary").stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append((start, start + chunk["duration"] / 1e7, chunk["text"]))
    return words


def synthesize(texts: list[str], out_dir: Path, voice: str = VOICE) -> list[Phrase]:
    """One audio file per phrase, so each scene lasts exactly as long as its words."""
    out_dir.mkdir(parents=True, exist_ok=True)
    phrases = []
    for i, text in enumerate(texts):
        target = out_dir / f"voice-{i}.mp3"
        words = asyncio.run(_speak(text, target, voice))
        if not words or target.stat().st_size == 0:
            raise RuntimeError(f"no speech for phrase {i}")
        phrases.append(Phrase(target, _probe_seconds(target) + TAIL, words))
    return phrases
