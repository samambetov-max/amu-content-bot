"""Fresh market news from RSS feeds, filtered to what matters for importers."""
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

log = logging.getLogger(__name__)
MAX_AGE = timedelta(days=7)
UA = {"User-Agent": "Mozilla/5.0 (amu-content-bot)"}


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_feed(xml_text: str) -> list[dict]:
    """RSS 2.0 and Atom items as {title, link, date}."""
    root = ET.fromstring(xml_text)
    items = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {child.tag.split("}")[-1]: child for child in node}
        title = (fields.get("title").text or "").strip() if "title" in fields else ""
        link_node = fields.get("link")
        link = ""
        if link_node is not None:
            link = (link_node.text or link_node.get("href") or "").strip()
        # Elements without children are falsy, so `or` would skip them: compare with None.
        date_node = next((fields[k] for k in ("pubDate", "published", "updated") if fields.get(k) is not None), None)
        items.append({"title": title, "link": link, "date": _parse_date(date_node.text if date_node is not None else None)})
    return items


def matches(title: str, keywords: list[str]) -> bool:
    low = title.lower()
    return any(k.lower() in low for k in keywords)


def fresh_headlines(brand: dict, now: datetime, limit: int = 25) -> list[str]:
    keywords = brand.get("news_keywords", [])
    found = []
    for url in brand.get("news_feeds", []):
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            items = parse_feed(r.text)
        except (requests.RequestException, ET.ParseError) as err:
            log.warning("feed %s skipped: %s", url, err)
            continue
        for it in items:
            if it["date"] and now - it["date"] > MAX_AGE:
                continue
            if it["title"] and matches(it["title"], keywords):
                found.append(f"{it['title']} ({it['link']})")
    seen = set()
    unique = [h for h in found if not (h in seen or seen.add(h))]
    return unique[:limit]
