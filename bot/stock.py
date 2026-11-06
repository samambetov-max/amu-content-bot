"""Free stock photos for covers: Pixabay or Pexels (free keys, no attribution needed), Openverse public domain as a fallback.

Only sources whose license allows commercial use without credit: never images scraped from arbitrary sites.
"""
import logging
import random
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)
PEXELS_URL = "https://api.pexels.com/v1/search"
PIXABAY_URL = "https://pixabay.com/api/"
OPENVERSE_URL = "https://api.openverse.org/v1/images/"
MIN_WIDTH = 1080
UA = {"User-Agent": "amu-content-bot (github.com/samambetov-max/amu-content-bot)"}


@dataclass(frozen=True)
class StockPhoto:
    key: str  # "pexels:123": remembered in history so the same photo is not reused
    url: str
    credit: str


def _pexels(query: str, api_key: str) -> list[StockPhoto]:
    r = requests.get(PEXELS_URL, params={"query": query, "orientation": "portrait", "per_page": 30, "size": "large"},
                     headers={**UA, "Authorization": api_key}, timeout=30)
    r.raise_for_status()
    return [StockPhoto(f"pexels:{p['id']}", p["src"]["original"] + "?auto=compress&cs=tinysrgb&w=1600",
                       f"{p.get('photographer', '')} / Pexels")
            for p in r.json().get("photos", []) if p.get("width", 0) >= MIN_WIDTH]


# Tags that make a cover look fake or out of place for the audience
BANNED_TAGS = {"toy", "model", "miniature", "lego", "military", "army", "tank", "weapon", "war", "alcohol", "beer",
               "wine", "pork", "pig", "bikini", "cartoon", "illustration", "3d", "render", "barber", "barbershop",
               "hair", "hairdresser", "salon", "cosmetology", "makeup", "beauty", "fashion", "wedding", "bride",
               "passport", "visa", "travel", "traveler", "tourist", "tourism", "trip", "vacation", "immigration",
               "emigration", "citizenship", "identification"}
# A cover must show something from the trade. Short words match whole (plural too): "port" is not "portrait".
DOMAIN_STEMS = ("container", "cargo", "truck", "lorry", "freight", "logistic", "shipping", "shipment", "ship", "port",
                "harbor", "harbour", "warehouse", "pallet", "forklift", "crane", "train", "railway", "rail", "wagon", "locomotive",
                "delivery", "package", "parcel", "box", "cardboard", "customs", "border", "transport", "aircraft",
                "airplane", "plane", "airport", "document", "invoice", "paperwork", "contract", "calculator", "stamp",
                "export", "import", "trade", "storage", "highway", "goods")
# Words that fit almost any photo and prove nothing about the topic
GENERIC_WORDS = {"office", "business", "people", "person", "man", "men", "woman", "women", "work", "worker", "job",
                 "life", "lifestyle", "young", "city", "modern", "professional", "background", "concept"}


def _words(tags: str) -> set[str]:
    return {w for t in tags.lower().split(",") for w in t.split()}


def _is_trade(word: str) -> bool:
    return any(word.rstrip("s") == s or (len(s) >= 6 and word.startswith(s)) for s in DOMAIN_STEMS)


def trade_score(tags: str) -> int:
    return sum(map(_is_trade, _words(tags)))


def _clean(tags: str) -> bool:
    """No banned tag, and at least one tag from the logistics trade."""
    words = _words(tags)
    return not ({t.strip().lower() for t in tags.split(",")} & BANNED_TAGS or words & BANNED_TAGS) \
        and trade_score(tags) > 0


def relevance(query: str, tags: str) -> int:
    """How many query words the photo's tags contain. Words match by stem: "containers" fits "container",
    "shipping" fits "ship"."""
    words = [w for w in query.lower().split() if len(w) > 2 and w not in GENERIC_WORDS]
    tag_words = _words(tags) - GENERIC_WORDS
    return sum(any(t.startswith(w[:5]) or (len(t) >= 4 and w.startswith(t)) for t in tag_words) for w in words)


def _pixabay(query: str, api_key: str) -> list[StockPhoto]:
    """Editors' choice first (real photography, not hobby shots), only photos whose tags match the query."""
    need = min(2, len([w for w in query.lower().split() if len(w) > 2 and w not in GENERIC_WORDS])) or 1
    base = {"key": api_key, "q": query, "image_type": "photo", "safesearch": "true", "per_page": 100, "order": "popular"}
    ranked = []
    for extra in ({"editors_choice": "true"}, {}):
        r = requests.get(PIXABAY_URL, params={**base, **extra}, headers=UA, timeout=30)
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", []) if _clean(h.get("tags", "")) and relevance(query, h.get("tags", "")) >= need]
        # Most relevant first; portrait photos fit the 4:5 card without heavy cropping
        ranked = sorted(hits, key=lambda h: (-relevance(query, h["tags"]), -trade_score(h["tags"]), h.get("imageHeight", 0) < h.get("imageWidth", 0)))
        if len(ranked) >= 3:
            break
    # Pixabay forbids hotlinking: the photo is downloaded once and rendered into our own image.
    return [StockPhoto(f"pixabay:{h['id']}", h["largeImageURL"], f"{h.get('user', '')} / Pixabay") for h in ranked[:8]]


def _openverse(query: str) -> list[StockPhoto]:
    r = requests.get(OPENVERSE_URL, params={"q": query, "license": "cc0,pdm", "page_size": 30, "mature": "false"},
                     headers=UA, timeout=40)
    r.raise_for_status()
    return [StockPhoto(f"openverse:{p['id']}", p["url"], p.get("creator") or p.get("source", ""))
            for p in r.json().get("results", []) if (p.get("width") or 0) >= MIN_WIDTH
            and _clean(", ".join(t.get("name", "") for t in p.get("tags") or []) + ", " + (p.get("title") or ""))]


def find(query: str, used: list[str], rng: random.Random, pexels_key: str = "", pixabay_key: str = "") -> StockPhoto | None:
    """A fresh photo for the query, or None when no source answers."""
    found = candidates(query, used, rng, pexels_key, pixabay_key, limit=1)
    return found[0] if found else None


def candidates(query: str, used: list[str], rng: random.Random, pexels_key: str = "", pixabay_key: str = "",
               limit: int = 3) -> list[StockPhoto]:
    """A few fresh photos for the query in random order, from the first source that answers."""
    sources = ([lambda: _pixabay(query, pixabay_key)] if pixabay_key else []) \
        + ([lambda: _pexels(query, pexels_key)] if pexels_key else []) + [lambda: _openverse(query)]
    for source in sources:
        try:
            photos = [p for p in source() if p.key not in used]
        except (requests.RequestException, ValueError, KeyError) as err:
            log.warning("stock search failed: %s", err)
            continue
        if photos:
            return rng.sample(photos[:15], min(limit, len(photos[:15])))
    return []


def download(photo: StockPhoto, target: Path) -> Path | None:
    try:
        r = requests.get(photo.url, headers=UA, timeout=60)
        r.raise_for_status()
    except requests.RequestException as err:
        log.warning("stock download failed: %s", err)
        return None
    if not r.headers.get("content-type", "").startswith("image/"):
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(r.content)
    return target
