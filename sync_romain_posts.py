"""Fetch and store all posts for trader RomainRoth."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from etoro_client import get_user_feed_posts, get_user_profile

TRADER_USERNAME = "RomainRoth"
OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "trader_posts_romainroth.json"
IMAGES_DIR = Path(__file__).resolve().parent / "data" / "trader_post_images"


def _extract_user_id(profile: dict) -> str | None:
    user_id = (
        profile.get("gcid")
        or profile.get("UserID")
        or profile.get("userID")
        or profile.get("id")
        or profile.get("realCID")
        or profile.get("demoCID")
    )
    return str(user_id) if user_id is not None else None


def _normalize_post(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    owner = raw.get("owner") if isinstance(raw.get("owner"), dict) else {}
    raw_message = raw.get("message") or raw.get("text") or raw.get("content") or ""
    if isinstance(raw_message, dict):
        # Certains formats eToro renvoient un objet pour le texte.
        raw_message = (
            raw_message.get("text")
            or raw_message.get("value")
            or raw_message.get("message")
            or ""
        )
    elif isinstance(raw_message, list):
        raw_message = " ".join(str(x) for x in raw_message if x is not None)
    message = str(raw_message).strip()
    if not message:
        return None
    if message.startswith("@"):
        # Exclure les posts de réponse/mention commençant par @
        return None
    created = str(raw.get("created") or "")
    post_id = raw.get("id") or raw.get("postId") or raw.get("obsoleteId")
    if not post_id:
        post_id = f"{created}-{abs(hash(message))}"
    image_url = None
    attachments = raw.get("attachments") if isinstance(raw.get("attachments"), list) else []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        media = att.get("media") if isinstance(att.get("media"), dict) else {}
        image = media.get("image") if isinstance(media.get("image"), dict) else {}
        candidate = (
            image.get("url")
            or att.get("url")
            or att.get("imageUrl")
            or att.get("thumbnailUrl")
        )
        if isinstance(candidate, str) and candidate.strip():
            image_url = candidate.strip()
            break
    return {
        "id": str(post_id),
        "created": created,
        "message": message,
        "owner": owner.get("username") or owner.get("userName") or TRADER_USERNAME,
        "image_remote_url": image_url,
    }


def _download_post_image(url: str, post_id: str) -> str | None:
    """Télécharge l'image du post et retourne le nom de fichier local."""
    if not url:
        return None
    try:
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        parsed = urlparse(url)
        ext = Path(parsed.path).suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            ext = ".png"
        safe_id = "".join(ch for ch in str(post_id) if ch.isalnum() or ch in ("-", "_"))[:64] or "post"
        filename = f"{safe_id}{ext}"
        target = IMAGES_DIR / filename
        if target.exists():
            return filename
        r = requests.get(url, timeout=20)
        if r.status_code != 200 or not r.content:
            return None
        target.write_bytes(r.content)
        return filename
    except Exception:
        return None


def fetch_all_posts(username: str, take: int = 100, max_pages: int = 200) -> list[dict]:
    profile = get_user_profile(username)
    if not profile:
        raise RuntimeError(f"Trader introuvable: {username}")
    user_id = _extract_user_id(profile)
    if not user_id:
        raise RuntimeError(f"Impossible de trouver user_id pour: {username}")

    posts: list[dict] = []
    seen_ids: set[str] = set()
    page_size = min(max(take, 1), 100)
    offset = 0
    pages = 0

    while pages < max_pages:
        data = get_user_feed_posts(
            user_id=user_id,
            take=page_size,
            offset=offset,
            requester_user_id=user_id,
        )
        if not data:
            break
        discussions = data.get("discussions") or []
        if not discussions:
            break
        added_on_page = 0
        for item in discussions:
            raw_post = item.get("post") if isinstance(item.get("post"), dict) else None
            post = _normalize_post(raw_post or {})
            if not post:
                continue
            if post["id"] in seen_ids:
                continue
            seen_ids.add(post["id"])
            posts.append(post)
            added_on_page += 1
        if len(discussions) < page_size or added_on_page == 0:
            break
        offset += page_size
        pages += 1

    posts.sort(key=lambda x: x.get("created", ""), reverse=True)
    return posts


def save_posts(posts: list[dict], output_path: Path = OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    enriched: list[dict] = []
    for p in posts:
        item = dict(p)
        remote = str(item.get("image_remote_url") or "").strip()
        local_file = _download_post_image(remote, str(item.get("id") or ""))
        item["image_file"] = local_file
        item["image_url"] = f"/api/trader-post-image/{local_file}" if local_file else None
        enriched.append(item)
    payload = {
        "username": TRADER_USERNAME,
        "updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "count": len(enriched),
        "posts": enriched,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    posts = fetch_all_posts(TRADER_USERNAME)
    save_posts(posts, OUTPUT_PATH)
    print(f"{len(posts)} posts saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
