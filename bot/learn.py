"""Self-improvement: collects post statistics and lets the model retune the strategy once a week."""
import logging
from datetime import datetime, timedelta

from . import instagram, llm, storage
from .config import Secrets

log = logging.getLogger(__name__)
METRICS_EVERY = timedelta(hours=20)
LEARN_EVERY = timedelta(days=7)
WINDOW = timedelta(days=45)
MIN_POSTS = 3
MIN_WEIGHT = 0.04  # never drop a format or type completely: the bot keeps testing it


def _due(last: str | None, every: timedelta, now: datetime) -> bool:
    return not last or now - datetime.fromisoformat(last) >= every


def collect_metrics(secrets: Secrets, history: dict, now: datetime) -> dict:
    posts = []
    for p in history.get("posts", []):
        recent = p.get("published_at") and now - datetime.fromisoformat(p["published_at"]) < WINDOW
        if recent and p.get("ig_id") and secrets.ig_token:
            ig = instagram.insights(p["ig_id"], secrets.ig_token, p["format"])
            if ig:
                p = {**p, "metrics": {**p.get("metrics", {}), "ig": ig}}
        posts.append(p)
    return {**history, "posts": posts}


def score(p: dict) -> float:
    """Engagement score: saves and shares mean the post was useful, so they weigh more."""
    ig = p.get("metrics", {}).get("ig", {})
    reach = max(ig.get("reach", 0), 1)
    actions = ig.get("likes", 0) + 3 * ig.get("saved", 0) + 3 * ig.get("shares", 0) + 2 * ig.get("comments", 0)
    tg = p.get("metrics", {}).get("tg_reactions", 0)
    return round(1000 * actions / reach + 5 * tg, 1) if ig else round(5 * tg, 1)


def _clean_weights(new: dict, old: dict) -> dict:
    merged = {k: float(new.get(k, v)) if isinstance(new.get(k, v), (int, float)) else v for k, v in old.items()}
    merged = {k: max(v, MIN_WEIGHT) for k, v in merged.items()}
    total = sum(merged.values())
    return {k: round(v / total, 3) for k, v in merged.items()}


def _clean_list(value, limit: int = 6) -> list[str]:
    return [str(x)[:200] for x in value][:limit] if isinstance(value, list) else []


def sanitize(proposal: dict, old: dict, now: datetime) -> dict:
    """The model proposes, the code keeps it within safe limits. Owner rules are never touched."""
    ppw = proposal.get("posts_per_week", old["posts_per_week"])
    hours = [h for h in proposal.get("best_hours", []) if isinstance(h, int) and 7 <= h <= 21]
    days = [d for d in proposal.get("posting_days", []) if isinstance(d, int) and 0 <= d <= 6]
    return {
        **old,
        "version": old.get("version", 1) + 1,
        "updated_at": now.isoformat(timespec="minutes"),
        "posts_per_week": min(max(int(ppw), 2), 14) if isinstance(ppw, (int, float)) else old["posts_per_week"],
        "best_hours": sorted(set(hours))[:5] or old["best_hours"],
        "posting_days": sorted(set(days)) or old["posting_days"],
        "format_weights": _clean_weights(proposal.get("format_weights", {}), old["format_weights"]),
        "type_weights": _clean_weights(proposal.get("type_weights", {}), old["type_weights"]),
        "style_notes": _clean_list(proposal.get("style_notes")) or old["style_notes"],
        "topics_to_try": _clean_list(proposal.get("topics_to_try"), 10),
        "topics_to_avoid": _clean_list(proposal.get("topics_to_avoid"), 10),
        "learnings": _clean_list(proposal.get("learnings"), 8),
        "admin_rules": old.get("admin_rules", []),
    }


def learn(secrets: Secrets, brand: dict, strategy: dict, history: dict, now: datetime,
          force: bool = False) -> tuple[dict, str] | None:
    posts = [p for p in history.get("posts", [])
             if p.get("published_at") and now - datetime.fromisoformat(p["published_at"]) < WINDOW]
    rejected = [p for p in history.get("posts", []) if p.get("status") == "rejected"][-10:]
    if len(posts) < MIN_POSTS and not force:
        return None
    rows = "\n".join(
        f"- {p['published_at'][:16]} {datetime.fromisoformat(p['published_at']).strftime('%a')} | {p['format']} | "
        f"{p['content_type']} | «{p['topic']}» | score {score(p)} | ig {p.get('metrics', {}).get('ig', {})} | "
        f"tg реакции {p.get('metrics', {}).get('tg_reactions', 0)} | правок {p.get('edits', 0)}"
        for p in posts)
    prompt = f"""Сегодня {now:%d.%m.%Y}. Ты анализируешь контент Telegram-канала и Instagram логистической компании
в Узбекистане. Стратегия сейчас:
{strategy}

Опубликованные посты за 45 дней (score — вовлечённость: сохранения и репосты весят больше лайков):
{rows or '- нет данных'}

Черновики, которые владелец отклонил (так делать не надо):
{chr(10).join('- ' + p.get('topic', '') for p in rejected) or '- нет'}

Найди, что работает (формат, тип, тема, день, час, стиль), и предложи улучшенную стратегию.
Оставляй 10–20% на эксперименты. Мало данных — меняй осторожно. Учитывай свежие тренды и законы Узбекистана
в сфере ВЭД и логистики (поищи), чтобы предложить темы.

Верни JSON с ключами: posts_per_week (2–14), best_hours (часы Ташкента 7–21), posting_days (0=Пн … 6=Вс),
format_weights {{photo, carousel, reels}}, type_weights {{{', '.join(strategy['type_weights'])}}},
style_notes (до 6 коротких правил стиля), topics_to_try (до 10), topics_to_avoid (до 10),
learnings (до 8 выводов из цифр), summary_ru (3–5 предложений для владельца: что поменял и почему)."""
    proposal, _ = llm.generate_json(secrets, brand, "Ты — аналитик SMM. Отвечай только JSON.", prompt, search=True)
    new = sanitize(proposal, strategy, now)
    log_entry = {"at": now.isoformat(timespec="minutes"), "summary": str(proposal.get("summary_ru", ""))[:1500],
                 "before": {k: strategy[k] for k in ("posts_per_week", "best_hours", "format_weights", "type_weights")},
                 "after": {k: new[k] for k in ("posts_per_week", "best_hours", "format_weights", "type_weights")}}
    journal = storage.load("strategy_log", {"entries": []})
    storage.save("strategy_log", {"entries": (journal["entries"] + [log_entry])[-52:]})
    return new, log_entry["summary"]


def metrics_due(state: dict, now: datetime) -> bool:
    return _due(state.get("last_metrics_at"), METRICS_EVERY, now)


def learn_due(state: dict, now: datetime) -> bool:
    return _due(state.get("last_learn_at"), LEARN_EVERY, now)
