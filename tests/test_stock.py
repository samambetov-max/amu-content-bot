"""Stock photo search and the cover photo choice."""
import random

import requests

from bot import pipeline, stock
from bot.config import Secrets, load_brand

PEXELS = {"photos": [{"id": i, "width": 3000, "photographer": "A", "src": {"original": f"https://p/{i}.jpg"}} for i in (1, 2)]
          + [{"id": 3, "width": 600, "src": {"original": "https://p/small.jpg"}}]}
OPENVERSE = {"results": [{"id": "x", "width": 2000, "url": "https://o/x.jpg", "creator": "B"}]}


class Resp:
    def __init__(self, body=None, content=b"", ctype="application/json", status=200):
        self.body, self.content, self.headers, self.status_code = body, content, {"content-type": ctype}, status

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_pexels_first_skips_used_and_small(monkeypatch):
    monkeypatch.setattr(stock.requests, "get", lambda url, **kw: Resp(PEXELS if "pexels" in url else OPENVERSE))
    got = stock.find("truck", ["pexels:1"], random.Random(1), pexels_key="k")
    assert got.key == "pexels:2" and "w=1600" in got.url


def test_openverse_when_no_key_or_pexels_down(monkeypatch):
    def get(url, **kw):
        return Resp(status=500) if "pexels" in url else Resp(OPENVERSE)
    monkeypatch.setattr(stock.requests, "get", get)
    assert stock.find("truck", [], random.Random(1), pexels_key="k").key == "openverse:x"
    assert stock.find("truck", [], random.Random(1)).key == "openverse:x"


def test_nothing_found(monkeypatch):
    monkeypatch.setattr(stock.requests, "get", lambda url, **kw: Resp({"results": []}))
    assert stock.find("truck", [], random.Random(1)) is None


def test_download_rejects_non_images(monkeypatch, tmp_path):
    photo = stock.StockPhoto("pexels:1", "https://p/1.jpg", "A")
    monkeypatch.setattr(stock.requests, "get", lambda url, **kw: Resp(content=b"<html>", ctype="text/html"))
    assert stock.download(photo, tmp_path / "a.jpg") is None
    monkeypatch.setattr(stock.requests, "get", lambda url, **kw: Resp(content=b"img", ctype="image/jpeg"))
    assert stock.download(photo, tmp_path / "a.jpg").read_bytes() == b"img"


def ctx(share):
    brand = {**load_brand(), "stock_photo_share": share}
    return pipeline.Ctx(brand, Secrets("", "", "t", "1", "@c", "", "", "", pexels_key="k"), {}, {}, {"posts": []},
                        random.Random(1))


def test_choose_photo_uses_stock_or_own(monkeypatch, tmp_path):
    monkeypatch.setattr(stock.requests, "get", lambda url, **kw: Resp(PEXELS) if "api." in url
                        else Resp(content=b"img", ctype="image/jpeg"))
    post = {"photo_query": "cargo truck", "photo_tag": "truck"}
    path, key = pipeline.choose_photo(ctx(1), post, tmp_path)
    assert key.startswith("pexels:") and path.read_bytes() == b"img"
    path, key = pipeline.choose_photo(ctx(0), post, tmp_path)
    assert key.endswith(".jpg") and "truck" in key


def test_choose_photo_falls_back_to_own(monkeypatch, tmp_path):
    monkeypatch.setattr(stock.requests, "get", lambda url, **kw: Resp(status=503))
    _, key = pipeline.choose_photo(ctx(1), {"photo_query": "x"}, tmp_path)
    assert key.endswith(".jpg")


def test_pixabay_first_when_key(monkeypatch):
    hits = {"hits": [{"id": 7, "largeImageURL": "https://x/7.jpg", "user": "C"}]}
    monkeypatch.setattr(stock.requests, "get", lambda url, **kw: Resp(hits if "pixabay" in url else PEXELS))
    assert stock.find("truck", [], random.Random(1), pexels_key="k", pixabay_key="p").key == "pixabay:7"
