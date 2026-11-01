"""Instagram Graph API (Business account linked to a Facebook Page): photo, carousel and reels publishing, insights."""
import logging
import os
import time

import requests

log = logging.getLogger(__name__)
VERSION = os.getenv("IG_GRAPH_VERSION", "v23.0")
BASE = f"https://graph.facebook.com/{VERSION}"
POLL_SECONDS = 10
POLL_LIMIT = 60  # 10 minutes for a reel to process


class InstagramError(RuntimeError):
    pass


def _call(method: str, path: str, token: str, **params) -> dict:
    try:
        r = requests.request(method, f"{BASE}/{path}", params={**params, "access_token": token}, timeout=120)
        body = r.json()
    except (requests.RequestException, ValueError) as err:
        raise InstagramError(f"{path}: {err}") from err
    if "error" in body:
        raise InstagramError(f"{path}: {body['error'].get('message')}")
    return body


def _wait_ready(container_id: str, token: str) -> None:
    for _ in range(POLL_LIMIT):
        status = _call("GET", container_id, token, fields="status_code,status").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise InstagramError(f"media container {container_id}: {status}")
        time.sleep(POLL_SECONDS)
    raise InstagramError(f"media container {container_id} still processing")


def publish(user_id: str, token: str, fmt: str, urls: list[str], caption: str) -> dict:
    """Returns {"id", "permalink"} of the published post."""
    if fmt == "reels":
        container = _call("POST", f"{user_id}/media", token, media_type="REELS", video_url=urls[0],
                          caption=caption, share_to_feed="true")["id"]
    elif fmt == "carousel" and len(urls) > 1:
        children = [_call("POST", f"{user_id}/media", token, image_url=u, is_carousel_item="true")["id"]
                    for u in urls[:10]]
        for child in children:
            _wait_ready(child, token)
        container = _call("POST", f"{user_id}/media", token, media_type="CAROUSEL",
                          children=",".join(children), caption=caption)["id"]
    else:
        container = _call("POST", f"{user_id}/media", token, image_url=urls[0], caption=caption)["id"]
    _wait_ready(container, token)
    media_id = _call("POST", f"{user_id}/media_publish", token, creation_id=container)["id"]
    permalink = _call("GET", media_id, token, fields="permalink").get("permalink", "")
    return {"id": media_id, "permalink": permalink}


def insights(media_id: str, token: str, fmt: str) -> dict:
    metrics = ["reach", "likes", "comments", "saved", "shares"] + (["views"] if fmt == "reels" else [])
    try:
        data = _call("GET", f"{media_id}/insights", token, metric=",".join(metrics)).get("data", [])
    except InstagramError as err:
        log.warning("insights for %s: %s", media_id, err)
        return {}
    return {m["name"]: (m.get("values") or [{}])[0].get("value", m.get("total_value", {}).get("value", 0))
            for m in data}
