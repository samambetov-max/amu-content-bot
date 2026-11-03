"""Telegram Business loop: receives client messages, waits until the client finishes typing, answers like a person.

One process, long polling, everything in order: no races between receiving and replying.
"""
import logging
import random
import time
from datetime import datetime, timedelta
from typing import Callable

from bot.config import load_brand, tz as brand_tz
from bot.telegram import TelegramError, api

from . import brain, learning
from .db import BOT, CLIENT, DB, OWNER
from .settings import Env, company_facts, load_config, load_style

log = logging.getLogger(__name__)
UPDATES = ["message", "business_connection", "business_message", "edited_business_message"]
POLL_SECONDS = 25

HELP = """Я отвечаю вашим клиентам от вашего имени, пока вы заняты.

Как я учусь:
• Когда вы сами отвечаете клиенту, я запоминаю, как вы пишете, и не вмешиваюсь в этот чат {hours} ч.
• Каждые {every} ваших ответов я пересматриваю свои правила.
• Напишите мне сюда любое указание или факт — я сразу это учту. Например:
  «Сборный груз из Иу до Ташкента 18–22 дня, не обещай меньше»
  «Не называй цену за кг, сначала спроси вес и объём»

Команды:
/status — что со мной сейчас
/test — режим проверки: пишите мне как клиент, я отвечу как отвечал бы клиенту
/playbook — правила, которые я выучил
/learn — пересмотреть правила прямо сейчас
/forget — забыть выученные правила
/off — перестать отвечать клиентам, /on — снова отвечать
/resume — снова отвечать во всех чатах, где вы вмешались"""


def message_text(msg: dict) -> str:
    """Text of a message, with media described in words so the AI understands what the client sent."""
    text = msg.get("text") or msg.get("caption") or ""
    marks = []
    if msg.get("voice"):
        marks.append(f"[голосовое сообщение {msg['voice'].get('duration', 0)} с]")
    if msg.get("video_note"):
        marks.append("[видеосообщение-кружок]")
    if msg.get("photo"):
        marks.append("[фото]")
    if msg.get("video"):
        marks.append("[видео]")
    if msg.get("document"):
        marks.append(f"[файл {msg['document'].get('file_name', '')}]".replace(" ]", "]"))
    if msg.get("sticker"):
        marks.append(f"[стикер {msg['sticker'].get('emoji', '')}]".replace(" ]", "]"))
    if msg.get("location"):
        marks.append("[геолокация]")
    if msg.get("contact"):
        marks.append(f"[контакт {msg['contact'].get('phone_number', '')}]")
    return " ".join(marks + [text]).strip() or "[сообщение без текста]"


def typing_seconds(text: str) -> float:
    return max(1.5, min(9.0, len(text) * 0.05))


def in_quiet_hours(cfg: dict, now: datetime) -> bool:
    quiet = cfg.get("quiet_hours")
    if not quiet:
        return False
    start, end = int(quiet[0]), int(quiet[1])
    return (now.hour >= start or now.hour < end) if start > end else start <= now.hour < end


def quiet_end(cfg: dict, now: datetime) -> datetime:
    end_hour = int(cfg["quiet_hours"][1])
    end = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    return end if end > now else end + timedelta(days=1)


