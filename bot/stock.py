"""Free stock photos for covers: Pexels (free key, no attribution needed) with Openverse public-domain as a fallback.

Only sources whose license allows commercial use without credit: never images scraped from arbitrary sites.
"""
import logging
import random
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)
PEXELS_URL = "https://api.pexels.com/v1/search"
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


def _openverse(query: str) -> list[StockPhoto]:
    r = requests.get(OPENVERSE_URL, params={"q": query, "license": "cc0,pdm", "page_size": 30, "mature": "false"},
                     headers=UA, timeout=40)
    r.raise_for_status()
    return [StockPhoto(f"openverse:{p['id']}", p["url"], p.get("creator") or p.get("source", ""))
            for p in r.json().get("results", []) if (p.get("width") or 0) >= MIN_WIDTH]


def find(query: str, used: list[str], rng: random.Random, pexels_key: str = "") -> StockPhoto | None:
    """A fresh photo for the query, or None when no source answers."""
    sources = ([lambda: _pexels(query, pexels_key)] if pexels_key else []) + [lambda: _openverse(query)]
    for source in sources:
        try:
            photos = [p for p in source() if p.key not in used]
        except (requests.RequestException, ValueError, KeyError) as err:
            log.warning("stock search failed: %s", err)
            continue
        if photos:
            return rng.choice(photos[:15])
    return None


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
