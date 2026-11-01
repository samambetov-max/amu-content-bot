"""Paths, brand settings and secrets from the environment."""
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ASSETS_DIR = ROOT / "assets"
MEDIA_DIR = ROOT / ".media"  # checkout of the gh-pages branch that serves media to Instagram
OUT_DIR = ROOT / "out"


def load_brand(path: Path = ROOT / "config" / "brand.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class Secrets:
    gemini_key: str
    anthropic_key: str
    tg_token: str
    tg_admin_id: str
    tg_channel: str
    ig_user_id: str
    ig_token: str
    media_base_url: str
    claude_oauth_token: str = ""
    pexels_key: str = ""

    @property
    def instagram_ready(self) -> bool:
        return bool(self.ig_user_id and self.ig_token and self.media_base_url)


def _pages_url() -> str:
    """GitHub Pages address of this repository, e.g. https://user.github.io/repo."""
    explicit = os.getenv("MEDIA_BASE_URL", "").rstrip("/")
    if explicit:
        return explicit
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        return ""
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}"


def load_secrets() -> Secrets:
    env = os.environ.get
    return Secrets(
        gemini_key=env("GEMINI_API_KEY", ""),
        anthropic_key=env("ANTHROPIC_API_KEY", ""),
        tg_token=env("TG_BOT_TOKEN", ""),
        tg_admin_id=env("TG_ADMIN_ID", ""),
        tg_channel=env("TG_CHANNEL") or "@amulogistics",  # GitHub passes "" for a secret that is not set
        ig_user_id=env("IG_USER_ID", ""),
        ig_token=env("IG_ACCESS_TOKEN", ""),
        media_base_url=_pages_url(),
        claude_oauth_token=env("CLAUDE_CODE_OAUTH_TOKEN", ""),
        pexels_key=env("PEXELS_API_KEY", ""),
    )


def tz(brand: dict) -> ZoneInfo:
    return ZoneInfo(brand.get("timezone", "Asia/Tashkent"))
