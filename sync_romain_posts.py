"""Fetch and store the 10 latest posts for trader RomainRoth."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from etoro_client import get_user_feed_posts, get_user_profile

TRADER_USERNAME = "RomainRoth"
POSTS_LIMIT = 10
OUTPUT_PATH = Path(__file__).resolve().parent / "data" / "trader_posts_romainroth.json"


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
    created = str(raw.get("created") or "")
    post_id = raw.get("id") or raw.get("postId") or raw.get("obsoleteId")
    if not post_id:
        post_id = f"{created}-{abs(hash(message))}"
    return {
        "id": str(post_id),
        "created": created,
        "message": message,
        "owner": owner.get("username") or owner.get("userName") or TRADER_USERNAME,
    }


def fetch_latest_posts(username: str, limit: int = POSTS_LIMIT) -> list[dict]:
    profile = get_user_profile(username)
    if not profile:
        raise RuntimeError(f"Trader introuvable: {username}")
    user_id = _extract_user_id(profile)
    if not user_id:
        raise RuntimeError(f"Impossible de trouver user_id pour: {username}")

    data = get_user_feed_posts(user_id=user_id, take=min(max(limit, 1), 100), offset=0, requester_user_id=user_id)
    if not data:
        raise RuntimeError("Aucune reponse de l'API feed user eToro")

    discussions = data.get("discussions") or []
    posts: list[dict] = []
    seen_ids: set[str] = set()

    for item in discussions:
        raw_post = item.get("post") if isinstance(item.get("post"), dict) else None
        post = _normalize_post(raw_post or {})
        if not post:
            continue
        if post["id"] in seen_ids:
            continue
        seen_ids.add(post["id"])
        posts.append(post)
        if len(posts) >= limit:
            break

    posts.sort(key=lambda x: x.get("created", ""), reverse=True)
    return posts[:limit]


def save_posts(posts: list[dict], output_path: Path = OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "username": TRADER_USERNAME,
        "updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "count": len(posts),
        "posts": posts,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    posts = fetch_latest_posts(TRADER_USERNAME, POSTS_LIMIT)
    save_posts(posts, OUTPUT_PATH)
    print(f"{len(posts)} posts saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
