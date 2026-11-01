import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bot import admin, captions, learn, llm, news, planner, video, writer
from bot.config import load_brand

TZ = ZoneInfo("Asia/Tashkent")
BRAND = {**load_brand(), "languages": ["ru", "kaa"], "card_languages": ["ru", "kaa"]}
STRATEGY = json.loads((Path(__file__).parent.parent / "data" / "strategy.json").read_text(encoding="utf-8"))
SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "sample_post.json").read_text(encoding="utf-8"))


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)  # 2 Nov 2026 is a Monday


# --- planner ---------------------------------------------------------------

def test_drafts_two_hours_before_a_posting_hour():
    ok, _ = planner.should_draft(at(2, 7), STRATEGY, {}, {"posts": []}, lead_hours=2)
    assert ok


def test_no_draft_outside_posting_hours():
    ok, reason = planner.should_draft(at(2, 8), STRATEGY, {}, {"posts": []}, lead_hours=2)
    assert not ok and "posting hour" in reason


def test_no_draft_on_sunday():
    ok, reason = planner.should_draft(at(8, 7), STRATEGY, {}, {"posts": []}, lead_hours=2)
    assert not ok and reason == "not a posting day"


def test_no_draft_while_paused_or_pending():
    assert not planner.should_draft(at(2, 7), STRATEGY, {"paused": True}, {"posts": []}, 2)[0]
    assert not planner.should_draft(at(2, 7), STRATEGY, {"pending": {"id": "x"}}, {"posts": []}, 2)[0]


def test_respects_gap_between_posts():
    history = {"posts": [{"published_at": at(2, 9).isoformat()}]}
    assert not planner.should_draft(at(2, 11), STRATEGY, {}, history, lead_hours=2)[0]  # 13:00, 4 h later
    assert planner.should_draft(at(3, 11), STRATEGY, {}, history, lead_hours=2)[0]  # next day


def test_publish_slot_is_on_the_hour():
    assert planner.publish_slot(at(2, 7, 14), 2) == at(2, 9)


def test_next_slot_waits_for_morning_and_skips_sunday():
    assert planner.next_slot(at(2, 1, 40), STRATEGY) == at(2, 9)
    assert planner.next_slot(at(2, 13), STRATEGY) == at(2, 13)
    assert planner.next_slot(at(7, 20), STRATEGY) == at(9, 9)  # Saturday evening -> Monday morning


def test_pick_weighted_follows_weights():
    rng = random.Random(7)
    picks = [planner.pick_weighted({"a": 0.9, "b": 0.1}, rng, explore=0) for _ in range(2000)]
    assert 0.85 < picks.count("a") / 2000 < 0.95


def test_pick_weighted_explores_zero_weight_options():
    rng = random.Random(3)
    picks = {planner.pick_weighted({"a": 1, "b": 0}, rng, explore=0.5) for _ in range(200)}
    assert picks == {"a", "b"}


def test_upcoming_holiday():
    assert "Навруз" in planner.upcoming_holiday(datetime(2027, 3, 20, 9, tzinfo=TZ), BRAND["holidays"])
    assert planner.upcoming_holiday(at(4, 9), BRAND["holidays"]) is None


# --- captions --------------------------------------------------------------

def test_caption_has_both_languages_contacts_and_hashtags():
    text = captions.build(SAMPLE, BRAND, "instagram")
    assert SAMPLE["caption_ru"] in text and SAMPLE["caption_kaa"] in text
    assert "+86 193 2064 5050" in text and "+998 33 150 88 88 (Узбекистан)" in text and "Telegram: @amulogistics" in text
    assert "#amulogistics" in text and "#пошлина" in text


def test_caption_fits_instagram_limit():
    long = {**SAMPLE, "caption_ru": "Слово. " * 600, "caption_kaa": "Sóz. " * 600}
    text = captions.build(long, BRAND, "instagram")
    assert len(text) <= captions.IG_CAPTION_LIMIT
    assert "#amulogistics" in text


