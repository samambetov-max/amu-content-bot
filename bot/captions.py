"""Final post text for each platform: languages, call to action, hashtags, length limits."""
TG_MESSAGE_LIMIT = 4096
TG_CAPTION_LIMIT = 1024
IG_CAPTION_LIMIT = 2200
IG_MAX_HASHTAGS = 30
SEPARATOR = "➖➖➖"

CTA = {
    "ru": "📦 Рассчитаем доставку и растаможку — пишите или звоните",
    "kaa": "📦 Jetkizip beriwdi hám bajıxana tólemlerin esaplap beremiz — jazıń yamasa qońıraw etiń",
    "uz": "📦 Yetkazib berish va bojxona toʻlovlarini hisoblab beramiz — yozing yoki qoʻngʻiroq qiling",
}


def contacts_line(brand: dict, platform: str) -> str:
    c = brand["company"]["contacts"]
    parts = [f"📞 {c['phone']}"]
    if c.get("telegram_bot"):
        parts.append(f"🤖 {c['telegram_bot']}")
    if platform == "instagram" and c.get("telegram_channel"):
        parts.append(f"✈️ Telegram: {c['telegram_channel']}")
    if platform == "telegram" and c.get("instagram"):
        parts.append(f"📸 Instagram: {c['instagram']}")
    if c.get("site"):
        parts.append(f"🌐 {c['site']}")
    return "\n".join(parts)


def hashtags(post: dict, brand: dict) -> str:
    tags = []
    for t in brand.get("hashtags_base", []) + list(post.get("hashtags", [])):
        t = "#" + str(t).lstrip("#").replace(" ", "")
        if len(t) > 1 and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    return " ".join(tags[:IG_MAX_HASHTAGS])


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    for stop in ("\n\n", ". ", "\n"):
        pos = cut.rfind(stop)
        if pos > limit * 0.6:
            return cut[: pos + 1].rstrip()
    return cut.rstrip() + "…"


def build(post: dict, brand: dict, platform: str) -> str:
    langs = brand["languages"]
    limit = IG_CAPTION_LIMIT if platform == "instagram" else TG_MESSAGE_LIMIT
    tail = "\n\n".join(filter(None, [
        "\n".join(CTA.get(l, "") for l in langs if CTA.get(l)),
        contacts_line(brand, platform),
        hashtags(post, brand),
    ]))
    budget = (limit - len(tail) - 2 - (len(SEPARATOR) + 4) * (len(langs) - 1)) // len(langs)
    bodies = [_trim(str(post[f"caption_{l}"]).strip(), budget) for l in langs]
    return f"\n\n{SEPARATOR}\n\n".join(bodies) + "\n\n" + tail