class ClientBot:
    def __init__(self, env: Env, db: DB, call: Callable | None = None, clock: Callable = time.time,
                 sleep: Callable = time.sleep, rng: random.Random | None = None,
                 decide: Callable = brain.decide_reply, load_cfg: Callable = load_config):
        self.env, self.db = env, db
        self.call = call or (lambda method, data=None: api(env.tg_token, method, data))
        self.clock, self.sleep, self.rng = clock, sleep, rng or random.Random()
        self.decide, self.load_cfg = decide, load_cfg
        self.tz = brand_tz(load_brand())

    # --- helpers -----------------------------------------------------------
    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), self.tz)

    def admin_ids(self) -> set[int]:
        ids = {c["owner_id"] for c in self.db.connections()}
        if self.env.admin_id:
            ids.add(self.env.admin_id)
        return ids

    def admin_chat(self) -> int | None:
        if self.env.admin_id:
            return self.env.admin_id
        conns = self.db.connections()
        return conns[0]["owner_id"] if conns else None

    def tell_admin(self, text: str) -> None:
        chat = self.admin_chat()
        if not chat:
            log.info("no admin to tell: %s", text)
            return
        try:
            self.call("sendMessage", {"chat_id": chat, "text": text, "link_preview_options": {"is_disabled": True}})
        except TelegramError as err:
            log.warning("could not message the owner (did they press Start in the bot?): %s", err)

    def enabled(self, cfg: dict) -> bool:
        return bool(cfg.get("enabled", True)) and bool(self.db.get("enabled", True))

    def connection(self, conn_id: str) -> dict | None:
        conn = self.db.connection(conn_id)
        if conn or not conn_id:
            return conn
        try:
            self.save_connection(self.call("getBusinessConnection", {"business_connection_id": conn_id}))
        except TelegramError as err:
            log.warning("unknown business connection %s: %s", conn_id, err)
        return self.db.connection(conn_id)

    def save_connection(self, bc: dict) -> None:
        rights = bc.get("rights") or {}
        can_reply = bool(rights.get("can_reply", bc.get("can_reply", False)))
        self.db.save_connection(bc["id"], bc["user"]["id"], can_reply, bool(bc.get("is_enabled", True)))

    # --- updates -----------------------------------------------------------
    def handle(self, update: dict) -> None:
        if "business_connection" in update:
            self.on_connection(update["business_connection"])
        elif "business_message" in update:
            self.on_business_message(update["business_message"])
        elif "edited_business_message" in update:
            msg = update["edited_business_message"]
            self.db.edit_message(msg["chat"]["id"], msg["message_id"], message_text(msg))
        elif "message" in update:
            self.on_direct_message(update["message"])

    def on_connection(self, bc: dict) -> None:
        self.save_connection(bc)
        conn = self.db.connection(bc["id"])
        if not conn["enabled"]:
            self.tell_admin("Я отключён от вашего аккаунта и больше не отвечаю клиентам.")
        elif not conn["can_reply"]:
            self.tell_admin("Я подключён, но без права отвечать. Включите «Отвечать на сообщения» "
                            "в Настройки → Telegram для бизнеса → Чат-боты.")
        else:
            self.tell_admin("Подключился ✅ Теперь отвечаю вашим клиентам. Напишите /help, чтобы узнать, как меня учить.")

    def on_business_message(self, msg: dict) -> None:
        conn = self.connection(msg.get("business_connection_id", ""))
        if not conn:
            return
        chat = msg["chat"]
        chat_id, tg_id, text = chat["id"], msg["message_id"], message_text(msg)
        sender = (msg.get("from") or {}).get("id")
        if self.db.has_message(chat_id, tg_id):
            return
        name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
        self.db.touch_chat(chat_id, conn["id"], name, chat.get("username", ""))
        now = self.clock()
        if sender == conn["owner_id"]:
            if msg.get("sender_business_bot"):
                self.db.add_message(chat_id, BOT, text, tg_id, ts=msg.get("date"))
                return
            # The owner answered by hand: learn from it and stay out of this chat for a while.
            self.db.add_message(chat_id, OWNER, text, tg_id, ts=msg.get("date"))
            hours = float(self.load_cfg().get("owner_takeover_hours", 6))
            self.db.update_chat(chat_id, paused_until=now + hours * 3600, due=None)
            return
        self.db.add_message(chat_id, CLIENT, text, tg_id, ts=msg.get("date"))
        self.db.update_chat(chat_id, last_client=now)
        self.schedule(chat_id)

    def schedule(self, chat_id: int, sandbox: bool = False) -> None:
        cfg = self.load_cfg()
        chat = self.db.chat(chat_id)
        now = self.clock()
        if not sandbox and (chat["paused_until"] > now or not self.enabled(cfg)):
            return
        if sandbox:
            due = now + 2
        else:
            low, high = (cfg.get("reply_delay_seconds") or [5, 40])[:2]
            due = now + float(cfg.get("debounce_seconds", 25)) + self.rng.uniform(float(low), float(high))
            when = datetime.fromtimestamp(due, self.tz)
            if in_quiet_hours(cfg, when):
                due = quiet_end(cfg, when).timestamp() + self.rng.uniform(5, 40) * 60
        self.db.update_chat(chat_id, due=due)

    # --- owner's chat with the bot -------------------------------------------
    def on_direct_message(self, msg: dict) -> None:
        sender = (msg.get("from") or {}).get("id")
        if msg["chat"].get("type") != "private" or sender not in self.admin_ids():
            return
        text = (msg.get("text") or "").strip()
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        cfg = self.load_cfg()
        reply = None
        if command in ("/start", "/help"):
            reply = HELP.format(hours=cfg.get("owner_takeover_hours", 6), every=cfg.get("learn_every_owner_messages", 15))
            if not self.db.connections():
                reply += ("\n\nЧтобы я начал отвечать клиентам: Настройки → Telegram для бизнеса → Чат-боты → "
                          "впишите моё @имя и разрешите отвечать на сообщения.")
        elif command == "/status":
            reply = self.status(cfg)
        elif command in ("/on", "/off"):
            self.db.set("enabled", command == "/on")
            reply = "Снова отвечаю клиентам." if command == "/on" else "Больше не отвечаю клиентам, пока не напишете /on."
        elif command == "/resume":
            self.db.resume_all()
            reply = "Снова отвечаю во всех чатах."
        elif command == "/playbook":
            reply = self.db.playbook() or "Пока ничего не выучил. Отвечайте клиентам сами или пишите мне указания."
        elif command == "/forget":
            self.db.save_playbook("", "reset by owner")
            reply = "Забыл все выученные правила. Стиль из config/clientbot_style.md остаётся."
        elif command == "/learn":
            try:
                changes = learning.reflect(self.env, cfg, self.db, self.tz)
                reply = f"Пересмотрел правила. {changes}" if changes else "Нового материала для обучения пока нет."
            except Exception as err:  # noqa: BLE001 - the owner should see any failure
                reply = f"Не получилось: {err}"
        elif command == "/test":
            on = not self.db.get("sandbox", False)
            self.db.set("sandbox", on)
            reply = ("Режим проверки включён. Пишите мне как клиент, на любом языке. /test — выключить."
                     if on else "Режим проверки выключен. Теперь всё, что вы пишете, я воспринимаю как указания.")
        elif command:
            reply = "Не знаю такой команды. /help — список."
        elif self.db.get("sandbox", False):
            self.sandbox_message(msg)
        elif text:
            try:
                changes = learning.teach(self.env, cfg, self.db, text)
                reply = f"Понял, запомнил. {changes}".strip()
            except Exception as err:  # noqa: BLE001
                reply = f"Не получилось запомнить: {err}"
        if reply:
            self.call("sendMessage", {"chat_id": msg["chat"]["id"], "text": reply})

    def sandbox_message(self, msg: dict) -> None:
        """The owner pretends to be a client. Stored under a negative id so it never mixes with real chats."""
        chat_id = -msg["chat"]["id"]
        self.db.touch_chat(chat_id, "", "Тестовый клиент", "")
        self.db.add_message(chat_id, CLIENT, message_text(msg), msg["message_id"], ts=msg.get("date"))
        self.schedule(chat_id, sandbox=True)

    def status(self, cfg: dict) -> str:
        conns = self.db.connections()
        conn = conns[0] if conns else None
        if not conn:
            link = "не подключён к аккаунту (Настройки → Telegram для бизнеса → Чат-боты)"
        elif not conn["enabled"]:
            link = "отключён от аккаунта"
        else:
            link = "подключён" + ("" if conn["can_reply"] else ", но без права отвечать")
        stats = self.db.chat_stats(self.clock())
        return "\n".join([
            f"Аккаунт: {link}",
            f"Отвечаю клиентам: {'да' if self.enabled(cfg) else 'нет (/on)'}",
            f"Чатов: {stats['chats']}, из них вы ведёте сами сейчас: {stats['paused']}",
            f"Ваших ответов, на которых я учусь: {self.db.count(OWNER)}",
            f"Моих ответов клиентам: {self.db.count(BOT)}",
            f"Версия выученных правил: {self.db.playbook_version()}",
            f"Режим проверки: {'включён' if self.db.get('sandbox', False) else 'выключен'}",
        ])

    # --- replying ------------------------------------------------------------
    def process_due(self) -> None:
        for chat in self.db.due_chats(self.clock()):
            try:
                self.reply(chat)
            except Exception:  # noqa: BLE001 - one broken chat must not stop the others
                log.exception("reply to %s failed", chat["chat_id"])
                self.db.update_chat(chat["chat_id"], due=None)

    def reply(self, chat: dict) -> None:
        chat_id = chat["chat_id"]
        sandbox = chat_id < 0
        self.db.update_chat(chat_id, due=None)
        cfg = self.load_cfg()
        conn = None if sandbox else self.db.connection(chat["connection_id"])
        if not sandbox:
            if not conn or not conn["enabled"] or not conn["can_reply"] or not self.enabled(cfg):
                return
            if chat["paused_until"] > self.clock():
                return
            if in_quiet_hours(cfg, self.now()):
                self.schedule(chat_id)
                return
        history = self.db.history(chat_id, int(cfg.get("history_messages", 40)))
        if not history or history[-1]["role"] != CLIENT:
            return  # someone already answered
        biz = None if sandbox else conn["id"]
        target = -chat_id if sandbox else chat_id
        if biz:
            self._try("readBusinessMessage", {"business_connection_id": biz, "chat_id": chat_id,
                                              "message_id": history[-1]["tg_id"]})
        system = brain.build_system(cfg, load_style(), company_facts(), self.db.playbook())
        try:
            answer = self.decide(self.env, cfg, system, chat, history, self.db.owner_examples(), self.now())
        except Exception as err:  # noqa: BLE001
            log.exception("AI failed for chat %s", chat_id)
            self.tell_admin(f"⚠️ Не смог ответить клиенту {self.client_label(chat)}: {err}\n"
                            f"Ответьте, пожалуйста, сами.")
            return
        for i, text in enumerate(answer.messages):
            self._try("sendChatAction", {"business_connection_id": biz, "chat_id": target, "action": "typing"})
            self.sleep(typing_seconds(text) + (self.rng.uniform(0.5, 2.0) if i else 0))
            sent = self.call("sendMessage", {"business_connection_id": biz, "chat_id": target, "text": text,
                                             "link_preview_options": {"is_disabled": True}})
            self.db.add_message(chat_id, BOT, text, sent.get("message_id"))
        self.db.update_chat(chat_id, last_bot=self.clock(), notes=answer.client_notes or chat.get("notes") or "")
        if answer.handoff:
            last = history[-1]["text"]
            self.tell_admin(f"🙋 Нужны вы: {self.client_label(chat)}\n"
                            f"Причина: {answer.handoff_reason or 'не уточнил'}\n"
                            f"Клиент написал: {last[:500]}")

    def client_label(self, chat: dict) -> str:
        if chat["chat_id"] < 0:
            return "тестовый клиент"
        if chat.get("username"):
            return f"{chat.get('name') or ''} @{chat['username']}".strip()
        return chat.get("name") or str(chat["chat_id"])

    def _try(self, method: str, data: dict) -> None:
        try:
            self.call(method, data)
        except TelegramError as err:
            log.info("%s skipped: %s", method, err)

    # --- learning ---------------------------------------------------------------
    def maybe_learn(self) -> None:
        cfg = self.load_cfg()
        if not learning.should_reflect(cfg, self.db):
            return
        try:
            changes = learning.reflect(self.env, cfg, self.db, self.tz)
        except Exception:  # noqa: BLE001
            log.exception("reflection failed")
            return
        if changes:
            self.tell_admin(f"📚 Я поучился на ваших последних ответах клиентам. {changes}\n/playbook — все правила.")

    # --- main loop ------------------------------------------------------------
    def poll_once(self) -> None:
        offset = self.db.get("offset", 0)
        next_due = self.db.next_due()
        wait = POLL_SECONDS if next_due is None else max(0, min(POLL_SECONDS, int(next_due - self.clock())))
        updates = self.call("getUpdates", {"offset": offset, "timeout": wait, "allowed_updates": UPDATES})
        for update in updates:
            try:
                self.handle(update)
            except Exception:  # noqa: BLE001
                log.exception("update %s failed", update.get("update_id"))
            self.db.set("offset", update["update_id"] + 1)
        self.process_due()
        self.maybe_learn()

    def run(self) -> None:
        log.info("client bot started")
        while True:
            try:
                self.poll_once()
            except TelegramError as err:
                log.warning("telegram: %s", err)
                self.sleep(5)
