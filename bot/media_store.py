"""Public media for Instagram: files live on the gh-pages branch, served by GitHub Pages.

Instagram downloads images and videos by URL, so every post's media must be public for a while.
The branch is rewritten as a single commit each time, so old videos never pile up in git history.
"""
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import requests

from .config import MEDIA_DIR, ROOT

log = logging.getLogger(__name__)
BRANCH = "gh-pages"
KEEP_DAYS = 10


def enabled() -> bool:
    return os.getenv("GITHUB_ACTIONS") == "true"


def _git(*args: str, env: dict | None = None) -> str:
    res = subprocess.run(["git", *args], cwd=ROOT, env={**os.environ, **(env or {})},
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout.strip()


def pull() -> None:
    MEDIA_DIR.mkdir(exist_ok=True)
    if not enabled():
        return
    try:
        _git("fetch", "--depth=1", "origin", BRANCH)
    except RuntimeError:
        log.info("no %s branch yet, starting empty", BRANCH)
        return
    index = {"GIT_INDEX_FILE": str(ROOT / ".git" / "media-index")}
    _git("--work-tree", str(MEDIA_DIR), "checkout", "FETCH_HEAD", "--", ".", env=index)


def cleanup(now: datetime, keep: set[str]) -> None:
    limit = now - timedelta(days=KEEP_DAYS)
    for folder in MEDIA_DIR.iterdir() if MEDIA_DIR.exists() else []:
        if not folder.is_dir() or folder.name in keep:
            continue
        try:
            created = datetime.strptime(folder.name[:13], "%Y%m%d-%H%M").replace(tzinfo=now.tzinfo)
        except ValueError:
            continue
        if created < limit:
            shutil.rmtree(folder)


def push() -> None:
    if not enabled():
        return
    (MEDIA_DIR / ".nojekyll").touch()
    env = {"GIT_INDEX_FILE": str(ROOT / ".git" / "media-index")}
    _git("--work-tree", str(MEDIA_DIR), "add", "-A", ".", env=env)
    tree = _git("write-tree", env=env)
    commit = _git("commit-tree", tree, "-m", "media for posts", env=env)
    _git("push", "-f", "origin", f"{commit}:refs/heads/{BRANCH}")


def store(post_id: str, files: list[Path]) -> list[Path]:
    target = MEDIA_DIR / post_id
    target.mkdir(parents=True, exist_ok=True)
    return [Path(shutil.copy2(f, target / f.name)) for f in files]


def public_url(base_url: str, path: Path) -> str:
    return f"{base_url}/{path.relative_to(MEDIA_DIR).as_posix()}"


def is_live(url: str) -> bool:
    """GitHub Pages needs a minute or two after a push before the file is served."""
    try:
        return requests.head(url, timeout=20, allow_redirects=True).status_code == 200
    except requests.RequestException:
        return False
