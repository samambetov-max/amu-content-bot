"""Command line:
    python -m clientbot          run the bot (long polling, keeps running)
"""
import logging
import sys

from .db import DB
from .engine import ClientBot
from .settings import load_env


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    env = load_env()
    if not env.tg_token:
        sys.exit("CLIENT_BOT_TOKEN is not set: create a bot in @BotFather and enable Business Mode for it")
    if not (env.anthropic_key or env.claude_oauth_token or env.gemini_key):
        sys.exit("no AI key: set ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN or GEMINI_API_KEY")
    ClientBot(env, DB(env.data_dir / "clientbot.db")).run()


if __name__ == "__main__":
    main()
