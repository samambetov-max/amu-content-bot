"""Telegram Bot API: posting to the channel, previews and buttons for the admin, reading updates."""
import json
import logging
from contextlib import ExitStack
from pathlib import Path

import requests

from .captions import TG_CAPTION_LIMIT

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
UPDATES = ["message", "callback_query", "channel_post", "message_reaction_count"]


class TelegramError(RuntimeError):
    pass


def api(token: str, method: str, data: dict | None = None, files: dict | None = None) -> dict | list:
    payload = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
               for k, v in (data or {}).items() if v is not None}
    try:
        r = requests.post(API.format(token=token, method=method), data=payload, files=files, timeout=120)
        body = r.json()
    except (requests.RequestException, ValueError) as err:
        raise TelegramError(f"{method}: {err}") from err
    if not body.get("ok"):
        raise TelegramError(f"{method}: {body.get('description')}")
    return body["result"]


def get_updates(token: str, offset: int) -> list[dict]:
    return api(token, "getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": UPDATES})


def send_text(token: str, chat_id: str, text: str, markup: dict | None = None, reply_to: int | None = None,
              preview_url: str | None = None) -> dict:
    data = {"chat_id": chat_id, "text": text, "reply_markup": markup}
    if reply_to:
        data["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    if preview_url:
        data["link_preview_options"] = {"url": preview_url, "prefer_large_media": True, "show_above_text": True}
    else:
        data["link_preview_options"] = {"is_disabled": True}
    return api(token, "sendMessage", data)


def send_post(token: str, chat_id: str, fmt: str, files: list[Path], caption: str,
              first_image_url: str | None = None) -> list[int]:
    """Posts media and text as one unit. Text longer than a caption allows goes in a message right after."""
    fits = len(caption) <= TG_CAPTION_LIMIT
    if fmt == "photo" and not fits and first_image_url:
        # A link preview puts the picture above a long text in a single message.
        try:
            return [send_text(token, chat_id, caption, preview_url=first_image_url)["message_id"]]
        except TelegramError as err:
            log.warning("preview post failed, sending photo + text: %s", err)
    with ExitStack() as stack:
        handles = [stack.enter_context(open(f, "rb")) for f in files]
        if fmt == "reels":
            sent = [api(token, "sendVideo", {"chat_id": chat_id, "caption": caption if fits else None,
                                             "supports_streaming": True, "width": 1080, "height": 1920},
                        files={"video": handles[0]})]
        elif fmt == "photo":
            sent = [api(token, "sendPhoto", {"chat_id": chat_id, "caption": caption if fits else None},
                        files={"photo": handles[0]})]
        else:
            media = [{"type": "photo", "media": f"attach://f{i}"} for i in range(len(handles))]
            if fits:
                media[0]["caption"] = caption
            sent = api(token, "sendMediaGroup", {"chat_id": chat_id, "media": media},
                       files={f"f{i}": h for i, h in enumerate(handles)})
    ids = [m["message_id"] for m in sent]
    if not fits:
        ids.append(send_text(token, chat_id, caption)["message_id"])
    return ids


def answer_callback(token: str, callback_id: str, text: str = "") -> None:
    try:
        api(token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
    except TelegramError as err:
        # Callbacks older than a few minutes cannot be answered; the action itself still runs.
        log.info("callback answer skipped: %s", err)


def clear_buttons(token: str, chat_id: str, message_id: int) -> None:
    try:
        api(token, "editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                              "reply_markup": {"inline_keyboard": []}})
    except TelegramError as err:
        log.info("could not clear buttons: %s", err)
