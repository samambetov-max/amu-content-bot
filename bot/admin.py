"""Admin control over Telegram: buttons under drafts and text commands that retune the bot."""
from dataclasses import dataclass, field

FORMATS = {"фото": "photo", "photo": "photo", "карусель": "carousel", "carousel": "carousel",
           "рилс": "reels", "reels": "reels", "видео": "reels"}
MAX_RULES = 20

HELP = """Команды контент-бота AMU Logistics:

/status — что сейчас происходит, когда следующий пост
/now [формат] [пожелание] — сделать черновик сейчас. Например: /now рилс про сроки доставки ЖД
/pause — остановить публикации · /resume — продолжить
/freq 5 — постов в неделю
/hours 9 13 19 — часы публикаций (Ташкент)
/rule текст — постоянное правило для бота. Например: /rule не писать про авиадоставку
/rules — список правил · /unrule 2 — удалить правило №2
/strategy — текущая стратегия бота
/learn — проанализировать статистику и улучшить стратегию сейчас

Под каждым черновиком есть кнопки. Чтобы поправить текст, ответьте на сообщение с черновиком."""


@dataclass(frozen=True)
class Intent:
    kind: str  # button action (pub, edit, new, fmt, rej), cmd, rewrite, chat, reactions
    arg: str = ""
    draft_id: str = ""
    callback_id: str = ""
    message_id: int = 0
    count: int = 0
    extra: dict = field(default_factory=dict)


def parse_update(update: dict, admin_id: str, state: dict) -> Intent | None:
    if "message_reaction_count" in update:
        r = update["message_reaction_count"]
        total = sum(x.get("total_count", 0) for x in r.get("reactions", []))
        return Intent("reactions", message_id=r.get("message_id", 0), count=total)
    if "callback_query" in update:
        cq = update["callback_query"]
        if str(cq.get("from", {}).get("id")) != str(admin_id):
            return None
        action, _, draft_id = cq.get("data", "").partition(":")
        return Intent(action, draft_id=draft_id, callback_id=cq["id"],
                      message_id=cq.get("message", {}).get("message_id", 0))
    msg = update.get("message")
    if not msg or str(msg.get("from", {}).get("id")) != str(admin_id):
        return None
    text = (msg.get("text") or "").strip()
    if not text:
        return None
    if text.startswith("/"):
        name, _, arg = text[1:].partition(" ")
        return Intent("cmd", arg=arg.strip(), extra={"name": name.split("@")[0].lower()})
    pending = state.get("pending") or {}
    replied = (msg.get("reply_to_message") or {}).get("message_id")
    if pending and (replied == pending.get("preview_msg_id") or state.get("awaiting_edit") == pending.get("id")):
        return Intent("rewrite", arg=text, draft_id=pending["id"])
    return Intent("chat", arg=text)


def split_now_arg(arg: str) -> tuple[str | None, str]:
    """'/now рилс про ЖД' → ('reels', 'про ЖД')."""
    first, _, rest = arg.partition(" ")
    fmt = FORMATS.get(first.lower())
    return (fmt, rest.strip()) if fmt else (None, arg)


def apply_setting(name: str, arg: str, strategy: dict, state: dict) -> tuple[dict, dict, str]:
    """Commands that only change settings. Returns new strategy, new state and the reply."""
    if name == "pause":
        return strategy, {**state, "paused": True}, "⏸ Публикации остановлены. /resume — продолжить."
    if name == "resume":
        return strategy, {**state, "paused": False}, "▶️ Публикации продолжаются."
    if name == "freq":
        try:
            n = int(arg)
        except ValueError:
            return strategy, state, "Напишите число: /freq 5"
        if not 1 <= n <= 21:
            return strategy, state, "Можно от 1 до 21 поста в неделю."
        return {**strategy, "posts_per_week": n}, state, f"Теперь {n} постов в неделю. Бот может менять это после анализа; правило /rule закрепит частоту."
    if name == "hours":
        try:
            hours = sorted({int(h) for h in arg.replace(",", " ").split()})
        except ValueError:
            hours = []
        if not hours or any(not 6 <= h <= 22 for h in hours):
            return strategy, state, "Напишите часы от 6 до 22: /hours 9 13 19"
        return {**strategy, "best_hours": hours}, state, f"Часы публикаций: {', '.join(map(str, hours))}."
    if name == "rule":
        if not arg:
            return strategy, state, "Напишите правило после команды: /rule ..."
        rules = (strategy.get("admin_rules", []) + [arg[:300]])[-MAX_RULES:]
        return {**strategy, "admin_rules": rules}, state, f"✅ Правило №{len(rules)} сохранено. Бот будет соблюдать его всегда."
    if name == "rules":
        rules = strategy.get("admin_rules", [])
        return strategy, state, "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1)) or "Правил пока нет."
    if name == "unrule":
        rules = strategy.get("admin_rules", [])
        try:
            i = int(arg) - 1
            removed = rules[i]
        except (ValueError, IndexError):
            return strategy, state, "Укажите номер из /rules: /unrule 2"
        return {**strategy, "admin_rules": rules[:i] + rules[i + 1:]}, state, f"Удалено: {removed}"
    if name == "strategy":
        return strategy, state, describe_strategy(strategy)
    return strategy, state, HELP


def describe_strategy(s: dict) -> str:
    pct = lambda w: ", ".join(f"{k} {round(v * 100)}%" for k, v in sorted(w.items(), key=lambda x: -x[1]))
    days = "Пн Вт Ср Чт Пт Сб Вс".split()
    return "\n".join([
        f"📊 Стратегия (обновлена: {s.get('updated_at') or 'ещё не обновлялась'})",
        f"Постов в неделю: {s['posts_per_week']}",
        f"Часы: {', '.join(map(str, s['best_hours']))} · дни: {' '.join(days[d] for d in s['posting_days'])}",
        f"Форматы: {pct(s['format_weights'])}",
        f"Типы: {pct(s['type_weights'])}",
        "Стиль: " + ("; ".join(s.get("style_notes", [])) or "—"),
        "Выводы: " + ("; ".join(s.get("learnings", [])) or "—"),
        "Правила владельца: " + ("; ".join(s.get("admin_rules", [])) or "—"),
    ])
