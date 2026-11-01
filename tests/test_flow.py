"""End-to-end runs of the scheduler with fake Telegram, Instagram, model and renderer."""
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bot import llm, pipeline, storage, tick
from bot.config import Secrets, load_brand
from bot.pipeline import Ctx

TZ = ZoneInfo("Asia/Tashkent")
SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "sample_post.json").read_text(encoding="utf-8"))
STRATEGY = json.loads((Path(__file__).parent.parent / "data" / "strategy.json").read_text(encoding="utf-8"))
SECRETS = Secrets(gemini_key="g", anthropic_key="", tg_token="t", tg_admin_id="42", tg_channel="@chan",
                  ig_user_id="ig", ig_token="tok", media_base_url="https://u.github.io/r")


class FakeTelegram:
    def __init__(self):
        self.updates, self.sent, self.posts, self.next_id = [], [], [], 100

    def get_updates(self, token, offset):
        return [u for u in self.updates if u["update_id"] >= offset]

    def send_text(self, token, chat_id, text, markup=None, reply_to=None, preview_url=None):
        self.next_id += 1
        self.sent.append({"chat": chat_id, "text": text, "markup": markup})
        return {"message_id": self.next_id}

    def send_post(self, token, chat_id, fmt, files, caption, first_image_url=None, markup=None):
        self.next_id += 1
        self.posts.append({"chat": chat_id, "fmt": fmt, "caption": caption, "markup": markup})
        return [self.next_id]

    def answer_callback(self, *a, **k):
        pass

    def clear_buttons(self, *a, **k):
        pass

    def texts(self):
        return "\n".join(m["text"] for m in self.sent)


@pytest.fixture
def env(monkeypatch, tmp_path):
    tg = FakeTelegram()
    for name in ("get_updates", "send_text", "send_post", "answer_callback", "clear_buttons"):
        monkeypatch.setattr(f"bot.telegram.{name}", getattr(tg, name))
    calls = {"write": 0, "ig": []}

    def write_post(*a, wish="", fmt=None, content_type=None, **k):
        calls["write"] += 1
        return {**SAMPLE, "topic": f"тема {calls['write']} {wish}".strip(), "format": fmt or "carousel"}

    monkeypatch.setattr("bot.writer.write_post", write_post)
    monkeypatch.setattr("bot.writer.rewrite_post", lambda s, b, st, post, instr: {**post, "caption_ru": instr})
    monkeypatch.setattr("bot.pipeline.render_media", lambda ctx, post, pid: {"files": [f"{pid}/feed-1.jpg"], "photo": "a.jpg"})
    monkeypatch.setattr("bot.media_store.is_live", lambda url: True)
    monkeypatch.setattr("bot.instagram.publish", lambda *a: calls["ig"].append(a) or {"id": "m1", "permalink": "https://ig/p/1"})
    monkeypatch.setattr("bot.instagram.insights", lambda *a: {"reach": 100, "likes": 5})
    monkeypatch.setattr("bot.pipeline.MEDIA_DIR", tmp_path)
    monkeypatch.setattr("bot.tick.MEDIA_DIR", tmp_path)
    monkeypatch.setattr("bot.media_store.MEDIA_DIR", tmp_path)
    monkeypatch.setattr("bot.storage.DATA_DIR", tmp_path)
    return tg, calls


def make_ctx(brand=None, state=None, history=None):
    return Ctx(brand=brand or load_brand(), secrets=SECRETS, strategy=dict(STRATEGY),
               state={**tick.DEFAULT_STATE, **(state or {})}, history=history or {"posts": []}, rng=random.Random(1))


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def step(ctx, now):
    ctx = tick.process_updates(ctx, now)
    ctx = tick.maybe_publish(ctx, now)
    ctx = tick.maybe_draft(ctx, now)
    return tick.maybe_publish(ctx, now)


def button(uid, action, draft_id):
    return {"update_id": uid, "callback_query": {"id": f"c{uid}", "from": {"id": 42}, "data": f"{action}:{draft_id}",
                                                 "message": {"message_id": 1}}}


def test_draft_then_admin_approves(env):
    tg, calls = env
    ctx = step(make_ctx(), at(2, 7))
    draft = ctx.state["pending"]
    assert draft and draft["publish_at"] == at(2, 9).isoformat()
    assert tg.posts[0]["chat"] == "42" and "Черновик" in tg.texts()

    tg.updates.append(button(1, "pub", draft["id"]))
    ctx = step(ctx, at(2, 7, 15))
    assert ctx.state["pending"] is None
    post = ctx.history["posts"][-1]
    assert post["approved_by"] == "admin" and post["ig_permalink"] == "https://ig/p/1"
    assert tg.posts[-1]["chat"] == "@chan" and "Опубликовано" in tg.texts()
    assert "Рассчитать доставку" in str(tg.posts[-1]["markup"])
    assert calls["ig"][0][2] == "carousel"


def test_no_answer_publishes_at_slot_time(env):
    tg, _ = env
    ctx = step(make_ctx(), at(2, 7))
    ctx = step(ctx, at(2, 8, 45))
    assert ctx.state["pending"] is not None
    ctx = step(ctx, at(2, 9))
    assert ctx.history["posts"][-1]["approved_by"] == "timeout"


def test_manual_mode_waits_for_button(env):
    brand = {**load_brand(), "approval_mode": "manual"}
    ctx = step(make_ctx(brand=brand), at(2, 7))
    ctx = step(ctx, at(2, 12))
    assert ctx.state["pending"] and not ctx.history["posts"]


