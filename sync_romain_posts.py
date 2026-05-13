"""Fetch and store all posts for trader RomainRoth."""

from __future__ import annotations

import argparse
import html
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
from newsletter_i18n import (
    build_new_posts_newsletter_html,
    build_new_posts_newsletter_plain,
    format_new_posts_hello_html,
    new_posts_email_subject,
    new_posts_empty_posts_html,
    new_posts_image_alt,
    normalize_newsletter_lang,
    parse_newsletter_ui_lang_from_message,
)
from trader_post_lang import filter_posts_by_ui_lang
from trader_post_slug import assign_slugs_to_posts

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


def _get_newsletter_recipients() -> list[tuple[str, str, str]]:
    conn = _get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT LOWER(email) AS email, COALESCE(first_name, ''), COALESCE(last_name, ''),
                       COALESCE(subject, ''), COALESCE(message, ''), created_at
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

    recipients: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for email, first_name, last_name, subject, message, _created_at in rows:
        if email in seen:
            continue
        seen.add(email)
        if subject != "Newsletter":
            continue
        full_name = f"{(first_name or '').strip()} {(last_name or '').strip()}".strip()
        ui_lang = parse_newsletter_ui_lang_from_message(message)
        recipients.append((email, full_name, ui_lang))
    return recipients


def _build_newsletter_html(
    recipient_email: str,
    recipient_name: str,
    new_posts: list[dict],
    ui_lang: str,
) -> str:
    """new_posts : déjà filtrés par langue d’interface du destinataire."""
    base_url = (os.getenv("SITE_BASE_URL") or "https://romainroth.com").strip().rstrip("/")
    unsub_token = _newsletter_unsubscribe_token(recipient_email)
    unsub_link = (
        f"{base_url}/newsletter/unsubscribe?email={quote_plus(recipient_email)}&token={quote_plus(unsub_token)}"
    )
    posts_page_url = f"{base_url}/posts"
    etoro_profile_url = "https://www.etoro.com/people/romainroth"
    etoro_copy_invite_url = "https://etoro.tw/46rrJQC"
    posts_href = html.escape(posts_page_url)
    unsub_href = html.escape(unsub_link)
    profile_href = html.escape(etoro_profile_url)
    copy_href = html.escape(etoro_copy_invite_url)
    img_alt = new_posts_image_alt(ui_lang)
    posts_html: list[str] = []
    for post in new_posts[:5]:
        msg = str(post.get("message") or "").strip()
        preview_raw = (msg[:260] + "...") if len(msg) > 260 else msg
        preview = html.escape(preview_raw)
        slug = str(post.get("slug") or "").strip()
        post_url = f"{base_url}/posts/{quote_plus(slug)}" if slug else ""
        image_url = str(post.get("image_url") or "").strip()
        remote_image_url = str(post.get("image_remote_url") or "").strip()
        if image_url.startswith("/"):
            image_url = f"{base_url}{image_url}"
        if not image_url and remote_image_url.startswith("http"):
            image_url = remote_image_url
        image_url_esc = html.escape(image_url, quote=True)
        image_block = (
            f"<img src=\"{image_url_esc}\" alt=\"{html.escape(img_alt)}\" "
            "style=\"display:block;width:100%;max-width:100%;height:auto;border-radius:10px;border:1px solid #eee;\">"
            if image_url
            else ""
        )
        read_more_label = "Lire la suite" if normalize_newsletter_lang(ui_lang) == "fr" else "Read more"
        read_more_button = (
            "<p style='margin:12px 0 0;'>"
            f"<a href=\"{html.escape(post_url, quote=True)}\" target=\"_blank\" rel=\"noopener noreferrer\" "
            "style=\"display:inline-block;background:#111;color:#fff;text-decoration:none;"
            "padding:10px 16px;border-radius:8px;font-weight:700;font-size:14px;line-height:1.2;\">"
            f"{html.escape(read_more_label)}</a></p>"
            if post_url
            else ""
        )
        posts_html.append(
            "<div style='margin-bottom:24px;padding-bottom:22px;border-bottom:1px solid #e5e7eb;'>"
            f"<p style='margin:0 0 12px;font-size:15px;line-height:1.6;'>{preview}</p>"
            f"{image_block}"
            f"{read_more_button}"
            "</div>"
        )
    posts_block = "".join(posts_html) or new_posts_empty_posts_html(ui_lang)
    hello_line = format_new_posts_hello_html(ui_lang, recipient_name)
    return build_new_posts_newsletter_html(
        ui_lang,
        TRADER_USERNAME,
        hello_line,
        posts_block,
        profile_href,
        copy_href,
        posts_href,
        unsub_href,
        base_url,
    )


