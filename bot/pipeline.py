"""Draft → preview for the admin → publication on every platform."""
import logging
import random
import secrets as pysecrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from . import captions, instagram, media_store, render, stock, telegram, video, writer
from .config import MEDIA_DIR, OUT_DIR, Secrets

log = logging.getLogger(__name__)
FORMAT_NAMES = {"photo": "фото", "carousel": "карусель", "reels": "рилс"}
MEDIA_WAIT = timedelta(minutes=45)  # how long to wait for GitHub Pages before posting to Telegram only


@dataclass(frozen=True)
class Ctx:
    brand: dict
    secrets: Secrets
    strategy: dict
    state: dict
    history: dict
    rng: random.Random

    def with_(self, **changes) -> "Ctx":
        return replace(self, **changes)


class NotReady(Exception):
    """Media is not public yet; try again on the next run."""


def new_id(now: datetime) -> str:
    return f"{now:%Y%m%d-%H%M}-{pysecrets.token_hex(2)}"


def choose_photo(ctx: Ctx, post: dict, work: Path) -> tuple[Path, str]:
    """Cover photo: a free stock photo for the topic (share set in brand.yaml) or one of the company's own photos.
    Returns the file and the key remembered in history so photos do not repeat."""
    used = [p.get("photo", "") for p in ctx.history.get("posts", [])]
    query = str(post.get("photo_query", "")).strip()
    if query and ctx.rng.random() < ctx.brand.get("stock_photo_share", 0.6):
        found = stock.find(query, used, ctx.rng, ctx.secrets.pexels_key)
        path = stock.download(found, work / "stock.jpg") if found else None
        if path:
            return path, found.key
    own = render.pick_photo(post.get("photo_tag", ""), used, ctx.rng)
    return own, own.name


def render_media(ctx: Ctx, post: dict, post_id: str) -> dict:
    work = OUT_DIR / post_id
    photo, photo_key = choose_photo(ctx, post, work)
    if post["format"] == "reels":
        frames = render.render_slides(post, ctx.brand, work, "story", photo)
        files = [video.make_reel(frames, work / "reel.mp4", ctx.rng)]
    else:
        files = render.render_slides(post, ctx.brand, work, "feed", photo)
    stored = media_store.store(post_id, files)
    return {"files": [f.relative_to(MEDIA_DIR).as_posix() for f in stored], "photo": photo_key}


def make_draft(ctx: Ctx, now: datetime, publish_at: datetime, wish: str = "",
               fmt: str | None = None, content_type: str | None = None) -> dict:
    post = writer.write_post(ctx.secrets, ctx.brand, ctx.strategy, ctx.history, now, ctx.rng,
                             wish=wish, fmt=fmt, content_type=content_type)
    post_id = new_id(now)
    return {"id": post_id, "created_at": now.isoformat(), "publish_at": publish_at.isoformat(),
            "status": "waiting", "post": post, "media": render_media(ctx, post, post_id), "edits": 0, "wish": wish}


def media_paths(pending: dict) -> list[Path]:
    return [MEDIA_DIR / f for f in pending["media"]["files"]]


def keyboard(draft_id: str) -> dict:
    return {"inline_keyboard": [
        [{"text": "✅ Опубликовать сейчас", "callback_data": f"pub:{draft_id}"}],
        [{"text": "✏️ Изменить текст", "callback_data": f"edit:{draft_id}"},
         {"text": "🔄 Другая тема", "callback_data": f"new:{draft_id}"}],
        [{"text": "🎞 Другой формат", "callback_data": f"fmt:{draft_id}"},
         {"text": "❌ Отклонить", "callback_data": f"rej:{draft_id}"}],
    ]}


def preview_text(ctx: Ctx, pending: dict) -> str:
    post = pending["post"]
    mode = ctx.brand.get("approval_mode", "auto_after_timeout")
    when = datetime.fromisoformat(pending["publish_at"])
    lines = [
        f"📝 Черновик {pending['id']}",
        f"Тема: {post.get('topic', '')}",
        f"Тип: {post['content_type']} · формат: {FORMAT_NAMES.get(post['format'], post['format'])}",
    ]
    if post.get("sources"):
        lines.append("Источники: " + "; ".join(map(str, post["sources"]))[:600])
    if post.get("needs_fact_check"):
        lines.append("⚠️ В посте есть цифры или нормы — проверьте их.")
    if mode == "manual":
        lines.append("Опубликую только после кнопки «Опубликовать».")
    else:
        lines.append(f"⏰ Если не ответите, опубликую сам в {when:%H:%M} ({when:%d.%m}).")
    lines.append("Чтобы поправить текст — ответьте на это сообщение, что изменить.")
    return "\n".join(lines)


def send_preview(ctx: Ctx, pending: dict) -> dict:
    s = ctx.secrets
    caption = captions.build(pending["post"], ctx.brand, "telegram")
    telegram.send_post(s.tg_token, s.tg_admin_id, pending["post"]["format"], media_paths(pending), caption)
    msg = telegram.send_text(s.tg_token, s.tg_admin_id, preview_text(ctx, pending), markup=keyboard(pending["id"]))
    return {**pending, "preview_msg_id": msg["message_id"]}


def _media_urls(ctx: Ctx, pending: dict) -> list[str]:
    base = ctx.secrets.media_base_url
    return [media_store.public_url(base, p) for p in media_paths(pending)] if base else []


def publish(ctx: Ctx, pending: dict, now: datetime, approved_by: str) -> tuple[dict, list[str]]:
    """Returns the history entry and a list of problems to report to the admin."""
    post, s, platforms = pending["post"], ctx.secrets, ctx.brand.get("platforms", {})
    urls = _media_urls(ctx, pending)
    want_ig = platforms.get("instagram", True) and s.instagram_ready
    live = bool(urls) and all(media_store.is_live(u) for u in urls)
    waited = now - datetime.fromisoformat(pending.get("approved_at") or pending["created_at"])
    if want_ig and not live and waited < MEDIA_WAIT:
        raise NotReady("media is not on GitHub Pages yet")

    entry = {
        "id": pending["id"], "created_at": pending["created_at"], "published_at": now.isoformat(),
        "topic": post.get("topic", ""), "content_type": post["content_type"], "format": post["format"],
        "photo": pending["media"]["photo"], "approved_by": approved_by, "edits": pending.get("edits", 0),
        "metrics": {},
    }
    problems = []
    if platforms.get("telegram", True):
        try:
            ids = telegram.send_post(s.tg_token, s.tg_channel, post["format"], media_paths(pending),
                                     captions.build(post, ctx.brand, "telegram"),
                                     first_image_url=urls[0] if live else None)
            entry["tg_message_ids"] = ids
        except telegram.TelegramError as err:
            problems.append(f"Telegram: {err}")
    if want_ig:
        if not live:
            problems.append("Instagram: файлы не появились на GitHub Pages — проверьте, что Pages включён")
        else:
            try:
                res = instagram.publish(s.ig_user_id, s.ig_token, post["format"], urls,
                                        captions.build(post, ctx.brand, "instagram"))
                entry["ig_id"], entry["ig_permalink"] = res["id"], res["permalink"]
            except instagram.InstagramError as err:
                problems.append(f"Instagram: {err}")
    if "tg_message_ids" not in entry and "ig_id" not in entry:
        entry["published_at"] = None
        entry["status"] = "failed"
    return entry, problems
