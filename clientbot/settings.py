"""Settings of the client bot: config/clientbot.yaml, the style guide, company facts and secrets."""
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from bot.config import ROOT, load_brand

CONFIG_PATH = ROOT / "config" / "clientbot.yaml"
STYLE_PATH = ROOT / "config" / "clientbot_style.md"


@dataclass(frozen=True)
class Env:
    tg_token: str
    admin_id: int | None  # who may give commands; falls back to the owner of the business connection
    data_dir: Path
    anthropic_key: str
    claude_oauth_token: str
    gemini_key: str


def load_env() -> Env:
    env = os.environ.get
    admin = env("CLIENT_BOT_ADMIN_ID") or env("TG_ADMIN_ID") or ""
    return Env(
        tg_token=env("CLIENT_BOT_TOKEN", ""),
        admin_id=int(admin) if admin.strip().lstrip("-").isdigit() else None,
        data_dir=Path(env("CLIENT_BOT_DATA") or ROOT / "clientdata"),
        anthropic_key=env("ANTHROPIC_API_KEY", ""),
        claude_oauth_token=env("CLAUDE_CODE_OAUTH_TOKEN", ""),
        gemini_key=env("GEMINI_API_KEY", ""),
    )


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_style(path: Path = STYLE_PATH) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def company_facts() -> str:
    """Company block of brand.yaml as plain text for the prompt."""
    company = load_brand().get("company", {})
    contacts = company.get("contacts", {})
    lines = [
        f"Компания: {company.get('name', '')}, работает с {company.get('since', '')} года, клиентов: {company.get('clients', '')}.",
        f"Что делаем: {' '.join(str(company.get('description', '')).split())}",
        f"Часы работы: {company.get('hours', '')}.",
    ]
    for key, label in (("phone", "Телефон"), ("phone_uz", "Телефон в Узбекистане"),
                       ("telegram_channel", "Telegram-канал"), ("instagram", "Instagram"), ("site", "Сайт")):
        if contacts.get(key):
            lines.append(f"{label}: {contacts[key]}")
    return "\n".join(lines)
