"""Command line:
    python -m bot tick                          one scheduled run (GitHub Actions)
    python -m bot draft [--format reels] [--wish "..."]   local test: write and render a post into out/, publish nothing
    python -m bot sample                        render tests/fixtures/sample_post.json into out/sample (no API keys needed)
"""
import argparse
import json
import logging
import random
from datetime import datetime

from . import captions, render, storage, tick, video, writer
from .config import OUT_DIR, ROOT, load_brand, load_secrets, tz


def render_local(post: dict, brand: dict, folder: str) -> None:
    rng = random.Random()
    photo = render.pick_photo(post.get("photo_tag", ""), [], rng)
    out = OUT_DIR / folder
    if post["format"] == "reels":
        frames = render.render_slides(post, brand, out, "story", photo)
        video.make_reel(frames, out / "reel.mp4", rng)
    else:
        render.render_slides(post, brand, out, "feed", photo)
    for platform in ("telegram", "instagram"):
        (out / f"caption-{platform}.txt").write_text(captions.build(post, brand, platform), encoding="utf-8")
    (out / "post.json").write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Готово: {out}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="bot")
    parser.add_argument("command", choices=["tick", "draft", "sample"])
    parser.add_argument("--format", choices=["photo", "carousel", "reels"])
    parser.add_argument("--type", dest="content_type")
    parser.add_argument("--wish", default="")
    args = parser.parse_args()
    brand = load_brand()
    if args.command == "tick":
        tick.run()
    elif args.command == "sample":
        post = json.loads((ROOT / "tests" / "fixtures" / "sample_post.json").read_text(encoding="utf-8"))
        render_local({**post, "format": args.format or post["format"]}, brand, "sample")
    else:
        now = datetime.now(tz(brand))
        post = writer.write_post(load_secrets(), brand, storage.load("strategy", {}),
                                 storage.load("history", {"posts": []}), now, random.Random(),
                                 wish=args.wish, fmt=args.format, content_type=args.content_type)
        render_local(post, brand, f"draft-{now:%Y%m%d-%H%M%S}")


if __name__ == "__main__":
    main()
