"""Self-improvement: the bot rewrites its own playbook from how the owner answers and from the owner's instructions."""
import logging

from .brain import complete_json, format_transcript
from .db import BOT, CLIENT, DB, OWNER
from .settings import Env

log = logging.getLogger(__name__)
PLAYBOOK_LIMIT = 6000  # characters: the playbook goes into every prompt, it must stay short

SYSTEM = """Ты тренер по продажам и переписке. Ты улучшаешь памятку для помощника, который отвечает клиентам
логистической компании в Telegram от имени владельца на русском, узбекском, каракалпакском и казахском.
Памятка — короткий список конкретных правил: как владелец пишет (длина, приветствия, обращения, слова,
эмодзи, язык), что он говорит о ценах, сроках, услугах, как отвечает на возражения, как ведёт к сделке,
какие ошибки помощника надо исправить. Только то, что подтверждается данными. Без общих слов.
Пиши памятку по-русски, примеры фраз — на языке оригинала. Не больше 40 пунктов. Отвечай только JSON."""

REFLECT = """Текущая памятка:
{playbook}

=== Как владелец сам отвечал клиентам (новые примеры) ===
{examples}

=== Переписки, где отвечал помощник; если после него вмешался владелец — значит, помощник ошибся или не справился ===
{dialogs}

Обнови памятку: добавь новые правила, исправь неверные, убери лишние и повторы.
Верни JSON: {{"playbook": "полный новый текст памятки", "changes": "что поменял и почему, 1-3 предложения"}}"""

TEACH = """Текущая памятка:
{playbook}

Владелец написал помощнику указание или новую информацию:
«{instruction}»

Встрой это в памятку (указания владельца важнее всего остального, они заменяют противоречащие правила).
Верни JSON: {{"playbook": "полный новый текст памятки", "changes": "что поменял, одно предложение"}}"""


def _save(db: DB, data: dict, reason: str) -> str:
    text = str(data.get("playbook") or "").strip()
    if not text:
        raise ValueError("empty playbook from the model")
    db.save_playbook(text[:PLAYBOOK_LIMIT], reason)
    return str(data.get("changes") or "").strip()


def teach(env: Env, cfg: dict, db: DB, instruction: str) -> str:
    prompt = TEACH.format(playbook=db.playbook() or "Пока пусто.", instruction=instruction.strip())
    return _save(db, complete_json(env, cfg, SYSTEM, prompt), f"owner: {instruction[:200]}")


def _bot_dialogs(db: DB, since_id: int, tz, limit: int = 8) -> str:
    """Recent chats where the bot answered, with what happened after it."""
    chat_ids = []
    for m in db.messages_since(since_id, (BOT,)):
        if m["chat_id"] not in chat_ids:
            chat_ids.append(m["chat_id"])
    parts = [format_transcript(db.history(chat_id, 16), tz) for chat_id in chat_ids[-limit:]]
    return "\n\n---\n\n".join(parts) or "Нет."


def should_reflect(cfg: dict, db: DB) -> bool:
    since = db.get("learned_up_to", 0)
    new_owner = len(db.messages_since(since, (OWNER,)))
    return new_owner >= int(cfg.get("learn_every_owner_messages", 15))


def reflect(env: Env, cfg: dict, db: DB, tz) -> str:
    since = db.get("learned_up_to", 0)
    top = db.max_id()
    examples = [e for e in db.owner_examples() if e["id"] > since][:40]
    if not examples and not db.messages_since(since, (BOT, CLIENT)):
        db.set("learned_up_to", top)
        return ""
    text = "\n---\n".join(f"Клиент: {e['client']}\nВладелец: {e['owner']}" for e in examples) or "Нет."
    prompt = REFLECT.format(playbook=db.playbook() or "Пока пусто.", examples=text, dialogs=_bot_dialogs(db, since, tz))
    changes = _save(db, complete_json(env, cfg, SYSTEM, prompt), "reflection")
    db.set("learned_up_to", top)
    log.info("playbook updated: %s", changes)
    return changes
