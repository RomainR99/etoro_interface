"""Fetch and store all posts for trader RomainRoth."""

from __future__ import annotations

import io
import json
import os
import hashlib
import hmac
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote_plus
from urllib.parse import urlparse

import psycopg2
import requests
from PIL import Image
from env_load import load_app_dotenv
from etoro_client import get_user_feed_posts, get_user_profile

load_app_dotenv(Path(__file__).resolve().parent)

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


def _bytes_to_webp(data: bytes, out_path: Path) -> bool:
    """Écrit les octets image en WebP sur disque."""
    try:
        if len(data) >= 12 and data[8:12] == b"WEBP":
            out_path.write_bytes(data)
            return out_path.is_file()
        bio = io.BytesIO(data)
        with Image.open(bio) as img:
            if getattr(img, "is_animated", False):
                img.seek(0)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            img.save(out_path, format="WEBP", quality=85, method=6)
        return out_path.is_file()
    except Exception:
        return False


def _download_post_image(url: str, post_id: str) -> str | None:
    """Télécharge l'image du post, la convertit en WebP et retourne le nom de fichier local."""
    if not url:
        return None
    try:
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        parsed = urlparse(url)
        ext = Path(parsed.path).suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            ext = ".png"
        safe_id = "".join(ch for ch in str(post_id) if ch.isalnum() or ch in ("-", "_"))[:64] or "post"
        filename = f"{safe_id}.webp"
        target = IMAGES_DIR / filename
        if target.exists():
            return filename
        r = requests.get(url, timeout=20)
        if r.status_code != 200 or not r.content:
            return None
        src = r.content
        head = src[:200].lstrip().lower()
        if head.startswith(b"<!doctype") or head.startswith(b"<html"):
            return None
        if len(src) >= 12 and src[8:12] == b"WEBP":
            target.write_bytes(src)
            return filename if target.is_file() else None
        if not _bytes_to_webp(src, target):
            return None
        return filename if target.is_file() else None
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


def _load_existing_post_ids(output_path: Path = OUTPUT_PATH) -> set[str]:
    if not output_path.is_file():
        return set()
    try:
        data = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    rows = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return set()
    out: set[str] = set()
    for row in rows:
        if isinstance(row, dict) and row.get("id"):
            out.add(str(row.get("id")))
    return out


def _get_pg_connection():
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(database_url)


def _newsletter_unsubscribe_token(email: str) -> str:
    secret = (
        (os.getenv("NEWSLETTER_UNSUBSCRIBE_SECRET") or "").strip()
        or (os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "dev-secret-change-me").strip()
    )
    normalized = (email or "").strip().lower()
    return hmac.new(secret.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def _get_newsletter_recipients() -> list[tuple[str, str]]:
    conn = _get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT LOWER(email) AS email, COALESCE(first_name, ''), COALESCE(last_name, ''), COALESCE(subject, ''), created_at
                FROM contact_messages
                WHERE email IS NOT NULL
                  AND email <> ''
                  AND subject IN ('Newsletter', 'Newsletter Unsubscribe')
                ORDER BY LOWER(email), created_at DESC
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    recipients: list[tuple[str, str]] = []
    seen: set[str] = set()
    for email, first_name, last_name, subject, _created_at in rows:
        if email in seen:
            continue
        seen.add(email)
        if subject != "Newsletter":
            continue
        full_name = f"{(first_name or '').strip()} {(last_name or '').strip()}".strip()
        recipients.append((email, full_name))
    return recipients


def _build_newsletter_html(recipient_email: str, recipient_name: str, new_posts: list[dict]) -> str:
    base_url = (os.getenv("SITE_BASE_URL") or "https://romainroth.com").strip().rstrip("/")
    unsub_token = _newsletter_unsubscribe_token(recipient_email)
    unsub_link = (
        f"{base_url}/newsletter/unsubscribe?email={quote_plus(recipient_email)}&token={quote_plus(unsub_token)}"
    )
    posts_html = []
    for post in new_posts[:5]:
        msg = str(post.get("message") or "").strip()
        created = str(post.get("created") or "").strip()
        preview = (msg[:260] + "...") if len(msg) > 260 else msg
        posts_html.append(
            f"<li style='margin-bottom:14px;'><strong>{created}</strong><br>{preview}</li>"
        )
    posts_block = "".join(posts_html) or "<li>Nouveau contenu disponible sur le profil.</li>"
    hello = f"Bonjour {recipient_name}," if recipient_name else "Bonjour,"
    return f"""
<html>
  <body style="font-family:Arial,sans-serif;color:#111;line-height:1.5;">
    <p>{hello}</p>
    <p>De nouveaux posts eToro de <strong>{TRADER_USERNAME}</strong> sont disponibles.</p>
    <ul>
      {posts_block}
    </ul>
    <p><a href="{base_url}/posts">Voir tous les posts sur le site</a></p>
    <hr>
    <p style="font-size:12px;color:#666;">
      Vous recevez cet email car vous avez accepté la newsletter.<br>
      <a href="{unsub_link}">Se désabonner</a>
    </p>
  </body>
</html>
""".strip()


def _send_html_email(to_email: str, subject: str, html_body: str) -> None:
    smtp_host = (os.getenv("SMTP_HOST") or "").strip()
    smtp_port = int((os.getenv("SMTP_PORT") or "587").strip() or "587")
    smtp_user = (os.getenv("SMTP_USER") or "").strip()
    smtp_password = (os.getenv("SMTP_PASSWORD") or "").strip()
    smtp_from = (os.getenv("SMTP_FROM") or smtp_user).strip()
    smtp_use_tls = (os.getenv("SMTP_USE_TLS") or "1").strip().lower() in ("1", "true", "yes", "on")
    if not smtp_host or not smtp_from:
        raise RuntimeError("SMTP configuration missing (SMTP_HOST / SMTP_FROM)")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_email
    plain = "De nouveaux posts sont disponibles sur https://romainroth.com/posts"
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if smtp_use_tls:
            server.starttls()
        if smtp_user:
            server.login(smtp_user, smtp_password)
        server.sendmail(smtp_from, [to_email], msg.as_string())


def _send_newsletter_for_new_posts(new_posts: list[dict]) -> None:
    if not new_posts:
        print("No new posts -> no newsletter sent.")
        return
    recipients = _get_newsletter_recipients()
    if not recipients:
        print("No active newsletter recipients found.")
        return
    sent = 0
    failed = 0
    subject = f"[RomainRoth] {len(new_posts)} nouveau(x) post(s) disponible(s)"
    for email, name in recipients:
        try:
            html = _build_newsletter_html(email, name, new_posts)
            _send_html_email(email, subject, html)
            sent += 1
        except Exception as exc:
            failed += 1
            print(f"Newsletter send failed for {email}: {exc}")
    print(f"Newsletter done: sent={sent}, failed={failed}, recipients={len(recipients)}")


def main() -> None:
    existing_ids = _load_existing_post_ids(OUTPUT_PATH)
    posts = fetch_all_posts(TRADER_USERNAME)
    new_posts = [p for p in posts if str(p.get("id") or "") not in existing_ids]
    save_posts(posts, OUTPUT_PATH)
    _send_newsletter_for_new_posts(new_posts)
    print(f"{len(posts)} posts saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
