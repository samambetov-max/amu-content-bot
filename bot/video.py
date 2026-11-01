"""Reels: 9:16 slides joined into an MP4 with a slow zoom, crossfades and optional background music."""
import logging
import random
import subprocess
from pathlib import Path

from .config import ASSETS_DIR

log = logging.getLogger(__name__)
FPS = 30
SLIDE_SECONDS = 4.5
FADE_SECONDS = 0.5
ZOOM = 0.06  # each slide grows 6% while on screen


def total_seconds(count: int) -> float:
    return count * SLIDE_SECONDS - (count - 1) * FADE_SECONDS


def pick_music(rng: random.Random) -> Path | None:
    tracks = sorted((ASSETS_DIR / "music").glob("*.mp3")) + sorted((ASSETS_DIR / "music").glob("*.m4a"))
    return rng.choice(tracks) if tracks else None


def build_command(images: list[Path], target: Path, music: Path | None) -> list[str]:
    n = len(images)
    frames = int(SLIDE_SECONDS * FPS)
    duration = total_seconds(n)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for img in images:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", str(SLIDE_SECONDS), "-i", str(img)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    # Upscale before zoompan: zooming at output size makes the picture jitter.
    parts = [
        f"[{i}:v]scale=2160:3840,zoompan=z='1+{ZOOM}*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s=1080x1920:fps={FPS},setsar=1,format=yuv420p[v{i}]"
        for i in range(n)
    ]
    last = "v0"
    for k in range(1, n):
        offset = k * (SLIDE_SECONDS - FADE_SECONDS)
        parts.append(f"[{last}][v{k}]xfade=transition=fade:duration={FADE_SECONDS}:offset={offset:.2f}[x{k}]")
        last = f"x{k}"
    audio_in = n
    parts.append(f"[{audio_in}:a]volume=0.55,afade=t=in:d=1,afade=t=out:st={duration - 1.5:.2f}:d=1.5[a]")
    cmd += [
        "-filter_complex", ";".join(parts), "-map", f"[{last}]", "-map", "[a]",
        "-t", f"{duration:.2f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart", str(target),
    ]
    return cmd


def make_reel(images: list[Path], target: Path, rng: random.Random) -> Path:
    music = pick_music(rng)
    if not music:
        log.info("no music in assets/music, reel will be silent")
    subprocess.run(build_command(images, target, music), check=True)
    return target