def _send_html_email(to_email: str, subject: str, html_body: str, plain_body: str) -> None:
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
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if smtp_use_tls:
            server.starttls()
        if smtp_user:
            server.login(smtp_user, smtp_password)
        server.sendmail(smtp_from, [to_email], msg.as_string())


def _send_newsletter_for_new_posts(new_posts: list[dict], *, subject_prefix: str = "") -> None:
    if not new_posts:
        print("No new posts -> no newsletter sent.")
        return
    recipients = _get_newsletter_recipients()
    if not recipients:
        print("No active newsletter recipients found.")
        return
    base_plain = (os.getenv("SITE_BASE_URL") or "https://romainroth.com").strip().rstrip("/")
    posts_plain = f"{base_plain}/posts"
    etoro_profile_plain = "https://www.etoro.com/people/romainroth"
    etoro_copy_plain = "https://etoro.tw/46rrJQC"
    sent = 0
    failed = 0
    skipped = 0
    for email, name, ui_lang in recipients:
        posts_for_lang = filter_posts_by_ui_lang(new_posts, ui_lang)
        if not posts_for_lang:
            skipped += 1
            continue
        first_message = str((posts_for_lang[0] or {}).get("message") or "").strip()
        first_title = first_message.splitlines()[0].strip() if first_message else ""
        if len(first_title) > 140:
            first_title = first_title[:137].rstrip() + "..."
        subject = subject_prefix + new_posts_email_subject(ui_lang, len(posts_for_lang), first_title)
        plain = build_new_posts_newsletter_plain(
            ui_lang,
            posts_plain,
            etoro_profile_plain,
            etoro_copy_plain,
            len(posts_for_lang),
            base_plain,
        )
        try:
            html = _build_newsletter_html(email, name, posts_for_lang, ui_lang)
            _send_html_email(email, subject, html, plain)
            sent += 1
        except Exception as exc:
            failed += 1
            print(f"Newsletter send failed for {email}: {exc}")
    print(
        f"Newsletter done: sent={sent}, failed={failed}, skipped_no_post_in_lang={skipped}, "
        f"recipients={len(recipients)}"
    )


def _post_created_on_utc_date(post: dict, day_utc) -> bool:
    raw = str(post.get("created") or "").strip()
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date() == day_utc
    except ValueError:
        # Fallback format: "YYYY-MM-DD ..."
        return raw[:10] == day_utc.isoformat()


def _load_latest_posts_from_disk(limit: int = 5) -> list[dict]:
    """Posts les plus récents déjà présents dans le JSON (sans refetch eToro)."""
    if not OUTPUT_PATH.is_file():
        raise RuntimeError(f"Missing posts file: {OUTPUT_PATH}")
    try:
        data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Invalid JSON in {OUTPUT_PATH}: {exc}") from exc
    rows = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return []
    out: list[dict] = []
    for p in rows[:limit]:
        if isinstance(p, dict):
            out.append(dict(p))
    return out


def run_newsletter_test() -> None:
    """Envoie un mail réel aux abonnés actifs (même logique que la prod), contenu = derniers posts sur disque."""
    sample = _load_latest_posts_from_disk(5)
    if not sample:
        raise SystemExit(f"No posts in {OUTPUT_PATH}; run a normal sync first.")
    assign_slugs_to_posts(sample)
    prefix = "[TEST] "
    print(
        f"Newsletter test: sending email(s) with subject prefix {prefix!r} "
        f"({len(sample)} post(s) from disk) to active subscribers."
    )
    _send_newsletter_for_new_posts(sample, subject_prefix=prefix)


def main() -> None:
    existing_ids = _load_existing_post_ids(OUTPUT_PATH)
    posts = fetch_all_posts(TRADER_USERNAME)
    assign_slugs_to_posts(posts)
    new_posts = [p for p in posts if str(p.get("id") or "") not in existing_ids]
    today_utc = datetime.now(timezone.utc).date()
    new_posts_today = [p for p in new_posts if _post_created_on_utc_date(p, today_utc)]
    save_posts(posts, OUTPUT_PATH)
    _send_newsletter_for_new_posts(new_posts_today)
    print(f"{len(posts)} posts saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync eToro trader posts + optional newsletter test.")
    parser.add_argument(
        "--newsletter-test",
        action="store_true",
        help="Send one newsletter per active subscriber using the 5 latest posts already in data JSON "
        "(real SMTP); subject is prefixed with [TEST]. Does not change the sync schedule logic.",
    )
    args = parser.parse_args()
    if args.newsletter_test:
        run_newsletter_test()
    else:
        main()