def test_hashtags_deduplicated():
    tags = captions.hashtags({"hashtags": ["#Логистика", "логистика", "ВЭД"]}, BRAND).split()
    assert len(tags) == len({t.lower() for t in tags})
    assert "#ВЭД" in tags


# --- model answers -----------------------------------------------------------

def test_parse_json_tolerates_fences_and_chatter():
    assert llm.parse_json('Вот ответ:\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(llm.LLMError):
        llm.parse_json("нет json")


def test_validate_rejects_missing_language_and_trims_photo_slides():
    with pytest.raises(llm.LLMError):
        writer.validate({**SAMPLE, "caption_kaa": ""}, BRAND, "carousel")
    assert len(writer.validate(SAMPLE, BRAND, "photo")["slides"]) == 1
    assert len(SAMPLE["slides"]) == 5  # original untouched


def test_prompt_lists_recent_topics_and_owner_rules():
    strategy = {**STRATEGY, "admin_rules": ["не писать про авиа"]}
    history = {"posts": [{"topic": "Сроки ЖД доставки"}]}
    prompt = writer.build_prompt(BRAND, strategy, history, "customs_faq", "carousel", [], None, at(2, 9))
    assert "Сроки ЖД доставки" in prompt
    assert "не писать про авиа" in writer.system_prompt(BRAND, strategy)


# --- news --------------------------------------------------------------------

RSS = """<rss><channel>
<item><title>Изменены ставки таможенных пошлин</title><link>https://x/1</link><pubDate>Mon, 02 Nov 2026 08:00:00 +0500</pubDate></item>
<item><title>Футбол: итоги тура</title><link>https://x/2</link><pubDate>Mon, 02 Nov 2026 08:00:00 +0500</pubDate></item>
<item><title>Импорт из Китая вырос</title><link>https://x/3</link><pubDate>Mon, 05 Oct 2026 08:00:00 +0500</pubDate></item>
</channel></rss>"""


def test_feed_parsing_and_keyword_filter():
    items = news.parse_feed(RSS)
    assert [i["link"] for i in items] == ["https://x/1", "https://x/2", "https://x/3"]
    relevant = [i for i in items if news.matches(i["title"], BRAND["news_keywords"])]
    assert [i["link"] for i in relevant] == ["https://x/1", "https://x/3"]


def test_fresh_headlines_skips_old_and_offtopic(monkeypatch):
    class Resp:
        text = RSS
        def raise_for_status(self): pass
    monkeypatch.setattr(news.requests, "get", lambda *a, **k: Resp())
    got = news.fresh_headlines({**BRAND, "news_feeds": ["a"]}, at(2, 12))
    assert got == ["Изменены ставки таможенных пошлин (https://x/1)"]


# --- admin -------------------------------------------------------------------

def msg(text, user=42, reply_to=None):
    m = {"message": {"from": {"id": user}, "text": text}}
    if reply_to:
        m["message"]["reply_to_message"] = {"message_id": reply_to}
    return m


def test_only_admin_is_obeyed():
    assert admin.parse_update(msg("/pause", user=7), "42", {}) is None
    assert admin.parse_update(msg("/pause"), "42", {}).extra["name"] == "pause"


def test_reply_to_preview_is_a_rewrite():
    state = {"pending": {"id": "d1", "preview_msg_id": 99}}
    intent = admin.parse_update(msg("короче", reply_to=99), "42", state)
    assert intent.kind == "rewrite" and intent.arg == "короче"


def test_button_and_reaction_updates():
    cb = {"callback_query": {"id": "c", "from": {"id": 42}, "data": "pub:d1", "message": {"message_id": 5}}}
    assert admin.parse_update(cb, "42", {}).kind == "pub"
    reac = {"message_reaction_count": {"message_id": 10, "reactions": [{"total_count": 3}, {"total_count": 2}]}}
    assert admin.parse_update(reac, "42", {}).count == 5


def test_settings_commands():
    s, st, _ = admin.apply_setting("freq", "7", STRATEGY, {})
    assert s["posts_per_week"] == 7 and STRATEGY["posts_per_week"] == 5
    s, _, _ = admin.apply_setting("hours", "19, 9", STRATEGY, {})
    assert s["best_hours"] == [9, 19]
    s, _, _ = admin.apply_setting("rule", "без авиа", STRATEGY, {})
    s, _, reply = admin.apply_setting("unrule", "1", s, {})
    assert s["admin_rules"] == [] and "без авиа" in reply
    _, st, _ = admin.apply_setting("pause", "", STRATEGY, {"paused": False})
    assert st["paused"] is True
    _, _, reply = admin.apply_setting("freq", "abc", STRATEGY, {})
    assert "число" in reply


def test_now_argument_format():
    assert admin.split_now_arg("рилс про ЖД") == ("reels", "про ЖД")
    assert admin.split_now_arg("про пошлины") == (None, "про пошлины")


# --- learning ------------------------------------------------------------------

def test_sanitize_clamps_and_keeps_owner_rules():
    old = {**STRATEGY, "admin_rules": ["правило"]}
    proposal = {"posts_per_week": 50, "best_hours": [3, 10, 10, 18], "format_weights": {"photo": 0, "reels": 1, "x": 5},
                "admin_rules": [], "learnings": ["рилс лучше"] * 20}
    new = learn.sanitize(proposal, old, at(2, 9))
    assert new["posts_per_week"] == 14
    assert new["best_hours"] == [10, 18]
    assert set(new["format_weights"]) == {"photo", "carousel", "reels"}
    assert new["format_weights"]["photo"] > 0 and abs(sum(new["format_weights"].values()) - 1) < 0.01
    assert new["admin_rules"] == ["правило"]
    assert len(new["learnings"]) == 8


def test_score_rewards_saves_more_than_likes():
    likes = {"metrics": {"ig": {"reach": 100, "likes": 6}}}
    saves = {"metrics": {"ig": {"reach": 100, "saved": 2}}}
    assert learn.score(saves) == learn.score(likes)
    assert learn.score({"metrics": {"tg_reactions": 4}}) == 20


def test_learn_needs_enough_posts():
    assert learn.learn(None, BRAND, STRATEGY, {"posts": []}, at(2, 9)) is None


# --- video -------------------------------------------------------------------

def test_reel_command_chains_crossfades():
    cmd = video.build_command([Path(f"s{i}.jpg") for i in range(4)], Path("r.mp4"), None)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.count("xfade") == 3
    assert "anullsrc" in " ".join(cmd)
    assert video.total_seconds(4) == pytest.approx(16.5)


def test_first_post_is_welcome_then_strategy():
    rng = random.Random(1)
    assert writer.pick_type(STRATEGY, {"posts": [{"status": "rejected"}]}, at(2, 9), None, rng) == "welcome"
    published = {"posts": [{"published_at": at(1, 9).isoformat()}]}
    assert writer.pick_type(STRATEGY, published, at(2, 9), None, rng) in STRATEGY["type_weights"]
    assert writer.pick_type(STRATEGY, published, at(2, 9), "Навруз (завтра)", rng) == "holiday"


def test_welcome_prompt_ignores_earlier_drafts():
    history = {"posts": [{"topic": "Добро пожаловать в AMU Logistics", "status": "replaced"}]}
    prompt = writer.build_prompt(BRAND, STRATEGY, history, "welcome", "carousel", [], None, at(2, 9))
    assert "Добро пожаловать в AMU Logistics" not in prompt


def test_telegram_caption_is_clean():
    text = captions.build(SAMPLE, BRAND, "telegram")
    assert "#" not in text and "Instagram" not in text and "+86 193 2064 5050" in text
