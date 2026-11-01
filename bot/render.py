"""Turns slide texts into branded JPEG images with headless Chrome."""
import json
import logging
import random
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .config import ASSETS_DIR

log = logging.getLogger(__name__)
TEMPLATE = Path(__file__).parent / "templates" / "slide.html"
SIZES = {"feed": (1080, 1350), "story": (1080, 1920)}  # Instagram 4:5 feed and 9:16 reels

LABELS = {
    "customs_faq": {"ru": "Вопрос — ответ", "kaa": "Soraw — juwap"},
    "law_update": {"ru": "Новое в законе", "kaa": "Nızamda jańalıq"},
    "useful_tips": {"ru": "Полезный совет", "kaa": "Paydalı másláhát"},
    "interesting_fact": {"ru": "Интересный факт", "kaa": "Qızıqlı fakt"},
    "news_hype": {"ru": "Новости", "kaa": "Jańalıqlar"},
    "company_service": {"ru": "AMU Logistics", "kaa": "AMU Logistics"},
    "holiday": {"ru": "Праздник", "kaa": "Bayram"},
}
SWIPE = {"ru": "Листайте", "kaa": "Aylandırıń", "uz": "Varaqlang"}


def pick_photo(tag: str, used: list[str], rng: random.Random) -> Path:
    photos = sorted((ASSETS_DIR / "photos").glob("*.jpg"))
    tagged = [p for p in photos if tag and tag in p.stem] or photos
    fresh = [p for p in tagged if p.name not in used[-8:]] or tagged
    return rng.choice(fresh)


def slide_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/", payload).replace("{{ASSETS}}", data["assets"])


def _launch(p):
    """System Chrome is preinstalled on GitHub runners and most Macs; otherwise download Chromium once."""
    try:
        return p.chromium.launch(channel="chrome")
    except PlaywrightError:
        log.info("system Chrome not found, installing Chromium")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        return p.chromium.launch()


def render_slides(post: dict, brand: dict, out_dir: Path, size: str, photo: Path) -> list[Path]:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    width, height = SIZES[size]
    langs = brand.get("card_languages", brand["languages"])
    labels = list(dict.fromkeys(LABELS.get(post["content_type"], {}).get(l, "") for l in langs[:2]))
    slides = post["slides"]
    files = []
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": width, "height": height})
        for i, slide in enumerate(slides, 1):
            data = {
                "width": width, "height": height, "slide": slide, "langs": langs, "index": i, "total": len(slides),
                "labels": [l for l in labels if l], "swipe": SWIPE.get(langs[0], ""),
                "photo": photo.as_uri(), "assets": ASSETS_DIR.as_uri(), "contacts": brand["company"]["contacts"],
            }
            html_path = out_dir / f"slide-{i}.html"
            html_path.write_text(slide_html(data), encoding="utf-8")
            page.goto(html_path.as_uri(), wait_until="networkidle")
            scale = page.evaluate("window.fit()")
            if scale < 0.6:
                log.warning("slide %s text is long, shrunk to %.2f", i, scale)
            target = out_dir / f"{size}-{i}.jpg"
            page.screenshot(path=str(target), type="jpeg", quality=92)
            html_path.unlink()
            files.append(target)
        browser.close()
    return files
