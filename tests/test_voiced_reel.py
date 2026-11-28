"""Voiced reels: subtitle timing, scene layout and the fallback to the slideshow."""
import random
from pathlib import Path

from bot import pipeline, video, voice
from bot.config import Secrets, load_brand


def phrase(words, duration):
    return voice.Phrase(Path("a.mp3"), duration, words)


def test_subtitles_chunks_follow_words_and_skip_slides():
    p1 = phrase([(0.1, 0.4, "Кто"), (0.4, 0.9, "платит?")], 1.2)  # over the cover: no subtitles
    p2 = phrase([(0.0, 0.3, "Груз"), (0.3, 0.6, "из"), (0.6, 1.0, "Китая"), (1.0, 1.8, "застрахован")], 2.0)
    ass = video.subtitles([p1, p2], [False, True])
    events = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert len(events) == 2
    assert events[0].startswith("Dialogue: 0,0:00:01.20,0:00:02.20,") and events[0].endswith("Груз из Китая")
    assert events[1].startswith("Dialogue: 0,0:00:02.20,0:00:03.00,") and events[1].endswith("застрахован")


def ctx():
    return pipeline.Ctx(load_brand(), Secrets("", "", "t", "1", "@c", "", "", "", pixabay_key="k"), {}, {},
                        {"posts": [{"clips": ["pixabay-video:1"]}]}, random.Random(1))


def test_voiced_reel_puts_slides_at_ends_and_clips_between(monkeypatch, tmp_path):
    texts = [{"text": f"фраза {i}", "video_query": "container ship"} for i in range(4)]
    monkeypatch.setattr(voice, "synthesize", lambda t, d: [phrase([(0, 1, "x")], 1.0)] * len(t))
    seen_used = []

    def clip_for(c, query, topic, used, work, i):
        seen_used.append(list(used))
        return (tmp_path / f"clip-{i}.mp4", f"pixabay-video:{i + 10}")

    monkeypatch.setattr(pipeline, "_clip_for", clip_for)
    made = {}
    monkeypatch.setattr(video, "make_voiced_reel", lambda scenes, phrases, target, rng: made.setdefault("s", scenes) and target)
    frames = [tmp_path / "story-1.jpg", tmp_path / "story-2.jpg", tmp_path / "story-3.jpg"]
    reel, keys = pipeline._voiced_reel(ctx(), {"voiceover": texts, "topic": "t"}, frames, tmp_path)
    assert reel == tmp_path / "reel.mp4"
    assert made["s"] == [frames[0], tmp_path / "clip-1.mp4", tmp_path / "clip-2.mp4", frames[-1]]
    assert keys == ["pixabay-video:11", "pixabay-video:12"]
    assert seen_used == [["pixabay-video:1"], ["pixabay-video:1", "pixabay-video:11"]]


def test_voiced_reel_falls_back_when_speech_fails(monkeypatch, tmp_path):
    def broken(t, d):
        raise RuntimeError("tts down")

    monkeypatch.setattr(voice, "synthesize", broken)
    texts = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    assert pipeline._voiced_reel(ctx(), {"voiceover": texts}, [tmp_path / "s.jpg"], tmp_path) == (None, [])
