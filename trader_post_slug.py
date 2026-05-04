"""Slugs d’URL pour les posts trader (dérivés du titre = première ligne du message)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def post_title_line(message: str) -> str:
    """Première ligne non vide du message (aligné sur l’affichage du profil)."""
    if not message:
        return ""
    for line in str(message).splitlines():
        line = line.strip()
        if line:
            if len(line) > 160:
                return line[:157] + "…"
            return line
    return ""


def slugify_for_url(text: str, max_len: int = 72) -> str:
    """Chaîne URL-friendly (a-z, 0-9, tirets)."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", text)
    ascii_str = norm.encode("ascii", "ignore").decode("ascii")
    s = ascii_str.lower().strip()
    s = _SLUG_RE.sub("-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s


def base_slug_for_post(message: str, post_id: str) -> str:
    slug = slugify_for_url(post_title_line(message))
    if not slug:
        safe = "".join(ch for ch in str(post_id) if ch.isalnum())[:24].lower() or "post"
        slug = f"post-{safe}"
    return slug


def assign_slugs_to_posts(posts: list[dict]) -> None:
    """Ajoute la clé « slug » à chaque post (unique dans la liste)."""
    if not posts:
        return
    bases = [
        base_slug_for_post(str(p.get("message") or ""), str(p.get("id") or ""))
        for p in posts
    ]
    counts = Counter(bases)
    used: set[str] = set()
    for p, base in zip(posts, bases):
        pid = str(p.get("id") or "")
        if counts[base] > 1:
            suf = hashlib.sha256(pid.encode("utf-8")).hexdigest()[:6]
            slug = f"{base}-{suf}"
        else:
            slug = base
        n = 0
        while slug in used:
            n += 1
            suf = hashlib.sha256(f"{pid}:{n}".encode("utf-8")).hexdigest()[:6]
            slug = f"{base}-{suf}"
        used.add(slug)
        p["slug"] = slug
