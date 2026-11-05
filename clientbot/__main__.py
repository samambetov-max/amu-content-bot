"""Command line:
    python -m clientbot          run the bot (long polling, keeps running)
"""
import logging
import os
import sys

from bot.config import ROOT

from .db import DB
from .engine import ClientBot
from .settings import load_env


def load_dotenv(path=ROOT / ".env") -> None:
    """KEY=value lines from .env, so on a Mac the bot starts with one command. Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    env = load_env()
    if not env.tg_token:
        sys.exit("CLIENT_BOT_TOKEN is not set: create a bot in @BotFather and enable Business Mode for it")
    if not (env.anthropic_key or env.claude_oauth_token or env.gemini_key):
        sys.exit("no AI key: set ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN or GEMINI_API_KEY")
    ClientBot(env, DB(env.data_dir / "clientbot.db")).run()


if __name__ == "__main__":
    main()
