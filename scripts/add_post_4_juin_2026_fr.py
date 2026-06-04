#!/usr/bin/env python3
"""Ajoute le post manuel du 4 juin 2026 (FR) + image WebP locale."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trader_post_slug import assign_slugs_to_posts

IMAGES_DIR = ROOT / "data" / "trader_post_images"
POSTS_JSON = ROOT / "data" / "trader_posts_romainroth.json"
SRC_IMAGE = IMAGES_DIR / "4_juin_2026_fr.png"
MESSAGE_FILE = ROOT / "data" / "post_drafts" / "4_juin_2026_fr_message.txt"
TRADER_USERNAME = "RomainRoth"
SLUG = "ai-tina-fomo-ou-femo-4-juin-2026"


def _png_to_webp(src: Path, dest: Path) -> None:
    with Image.open(src) as img:
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")
        img.save(dest, format="WEBP", quality=85, method=6)


def main() -> None:
    if not SRC_IMAGE.is_file():
        raise SystemExit(f"Image manquante: {SRC_IMAGE}")
    if not MESSAGE_FILE.is_file():
        raise SystemExit(f"Texte manquant: {MESSAGE_FILE}")
    if not POSTS_JSON.is_file():
        raise SystemExit(f"JSON manquant: {POSTS_JSON}")

    message = MESSAGE_FILE.read_text(encoding="utf-8").strip()
    if not message:
        raise SystemExit("Message vide")

    post_id = str(uuid.uuid4())
    webp_name = f"{post_id}.webp"
    webp_path = IMAGES_DIR / webp_name
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    _png_to_webp(SRC_IMAGE, webp_path)

    data = json.loads(POSTS_JSON.read_text(encoding="utf-8"))
    posts = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(posts, list):
        raise SystemExit("Format JSON invalide")

    for p in posts:
        if not isinstance(p, dict):
            continue
        if p.get("slug") == SLUG or (p.get("message") or "").strip() == message:
            print(f"Post déjà présent (id={p.get('id')})")
            return

    new_post = {
        "id": post_id,
        "created": "2026-06-04T10:00:00.000Z",
        "message": message,
        "owner": TRADER_USERNAME,
        "image_remote_url": None,
        "image_file": webp_name,
        "image_url": f"/api/trader-post-image/{webp_name}",
    }
    posts.insert(0, new_post)
    assign_slugs_to_posts(posts)
    new_post["slug"] = SLUG

    payload = {
        "username": TRADER_USERNAME,
        "updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "count": len(posts),
        "posts": posts,
    }
    POSTS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: post {post_id} ajouté ({len(posts)} posts), image {webp_name}, slug {SLUG}")


if __name__ == "__main__":
    main()
