"""Client bot: business messages, debounce, owner takeover, sandbox, learning — with a fake Telegram and a fake AI."""
import random
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from clientbot import brain, engine, learning
from clientbot.brain import Reply
from clientbot.db import BOT, CLIENT, DB, OWNER
from clientbot.settings import Env

OWNER_ID, CLIENT_ID, CONN = 111, 222, "conn-1"
TZ = ZoneInfo("Asia/Tashkent")
NOON = datetime(2026, 11, 3, 12, 0, tzinfo=TZ).timestamp()
NIGHT = datetime(2026, 11, 3, 23, 30, tzinfo=TZ).timestamp()
CFG = {"enabled": True, "debounce_seconds": 20, "reply_delay_seconds": [0, 0], "owner_takeover_hours": 6,
       "quiet_hours": [23, 8], "history_messages": 40, "learn_every_owner_messages": 2, "owner": {"name": "Самат"}}


class FakeTelegram:
    def __init__(self):
        self.calls, self.updates, self.next_id = [], [], 1000

    def __call__(self, method, data=None):
        self.calls.append((method, data or {}))
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return out
        if method == "sendMessage":
            self.next_id += 1
            return {"message_id": self.next_id}
        return True

    def sent(self, chat_id=None):
        return [d for m, d in self.calls if m == "sendMessage" and (chat_id is None or d["chat_id"] == chat_id)]


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def setup(tmp_path):
    tg, clock, seen = FakeTelegram(), Clock(NOON), []

    def decide(env, cfg, system, chat, history, examples, now):
        seen.append({"system": system, "history": history, "examples": examples})
        return Reply(messages=["Assalomu alaykum! Yukingiz qancha og'irlikda?"], handoff=False, handoff_reason="",
                     client_notes="Xitoydan yuk", language="uz-latn")

    env = Env(tg_token="t", admin_id=None, data_dir=tmp_path, anthropic_key="", claude_oauth_token="", gemini_key="")
    bot = engine.ClientBot(env, DB(tmp_path / "db.sqlite"), call=tg, clock=clock, sleep=lambda s: None,
                           rng=random.Random(1), decide=decide, load_cfg=lambda: CFG)
    bot.handle({"business_connection": {"id": CONN, "user": {"id": OWNER_ID}, "is_enabled": True,
                                        "rights": {"can_reply": True}}})
    tg.calls.clear()
    return bot, tg, clock, seen


def client_msg(text, mid, sender=CLIENT_ID, **extra):
    return {"business_message": {"business_connection_id": CONN, "message_id": mid, "date": int(NOON),
                                 "chat": {"id": CLIENT_ID, "type": "private", "first_name": "Бахтияр", "username": "bax"},
                                 "from": {"id": sender}, "text": text, **extra}}


def test_waits_for_client_to_finish_then_replies_via_business_connection(setup):
    bot, tg, clock, seen = setup
    bot.handle(client_msg("Salom", 1))
    clock.t += 10
    bot.handle(client_msg("Xitoydan yuk olib kelish kerak", 2))
    clock.t += 10
    bot.process_due()
    assert tg.sent() == []  # client may still be typing
    clock.t += 15
    bot.process_due()
    sent = tg.sent(CLIENT_ID)
    assert len(sent) == 1 and sent[0]["business_connection_id"] == CONN
    assert [m["text"] for m in seen[0]["history"]] == ["Salom", "Xitoydan yuk olib kelish kerak"]
    assert ("sendChatAction", {"business_connection_id": CONN, "chat_id": CLIENT_ID, "action": "typing"}) in tg.calls
    assert bot.db.history(CLIENT_ID, 10)[-1]["role"] == BOT
    assert bot.db.chat(CLIENT_ID)["notes"] == "Xitoydan yuk"


def test_owner_reply_pauses_bot_and_becomes_a_learning_example(setup):
    bot, tg, clock, _ = setup
    bot.handle(client_msg("Narxi qancha?", 1))
    bot.handle(client_msg("Hozir hisoblab beraman aka", 2, sender=OWNER_ID))
    clock.t += 120
    bot.process_due()
    assert tg.sent(CLIENT_ID) == []
    bot.handle(client_msg("Rahmat", 3))
    clock.t += 120
    bot.process_due()
    assert tg.sent(CLIENT_ID) == []  # still the owner's chat
    examples = bot.db.owner_examples()
    assert examples[0]["client"] == "Narxi qancha?" and examples[0]["owner"] == "Hozir hisoblab beraman aka"
    clock.t += 7 * 3600
    bot.handle(client_msg("Salom, yana yuk bor", 4))
    clock.t += 60
    bot.process_due()
    assert len(tg.sent(CLIENT_ID)) == 1


def test_messages_sent_by_the_bot_itself_are_not_owner_replies(setup):
    bot, tg, clock, _ = setup
    bot.handle(client_msg("Salom", 1))
    bot.handle(client_msg("Assalomu alaykum", 2, sender=OWNER_ID, sender_business_bot={"id": 5}))
    assert bot.db.count(OWNER) == 0 and bot.db.chat(CLIENT_ID)["paused_until"] == 0


def test_night_messages_are_answered_in_the_morning(setup):
    bot, tg, clock, _ = setup
    clock.t = NIGHT
    bot.handle(client_msg("Сәлеметсіз бе", 1))
    due = datetime.fromtimestamp(bot.db.chat(CLIENT_ID)["due"], TZ)
    assert due.hour == 8 and due.day == 4


