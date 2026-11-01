"""One run of the bot (every 15 minutes on GitHub Actions): read admin commands, publish, learn, draft."""
import logging
import random
import shutil
from datetime import datetime, timedelta

from . import admin, learn, media_store, pipeline, planner, storage, telegram, writer
from .config import MEDIA_DIR, load_brand, load_secrets, tz
from .pipeline import Ctx

log = logging.getLogger(__name__)
DEFAULT_STATE = {"tg_offset": 0, "paused": False, "pending": None, "awaiting_edit": None,
                 "last_metrics_at": None, "last_learn_at": None, "last_error_at": None,
                 "last_draft_at": None}
ERROR_NOTICE_EVERY = timedelta(hours=3)
MANUAL_EXPIRY = timedelta(hours=48)


def notify(ctx: Ctx, text: str, markup: dict | None = None) -> None:
    try:
        telegram.send_text(ctx.secrets.tg_token, ctx.secrets.tg_admin_id, text[:4000], markup=markup)
    except telegram.TelegramError as err:
        log.error("admin notice failed: %s", err)


def report_error(ctx: Ctx, now: datetime, what: str, err: Exception) -> Ctx:
    log.exception("%s failed", what)
    last = ctx.state.get("last_error_at")
    if not last or now - datetime.fromisoformat(last) > ERROR_NOTICE_EVERY:
        notify(ctx, f"⚠️ {what}: {str(err)[:500]}\nБот попробует снова при следующем запуске.")
        return ctx.with_(state={**ctx.state, "last_error_at": now.isoformat()})
    return ctx


def drop_media(draft: dict) -> None:
    shutil.rmtree(MEDIA_DIR / draft["id"], ignore_errors=True)


def record_rejected(ctx: Ctx, draft: dict, reason: str) -> Ctx:
    post = draft["post"]
    entry = {"id": draft["id"], "created_at": draft["created_at"], "published_at": None, "status": reason,
             "topic": post.get("topic", ""), "content_type": post["content_type"], "format": post["format"]}
    drop_media(draft)
    return ctx.with_(history={**ctx.history, "posts": ctx.history["posts"] + [entry]},
                     state={**ctx.state, "pending": None, "awaiting_edit": None})


def lead_time(brand: dict) -> timedelta:
    if brand.get("approval_mode") == "auto":
        return timedelta(0)
    return timedelta(hours=float(brand.get("approval_timeout_hours", 2)))


def create_draft(ctx: Ctx, now: datetime, publish_at: datetime, **kwargs) -> Ctx:
    draft = pipeline.make_draft(ctx, now, publish_at, **kwargs)
    draft = pipeline.send_preview(ctx, draft)
    return ctx.with_(state={**ctx.state, "pending": draft, "awaiting_edit": None, "last_draft_at": now.isoformat()})


def replace_draft(ctx: Ctx, now: datetime, old: dict, **kwargs) -> Ctx:
    """New draft in place of the old one, keeping its publishing time."""
    publish_at = max(datetime.fromisoformat(old["publish_at"]), now + timedelta(minutes=30))
    ctx = record_rejected(ctx, old, "replaced")
    return create_draft(ctx, now, publish_at, **kwargs)


def status_text(ctx: Ctx, now: datetime) -> str:
    p, s = ctx.state.get("pending"), ctx.strategy
    last = planner.last_post_time(ctx.history)
    lines = [
        "⏸ На паузе" if ctx.state.get("paused") else "▶️ Работаю",
        f"Последний пост: {last:%d.%m %H:%M}" if last else "Постов ещё не было",
        f"Черновик: {p['post'].get('topic')} → {datetime.fromisoformat(p['publish_at']):%d.%m %H:%M}" if p else "Черновика нет",
        f"Режим: {ctx.brand.get('approval_mode')} · {s['posts_per_week']} постов в неделю · часы {s['best_hours']}",
        f"Опубликовано всего: {sum(1 for x in ctx.history['posts'] if x.get('published_at'))}",
    ]
    return "\n".join(lines)


def handle_command(ctx: Ctx, intent: admin.Intent, now: datetime) -> Ctx:
    name = intent.extra["name"]
    if name == "now":
        if ctx.state.get("pending"):
            notify(ctx, "Уже есть черновик. Опубликуйте или отклоните его, затем /now.")
            return ctx
        fmt, wish = admin.split_now_arg(intent.arg)
        notify(ctx, "⏳ Готовлю черновик, это займёт 1–3 минуты…")
        return create_draft(ctx, now, now + lead_time(ctx.brand), wish=wish, fmt=fmt)
    if name == "status":
        notify(ctx, status_text(ctx, now))
        return ctx
    if name == "learn":
        return run_learning(ctx, now, force=True)
    strategy, state, reply = admin.apply_setting(name, intent.arg, ctx.strategy, ctx.state)
    notify(ctx, reply)
    return ctx.with_(strategy=strategy, state=state)


