#!/bin/bash
# Starts the client bot on a Mac: installs what it needs once, keeps the Mac awake while it runs.
# Keys go into the .env file next to this script (see CLIENT_BOT.md).
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q requests==2.34.2 PyYAML==6.0.3 anthropic==2.4.0
fi
# caffeinate: the Mac does not fall asleep while the bot is running (the lid must stay open)
exec caffeinate -i .venv/bin/python -m clientbot
