"""Télécharge la photo de profil eToro dans images/trader_avatar.<ext> (une fois).

Nécessite les clés ETORO_API_KEY et ETORO_USER_KEY dans .env.

    python sync_trader_avatar.py
"""

from __future__ import annotations

from pathlib import Path

import requests

from etoro_client import get_user_profile

TRADER_USERNAME = "RomainRoth"
IMAGES_DIR = Path(__file__).resolve().parent / "images"


def _avatar_url_from_profile(profile: dict) -> str | None:
    avatars = profile.get("avatars") or profile.get("Avatars") or []
    if not avatars:
        return None
    last = avatars[-1] if isinstance(avatars[-1], dict) else {}
    first = avatars[0] if isinstance(avatars[0], dict) else {}
    url = (last.get("url") or last.get("Url")) or (first.get("url") or first.get("Url"))
    return url.strip() if isinstance(url, str) and url.strip() else None


def _ext_from_bytes(data: bytes) -> str:
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def main() -> None:
    profile = get_user_profile(TRADER_USERNAME)
    if not profile:
        raise SystemExit("Profil eToro introuvable (clés API, réseau ou pseudo).")
    url = _avatar_url_from_profile(profile)
    if not url:
        raise SystemExit("Aucune URL d'avatar dans la réponse API.")
    r = requests.get(
        url,
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0 (compatible; etoro_interface/1.0)"},
    )
    r.raise_for_status()
    data = r.content
    ext = _ext_from_bytes(data)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    for p in IMAGES_DIR.glob("trader_avatar.*"):
        p.unlink(missing_ok=True)
    out = IMAGES_DIR / f"trader_avatar{ext}"
    out.write_bytes(data)
    print(f"OK — {out} ({len(data)} octets)")


if __name__ == "__main__":
    main()