def handle_button(ctx: Ctx, intent: admin.Intent, now: datetime) -> Ctx:
    s, pending = ctx.secrets, ctx.state.get("pending")
    if not pending or pending["id"] != intent.draft_id:
        telegram.answer_callback(s.tg_token, intent.callback_id, "Этот черновик уже неактуален")
        return ctx
    telegram.clear_buttons(s.tg_token, s.tg_admin_id, intent.message_id)
    if intent.kind == "pub":
        telegram.answer_callback(s.tg_token, intent.callback_id, "Публикую")
        return ctx.with_(state={**ctx.state, "pending": {**pending, "status": "approved", "approved_at": now.isoformat()}})
    if intent.kind == "edit":
        telegram.answer_callback(s.tg_token, intent.callback_id)
        notify(ctx, "✏️ Напишите одним сообщением, что изменить в тексте.")
        return ctx.with_(state={**ctx.state, "awaiting_edit": pending["id"]})
    if intent.kind == "rej":
        telegram.answer_callback(s.tg_token, intent.callback_id, "Отклонено")
        notify(ctx, "❌ Черновик отклонён. Бот учтёт это при анализе. Следующий — в следующее окно публикации.")
        return record_rejected(ctx, pending, "rejected")
    telegram.answer_callback(s.tg_token, intent.callback_id, "Готовлю новый вариант…")
    post = pending["post"]
    if intent.kind == "fmt":
        others = [f for f in ctx.strategy["format_weights"] if f != post["format"]]
        return replace_draft(ctx, now, pending, fmt=ctx.rng.choice(others), content_type=post["content_type"],
                             wish=f"та же тема: {post.get('topic', '')}")
    return replace_draft(ctx, now, pending, wish=f"другая тема, не «{post.get('topic', '')}»")


def handle_rewrite(ctx: Ctx, intent: admin.Intent, now: datetime) -> Ctx:
    pending = ctx.state["pending"]
    notify(ctx, "⏳ Переписываю…")
    post = writer.rewrite_post(ctx.secrets, ctx.brand, ctx.strategy, pending["post"], intent.arg)
    new_id = pipeline.new_id(now)
    draft = {**pending, "id": new_id, "post": post, "media": pipeline.render_media(ctx, post, new_id),
             "edits": pending.get("edits", 0) + 1, "status": "waiting",
             "publish_at": max(datetime.fromisoformat(pending["publish_at"]), now + timedelta(minutes=30)).isoformat()}
    drop_media(pending)
    draft = pipeline.send_preview(ctx, draft)
    return ctx.with_(state={**ctx.state, "pending": draft, "awaiting_edit": None})


def add_reactions(ctx: Ctx, intent: admin.Intent) -> Ctx:
    posts = [{**p, "metrics": {**p.get("metrics", {}), "tg_reactions": intent.count}}
             if intent.message_id in p.get("tg_message_ids", []) else p for p in ctx.history["posts"]]
    return ctx.with_(history={**ctx.history, "posts": posts})


def process_updates(ctx: Ctx, now: datetime) -> Ctx:
    try:
        updates = telegram.get_updates(ctx.secrets.tg_token, ctx.state.get("tg_offset", 0))
    except telegram.TelegramError as err:
        log.error("getUpdates failed: %s", err)
        return ctx
    for upd in updates:
        ctx = ctx.with_(state={**ctx.state, "tg_offset": upd["update_id"] + 1})
        intent = admin.parse_update(upd, ctx.secrets.tg_admin_id, ctx.state)
        if not intent:
            continue
        try:
            if intent.kind == "reactions":
                ctx = add_reactions(ctx, intent)
            elif intent.kind == "cmd":
                ctx = handle_command(ctx, intent, now)
            elif intent.kind == "rewrite":
                ctx = handle_rewrite(ctx, intent, now)
            elif intent.kind == "chat":
                notify(ctx, "Чтобы поправить черновик, ответьте прямо на сообщение с ним. Все команды: /help")
            else:
                ctx = handle_button(ctx, intent, now)
        except Exception as err:  # one bad request must not block the others
            ctx = report_error(ctx, now, f"Команда «{intent.kind} {intent.arg[:40]}»", err)
    return ctx


