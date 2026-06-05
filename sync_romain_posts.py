"""Fetch and store all posts for trader RomainRoth."""

from __future__ import annotations

import argparse
import html
import io
import json
import sys
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
from etoro_client import get_user_feed_posts, get_user_profile, user_feed_ref_candidates
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
from trader_performance_metrics import get_newsletter_etoro_stats_snapshot

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


def _fetch_all_posts_for_ref(
    user_ref: str,
    requester_user_id: str,
    take: int = 100,
    max_pages: int = 200,
) -> list[dict]:
    posts: list[dict] = []
    seen_ids: set[str] = set()
    page_size = min(max(take, 1), 100)
    offset = 0
    pages = 0

    while pages < max_pages:
        data = get_user_feed_posts(
            user_id=user_ref,
            take=page_size,
            offset=offset,
            requester_user_id=requester_user_id,
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


def fetch_all_posts(username: str, take: int = 100, max_pages: int = 200) -> list[dict]:
    profile = get_user_profile(username)
    if not profile:
        raise RuntimeError(f"Trader introuvable: {username}")
    requester_id = _extract_user_id(profile)
    if not requester_id:
        raise RuntimeError(f"Impossible de trouver user_id pour: {username}")

    refs = user_feed_ref_candidates(username, profile)
    for user_ref in refs:
        posts = _fetch_all_posts_for_ref(
            user_ref, requester_id, take=take, max_pages=max_pages
        )
        if posts:
            print(f"fetch_all_posts: {len(posts)} post(s) via feed ref {user_ref!r}")
            return posts

    print(
        f"fetch_all_posts: aucun post via refs {refs!r} (voir messages get_user_feed_posts)",
        file=sys.stderr,
    )
    return []


def _resolve_post_image_file(item: dict) -> str | None:
    """Fichier WebP local : télécharge si URL distante, sinon conserve l'image déjà sur disque."""
    post_id = str(item.get("id") or "")
    remote = str(item.get("image_remote_url") or "").strip()
    if remote:
        downloaded = _download_post_image(remote, post_id)
        if downloaded:
            return downloaded

    existing_file = str(item.get("image_file") or "").strip()
    if existing_file and (IMAGES_DIR / existing_file).is_file():
        return existing_file

    image_url = str(item.get("image_url") or "").strip()
    if image_url.startswith("/api/trader-post-image/"):
        name = Path(image_url).name
        if name and (IMAGES_DIR / name).is_file():
            return name

    if post_id:
        default_name = f"{post_id}.webp"
        if (IMAGES_DIR / default_name).is_file():
            return default_name
    return None


def save_posts(posts: list[dict], output_path: Path = OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    enriched: list[dict] = []
    for p in posts:
        item = dict(p)
        local_file = _resolve_post_image_file(item)
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
    *,
    etoro_newsletter_stats: dict | None = None,
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
        read_more_label = "Accéder au post" if normalize_newsletter_lang(ui_lang) == "fr" else "Read more"
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
        etoro_newsletter_stats=etoro_newsletter_stats,
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


def _send_newsletter_for_new_posts(
    new_posts: list[dict],
    *,
    subject_prefix: str = "",
    recipients_override: list[tuple[str, str, str]] | None = None,
) -> None:
    if not new_posts:
        print("No new posts -> no newsletter sent.")
        return
    recipients = (
        list(recipients_override)
        if recipients_override is not None
        else _get_newsletter_recipients()
    )
    if not recipients:
        print("No active newsletter recipients found.")
        return
    if subject_prefix or recipients_override is not None:
        dest = ", ".join(email for email, _name, _lang in recipients)
        print(f"Newsletter destination(s): {dest}")
    base_plain = (os.getenv("SITE_BASE_URL") or "https://romainroth.com").strip().rstrip("/")
    posts_plain = f"{base_plain}/posts"
    etoro_profile_plain = "https://www.etoro.com/people/romainroth"
    etoro_copy_plain = "https://etoro.tw/46rrJQC"
    y = datetime.now(timezone.utc).year
    try:
        etoro_stats = get_newsletter_etoro_stats_snapshot(TRADER_USERNAME, y)
    except Exception:
        etoro_stats = None
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
            etoro_newsletter_stats=etoro_stats,
        )
        try:
            html = _build_newsletter_html(
                email,
                name,
                posts_for_lang,
                ui_lang,
                etoro_newsletter_stats=etoro_stats,
            )
            _send_html_email(email, subject, html, plain)
            sent += 1
            print(f"Newsletter sent OK to {email}")
        except Exception as exc:
            failed += 1
            print(f"Newsletter send failed for {email}: {exc}")
    print(
        f"Newsletter done: sent={sent}, failed={failed}, skipped_no_post_in_lang={skipped}, "
        f"recipients={len(recipients)}"
    )


def load_local_posts(path: Path | str = OUTPUT_PATH, *, limit: int | None = None) -> list[dict]:
    """Charge les posts depuis le JSON local (cache), sans appel API eToro."""
    json_path = Path(path)
    if not json_path.is_file():
        return []
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"load_local_posts: JSON invalide ({json_path}): {exc}", file=sys.stderr)
        return []
    rows = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for p in rows:
        if not isinstance(p, dict):
            continue
        message = str(p.get("message") or "").strip()
        if not message:
            continue
        out.append(dict(p))
    out.sort(key=lambda x: x.get("created", ""), reverse=True)
    if limit is not None:
        return out[: max(0, limit)]
    return out


