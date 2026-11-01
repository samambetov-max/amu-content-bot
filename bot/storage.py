"""JSON files in data/: the bot's memory between runs (committed back to the repository)."""
import json
import os
from pathlib import Path

from .config import DATA_DIR


def load(name: str, default: dict, data_dir: Path | None = None) -> dict:
    path = (data_dir or DATA_DIR) / f"{name}.json"
    if not path.exists():
        return dict(default)
    with open(path, encoding="utf-8") as f:
        return {**default, **json.load(f)}


def save(name: str, obj: dict, data_dir: Path | None = None) -> None:
    data_dir = data_dir or DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{name}.json"
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