def maybe_publish(ctx: Ctx, now: datetime) -> Ctx:
    p = ctx.state.get("pending")
    if not p:
        return ctx
    mode = ctx.brand.get("approval_mode", "auto_after_timeout")
    approved = p.get("status") == "approved"
    due = now >= datetime.fromisoformat(p["publish_at"])
    if mode == "manual" and not approved:
        if now - datetime.fromisoformat(p["created_at"]) > MANUAL_EXPIRY:
            notify(ctx, "Черновик пролежал 2 суток без ответа, удаляю его.")
            return record_rejected(ctx, p, "expired")
        return ctx
    if mode == "auto_after_timeout" and not approved and not due:
        return ctx
    if ctx.state.get("paused") and not approved:
        return ctx
    by = "admin" if approved else ("auto" if mode == "auto" else "timeout")
    try:
        entry, problems = pipeline.publish(ctx, p, now, by)
    except pipeline.NotReady as err:
        log.info("publish postponed: %s", err)
        return ctx
    if p.get("preview_msg_id"):
        telegram.clear_buttons(ctx.secrets.tg_token, ctx.secrets.tg_admin_id, p["preview_msg_id"])
    links = [f"Telegram: {ctx.secrets.tg_channel}"] if entry.get("tg_message_ids") else []
    if entry.get("ig_permalink"):
        links.append(f"Instagram: {entry['ig_permalink']}")
    head = "✅ Опубликовано" if entry.get("published_at") else "❌ Не удалось опубликовать"
    notify(ctx, "\n".join([f"{head}: {entry['topic']}"] + links + [f"⚠️ {x}" for x in problems]))
    return ctx.with_(history={**ctx.history, "posts": ctx.history["posts"] + [entry]},
                     state={**ctx.state, "pending": None, "awaiting_edit": None})


def run_learning(ctx: Ctx, now: datetime, force: bool = False) -> Ctx:
    history = learn.collect_metrics(ctx.secrets, ctx.history, now)
    ctx = ctx.with_(history=history, state={**ctx.state, "last_metrics_at": now.isoformat()})
    if not (force or learn.learn_due(ctx.state, now)):
        return ctx
    result = learn.learn(ctx.secrets, ctx.brand, ctx.strategy, history, now, force=force)
    state = {**ctx.state, "last_learn_at": now.isoformat()}
    if not result:
        if force:
            notify(ctx, "Пока мало опубликованных постов для выводов (нужно от 3).")
        return ctx.with_(state=state)
    strategy, summary = result
    notify(ctx, f"🧠 Обновил стратегию.\n{summary}\n\n{admin.describe_strategy(strategy)}")
    return ctx.with_(strategy=strategy, state=state)


def maybe_learn(ctx: Ctx, now: datetime) -> Ctx:
    if not learn.metrics_due(ctx.state, now):
        return ctx
    try:
        return run_learning(ctx, now)
    except Exception as err:
        return report_error(ctx, now, "Анализ статистики", err)


def maybe_draft(ctx: Ctx, now: datetime) -> Ctx:
    lead = lead_time(ctx.brand)
    ok, reason = planner.should_draft(now, ctx.strategy, ctx.state, ctx.history, lead.total_seconds() / 3600)
    log.info("draft: %s (%s)", ok, reason)
    if not ok:
        return ctx
    try:
        return create_draft(ctx, now, planner.publish_slot(now, lead.total_seconds() / 3600))
    except Exception as err:
        return report_error(ctx, now, "Подготовка поста", err)


def run() -> None:
    brand, secrets = load_brand(), load_secrets()
    if not (secrets.tg_token and secrets.tg_admin_id):
        raise SystemExit("TG_BOT_TOKEN and TG_ADMIN_ID are required")
    now = datetime.now(tz(brand))
    ctx = Ctx(brand=brand, secrets=secrets, strategy=storage.load("strategy", {}),
              state=storage.load("state", DEFAULT_STATE), history=storage.load("history", {"posts": []}),
              rng=random.Random())
    media_store.pull()
    try:
        ctx = process_updates(ctx, now)
        ctx = maybe_publish(ctx, now)
        ctx = maybe_learn(ctx, now)
        ctx = maybe_draft(ctx, now)
        ctx = maybe_publish(ctx, now)  # approval_mode=auto publishes the fresh draft in the same run
    finally:
        storage.save("strategy", ctx.strategy)
        storage.save("state", ctx.state)
        storage.save("history", ctx.history)
        pending = ctx.state.get("pending")
        media_store.cleanup(now, keep={pending["id"]} if pending else set())
        media_store.push()
