#!/usr/bin/env python3
"""Ajoute un post manuel (texte + PNG local → WebP) dans trader_posts_romainroth.json."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from convert_trader_post_images_to_webp import _save_webp_from_path
from trader_post_slug import assign_slugs_to_posts

IMAGES_DIR = ROOT / "data" / "trader_post_images"
POSTS_JSON = ROOT / "data" / "trader_posts_romainroth.json"
TRADER_USERNAME = "RomainRoth"


def main() -> None:
    parser = argparse.ArgumentParser(description="Ajouter un post trader manuel")
    parser.add_argument("--png", required=True, help="Chemin PNG dans data/trader_post_images/")
    parser.add_argument("--message-file", required=True, help="Fichier texte UTF-8 du message")
    parser.add_argument("--slug", required=True, help="Slug URL (/posts/...)")
    parser.add_argument(
        "--created",
        default="2026-06-04T10:05:00.000Z",
        help="Horodatage ISO du post",
    )
    parser.add_argument(
        "--after-slug",
        default="",
        help="Insérer juste après ce slug (sinon en tête de liste)",
    )
    args = parser.parse_args()

    src_image = Path(args.png)
    if not src_image.is_absolute():
        src_image = ROOT / src_image
    message_file = Path(args.message_file)
    if not message_file.is_absolute():
        message_file = ROOT / message_file
    slug = args.slug.strip()

    if not src_image.is_file():
        raise SystemExit(f"Image manquante: {src_image}")
    if not message_file.is_file():
        raise SystemExit(f"Texte manquant: {message_file}")
    if not POSTS_JSON.is_file():
        raise SystemExit(f"JSON manquant: {POSTS_JSON}")

    message = message_file.read_text(encoding="utf-8").strip()
    if not message:
        raise SystemExit("Message vide")

    post_id = str(uuid.uuid4())
    webp_name = f"{post_id}.webp"
    webp_path = IMAGES_DIR / webp_name
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    if not _save_webp_from_path(src_image, webp_path):
        raise SystemExit(f"Échec conversion WebP: {src_image} → {webp_path}")

    data = json.loads(POSTS_JSON.read_text(encoding="utf-8"))
    posts = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(posts, list):
        raise SystemExit("Format JSON invalide")

    for p in posts:
        if not isinstance(p, dict):
            continue
        if p.get("slug") == slug or (p.get("message") or "").strip() == message:
            print(f"Post déjà présent (id={p.get('id')}, slug={p.get('slug')})")
            return

    new_post = {
        "id": post_id,
        "created": args.created,
        "message": message,
        "owner": TRADER_USERNAME,
        "image_remote_url": None,
        "image_file": webp_name,
        "image_url": f"/api/trader-post-image/{webp_name}",
        "slug": slug,
    }

    insert_at = 0
    if args.after_slug:
        for i, p in enumerate(posts):
            if isinstance(p, dict) and p.get("slug") == args.after_slug.strip():
                insert_at = i + 1
                break
    posts.insert(insert_at, new_post)
    assign_slugs_to_posts(posts)
    new_post["slug"] = slug

    payload = {
        "username": TRADER_USERNAME,
        "updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "count": len(posts),
        "posts": posts,
    }
    POSTS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"OK: post {post_id} inséré à l'index {insert_at} "
        f"({len(posts)} posts), {webp_name}, slug {slug}"
    )


if __name__ == "__main__":
    main()
