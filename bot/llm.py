"""Text generation: Claude through Claude Code (Pro/Max subscription), Anthropic API or Google Gemini (free tier)."""
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .config import Secrets

log = logging.getLogger(__name__)
TIMEOUT = 180
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_CODE_TIMEOUT = 600


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMResult:
    text: str
    sources: list = field(default_factory=list)


def parse_json(text: str) -> dict:
    """Takes the first JSON object out of a model answer (tolerates ```json fences and chatter)."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"no JSON object in answer: {text[:200]!r}")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as err:
        raise LLMError(f"broken JSON from model: {err}") from err


def _gemini(secrets: Secrets, models: list, system: str, prompt: str, search: bool, as_json: bool) -> LLMResult:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.9},
    }
    # Search grounding and JSON mode cannot be combined: with search on, JSON is asked for in the prompt.
    if search:
        body["tools"] = [{"google_search": {}}]
    elif as_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    errors = []
    for model in models:
        try:
            r = requests.post(
                GEMINI_URL.format(model=model),
                headers={"x-goog-api-key": secrets.gemini_key},
                json=body,
                timeout=TIMEOUT,
            )
        except requests.RequestException as err:
            errors.append(f"{model}: {err}")
            continue
        if r.status_code != 200:
            errors.append(f"{model}: HTTP {r.status_code} {r.text[:300]}")
            continue
        cand = (r.json().get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        if not text:
            errors.append(f"{model}: empty answer ({cand.get('finishReason')})")
            continue
        chunks = cand.get("groundingMetadata", {}).get("groundingChunks", [])
        sources = [c["web"].get("title") or c["web"].get("uri") for c in chunks if c.get("web")]
        return LLMResult(text=text, sources=sources)
    raise LLMError("Gemini failed: " + " | ".join(errors))


def _anthropic(secrets: Secrets, model: str, system: str, prompt: str, search: bool) -> LLMResult:
    body = {
        "model": model,
        "max_tokens": 8000,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    if search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}]
    try:
        r = requests.post(
            ANTHROPIC_URL,
            headers={"x-api-key": secrets.anthropic_key, "anthropic-version": "2023-06-01"},
            json=body,
            timeout=TIMEOUT,
        )
    except requests.RequestException as err:
        raise LLMError(f"Anthropic request failed: {err}") from err
    if r.status_code != 200:
        raise LLMError(f"Anthropic HTTP {r.status_code}: {r.text[:300]}")
    blocks = r.json().get("content", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    sources = [
        res.get("title") or res.get("url")
        for b in blocks
        if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list)
        for res in b["content"]
    ]
    return LLMResult(text=text, sources=sources)


def _claude_code(secrets: Secrets, model: str, system: str, prompt: str, search: bool,
                 files: tuple[Path, ...] = ()) -> LLMResult:
    """Headless Claude Code: bills the Claude subscription through CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`).
    Files are copied next to it and can be opened with Read (Claude sees images this way)."""
    tools = ",".join((["WebSearch", "WebFetch"] if search else []) + (["Read"] if files else []))
    cmd = ["claude", "-p", "--output-format", "json", "--model", model, "--system-prompt", system,
           "--tools", tools, "--setting-sources", "project", "--strict-mcp-config", "--no-session-persistence"]
    if tools:
        cmd += ["--allowedTools", tools]
    env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": secrets.claude_oauth_token} if secrets.claude_oauth_token else None
    # An empty folder as cwd: no project files or settings leak into the prompt.
    with tempfile.TemporaryDirectory() as cwd:
        for f in files:
            shutil.copy2(f, Path(cwd) / f.name)
        try:
            res = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd, env=env,
                                 timeout=CLAUDE_CODE_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as err:
            raise LLMError(f"Claude Code failed to run: {err}") from err
    try:
        out = json.loads(res.stdout)
    except json.JSONDecodeError as err:
        raise LLMError(f"Claude Code: {(res.stderr or res.stdout)[:300]}") from err
    if out.get("is_error") or not out.get("result"):
        raise LLMError(f"Claude Code: {str(out.get('result'))[:300]}")
    return LLMResult(text=out["result"])


def _providers(secrets: Secrets, cfg: dict) -> list[str]:
    """Chosen provider first, then whatever else has a key, so a hit limit does not stop the bot."""
    available = {
        "claude_code": bool(secrets.claude_oauth_token) or cfg.get("provider") == "claude_code",
        "anthropic": bool(secrets.anthropic_key),
        "gemini": bool(secrets.gemini_key),
    }
    chosen = cfg.get("provider", "gemini")
    order = [chosen] + [p for p in ("claude_code", "anthropic", "gemini") if p != chosen]
    return [p for p in order if available.get(p)]


def generate(secrets: Secrets, brand: dict, system: str, prompt: str, search: bool = False, as_json: bool = True) -> LLMResult:
    cfg = brand.get("llm", {})
    providers = _providers(secrets, cfg)
    if not providers:
        raise LLMError("no AI key: set CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY or GEMINI_API_KEY")
    errors = []
    for provider in providers:
        try:
            if provider == "claude_code":
                return _claude_code(secrets, cfg.get("claude_code_model", "sonnet"), system, prompt, search)
            if provider == "anthropic":
                return _anthropic(secrets, cfg.get("anthropic_model", "claude-sonnet-5-5"), system, prompt, search)
            return _gemini(secrets, cfg.get("gemini_models", ["gemini-flash-latest"]), system, prompt, search, as_json)
        except LLMError as err:
            log.warning("%s failed: %s", provider, err)
            errors.append(f"{provider}: {err}")
    raise LLMError(" | ".join(errors))


def generate_json(secrets: Secrets, brand: dict, system: str, prompt: str, search: bool = False) -> tuple[dict, list]:
    """One retry on broken JSON: free models sometimes cut the answer off."""
    last_err = None
    for _ in range(2):
        res = generate(secrets, brand, system, prompt, search=search, as_json=True)
        try:
            return parse_json(res.text), res.sources
        except LLMError as err:
            log.warning("retrying after bad JSON: %s", err)
            last_err = err
    raise last_err


PHOTO_CHECK = """Ты фоторедактор каналов логистической компании: доставка грузов из Китая в Узбекистан, растаможка.
Решаешь, подходит ли стоковое фото обложкой к посту. Подходит, только если на фото видно то, о чём пост, или
логистику в целом: грузы, контейнеры, фуры, поезда, склады, порты, таможенный досмотр, деловые документы на груз,
расчёты. Не подходит: паспорта, путешествия, туризм, люди без связи с грузами, салоны, еда, животные, игрушки,
военное, всё, что зритель не свяжет с темой поста. Ответь только JSON: {"fit": true или false, "reason": "коротко"}"""


def photo_fits(secrets: Secrets, brand: dict, image: Path, topic: str) -> tuple[bool, str]:
    """Claude looks at the cover photo and says whether it matches the post. Raises LLMError when no model can see it."""
    cfg = brand.get("llm", {})
    prompt = f"Тема поста: {topic}\nФото: файл {image.name}. Открой его и оцени."
    if secrets.claude_oauth_token or cfg.get("provider") == "claude_code":
        try:
            res = _claude_code(secrets, cfg.get("claude_code_model", "sonnet"), PHOTO_CHECK, prompt, False, (image,))
            verdict = parse_json(res.text)
            return bool(verdict.get("fit")), str(verdict.get("reason", ""))
        except LLMError as err:
            log.warning("photo check via Claude Code failed: %s", err)
    if secrets.anthropic_key:
        return _anthropic_photo_check(secrets, cfg.get("anthropic_model", "claude-sonnet-5-5"), image, topic)
    raise LLMError("no model available to check the photo")


def _anthropic_photo_check(secrets: Secrets, model: str, image: Path, topic: str) -> tuple[bool, str]:
    data = base64.b64encode(image.read_bytes()).decode()
    body = {"model": model, "max_tokens": 300, "system": PHOTO_CHECK, "messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
        {"type": "text", "text": f"Тема поста: {topic}"}]}]}
    try:
        r = requests.post(ANTHROPIC_URL, json=body, timeout=TIMEOUT,
                          headers={"x-api-key": secrets.anthropic_key, "anthropic-version": "2023-06-01"})
    except requests.RequestException as err:
        raise LLMError(f"Anthropic request failed: {err}") from err
    if r.status_code != 200:
        raise LLMError(f"Anthropic HTTP {r.status_code}: {r.text[:300]}")
    verdict = parse_json("".join(b.get("text", "") for b in r.json().get("content", [])))
    return bool(verdict.get("fit")), str(verdict.get("reason", ""))