def _load_latest_posts_from_disk(limit: int = 5) -> list[dict]:
    """Posts les plus récents déjà présents dans le JSON (sans refetch eToro)."""
    posts = load_local_posts(OUTPUT_PATH, limit=limit)
    if not posts and not OUTPUT_PATH.is_file():
        raise RuntimeError(f"Missing posts file: {OUTPUT_PATH}")
    return posts


def run_newsletter_test(*, test_to_email: str | None = None, test_ui_lang: str = "fr") -> None:
    """Envoie un mail réel aux abonnés actifs (même logique que la prod), contenu = derniers posts sur disque."""
    sample = _load_latest_posts_from_disk(5)
    if not sample:
        raise SystemExit(f"No posts in {OUTPUT_PATH}; run a normal sync first.")
    assign_slugs_to_posts(sample)
    prefix = "[TEST] "
    override: list[tuple[str, str, str]] | None = None
    if test_to_email:
        addr = test_to_email.strip().lower()
        if "@" not in addr or not addr.split("@", 1)[0] or not addr.split("@", 1)[1]:
            raise SystemExit(f"Invalid --newsletter-test-to address: {test_to_email!r}")
        lang = normalize_newsletter_lang(test_ui_lang)
        override = [(addr, "", lang)]
        print(
            f"Newsletter test: single recipient {addr!r} (lang={lang}), subject prefix {prefix!r}, "
            f"{len(sample)} post(s) from disk."
        )
    else:
        print(
            f"Newsletter test: sending email(s) with subject prefix {prefix!r} "
            f"({len(sample)} post(s) from disk) to active subscribers from the database."
        )
    _send_newsletter_for_new_posts(sample, subject_prefix=prefix, recipients_override=override)


def main() -> None:
    existing_ids = _load_existing_post_ids(OUTPUT_PATH)
    posts = fetch_all_posts(TRADER_USERNAME)
    if not posts:
        print("Aucun post récupéré depuis eToro API. Utilisation du cache local.")
        posts = load_local_posts(OUTPUT_PATH)
        if posts:
            print(f"Cache local: {len(posts)} post(s) chargé(s) depuis {OUTPUT_PATH}.")
    if not posts and existing_ids:
        print(
            f"ERROR: eToro API returned 0 posts but {len(existing_ids)} id(s) exist locally — "
            f"refusing to overwrite {OUTPUT_PATH}.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    assign_slugs_to_posts(posts)
    new_posts = [p for p in posts if str(p.get("id") or "") not in existing_ids]
    if new_posts:
        print(f"Newsletter: {len(new_posts)} new post id(s) since last sync.")
    save_posts(posts, OUTPUT_PATH)
    _send_newsletter_for_new_posts(new_posts)
    print(f"{len(posts)} posts saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync eToro trader posts + optional newsletter test.")
    parser.add_argument(
        "--newsletter-test",
        action="store_true",
        help="Send one newsletter per active subscriber using the 5 latest posts already in data JSON "
        "(real SMTP); subject is prefixed with [TEST]. Does not change the sync schedule logic.",
    )
    parser.add_argument(
        "--newsletter-test-to",
        metavar="EMAIL",
        default=None,
        help="With --newsletter-test: send only to this address (ignores DB subscriber list).",
    )
    parser.add_argument(
        "--newsletter-test-lang",
        choices=["fr", "en"],
        default="fr",
        help="With --newsletter-test-to: newsletter UI language (default: fr).",
    )
    args = parser.parse_args()
    if args.newsletter_test_to and not args.newsletter_test:
        parser.error("--newsletter-test-to requires --newsletter-test")
    if args.newsletter_test:
        run_newsletter_test(
            test_to_email=args.newsletter_test_to,
            test_ui_lang=args.newsletter_test_lang,
        )
    else:
        main()
