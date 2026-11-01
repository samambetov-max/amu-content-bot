"""Chooses the topic and writes the post: captions in every language plus slide texts for the images."""
import random
from datetime import datetime

from . import llm, news, planner
from .config import Secrets

SLIDES = {"photo": "ровно 1 слайд (kind=cover)", "carousel": "5–7 слайдов: cover, затем point/fact/qa, последний cta",
          "reels": "4–6 слайдов: cover, затем point/fact, последний cta (каждый читается за 4 секунды)"}
LANG_NAMES = {"ru": "русский", "kaa": "каракалпакский (латиница, современная орфография: á, ó, ú, ı, ǵ, ń, sh, ch)",
              "uz": "узбекский (латиница)"}
NEEDS_SEARCH = {"law_update", "news_hype", "customs_faq", "holiday"}


def system_prompt(brand: dict, strategy: dict) -> str:
    c = brand["company"]
    langs = ", ".join(LANG_NAMES.get(l, l) for l in brand["languages"])
    rules = "\n".join(f"- {r}" for r in brand.get("tone_rules", []))
    style = "\n".join(f"- {s}" for s in strategy.get("style_notes", []) + strategy.get("learnings", []))
    admin = "\n".join(f"- {r}" for r in strategy.get("admin_rules", [])) or "- (нет)"
    return f"""Ты — SMM-редактор и таможенный брокер компании {c['name']} ({c['city']}, с {c['since']} года).
Ведёшь Telegram-канал и Instagram компании.

О компании: {c['description']}
Аудитория: {c['audience']}
Языки поста: {langs}. Текст на каждом языке — полноценный, естественный, не дословный перевод.

Правила тона:
{rules}

Что работает лучше всего (выводы из статистики):
{style}

ОБЯЗАТЕЛЬНЫЕ правила владельца (важнее всего остального):
{admin}

Отвечай ТОЛЬКО JSON-объектом без пояснений."""


def build_prompt(brand: dict, strategy: dict, history: dict, content_type: str, fmt: str,
                 headlines: list[str], holiday: str | None, now: datetime, wish: str = "") -> str:
    recent = [p.get("topic", "") for p in history.get("posts", [])[-30:]]
    langs = brand["languages"]
    card_langs = brand.get("card_languages", langs)
    # One language leaves room for fuller texts on the cards and in the caption
    caption_len, slide_len = ("600–1000", 260) if len(langs) == 1 else ("350–700", 170)
    caption_fields = ",\n".join(f'  "caption_{l}": "текст поста на языке {l}: {caption_len} символов, без хэштегов и контактов"' for l in langs)
    slide_fields = ", ".join(f'"title_{l}": "до 60 символов", "text_{l}": "до {slide_len if len(card_langs) == 1 else 170} символов"' for l in card_langs)
    news_block = "\n".join(f"- {h}" for h in headlines) or "- (лента пуста — найди свежие новости поиском)"
    return f"""Сегодня {now:%d.%m.%Y}, {now:%A}.
Тип поста: {content_type} — {brand['content_types'].get(content_type, '')}
Формат: {fmt} — {SLIDES[fmt]}
{f'Праздник: {holiday}' if holiday else ''}
{f'Пожелание владельца к этому посту: {wish}' if wish else ''}

Свежие новости из лент:
{news_block}

Темы, которые уже были (не повторяй и не перефразируй их):
{chr(10).join('- ' + t for t in recent) or '- (пока постов не было)'}
Попробовать: {', '.join(strategy.get('topics_to_try', [])) or '—'}
Избегать: {', '.join(strategy.get('topics_to_avoid', [])) or '—'}

Сам выбери самую полезную и интересную сейчас тему этого типа. Для законов и новостей опирайся на то,
что нашёл поиском за последние недели; укажи источники. Никаких выдуманных цифр, номеров постановлений и дат.
Сверяй каждую дату с сегодняшней: что уже вступило в силу — пиши в настоящем или прошедшем времени
(«с 1 октября действует»), будущее время — только для дат после сегодняшней.

Верни JSON:
{{
  "topic": "тема одной строкой по-русски",
{caption_fields},
  "hashtags": ["5–8 хэштегов по теме, без общих #amulogistics"],
  "photo_tag": "одно из: truck, container, warehouse, rail, crates, pallets, night, arrival",
  "photo_query": "2–4 английских слова для поиска живого стокового фото под тему (cargo truck highway, customs officer documents, container port sunset). Реалистичная сцена без текста и логотипов; без алкоголя и неуместной одежды",
  "slides": [{{"kind": "cover|point|fact|qa|cta", "big": "для fact — короткая цифра до 7 символов (12%, 30 дней, 2027), иначе пусто", {slide_fields}}}],
  "sources": ["ссылки или названия источников"],
  "needs_fact_check": true или false — есть ли в посте цифры/нормы, которые человек должен проверить
}}"""


def pick_type(strategy: dict, history: dict, now: datetime, holiday: str | None, rng: random.Random) -> str:
    """The very first post greets the audience; then holidays first, otherwise the strategy's weights."""
    if not any(p.get("published_at") for p in history.get("posts", [])):
        return "welcome"
    if holiday and not _greeted(history, now):
        return "holiday"
    return planner.pick_weighted(strategy["type_weights"], rng)


def write_post(secrets: Secrets, brand: dict, strategy: dict, history: dict, now: datetime,
               rng: random.Random, wish: str = "", fmt: str | None = None, content_type: str | None = None) -> dict:
    holiday = planner.upcoming_holiday(now, brand.get("holidays", {}))
    content_type = content_type or pick_type(strategy, history, now, holiday, rng)
    fmt = fmt or ("carousel" if content_type == "welcome" else planner.pick_weighted(strategy["format_weights"], rng))
    headlines = news.fresh_headlines(brand, now)
    prompt = build_prompt(brand, strategy, history, content_type, fmt, headlines, holiday, now, wish)
    post, sources = llm.generate_json(secrets, brand, system_prompt(brand, strategy), prompt,
                                      search=content_type in NEEDS_SEARCH)
    post = validate(post, brand, fmt)
    return {**post, "content_type": content_type, "format": fmt,
            "sources": list(dict.fromkeys((post.get("sources") or []) + sources))[:6]}


def rewrite_post(secrets: Secrets, brand: dict, strategy: dict, post: dict, instruction: str) -> dict:
    keep = {k: v for k, v in post.items() if k not in ("media", "content_type", "format")}
    prompt = f"""Вот черновик поста в JSON:
{keep}

Владелец просит изменить: {instruction}

Перепиши с учётом просьбы, сохрани формат ({post['format']}) и ту же структуру JSON. Верни только JSON."""
    new, _ = llm.generate_json(secrets, brand, system_prompt(brand, strategy), prompt)
    return {**post, **validate(new, brand, post["format"])}


def validate(post: dict, brand: dict, fmt: str) -> dict:
    """Checks the model answer and returns it with the slide count fitted to the format."""
    missing = [f"caption_{l}" for l in brand["languages"] if not str(post.get(f"caption_{l}", "")).strip()]
    if not post.get("slides"):
        missing.append("slides")
    if missing:
        raise llm.LLMError(f"model answer misses {missing}")
    return {**post, "slides": post["slides"][:1] if fmt == "photo" else post["slides"][:10]}


def _greeted(history: dict, now: datetime) -> bool:
    """A holiday post in the last 3 days already covers today's or tomorrow's holiday."""
    return any(p.get("content_type") == "holiday" and p.get("created_at")
               and (now - datetime.fromisoformat(p["created_at"])).days < 3
               for p in history.get("posts", []))
