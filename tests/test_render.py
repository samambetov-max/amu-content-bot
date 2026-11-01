"""Real rendering with headless Chrome and ffmpeg (skipped where they are missing)."""
import json
import random
import shutil
from pathlib import Path

import pytest

from bot import render, video
from bot.config import load_brand

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "sample_post.json").read_text(encoding="utf-8"))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_story_slides_and_reel(tmp_path):
    rng = random.Random(1)
    post = {**SAMPLE, "slides": SAMPLE["slides"][:2]}
    frames = render.render_slides(post, load_brand(), tmp_path, "story", render.pick_photo("truck", [], rng))
    assert len(frames) == 2 and all(f.stat().st_size > 20_000 for f in frames)
    reel = video.make_reel(frames, tmp_path / "reel.mp4", rng)
    assert reel.stat().st_size > 50_000


def test_photo_rotation_avoids_recent():
    rng = random.Random(2)
    first = render.pick_photo("truck", [], rng)
    assert render.pick_photo("truck", [first.name], rng).name != first.name