def test_reply_rewrites_draft(env):
    tg, _ = env
    ctx = step(make_ctx(), at(2, 7))
    old = ctx.state["pending"]
    tg.updates.append({"update_id": 1, "message": {"from": {"id": 42}, "text": "сделай короче",
                                                    "reply_to_message": {"message_id": old["preview_msg_id"]}}})
    ctx = step(ctx, at(2, 7, 15))
    new = ctx.state["pending"]
    assert new["post"]["caption_ru"] == "сделай короче" and new["edits"] == 1 and new["id"] != old["id"]


def test_reject_and_new_topic(env):
    tg, calls = env
    ctx = step(make_ctx(), at(2, 7))
    tg.updates.append(button(1, "new", ctx.state["pending"]["id"]))
    ctx = step(ctx, at(2, 7, 15))
    assert calls["write"] == 2 and ctx.history["posts"][-1]["status"] == "replaced"
    tg.updates.append(button(2, "rej", ctx.state["pending"]["id"]))
    ctx = step(ctx, at(2, 7, 30))
    assert ctx.state["pending"] is None and ctx.history["posts"][-1]["status"] == "rejected"


def test_stale_button_is_ignored(env):
    tg, _ = env
    ctx = step(make_ctx(), at(2, 7))
    tg.updates.append(button(1, "pub", "old-id"))
    ctx = step(ctx, at(2, 7, 15))
    assert ctx.state["pending"]["status"] == "waiting"


def test_now_command_with_format(env):
    tg, _ = env
    tg.updates.append({"update_id": 5, "message": {"from": {"id": 42}, "text": "/now рилс про ЖД"}})
    ctx = step(make_ctx(), at(2, 15))
    assert ctx.state["pending"]["post"]["format"] == "reels"
    assert ctx.state["tg_offset"] == 6


def test_pause_stops_drafts(env):
    tg, calls = env
    tg.updates.append({"update_id": 1, "message": {"from": {"id": 42}, "text": "/pause"}})
    ctx = step(make_ctx(), at(2, 7))
    assert ctx.state["paused"] and calls["write"] == 0


def test_generation_error_is_reported_once(env, monkeypatch):
    tg, _ = env
    monkeypatch.setattr("bot.writer.write_post", lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("quota")))
    ctx = step(make_ctx(), at(2, 7))
    ctx = step(ctx, at(2, 7, 15))
    assert tg.texts().count("quota") == 1 and ctx.state["pending"] is None


def test_waits_for_github_pages(env, monkeypatch):
    tg, _ = env
    monkeypatch.setattr("bot.media_store.is_live", lambda url: False)
    ctx = step(make_ctx(), at(2, 7))
    tg.updates.append(button(1, "pub", ctx.state["pending"]["id"]))
    ctx = step(ctx, at(2, 7, 15))
    assert ctx.state["pending"]["status"] == "approved"  # postponed, not lost
    ctx = step(ctx, at(2, 8, 5))
    post = ctx.history["posts"][-1]
    assert post.get("tg_message_ids") and "ig_id" not in post and "GitHub Pages" in tg.texts()


def test_reactions_and_weekly_learning(env, monkeypatch):
    tg, _ = env
    posts = [{"id": f"p{i}", "published_at": (at(2, 9) - timedelta(days=i)).isoformat(), "topic": f"t{i}",
              "content_type": "customs_faq", "format": "photo", "ig_id": f"m{i}", "tg_message_ids": [i]} for i in range(1, 4)]
    tg.updates.append({"update_id": 1, "message_reaction_count": {"message_id": 2, "reactions": [{"total_count": 4}]}})
    monkeypatch.setattr("bot.llm.generate_json", lambda *a, **k: ({"posts_per_week": 6, "summary_ru": "Больше рилс"}, []))
    ctx = tick.process_updates(make_ctx(history={"posts": posts}), at(2, 10))
    ctx = tick.maybe_learn(ctx, at(2, 10))
    assert ctx.history["posts"][1]["metrics"]["tg_reactions"] == 4
    assert ctx.history["posts"][0]["metrics"]["ig"]["reach"] == 100
    assert ctx.strategy["posts_per_week"] == 6 and "Больше рилс" in tg.texts()
    assert storage.load("strategy_log", {"entries": []})["entries"]


def test_status_and_settings_commands(env):
    tg, _ = env
    tg.updates += [{"update_id": 1, "message": {"from": {"id": 42}, "text": "/status"}},
                   {"update_id": 2, "message": {"from": {"id": 42}, "text": "/freq 3"}},
                   {"update_id": 3, "message": {"from": {"id": 7}, "text": "/pause"}}]
    ctx = tick.process_updates(make_ctx(), at(2, 15))
    assert "Работаю" in tg.texts() and ctx.strategy["posts_per_week"] == 3 and not ctx.state["paused"]


def test_redo_rebuilds_same_type_and_format(env):
    tg, calls = env
    ctx = step(make_ctx(), at(2, 7))
    old = ctx.state["pending"]
    tg.updates.append({"update_id": 1, "message": {"from": {"id": 42}, "text": "/redo про ЖД"}})
    ctx = step(ctx, at(2, 7, 15))
    new = ctx.state["pending"]
    assert new["id"] != old["id"] and new["post"]["format"] == old["post"]["format"]
    assert "про ЖД" in new["post"]["topic"] and ctx.history["posts"][-1]["status"] == "replaced"