def test_handoff_notifies_owner(setup):
    bot, tg, clock, _ = setup
    bot.decide = lambda *a: Reply(["Hozir aniqlab yozaman"], True, "клиент просит скидку", "", "uz-latn")
    bot.handle(client_msg("Skidka bormi?", 1))
    clock.t += 60
    bot.process_due()
    to_owner = tg.sent(OWNER_ID)
    assert len(to_owner) == 1 and "скидку" in to_owner[0]["text"] and "@bax" in to_owner[0]["text"]


def test_ai_failure_tells_owner_and_does_not_reply(setup):
    bot, tg, clock, _ = setup

    def broken(*a):
        raise RuntimeError("limit")

    bot.decide = broken
    bot.handle(client_msg("Salom", 1))
    clock.t += 60
    bot.process_due()
    assert tg.sent(CLIENT_ID) == [] and "limit" in tg.sent(OWNER_ID)[0]["text"]


def test_off_command_stops_replies_and_strangers_are_ignored(setup):
    bot, tg, clock, _ = setup
    bot.handle({"message": {"message_id": 1, "chat": {"id": 999, "type": "private"}, "from": {"id": 999}, "text": "/off"}})
    assert tg.sent() == []
    bot.handle({"message": {"message_id": 2, "chat": {"id": OWNER_ID, "type": "private"}, "from": {"id": OWNER_ID},
                            "text": "/off"}})
    bot.handle(client_msg("Salom", 1))
    clock.t += 60
    bot.process_due()
    assert tg.sent(CLIENT_ID) == []


def test_sandbox_lets_owner_play_client_without_touching_learning(setup):
    bot, tg, clock, seen = setup

    def owner_says(text, mid):
        bot.handle({"message": {"message_id": mid, "chat": {"id": OWNER_ID, "type": "private"},
                                "from": {"id": OWNER_ID}, "text": text, "date": int(clock.t)}})

    owner_says("/test", 1)
    owner_says("Qalay, júk alıp keliw kerek", 2)
    clock.t += 5
    bot.process_due()
    replies = tg.sent(OWNER_ID)
    assert replies[-1]["text"].startswith("Assalomu") and replies[-1]["business_connection_id"] is None
    assert bot.db.messages_since(0, (CLIENT, BOT)) == []


def test_text_from_owner_teaches_the_playbook(setup, monkeypatch):
    bot, tg, clock, _ = setup
    monkeypatch.setattr(learning, "complete_json",
                        lambda env, cfg, system, prompt: {"playbook": "- Не называть цену за кг", "changes": "добавил"})
    bot.handle({"message": {"message_id": 1, "chat": {"id": OWNER_ID, "type": "private"}, "from": {"id": OWNER_ID},
                            "text": "Не называй цену за кг"}})
    assert bot.db.playbook() == "- Не называть цену за кг"
    assert "запомнил" in tg.sent(OWNER_ID)[-1]["text"]


def test_reflection_runs_after_enough_owner_replies(setup, monkeypatch):
    bot, tg, clock, _ = setup
    prompts = []

    def fake(env, cfg, system, prompt):
        prompts.append(prompt)
        return {"playbook": "- Писать «aka»", "changes": "учёл обращение"}

    monkeypatch.setattr(learning, "complete_json", fake)
    bot.handle(client_msg("Salom", 1))
    bot.handle(client_msg("Va alaykum assalom aka", 2, sender=OWNER_ID))
    bot.maybe_learn()
    assert prompts == []
    bot.handle(client_msg("Narxi?", 3))
    bot.handle(client_msg("Og'irligi qancha aka?", 4, sender=OWNER_ID))
    bot.maybe_learn()
    assert len(prompts) == 1 and "Og'irligi qancha aka?" in prompts[0]
    assert "учёл обращение" in tg.sent(OWNER_ID)[-1]["text"]
    bot.maybe_learn()
    assert len(prompts) == 1


def test_media_is_described_in_words():
    assert engine.message_text({"voice": {"duration": 7}}) == "[голосовое сообщение 7 с]"
    assert engine.message_text({"photo": [{}], "caption": "shu tovar"}) == "[фото] shu tovar"


def test_prompt_has_style_facts_and_similar_examples():
    system = brain.build_system(CFG, "Пишу коротко", "Компания: AMU", "- правило")
    assert "Самат" in system and "Пишу коротко" in system and "AMU" in system and "- правило" in system
    examples = [{"client": "Rahmat", "owner": "Arzimaydi", "id": 3},
                {"client": "Konteyner narxi qancha Guanchjoudan", "owner": "Hajmini ayting", "id": 2},
                {"client": "Salom", "owner": "Salom aka", "id": 1}]
    picked = brain.pick_examples(examples, "konteyner narxi")
    assert picked[0]["owner"] == "Hajmini ayting"


def test_parse_reply_limits_messages():
    r = brain.parse_reply({"messages": ["a", " ", "b", "c", "d"], "handoff": 1})
    assert r.messages == ["a", "b", "c"] and r.handoff is True


def test_decide_reply_builds_full_prompt(monkeypatch):
    prompts = []
    monkeypatch.setattr(brain, "complete_json", lambda env, cfg, system, prompt: prompts.append(prompt) or
                        {"messages": ["Здравствуйте!"], "language": "ru"})
    history = [{"role": CLIENT, "text": "Сколько стоит контейнер?", "ts": NOON},
               {"role": OWNER, "text": "Откуда везёте?", "ts": NOON}]
    r = brain.decide_reply(None, CFG, "sys", {"name": "Бахтияр", "username": "bax", "notes": ""}, history,
                           [{"client": "контейнер", "owner": "Скиньте объём", "id": 1}], datetime.fromtimestamp(NOON, TZ))
    assert r.messages == ["Здравствуйте!"]
    assert "Бахтияр @bax" in prompts[0] and "Скиньте объём" in prompts[0] and "Владелец (сам): Откуда везёте?" in prompts[0]
