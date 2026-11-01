"""When to post and what kind of post: pure functions, easy to test."""
import random
from datetime import datetime, timedelta

EXPLORE = 0.15  # share of random picks, so the bot keeps testing formats it rates low
MIN_PPW, MAX_PPW = 2, 14
DRAFT_COOLDOWN = timedelta(hours=3)  # after a draft (even a rejected one) wait for the next window


def pick_weighted(weights: dict, rng: random.Random, explore: float = EXPLORE) -> str:
    keys = [k for k, w in weights.items() if w is not None and w >= 0]
    if not keys:
        raise ValueError("no weights to pick from")
    if rng.random() < explore:
        return rng.choice(keys)
    total = sum(weights[k] for k in keys)
    if total <= 0:
        return rng.choice(keys)
    point = rng.random() * total
    for k in keys:
        point -= weights[k]
        if point <= 0:
            return k
    return keys[-1]


def last_post_time(history: dict) -> datetime | None:
    times = [datetime.fromisoformat(p["published_at"]) for p in history.get("posts", []) if p.get("published_at")]
    return max(times) if times else None


def min_gap(strategy: dict) -> timedelta:
    ppw = min(max(strategy.get("posts_per_week", 5), MIN_PPW), MAX_PPW)
    return timedelta(hours=168 / ppw * 0.8)


def should_draft(now: datetime, strategy: dict, state: dict, history: dict, lead_hours: float) -> tuple[bool, str]:
    """A draft is made `lead_hours` before a preferred publishing hour, so the admin has time to look at it.
    `now` must be timezone-aware in the brand's timezone."""
    if state.get("paused"):
        return False, "paused"
    if state.get("pending"):
        return False, "draft waiting"
    last_draft = state.get("last_draft_at")
    if last_draft and now - datetime.fromisoformat(last_draft) < DRAFT_COOLDOWN:
        return False, "a draft was made recently"
    slot = now + timedelta(hours=lead_hours)
    if slot.weekday() not in strategy.get("posting_days", range(7)):
        return False, "not a posting day"
    if slot.hour not in strategy.get("best_hours", []):
        return False, f"{slot.hour}:00 is not a posting hour"
    last = last_post_time(history)
    if last and slot - last < min_gap(strategy):
        return False, "too soon after the last post"
    return True, f"slot {slot:%a %H:00}"


def publish_slot(now: datetime, lead_hours: float) -> datetime:
    slot = now + timedelta(hours=lead_hours)
    return slot.replace(minute=0, second=0, microsecond=0)


def upcoming_holiday(now: datetime, holidays: dict) -> str | None:
    for delta in (0, 1):
        day = now + timedelta(days=delta)
        name = holidays.get(f"{day:%m-%d}")
        if name:
            return f"{name} ({'сегодня' if delta == 0 else 'завтра'}, {day:%d.%m})"
    return None
