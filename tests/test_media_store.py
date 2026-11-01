"""gh-pages media branch against a real local git remote."""
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bot import media_store


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    git(work, "config", "user.name", "t")
    git(work, "config", "user.email", "t@t")
    (work / "README").write_text("x")
    git(work, "add", ".")
    git(work, "commit", "-qm", "init")
    git(work, "push", "-q", "origin", "HEAD")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(media_store, "ROOT", work)
    monkeypatch.setattr(media_store, "MEDIA_DIR", work / ".media")
    return origin, work


def test_push_pull_and_cleanup_without_history_growth(repo, tmp_path):
    origin, work = repo
    tz = ZoneInfo("Asia/Tashkent")
    media_store.pull()  # no branch yet
    src = tmp_path / "feed-1.jpg"
    src.write_bytes(b"img")
    stored = media_store.store("20261020-0700-aaaa", [src])
    media_store.store("20261102-0700-bbbb", [src])
    assert media_store.public_url("https://u.github.io/r", stored[0]) == "https://u.github.io/r/20261020-0700-aaaa/feed-1.jpg"
    media_store.push()
    files = git(origin, "ls-tree", "-r", "--name-only", "gh-pages").split()
    assert {"20261020-0700-aaaa/feed-1.jpg", "20261102-0700-bbbb/feed-1.jpg", ".nojekyll"} <= set(files)

    # next run on a fresh checkout: pull restores files, cleanup drops the old folder
    import shutil
    shutil.rmtree(work / ".media")
    media_store.pull()
    assert (work / ".media" / "20261020-0700-aaaa" / "feed-1.jpg").exists()
    media_store.cleanup(datetime(2026, 11, 2, 9, tzinfo=tz), keep=set())
    media_store.push()
    files = git(origin, "ls-tree", "-r", "--name-only", "gh-pages").split()
    assert "20261020-0700-aaaa/feed-1.jpg" not in files and "20261102-0700-bbbb/feed-1.jpg" in files
    assert git(origin, "rev-list", "--count", "gh-pages") == "1"  # single commit, history never grows
