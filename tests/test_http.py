"""API wrappers against recorded-shape fake HTTP answers."""
from pathlib import Path

import pytest

from bot import instagram, llm, telegram
from bot.config import Secrets, load_brand

SECRETS = Secrets("g", "", "t", "42", "@chan", "ig", "tok", "https://u.github.io/r")


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.text = body, status, str(body)

    def json(self):
        return self.body


def test_gemini_falls_back_to_next_model_and_returns_sources(monkeypatch):
    seen = []

    def post(url, **kw):
        seen.append(url)
        if "first" in url:
            return Resp({"error": "quota"}, 429)
        assert kw["json"]["tools"] == [{"google_search": {}}]
        return Resp({"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]},
                                     "groundingMetadata": {"groundingChunks": [{"web": {"title": "lex.uz"}}]}}]})

    monkeypatch.setattr(llm.requests, "post", post)
    brand = {**load_brand(), "llm": {"provider": "gemini", "gemini_models": ["first", "second"]}}
    data, sources = llm.generate_json(SECRETS, brand, "sys", "prompt", search=True)
    assert data == {"ok": 1} and sources == ["lex.uz"] and len(seen) == 2


def test_gemini_all_models_fail(monkeypatch):
    monkeypatch.setattr(llm.requests, "post", lambda url, **kw: Resp({}, 500))
    brand = {**load_brand(), "llm": {"provider": "gemini"}}
    with pytest.raises(llm.LLMError, match="HTTP 500"):
        llm.generate(SECRETS, brand, "s", "p")


def test_anthropic_used_when_chosen(monkeypatch):
    monkeypatch.setattr(llm.requests, "post", lambda url, **kw: Resp(
        {"content": [{"type": "text", "text": '{"a": 2}'}]}) if "anthropic" in url else pytest.fail("wrong API"))
    brand = {**load_brand(), "llm": {"provider": "anthropic", "anthropic_model": "m"}}
    secrets = Secrets("", "key", "t", "42", "@c", "", "", "")
    assert llm.generate_json(secrets, brand, "s", "p")[0] == {"a": 2}


def test_long_caption_goes_in_a_second_message(monkeypatch, tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    calls = []

    def post(url, data=None, files=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        calls.append((method, data))
        result = [{"message_id": 1}, {"message_id": 2}] if method == "sendMediaGroup" else {"message_id": 3}
        return Resp({"ok": True, "result": result})

    monkeypatch.setattr(telegram.requests, "post", post)
    ids = telegram.send_post("t", "@c", "carousel", [img, img], "Ж" * 1500)
    assert [c[0] for c in calls] == ["sendMediaGroup", "sendMessage"] and ids == [1, 2, 3]
    calls.clear()
    telegram.send_post("t", "@c", "photo", [img], "short")
    assert calls[0][0] == "sendPhoto" and calls[0][1]["caption"] == "short"


def test_long_photo_post_uses_link_preview(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(telegram.requests, "post", lambda url, data=None, files=None, timeout=None:
                        calls.append(data) or Resp({"ok": True, "result": {"message_id": 9}}))
    assert telegram.send_post("t", "@c", "photo", [tmp_path / "x.jpg"], "Ж" * 1500, "https://u/x.jpg") == [9]
    assert '"show_above_text": true' in calls[0]["link_preview_options"]


def test_telegram_error_is_raised(monkeypatch):
    monkeypatch.setattr(telegram.requests, "post", lambda *a, **k: Resp({"ok": False, "description": "chat not found"}))
    with pytest.raises(telegram.TelegramError, match="chat not found"):
        telegram.send_text("t", "1", "hi")


def test_instagram_carousel_flow(monkeypatch):
    calls = []

    def request(method, url, params=None, timeout=None):
        path = url.split("/", 4)[-1]
        calls.append((method, path, params))
        if path.endswith("media_publish"):
            return Resp({"id": "M"})
        if method == "POST":
            return Resp({"id": f"C{len(calls)}"})
        if params.get("fields") == "permalink":
            return Resp({"permalink": "https://ig/p"})
        return Resp({"status_code": "FINISHED"})

    monkeypatch.setattr(instagram.requests, "request", request)
    res = instagram.publish("ig", "tok", "carousel", ["https://a/1.jpg", "https://a/2.jpg"], "cap")
    assert res == {"id": "M", "permalink": "https://ig/p"}
    carousel = [c for c in calls if c[2].get("media_type") == "CAROUSEL"][0]
    assert carousel[2]["children"].count(",") == 1 and carousel[2]["caption"] == "cap"


def test_instagram_failed_container(monkeypatch):
    monkeypatch.setattr(instagram.requests, "request", lambda m, u, params=None, timeout=None:
                        Resp({"id": "C"}) if m == "POST" else Resp({"status_code": "ERROR"}))
    with pytest.raises(instagram.InstagramError, match="ERROR"):
        instagram.publish("ig", "tok", "reels", ["https://a/r.mp4"], "cap")


class Done:
    def __init__(self, stdout, stderr=""):
        self.stdout, self.stderr = stdout, stderr


def test_claude_code_uses_subscription_token_and_search(monkeypatch):
    seen = {}

    def run(cmd, input=None, env=None, **kw):
        seen.update(cmd=cmd, input=input, env=env)
        return Done('{"is_error": false, "result": "Ответ: {\\"a\\": 1}"}')

    monkeypatch.setattr(llm.subprocess, "run", run)
    secrets = Secrets("", "", "t", "42", "@c", "", "", "", claude_oauth_token="sk-ant-oat")
    data, _ = llm.generate_json(secrets, load_brand(), "система", "задача", search=True)
    assert data == {"a": 1}
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat" and seen["input"] == "задача"
    assert seen["cmd"][seen["cmd"].index("--tools") + 1] == "WebSearch,WebFetch"


def test_claude_code_limit_falls_back_to_gemini(monkeypatch):
    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: Done('{"is_error": true, "result": "usage limit reached"}'))
    monkeypatch.setattr(llm.requests, "post", lambda url, **kw: Resp(
        {"candidates": [{"content": {"parts": [{"text": '{"b": 2}'}]}}]}))
    secrets = Secrets("g", "", "t", "42", "@c", "", "", "", claude_oauth_token="tok")
    assert llm.generate_json(secrets, load_brand(), "s", "p")[0] == {"b": 2}


def test_no_ai_configured():
    with pytest.raises(llm.LLMError, match="no AI key"):
        llm.generate(Secrets("", "", "t", "42", "@c", "", "", ""), {"llm": {"provider": "gemini"}}, "s", "p")
