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


# --- Voiced reels: stock clips + voice-over + burned-in subtitles ---
VIDEO_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", str(FPS),
              "-video_track_timescale", "15360"]  # one timescale for all segments, or the copy-concat cuts the video short
MUSIC_UNDER_VOICE = 0.10
SUB_WORDS, SUB_CHARS = 3, 18  # a subtitle line is read at a glance


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *cmd], check=True, cwd=cwd)


def _segment(source: Path, seconds: float, target: Path) -> Path:
    """One scene as a silent 1080x1920 clip: a slide slowly zooming in, or a stock clip cropped to 9:16."""
    if source.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        frames = int(seconds * FPS)
        vf = (f"scale=2160:3840,zoompan=z='1+{ZOOM}*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d=1:s=1080x1920:fps={FPS},setsar=1,format=yuv420p")
        _run(["-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}", "-i", str(source), "-vf", vf, "-an",
              *VIDEO_ARGS, str(target)])
    else:
        vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps={FPS},setsar=1,format=yuv420p"
        _run(["-stream_loop", "-1", "-i", str(source), "-t", f"{seconds:.3f}", "-vf", vf, "-an", *VIDEO_ARGS, str(target)])
    return target


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def subtitles(phrases: list, show: list[bool]) -> str:
    """ASS subtitles: each phrase split into short chunks timed to its words; phrases over a text slide are skipped."""
    lines = []
    start = 0.0
    for phrase, visible in zip(phrases, show):
        chunks, cur = [], []
        for w in phrase.words:
            if cur and (len(cur) >= SUB_WORDS or len(" ".join(x[2] for x in cur + [w])) > SUB_CHARS):
                chunks.append(cur)
                cur = []
            cur.append(w)
        if cur:
            chunks.append(cur)
        for k, chunk in enumerate(chunks if visible else []):
            end = chunks[k + 1][0][0] if k + 1 < len(chunks) else chunk[-1][1]
            text = " ".join(x[2] for x in chunk).replace("{", "(").replace("}", ")")
            lines.append(f"Dialogue: 0,{_ass_time(start + chunk[0][0])},{_ass_time(start + end)},Sub,,0,0,0,,{text}")
        start += phrase.duration
    return """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,DejaVu Sans,82,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,70,70,560,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(lines) + "\n"


def make_voiced_reel(scenes: list[Path], phrases: list, target: Path, rng: random.Random) -> Path:
    """scenes[i] (a slide image or a stock clip) is on screen while phrases[i] is spoken."""
    work = target.parent
    segments = [_segment(src, ph.duration, work / f"seg-{i}.mp4") for i, (src, ph) in enumerate(zip(scenes, phrases))]
    (work / "segments.txt").write_text("".join(f"file '{s.name}'\n" for s in segments))
    _run(["-f", "concat", "-safe", "0", "-i", "segments.txt", "-c", "copy", "joined.mp4"], cwd=work)
    show = [src.suffix.lower() not in {".jpg", ".jpeg", ".png"} for src in scenes]
    (work / "subs.ass").write_text(subtitles(phrases, show), encoding="utf-8")

    # The voice track is built on its own: mixed into one graph with the video, ffmpeg cut it after a few phrases.
    n = len(phrases)
    parts = [f"[{k}:a]aresample=44100,apad=whole_dur={ph.duration:.3f}[a{k}]" for k, ph in enumerate(phrases)]
    parts.append("".join(f"[a{k}]" for k in range(n)) + f"concat=n={n}:v=0:a=1[voice]")
    _run([x for ph in phrases for x in ("-i", str(ph.audio.resolve()))]
         + ["-filter_complex", ";".join(parts), "-map", "[voice]", "voice.wav"], cwd=work)

    music = pick_music(rng)
    cmd = ["-i", "joined.mp4", "-i", "voice.wav"]
    cmd += ["-stream_loop", "-1", "-i", str(music.resolve())] if music else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    graph = (f"[2:a]volume={MUSIC_UNDER_VOICE}[m];[1:a][m]amix=inputs=2:duration=first:normalize=0[a];"
             f"[0:v]subtitles=subs.ass:fontsdir={ASSETS_DIR / 'fonts'}[v]")
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", *VIDEO_ARGS,
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-shortest", "-movflags", "+faststart", target.name]
    _run(cmd, cwd=work)
    return target
