"""Application Flask pour visualiser le profil des traders eToro."""

from pathlib import Path

# Charger .env et/ou ETORO_ENV_FILE (variables systemd non écrasées)
from env_load import load_app_dotenv

load_app_dotenv(Path(__file__).resolve().parent)

import base64
import csv
import hashlib
import hmac
import io
import json
import math
import os
import sqlite3
import re
import threading
import time
import uuid
import pickle
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, wait

import psycopg2
import requests
from flask import Flask, Response, g, make_response, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from etoro_client import (
    get_user_profile,
    get_user_gain,
    get_user_portfolio,
    get_portfolio_instruments,
    get_most_copied_traders,
    get_instruments_by_exchange,
    get_all_stocks,
    get_posts_per_month,
    get_current_copiers,
    get_copiers_vs_performance,
    create_post as etoro_create_post,
)


try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

try:
    import redis
except ImportError:
    redis = None

app = Flask(__name__)
app.config["SECRET_KEY"] = (os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "dev-secret-change-me")
app.config["SESSION_COOKIE_NAME"] = "etoro_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = (os.getenv("SESSION_COOKIE_SECURE") or "0").strip().lower() in ("1", "true", "yes", "on")
app.config["SESSION_COOKIE_SAMESITE"] = (os.getenv("SESSION_COOKIE_SAMESITE") or "Lax").strip() or "Lax"


@app.context_processor
def inject_recaptcha_site_key():
    """reCAPTCHA v2 « case à cocher » uniquement (voir README si Google affiche « Type de clé non valide »)."""
    return {"recaptcha_site_key": (os.getenv("RECAPTCHA_SITE_KEY") or "").strip()}


def _request_has_cookie_consent() -> bool:
    """True si la requête HTTP porte déjà un choix de consentement cookies (v2 ou ancien nom)."""
    v = (request.cookies.get("cookieConsent_v2") or "").strip()
    if v in ("accepted", "necessary"):
        return True
    leg = (request.cookies.get("cookieConsent") or "").strip()
    return leg in ("accepted", "necessary")


_PROJECT_IMAGES = Path(__file__).resolve().parent / "images"


def _trader_avatar_image_filename() -> str | None:
    """Fichier images/trader_avatar.* après sync_trader_avatar.py ; sinon None."""
    if not _PROJECT_IMAGES.is_dir():
        return None
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = _PROJECT_IMAGES / f"trader_avatar{ext}"
        if p.is_file():
            return p.name
    return None


def _trader_avatar_disk_path() -> Path | None:
    """Chemin disque de la photo header / favicon (trader_avatar.* ou romain.png)."""
    fn = _trader_avatar_image_filename()
    if fn:
        p = _PROJECT_IMAGES / fn
        if p.is_file():
            return p
    r = _PROJECT_IMAGES / "romain.png"
    return r if r.is_file() else None


def _mime_for_image_path(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    return "image/png"


@app.context_processor
def inject_trader_avatar_url():
    """Photo header + favicon : fichier local (pas d’URL API côté navigateur)."""
    fn = _trader_avatar_image_filename()
    if fn:
        return {"trader_avatar_url": f"/images/{fn}"}
    if (_PROJECT_IMAGES / "romain.png").is_file():
        return {"trader_avatar_url": "/images/romain.png"}
    return {"trader_avatar_url": None}


@app.context_processor
def inject_cookie_banner_state():
    """
    État initial affiché par le HTML de la bannière cookies (sans attendre le JS).
    Sur mobile, le JS seul peut ne pas suffire ; le serveur voit les cookies de la requête.
    """
    try:
        from flask import has_request_context

        if not has_request_context():
            return {"show_cookie_banner": True}
        return {"show_cookie_banner": not _request_has_cookie_consent()}
    except Exception:
        return {"show_cookie_banner": True}


@app.template_filter("username_display")
def username_display_filter(name: str) -> str:
    """Affichage du pseudo : espace avant les majuscules (ex. RomainRoth → Romain Roth)."""
    if not name:
        return name
    return re.sub(r"([a-z\d])([A-Z])", r"\1 \2", name)


TRADER_USERNAME = "RomainRoth"
DATE_FROM = "2022-09"  # Données à partir de septembre 2022
COPIERS_VS_PERF_CACHE = os.path.join(os.path.dirname(__file__), "data", "copiers_vs_performance.json")
ETORO_PUBLISHED_POSTS_PATH = os.path.join(os.path.dirname(__file__), "data", "etoro_published_posts.json")
TRADER_POSTS_PATH = os.path.join(os.path.dirname(__file__), "data", "trader_posts_romainroth.json")
TRADER_POST_IMAGES_DIR = os.path.join(os.path.dirname(__file__), "data", "trader_post_images")
LEXIQUE_PATH = os.path.join(os.path.dirname(__file__), "data", "lexique.json")
FAQ_PATH = os.path.join(os.path.dirname(__file__), "data", "faq.json")
_newsletter_subscribe_rate: dict[str, list[float]] = {}
NEWSLETTER_SUBSCRIBE_MAX_PER_HOUR = 12
_lexique_json_cache: list | None = None
_faq_json_cache: list | None = None
_trader_posts_cache: list[dict] | None = None
_trader_posts_loaded_mtime: float | None = None
_CACHE_MISS = object()
_cache_lock = threading.Lock()
_response_cache: dict[str, tuple[float, object]] = {}
INDEX_EXTERNAL_FETCH_TIMEOUT_SEC = 4.0
STARTUP_WARMUP_MAX_SECONDS = max(
    1.0,
    float((os.getenv("STARTUP_WARMUP_MAX_SECONDS") or "10").strip() or "10"),
)
REDIS_URL = (os.getenv("REDIS_URL") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
WARMUP_TOKEN = (os.getenv("WARMUP_TOKEN") or "").strip()
AUTO_WARMUP_ON_START = (os.getenv("AUTO_WARMUP_ON_START") or "1").strip().lower() in ("1", "true", "yes", "on")
_redis_client = None
_redis_init_done = False
_startup_warmup_started = False
_startup_warmup_lock = threading.Lock()
API_AUTH_PASSWORD = (os.getenv("API_AUTH_PASSWORD") or "").strip()
ENFORCE_MUTATION_AUTH = (os.getenv("ENFORCE_MUTATION_AUTH") or "1").strip().lower() in ("1", "true", "yes", "on")
ALLOWED_FRONTEND_ORIGINS = {
    o.strip().rstrip("/")
    for o in (os.getenv("FRONTEND_ORIGINS") or "http://localhost:3000").split(",")
    if o.strip()
}


def get_pg_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL missing")
    return psycopg2.connect(DATABASE_URL)


# Rate limit par visitor_id : 5/min, 30/h, 100/j
CHAT_RATE_LIMIT = {"per_min": 5, "per_hour": 30, "per_day": 100}
_chat_rate_store: dict[str, list[float]] = {}  # visitor_id -> timestamps
_visitor_recent_messages: dict[str, list[str]] = {}  # visitor_id -> last 3 user messages (similarity)
VISITOR_COOKIE_NAME = "visitor_id"
VISITOR_COOKIE_MAX_AGE = 365 * 24 * 3600  # 1 an
CAPTCHA_AFTER_MESSAGES = 5  # demander CAPTCHA après N messages (24h)
CAPTCHA_FAST_RATE_THRESHOLD = 3  # demander CAPTCHA si >= N messages en 1 min

# Comportements anormaux
MAX_USER_MESSAGE_CHARS = 2000  # prompts énormes (copier-coller)
MAX_ESTIMATED_TOKENS = 12000  # ~4 chars/token, limite pour coût
SUSPICIOUS_UA_SUBSTRINGS = ("curl", "python", "wget", "httpie", "bot", "scrapy", "requests/")

# Taille des messages
MAX_REPLY_CHARS = 4000  # tronque la réponse IA si plus long
MAX_HISTORY_MESSAGES = 20  # nb max de messages (hors system) envoyés au modèle
MAX_COMPLETION_TOKENS = 1024  # max_tokens pour la réponse OpenAI
CHAT_OPENAI_TIMEOUT_SEC = 8.0  # délai max pour la réponse du modèle (chatbot)


def _cache_get(key: str):
    """Retourne la valeur en cache si valide, sinon _CACHE_MISS."""
    now = time.monotonic()
    with _cache_lock:
        row = _response_cache.get(key)
        if not row:
            return _CACHE_MISS
        expires_at, value = row
        if now >= expires_at:
            _response_cache.pop(key, None)
            return _CACHE_MISS
        return value


def _cache_set(key: str, value, ttl_seconds: float) -> None:
    """Stocke une valeur en cache pour ttl_seconds."""
    with _cache_lock:
        _response_cache[key] = (time.monotonic() + max(0.0, ttl_seconds), value)


def _redis_get_client():
    """Initialise (lazy) un client Redis si REDIS_URL est défini."""
    global _redis_client, _redis_init_done
    if _redis_init_done:
        return _redis_client
    _redis_init_done = True
    if not REDIS_URL or redis is None:
        return None
    try:
        _redis_client = redis.from_url(
            REDIS_URL,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
            decode_responses=False,
        )
        _redis_client.ping()
        return _redis_client
    except Exception:
        _redis_client = None
        return None


def _cache_get_redis(key: str):
    """Retourne la valeur depuis Redis, sinon _CACHE_MISS."""
    client = _redis_get_client()
    if client is None:
        return _CACHE_MISS
    try:
        payload = client.get(f"cache:{key}")
        if not payload:
            return _CACHE_MISS
        return pickle.loads(payload)
    except Exception:
        return _CACHE_MISS


def _cache_set_redis(key: str, value, ttl_seconds: float) -> None:
    """Écrit la valeur dans Redis avec TTL."""
    client = _redis_get_client()
    if client is None:
        return
    try:
        ttl = max(1, int(ttl_seconds))
        client.setex(f"cache:{key}", ttl, pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))
    except Exception:
        pass


def _cached_call(key: str, ttl_seconds: float, fn, fallback):
    """
    Appelle fn() avec cache TTL.
    En cas d'erreur, retourne fallback sans lever d'exception.
    """
    cached = _cache_get(key)
    if cached is not _CACHE_MISS:
        return cached
    cached_redis = _cache_get_redis(key)
    if cached_redis is not _CACHE_MISS:
        _cache_set(key, cached_redis, ttl_seconds)
        return cached_redis
    try:
        value = fn()
    except Exception:
        value = fallback
    _cache_set(key, value, ttl_seconds)
    _cache_set_redis(key, value, ttl_seconds)
    return value


def _is_allowed_frontend_origin(origin: str | None) -> bool:
    if not origin:
        return False
    return origin.rstrip("/") in ALLOWED_FRONTEND_ORIGINS


def _is_api_request_path(path: str) -> bool:
    return path.startswith("/api/") or path.startswith("/internal/")


@app.after_request
def _apply_cors_headers(response: Response):
    """Ajoute les en-têtes CORS uniquement pour les origines front autorisées."""
    origin = request.headers.get("Origin")
    if _is_api_request_path(request.path) and _is_allowed_frontend_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin.rstrip("/")
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Warmup-Token"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    return response


@app.before_request
def _handle_cors_preflight():
    if request.method == "OPTIONS" and _is_api_request_path(request.path):
        return ("", 204)
    return None


def _is_authenticated() -> bool:
    return bool(session.get("api_auth") is True)


def _require_mutation_auth_if_enabled():
    if not ENFORCE_MUTATION_AUTH:
        return None
    if _is_authenticated():
        return None
    return jsonify({"ok": False, "error": "unauthorized"}), 401


def _detect_abnormal_behavior(messages: list, current_message: str) -> str | None:
    """
    Détecte les comportements anormaux. Retourne un message d'erreur si anormal, None sinon.
    - Requêtes trop rapides : géré par rate limit
    - Prompts énormes (copier-coller)
    - Mêmes messages répétés : géré par CAPTCHA
    - User-agent étrange
    - Trop de tokens demandés
    """
    if len(current_message) > MAX_USER_MESSAGE_CHARS:
        return f"Message trop long (max {MAX_USER_MESSAGE_CHARS} caractères)."
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    est_tokens = total_chars // 4
    if est_tokens > MAX_ESTIMATED_TOKENS:
        return "Requête trop volumineuse."
    ua = (request.headers.get("User-Agent") or "").strip().lower()
    if not ua or len(ua) < 10:
        return "User-Agent invalide ou absent."
    for sub in SUSPICIOUS_UA_SUBSTRINGS:
        if sub in ua:
            return "Requête refusée (client non autorisé)."
    return None


def _get_or_set_visitor_id(response: Response | None = None) -> str:
    """
    Récupère le visitor_id du cookie (ou en génère un). Stocke dans g pour la requête.
    Si response fourni et cookie absent, définit le cookie dessus.
    Retourne visitor_id.
    """
    if not hasattr(g, "visitor_id"):
        vid = request.cookies.get(VISITOR_COOKIE_NAME)
        if not vid or len(vid) != 36:
            vid = str(uuid.uuid4())
            g.visitor_id_new = True
        else:
            g.visitor_id_new = False
        g.visitor_id = vid
    vid = g.visitor_id
    if response is not None and getattr(g, "visitor_id_new", False):
        response.set_cookie(
            VISITOR_COOKIE_NAME,
            vid,
            max_age=VISITOR_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
        )
    return vid


def _get_client_ip() -> str:
    """Retourne l'IP du client (X-Forwarded-For si proxy)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _log_cookie_consent_to_db(
    choice: str,
    visitor_id: str | None,
    lang: str | None,
    user_agent: str | None,
    client_ip: str | None,
) -> None:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cookie_consent_log
                (created_at, choice, visitor_id, lang, user_agent, client_ip)
                VALUES (NOW(), %s, %s, %s, %s, %s)
                """,
                (
                    choice,
                    visitor_id or "",
                    (lang or "")[:16],
                    (user_agent or "")[:512],
                    (client_ip or "")[:64],
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_contact_message(
    first_name: str | None,
    last_name: str | None,
    email: str,
    subject: str | None,
    message: str,
) -> None:
    conn = get_pg_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO contact_messages
            (first_name, last_name, email, subject, message)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                (first_name or "")[:100] or None,
                (last_name or "")[:100] or None,
                email[:255],
                (subject or "")[:255] or None,
                message[:10000],
            ),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def _newsletter_rate_ok(ip: str) -> bool:
    """True si l’IP n’a pas dépassé la limite d’inscriptions sur la dernière heure."""
    now = time.time()
    cutoff = now - 3600
    lst = _newsletter_subscribe_rate.setdefault(ip, [])
    lst[:] = [t for t in lst if t > cutoff]
    return len(lst) < NEWSLETTER_SUBSCRIBE_MAX_PER_HOUR


def _newsletter_rate_record(ip: str) -> None:
    _newsletter_subscribe_rate.setdefault(ip, []).append(time.time())


def _split_subscriber_name(full: str) -> tuple[str, str]:
    s = (full or "").strip()
    if not s:
        return "", ""
    parts = s.split(None, 1)
    first = parts[0][:100]
    last = (parts[1].strip()[:100] if len(parts) > 1 else "")[:100]
    return first, last


def _newsletter_unsubscribe_token(email: str) -> str:
    secret = (
        (os.getenv("NEWSLETTER_UNSUBSCRIBE_SECRET") or "").strip()
        or (os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "dev-secret-change-me").strip()
    )
    normalized = (email or "").strip().lower()
    return hmac.new(secret.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def _is_valid_newsletter_unsubscribe_token(email: str, token: str) -> bool:
    expected = _newsletter_unsubscribe_token(email)
    return bool(token) and hmac.compare_digest(expected, token.strip())


def _mark_newsletter_unsubscribed(email: str) -> None:
    _insert_contact_message(
        None,
        None,
        email.strip().lower(),
        "Newsletter Unsubscribe",
        "User clicked unsubscribe link from newsletter email.",
    )


def _check_chat_rate_limit(visitor_id: str) -> bool:
    """Vérifie et enregistre la requête par visitor_id. Retourne True si autorisée, False si limite dépassée."""
    now = time.time()
    if visitor_id not in _chat_rate_store:
        _chat_rate_store[visitor_id] = []
    ts_list = _chat_rate_store[visitor_id]
    # Garder uniquement les timestamps des 24 dernières heures
    cutoff = now - 86400
    ts_list[:] = [t for t in ts_list if t > cutoff]
    # Vérifier les 3 limites
    if len([t for t in ts_list if t > now - 60]) >= CHAT_RATE_LIMIT["per_min"]:
        return False
    if len([t for t in ts_list if t > now - 3600]) >= CHAT_RATE_LIMIT["per_hour"]:
        return False
    if len(ts_list) >= CHAT_RATE_LIMIT["per_day"]:
        return False
    ts_list.append(now)
    return True


def _should_require_captcha(visitor_id: str, current_message: str) -> bool:
    """
    Retourne True si un CAPTCHA doit être demandé :
    - après 5 messages (sur les 24h glissantes),
    - ou si rythme trop rapide (>= 3 messages en 1 min),
    - ou si requêtes similaires (message identique à l'un des 2 derniers).
    """
    now = time.time()
    ts_list = _chat_rate_store.get(visitor_id, [])
    ts_list = [t for t in ts_list if t > now - 86400]
    recent = _visitor_recent_messages.get(visitor_id, [])
    norm = (current_message or "").strip().lower()
    # Après 5 messages
    if len(ts_list) >= CAPTCHA_AFTER_MESSAGES:
        return True
    # Rythme trop rapide
    if len([t for t in ts_list if t > now - 60]) >= CAPTCHA_FAST_RATE_THRESHOLD:
        return True
    # Requêtes similaires (message identique ou quasi-identique)
    if norm and recent:
        for r in recent[-2:]:
            if r and (norm == r.strip().lower() or norm in r.strip().lower() or r.strip().lower() in norm):
                return True
    return False


def _verify_recaptcha(token: str) -> bool:
    """Vérifie le token reCAPTCHA v2 côté serveur. Retourne True si valide."""
    secret = os.getenv("RECAPTCHA_SECRET_KEY")
    if not secret or not (token or "").strip():
        return False
    try:
        r = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": secret, "response": token},
            timeout=5,
        )
        data = r.json()
        return bool(data.get("success"))
    except Exception:
        return False


INDEX_CONFIG = {
    "sp500": ("^GSPC", "S&P 500", "#8b949e"),
    "ndq": ("^NDX", "NASDAQ 100", "#e85d04"),
    "cac40tr": ("PUST.PA", "CAC 40 TR", "#0055a4"),
    "msci": ("SWDA.L", "MSCI World", "#1b5e20"),
}




def _get_index_monthly_returns(ticker_symbol: str) -> dict[str, float]:
    """Récupère les rendements mensuels d'un indice depuis DATE_FROM."""
    if not HAS_YFINANCE:
        return {}
    try:
        ticker = yf.Ticker(ticker_symbol)
        start = f"{DATE_FROM}-01"
        hist = ticker.history(start=start, auto_adjust=True)
        if hist.empty or len(hist) < 2:
            return {}
        try:
            monthly = hist.resample("ME").last()
        except TypeError:
            monthly = hist.resample("M").last()
        start_str = start[:7]
        monthly = monthly[[dt.strftime("%Y-%m") >= start_str for dt in monthly.index]]
        returns = {}
        prev_close = None
        for dt, row in monthly.iterrows():
            close = float(row["Close"])
            if prev_close is not None and prev_close > 0:
                returns[dt.strftime("%Y-%m")] = (close - prev_close) / prev_close * 100
            prev_close = close
        return returns
    except Exception:
        return {}


def _get_sp500_monthly_returns() -> dict[str, float]:
    return _get_index_monthly_returns("^GSPC")


def _gain_to_by_month(gain: dict | None) -> dict[str, float]:
    """Convertit les gains API en dict {mois: gain_pct}."""
    out: dict[str, float] = {}
    if gain and gain.get("monthly"):
        for e in gain["monthly"]:
            ts = e.get("timestamp")
            g = e.get("gain")
            if ts and ts[:7] >= DATE_FROM:
                out[ts[:7]] = float(g) if g is not None else 0.0
    return out


def _monthly_to_yearly_returns(by_month: dict[str, float]) -> dict[str, float]:
    """Calcule le rendement annuel composé à partir des rendements mensuels. Retourne {année: pct}."""
    years: dict[str, list[float]] = {}
    for month, pct in by_month.items():
        if len(month) >= 4:
            y = month[:4]
            years.setdefault(y, []).append(pct)
    out: dict[str, float] = {}
    for y, pcts in years.items():
        cum = 1.0
        for p in pcts:
            cum *= 1.0 + p / 100.0
        out[y] = (cum - 1.0) * 100.0
    return out


def _total_cumulative_return(by_month: dict[str, float]) -> float | None:
    """Rendement cumulé total sur toute la période (composé)."""
    if not by_month:
        return None
    cum = 1.0
    for month in sorted(by_month.keys()):
        cum *= 1.0 + by_month[month] / 100.0
    return (cum - 1.0) * 100.0


def _annualized_return_from_monthly(by_month: dict[str, float]) -> float | None:
    """Rentabilité annualisée (CAGR sur la durée observée) à partir des rendements mensuels composés."""
    if not by_month:
        return None
    months_sorted = sorted(by_month.keys())
    n = len(months_sorted)
    if n < 1:
        return None
    cum = 1.0
    for m in months_sorted:
        cum *= 1.0 + by_month[m] / 100.0
    years = n / 12.0
    if years <= 0:
        return None
    try:
        ann = (cum ** (1.0 / years) - 1.0) * 100.0
    except OverflowError:
        return None
    if not math.isfinite(ann):
        return None
    return ann


def _build_performance_table(gain: dict | None) -> tuple[list[dict], dict | None]:
    """
    Construit les données pour le tableau performance par année.
    Pour chaque année : détail mensuel du trader (RomainRoth) uniquement, pas de comparaison mensuelle au S&P 500.
    Retourne (rows, total) où rows = [{year, trader_months: [pct_jan..pct_dec], trader_pct, sp500_pct, ecart}, ...].
    """
    trader_monthly = _gain_to_by_month(gain)
    if not trader_monthly:
        return [], None
    sp500_monthly = _get_sp500_monthly_returns()
    trader_yearly = _monthly_to_yearly_returns(trader_monthly)
    sp500_yearly = _monthly_to_yearly_returns(sp500_monthly)
    all_years = sorted(set(trader_yearly.keys()) | set(sp500_yearly.keys()))
    rows: list[dict] = []
    for y in all_years:
        trader_months: list[float | None] = [None] * 12
        for m in range(1, 13):
            key = f"{y}-{m:02d}"
            if key in trader_monthly:
                trader_months[m - 1] = trader_monthly[key]
        t_pct = trader_yearly.get(y)
        s_pct = sp500_yearly.get(y)
        t_val = t_pct if t_pct is not None else None
        s_val = s_pct if s_pct is not None else None
        ecart = (t_val - s_val) if (t_val is not None and s_val is not None) else None
        rows.append({
            "year": y,
            "trader_months": trader_months,
            "trader_pct": t_val,
            "sp500_pct": s_val,
            "ecart": ecart,
        })
    total_t = _total_cumulative_return(trader_monthly)
    total_s = _total_cumulative_return(sp500_monthly)
    total_ecart = (total_t - total_s) if (total_t is not None and total_s is not None) else None
    total = {"trader_pct": total_t, "sp500_pct": total_s, "ecart": total_ecart} if (total_t is not None or total_s is not None) else None
    return rows, total


def _build_since_sep2022_summary(gain: dict | None) -> dict | None:
    """Performances cumulées depuis DATE_FROM (sept. 2022) : trader, S&P 500, CAC 40 TR et écarts."""
    trader_monthly = _gain_to_by_month(gain)
    sp500_monthly = _get_sp500_monthly_returns()
    cac_sym = INDEX_CONFIG["cac40tr"][0]
    cac_monthly = _get_index_monthly_returns(cac_sym)
    t = _total_cumulative_return(trader_monthly)
    t_ann = _annualized_return_from_monthly(trader_monthly) if trader_monthly else None
    s = _total_cumulative_return(sp500_monthly)
    c = _total_cumulative_return(cac_monthly)
    if t is None and s is None and c is None:
        return None
    return {
        "trader_pct": t,
        "trader_annualized_pct": t_ann,
        "sp500_pct": s,
        "cac40_pct": c,
        "delta_vs_sp500": (t - s) if t is not None and s is not None else None,
        "delta_vs_cac40": (t - c) if t is not None and c is not None else None,
    }


def _compute_chart_data(
    main_gain: dict | None,
    extra_traders: list[str] | None = None,
    include_sp500: bool = True,
    extra_indices: list[str] | None = None,
) -> tuple[list[str], list[dict]]:
    """
    Calcule les données du graphique.
    Retourne (labels, datasets) où chaque dataset = {label, data, color}.
    """
    labels: list[str] = []
    datasets: list[dict] = []

    traders_gain: dict[str, dict[str, float]] = {}
    traders_gain[TRADER_USERNAME] = _gain_to_by_month(main_gain)
    for username in extra_traders or []:
        try:
            gain = get_user_gain(username)
            traders_gain[username] = _gain_to_by_month(_filter_gain_from_date(gain))
        except Exception:
            pass

    index_returns: dict[str, dict[str, float]] = {}
    if include_sp500:
        index_returns["S&P 500"] = _get_sp500_monthly_returns()
    for key in extra_indices or []:
        if key in INDEX_CONFIG:
            sym, label, _ = INDEX_CONFIG[key]
            ret = _get_index_monthly_returns(sym)
            if ret:
                index_returns[label] = ret
    all_months = set()
    for r in index_returns.values():
        all_months.update(r.keys())
    for t in traders_gain.values():
        all_months.update(t.keys())
    all_months = sorted(all_months)

    if not all_months:
        return labels, datasets

    colors = [
        "#58a6ff",
        "#3fb950",
        "#f0883e",
        "#a371f7",
        "#ff7b72",
        "#79c0ff",
        "#7ee787",
        "#d2a8ff",
        "#ffa657",
        "#56d4dd",
    ]

    for i, (name, by_month) in enumerate(traders_gain.items()):
        cum = 100.0
        values = []
        for month in all_months:
            if month in by_month:
                cum *= 1 + by_month[month] / 100
            values.append(round(cum, 2))
        datasets.append({
            "label": name,
            "data": values,
            "color": colors[i % len(colors)],
        })

    index_colors = {"S&P 500": "#8b949e"}
    for key in INDEX_CONFIG:
        index_colors[INDEX_CONFIG[key][1]] = INDEX_CONFIG[key][2]
    for label, returns in index_returns.items():
        cum = 100.0
        values = []
        for month in all_months:
            if month in returns:
                cum *= 1 + returns[month] / 100
            values.append(round(cum, 2))
        datasets.append({
            "label": label,
            "data": values,
            "color": index_colors.get(label, "#8b949e"),
        })

    return all_months, datasets


def _compute_dca_simulation(
    main_gain: dict | None,
    initial_investment: float = 1000.0,
    monthly_investment: float = 100.0,
) -> tuple[list[str], list[float], list[float]]:
    """
    Simule un placement : investissement initial + versement fixe à la fin de chaque mois (0 = capital initial seul).
    Retourne (labels, romainroth_values, sp500_values) en dollars.
    """
    labels: list[str] = []
    romainroth_vals: list[float] = []
    sp500_vals: list[float] = []

    trader_returns = _gain_to_by_month(main_gain)
    sp500_returns = _get_sp500_monthly_returns()
    all_months = sorted(set(trader_returns.keys()) | set(sp500_returns.keys()))
    if not all_months:
        return labels, romainroth_vals, sp500_vals

    bal_trader = initial_investment
    bal_sp500 = initial_investment

    for month in all_months:
        labels.append(month)
        if month in trader_returns:
            bal_trader = bal_trader * (1 + trader_returns[month] / 100) + monthly_investment
        else:
            bal_trader += monthly_investment
        if month in sp500_returns:
            bal_sp500 = bal_sp500 * (1 + sp500_returns[month] / 100) + monthly_investment
        else:
            bal_sp500 += monthly_investment
        romainroth_vals.append(round(bal_trader, 2))
        sp500_vals.append(round(bal_sp500, 2))

    return labels, romainroth_vals, sp500_vals


def _get_reference_months() -> list[str]:
    """Mois de référence = DATE_FROM jusqu'à ce mois (même plage que graphique 1)."""
    from datetime import datetime, timezone
    out = []
    start = datetime.strptime(DATE_FROM + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)
    m = start
    while m <= end:
        out.append(m.strftime("%Y-%m"))
        if m.month == 12:
            m = m.replace(year=m.year + 1, month=1)
        else:
            m = m.replace(month=m.month + 1)
    return out


def _compute_cumulative_index(by_month: dict[str, float], all_months: list[str] | None = None) -> float | None:
    """
    Calcule l'indice cumulé (base 100) comme le graphique 1.
    Retourne la valeur finale (ex: 266 = +166% de gain).
    """
    if all_months is None:
        all_months = _get_reference_months()
    if not all_months:
        return None
    cum = 100.0
    for month in all_months:
        if month in by_month:
            g = by_month.get(month) or 0
            cum *= 1 + float(g) / 100
    return round(cum, 2)


def _build_copiers_vs_performance_real(limit: int = 50) -> list[dict]:
    """
    Récupère les N traders les plus copiés (>25 copieurs, gain<=500%),
    calcule la performance réelle via get_user_gain (données mensuelles),
    sauvegarde dans un fichier JSON.
    """
    raw = get_copiers_vs_performance(limit=200)
    traders = [p["userName"] for p in raw[:limit]]
    all_months = _get_reference_months()
    points = []
    for username in traders:
        try:
            gain = get_user_gain(username)
            by_month = _gain_to_by_month(_filter_gain_from_date(gain))
            perf = _compute_cumulative_index(by_month, all_months=all_months)
            copiers = next((p["copiers"] for p in raw if p["userName"] == username), 0)
            if perf is not None and copiers and perf <= 600:
                points.append({"userName": username, "copiers": copiers, "gain": perf})
        except Exception:
            pass
        time.sleep(0.25)
    os.makedirs(os.path.dirname(COPIERS_VS_PERF_CACHE), exist_ok=True)
    with open(COPIERS_VS_PERF_CACHE, "w", encoding="utf-8") as f:
        json.dump({"points": points, "updated": datetime.now(timezone.utc).isoformat()}, f, ensure_ascii=False)
    return points


def _load_copiers_vs_performance_cached(refresh: bool = False) -> list[dict]:
    """Charge depuis le cache JSON, ou recalcule et sauvegarde si absent ou refresh."""
    if not refresh and os.path.exists(COPIERS_VS_PERF_CACHE):
        try:
            with open(COPIERS_VS_PERF_CACHE, encoding="utf-8") as f:
                data = json.load(f)
                return data.get("points", [])
        except Exception:
            pass
    return _build_copiers_vs_performance_real(limit=100)


def _filter_gain_from_date(gain_data: dict | None) -> dict | None:
    """Filtre les gains pour ne garder que les entrées à partir de septembre 2022."""
    if not gain_data:
        return gain_data
    filtered = {}
    if gain_data.get("monthly"):
        filtered["monthly"] = [
            e for e in gain_data["monthly"]
            if e.get("timestamp") and e["timestamp"][:7] >= DATE_FROM
        ]
    if gain_data.get("yearly"):
        filtered["yearly"] = [
            e for e in gain_data["yearly"]
            if e.get("timestamp") and e["timestamp"][:4] >= DATE_FROM[:4]
        ]
    return filtered if filtered else gain_data


def _prime_homepage_cache(max_duration_sec: float = STARTUP_WARMUP_MAX_SECONDS) -> dict:
    """Préchauffe la home, avec budget de temps strict pour éviter un warmup trop long."""
    deadline = time.monotonic() + max(1.0, float(max_duration_sec))
    results: dict[str, object] = {"profile": None, "gain": None}

    def _remaining() -> float:
        return max(0.0, deadline - time.monotonic())

    executor = ThreadPoolExecutor(max_workers=6)
    try:
        future_to_key = {
            executor.submit(
                _cached_call,
                "index_profile",
                180.0,
                lambda: get_user_profile(TRADER_USERNAME),
                None,
            ): "profile",
            executor.submit(
                _cached_call,
                "index_gain",
                180.0,
                lambda: _filter_gain_from_date(get_user_gain(TRADER_USERNAME)),
                None,
            ): "gain",
            executor.submit(
                _cached_call,
                "index_portfolio",
                180.0,
                lambda: get_user_portfolio(TRADER_USERNAME),
                None,
            ): "portfolio",
            executor.submit(
                _cached_call,
                "index_portfolio_instruments",
                180.0,
                lambda: get_portfolio_instruments(TRADER_USERNAME),
                [],
            ): "portfolio_instruments",
            executor.submit(
                _cached_call,
                "index_most_copied_100",
                300.0,
                lambda: get_most_copied_traders(100),
                [],
            ): "most_copied",
            executor.submit(
                _cached_call,
                "index_current_copiers",
                90.0,
                lambda: get_current_copiers(TRADER_USERNAME),
                None,
            ): "current_copiers",
        }
        done, not_done = wait(future_to_key.keys(), timeout=_remaining())
        for fut in done:
            results[future_to_key[fut]] = fut.result()
        for fut in not_done:
            fut.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    gain = results.get("gain")
    if _remaining() > 0:
        _cached_call(
            "index_chart_data",
            300.0,
            lambda: _compute_chart_data(gain, [], include_sp500=True),
            ([], []),
        )
    if _remaining() > 0:
        _cached_call(
            "index_performance_table",
            300.0,
            lambda: _build_performance_table(gain),
            ([], None),
        )
    if _remaining() > 0:
        _cached_call(
            "index_perf_since_sep2022",
            300.0,
            lambda: _build_since_sep2022_summary(gain),
            None,
        )
    if _remaining() > 0:
        _cached_call(
            "index_dca_default",
            300.0,
            lambda: _compute_dca_simulation(gain, 1000.0, 100.0),
            ([], [], []),
        )
    timed_out = _remaining() <= 0
    return {
        "ok": True,
        "profile_loaded": results.get("profile") is not None,
        "gain_loaded": gain is not None,
        "redis_enabled": _redis_get_client() is not None,
        "timed_out": timed_out,
        "budget_seconds": max_duration_sec,
    }


def _start_background_warmup_once() -> None:
    """Lance un warmup en arrière-plan une seule fois par process."""
    global _startup_warmup_started
    if not AUTO_WARMUP_ON_START:
        return
    with _startup_warmup_lock:
        if _startup_warmup_started:
            return
        _startup_warmup_started = True
    app.logger.info("startup warmup: started")

    def _run():
        started = time.time()
        try:
            result = _prime_homepage_cache()
            elapsed_ms = round((time.time() - started) * 1000, 1)
            app.logger.info("startup warmup: done in %sms (redis=%s)", elapsed_ms, result.get("redis_enabled"))
        except Exception:
            app.logger.exception("startup warmup: failed")

    t = threading.Thread(target=_run, name="startup-warmup", daemon=True)
    t.start()


def _build_home_payload() -> dict:
    """Construit le payload JSON de la home (consommable par un frontend séparé)."""
    results = {
        "profile": None,
        "gain": None,
        "portfolio": None,
        "portfolio_instruments": [],
        "most_copied": [],
        "current_copiers": None,
    }
    executor = ThreadPoolExecutor(max_workers=6)
    try:
        future_to_key = {
            executor.submit(
                _cached_call,
                "index_profile",
                180.0,
                lambda: get_user_profile(TRADER_USERNAME),
                None,
            ): "profile",
            executor.submit(
                _cached_call,
                "index_gain",
                180.0,
                lambda: _filter_gain_from_date(get_user_gain(TRADER_USERNAME)),
                None,
            ): "gain",
            executor.submit(
                _cached_call,
                "index_portfolio",
                180.0,
                lambda: get_user_portfolio(TRADER_USERNAME),
                None,
            ): "portfolio",
            executor.submit(
                _cached_call,
                "index_portfolio_instruments",
                180.0,
                lambda: get_portfolio_instruments(TRADER_USERNAME),
                [],
            ): "portfolio_instruments",
            executor.submit(
                _cached_call,
                "index_most_copied_100",
                300.0,
                lambda: get_most_copied_traders(100),
                [],
            ): "most_copied",
            executor.submit(
                _cached_call,
                "index_current_copiers",
                90.0,
                lambda: get_current_copiers(TRADER_USERNAME),
                None,
            ): "current_copiers",
        }
        done, not_done = wait(
            future_to_key.keys(),
            timeout=INDEX_EXTERNAL_FETCH_TIMEOUT_SEC,
        )
        for future in done:
            results[future_to_key[future]] = future.result()
        for future in not_done:
            future.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    gain = results["gain"]
    chart_labels, chart_datasets = _cached_call(
        "index_chart_data",
        300.0,
        lambda: _compute_chart_data(gain, [], include_sp500=True),
        ([], []),
    )
    performance_yearly, performance_total = _cached_call(
        "index_performance_table",
        300.0,
        lambda: _build_performance_table(gain),
        ([], None),
    )
    perf_since_sep2022 = _cached_call(
        "index_perf_since_sep2022",
        300.0,
        lambda: _build_since_sep2022_summary(gain),
        None,
    )
    _dca_init, _dca_mo = 1000.0, 100.0
    dca_labels, dca_romainroth, dca_sp500 = _cached_call(
        "index_dca_default",
        300.0,
        lambda: _compute_dca_simulation(gain, _dca_init, _dca_mo),
        ([], [], []),
    )
    dca_total_invested = _dca_init + len(dca_labels) * _dca_mo if dca_labels else None

    return {
        "username": TRADER_USERNAME,
        "profile": results["profile"],
        "gain": gain,
        "portfolio": results["portfolio"],
        "portfolio_instruments": results["portfolio_instruments"],
        "most_copied_traders": results["most_copied"],
        "current_copiers": results["current_copiers"],
        "chart_labels": chart_labels,
        "chart_datasets": chart_datasets,
        "performance_yearly": performance_yearly,
        "performance_total": performance_total,
        "perf_since_sep2022": perf_since_sep2022,
        "dca_labels": dca_labels,
        "dca_romainroth": dca_romainroth,
        "dca_sp500": dca_sp500,
        "dca_total_invested": dca_total_invested,
        "trader_posts": [],
    }


@app.route("/api/v1/home")
def api_v1_home():
    _start_background_warmup_once()
    payload = _build_home_payload()
    return jsonify(payload)


@app.route("/api/v1/auth/login", methods=["POST"])
def api_v1_auth_login():
    data = request.get_json(silent=True) or {}
    password = (data.get("password") or "").strip()
    if not API_AUTH_PASSWORD:
        return jsonify({"ok": False, "error": "auth_not_configured"}), 503
    if password != API_AUTH_PASSWORD:
        return jsonify({"ok": False, "error": "invalid_credentials"}), 401
    session["api_auth"] = True
    session["api_auth_at"] = int(time.time())
    return jsonify({"ok": True})


@app.route("/api/v1/auth/logout", methods=["POST"])
def api_v1_auth_logout():
    session.pop("api_auth", None)
    session.pop("api_auth_at", None)
    return jsonify({"ok": True})


@app.route("/api/v1/auth/me")
def api_v1_auth_me():
    return jsonify({"authenticated": _is_authenticated()})


@app.route("/")
def index():
    _start_background_warmup_once()
    payload = _build_home_payload()
    resp = make_response(render_template(
        "profile.html",
        profile=payload["profile"],
        gain=payload["gain"],
        portfolio=payload["portfolio"],
        portfolio_instruments=payload["portfolio_instruments"],
        username=payload["username"],
        chart_labels=payload["chart_labels"],
        chart_datasets=payload["chart_datasets"],
        performance_yearly=payload["performance_yearly"],
        performance_total=payload["performance_total"],
        perf_since_sep2022=payload["perf_since_sep2022"],
        most_copied_traders=payload["most_copied_traders"],
        dca_labels=payload["dca_labels"],
        dca_romainroth=payload["dca_romainroth"],
        dca_sp500=payload["dca_sp500"],
        dca_total_invested=payload["dca_total_invested"],
        trader_posts=payload["trader_posts"],
        current_copiers=payload["current_copiers"],
    ))
    _get_or_set_visitor_id(resp)
    return resp


@app.route("/internal/warmup", methods=["POST", "GET"])
def internal_warmup():
    """Préchauffe les caches pour éviter les premiers chargements lents après déploiement."""
    if WARMUP_TOKEN and not _is_authenticated():
        provided = (
            request.headers.get("X-Warmup-Token")
            or request.args.get("token")
            or ""
        ).strip()
        if provided != WARMUP_TOKEN:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
    started = time.time()
    result = _prime_homepage_cache()
    result["duration_ms"] = round((time.time() - started) * 1000, 1)
    return jsonify(result)


@app.route("/api/dca-simulation")
def api_dca_simulation():
    """Recalcule la courbe DCA (montant initial + mensuel paramétrables)."""
    try:
        initial = float(request.args.get("initial", 1000))
        monthly = float(request.args.get("monthly", 100))
    except (TypeError, ValueError):
        return jsonify({"error": "Montants invalides"}), 400
    initial = max(0.0, min(initial, 50_000_000.0))
    monthly = max(0.0, min(monthly, 5_000_000.0))
    try:
        gain = get_user_gain(TRADER_USERNAME)
        gain = _filter_gain_from_date(gain)
    except Exception:
        gain = None
    try:
        labels, romainroth, sp500 = _compute_dca_simulation(gain, initial, monthly)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    n = len(labels)
    total_invested = float(initial + n * monthly) if n else None
    return jsonify(
        {
            "labels": labels,
            "romainroth": romainroth,
            "sp500": sp500,
            "total_invested": total_invested,
        }
    )


@app.route("/api/most-copied-traders")
def api_most_copied():
    """Retourne la liste des 10 traders les plus copiés."""
    try:
        traders = get_most_copied_traders(100)
        return jsonify(traders)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/instruments-by-exchange")
def api_instruments_by_exchange():
    """Retourne les instruments groupés par place de marché."""
    try:
        by_exchange = get_instruments_by_exchange(max_pages=10)
        return jsonify(by_exchange)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/all-stocks")
def api_all_stocks():
    """Retourne toutes les actions disponibles avec numérotation."""
    try:
        stocks = get_all_stocks(max_pages=50)
        stocks = [s for s in stocks if (s.get("instrumentId") or 0) >= 1001]
        numbered = [dict(n=i + 1, **s) for i, s in enumerate(stocks)]
        return jsonify({"stocks": numbered})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trader-posts")
def api_trader_posts():
    """Pagination des posts (filtré par langue UI), sans tout envoyer au client."""
    try:
        offset = max(0, int(request.args.get("offset", 0)))
        limit = max(1, min(100, int(request.args.get("limit", 4))))
    except (TypeError, ValueError):
        offset, limit = 0, 4
    lang = request.args.get("lang", "en")
    if lang not in ("fr", "en"):
        lang = "en"
    posts = _get_all_trader_posts_cached()
    filtered = _filter_posts_by_ui_lang(posts, lang)
    slice_posts = filtered[offset : offset + limit]
    return jsonify(
        {
            "posts": slice_posts,
            "total": len(filtered),
            "total_all": len(posts),
            "offset": offset,
            "has_more": offset + limit < len(filtered),
        }
    )


@app.route("/api/cookie-consent", methods=["POST"])
def api_cookie_consent():
    """Enregistre le choix de consentement cookies en base (SQLite) et pose visitor_id si besoin."""
    data = request.get_json(silent=True) or {}
    choice = (data.get("choice") or "").strip().lower()
    if choice not in ("accepted", "necessary"):
        return jsonify({"ok": False, "error": "invalid choice"}), 400
    lang = (data.get("lang") or "").strip()[:16]
    ua = (request.headers.get("User-Agent") or "")[:512]
    ip = _get_client_ip()
    resp = make_response(jsonify({"ok": True}))
    visitor_id = _get_or_set_visitor_id(resp)
    try:
        _log_cookie_consent_to_db(choice, visitor_id, lang, ua, ip)
    except Exception:
        app.logger.exception("cookie_consent_db")
        return jsonify({"ok": False, "error": "storage"}), 500
    return resp


@app.route("/api/newsletter-subscribe", methods=["POST"])
def api_newsletter_subscribe():
    """Inscription newsletter : enregistrement dans contact_messages (SQLite)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid_request"}), 400
    if (data.get("company") or "").strip():
        return jsonify({"ok": True})
    ip = _get_client_ip()
    if not _newsletter_rate_ok(ip):
        return jsonify({"ok": False, "error": "rate_limit"}), 429
    opt_in = data.get("newsletter_opt_in")
    if opt_in is not True and str(opt_in).lower() not in ("true", "1", "yes", "on"):
        return jsonify({"ok": False, "error": "opt_in_required"}), 400
    email = (data.get("email") or "").strip().lower()
    if not email or len(email) > 255 or "@" not in email or email.count("@") != 1:
        return jsonify({"ok": False, "error": "invalid_email"}), 400
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return jsonify({"ok": False, "error": "invalid_email"}), 400
    name = (data.get("name") or "").strip()
    if len(name) > 200:
        return jsonify({"ok": False, "error": "invalid_name"}), 400
    first_name, last_name = _split_subscriber_name(name)
    msg = (
        "Newsletter opt-in: user requested regular updates by email; "
        "privacy respected; details not shared with third parties."
    )
    try:
        _insert_contact_message(
            first_name or None,
            last_name or None,
            email,
            "Newsletter",
            msg,
        )
    except Exception:
        app.logger.exception("newsletter_subscribe_db")
        return jsonify({"ok": False, "error": "storage"}), 500
    _newsletter_rate_record(ip)
    return jsonify({"ok": True})


@app.route("/newsletter/unsubscribe", methods=["GET"])
def newsletter_unsubscribe():
    email = (request.args.get("email") or "").strip().lower()
    token = (request.args.get("token") or "").strip()
    if not email or "@" not in email or not _is_valid_newsletter_unsubscribe_token(email, token):
        return Response(
            "<h1>Invalid unsubscribe link</h1><p>Please request a new newsletter email and try again.</p>",
            status=400,
            mimetype="text/html",
        )
    try:
        _mark_newsletter_unsubscribed(email)
    except Exception:
        app.logger.exception("newsletter_unsubscribe_db")
        return Response(
            "<h1>Error</h1><p>Unable to process your unsubscribe request right now.</p>",
            status=500,
            mimetype="text/html",
        )
    return Response(
        "<h1>Unsubscribed</h1><p>You have been unsubscribed from the newsletter.</p>",
        status=200,
        mimetype="text/html",
    )


# Livres recommandés (page /reading) — titres et notes FR/EN
RECOMMENDED_BOOKS: list[dict] = [
    {
        "title_fr": "Security Analysis",
        "title_en": "Security Analysis",
        "author": "Benjamin Graham et David Dodd",
        "note_en": (
            "A reference work on in-depth financial analysis and sound valuation."
        ),
        "note_fr": (
            "Un ouvrage de référence sur l’analyse financière approfondie et une valorisation rigoureuse."
        ),
    },
    {
        "title_fr": "L’investisseur intelligent",
        "title_en": "The Intelligent Investor",
        "author": "Benjamin Graham",
        "note_en": (
            "A true manual of prudence and rationality for long-term investing."
        ),
        "note_fr": (
            "Un véritable manuel de prudence et de rationalité pour l’investissement à long terme."
        ),
    },
    {
        "title_fr": "The Snowball",
        "title_en": "The Snowball",
        "author": "Alice Schroeder",
        "note_en": (
            "The official biography of Warren Buffett — a great source of inspiration for his "
            "patience, discipline, and value-based strategy."
        ),
        "note_fr": (
            "La biographie officielle de Warren Buffett — une grande source d’inspiration sur sa "
            "patience, sa discipline et sa stratégie fondée sur la valeur."
        ),
    },
    {
        "title_fr": "Le Cygne noir",
        "title_en": "The Black Swan",
        "author": "Nassim Nicholas Taleb",
        "note_en": (
            "A crucial reminder of the outsized role of rare, unpredictable events, the impossibility "
            "of accurately estimating their probabilities, and the cognitive biases that blind us to "
            "their potential impact."
        ),
        "note_fr": (
            "Un rappel crucial du rôle démesuré des événements rares et imprévisibles, de "
            "l’impossibilité d’en estimer précisément les probabilités, et des biais cognitifs qui "
            "nous empêchent d’en mesurer l’impact potentiel."
        ),
    },
]


def _site_layout_context() -> dict:
    """Profil + copieurs pour le header partagé (toutes les pages)."""
    try:
        profile = get_user_profile(TRADER_USERNAME)
    except Exception:
        profile = None
    try:
        current_copiers = get_current_copiers(TRADER_USERNAME)
    except Exception:
        current_copiers = None
    return {
        "username": TRADER_USERNAME,
        "profile": profile,
        "current_copiers": current_copiers,
    }


@app.route("/lexique")
def page_lexique():
    """Lexique et questions courantes."""
    resp = make_response(
        render_template(
            "lexique.html",
            lexique_entries=_load_lexique_entries(),
            faq_entries=_load_faq_entries(),
            **_site_layout_context(),
        )
    )
    _get_or_set_visitor_id(resp)
    return resp


@app.route("/about")
def page_about():
    """Présentation personnelle (qui suis-je, pourquoi suivre le profil)."""
    resp = make_response(
        render_template(
            "about.html",
            **_site_layout_context(),
        )
    )
    _get_or_set_visitor_id(resp)
    return resp


@app.route("/mentions-legales")
def mentions_legales():
    """Mentions légales (éditeur, hébergeur, responsabilité)."""
    resp = make_response(
        render_template(
            "mentions_legales.html",
            **_site_layout_context(),
        )
    )
    _get_or_set_visitor_id(resp)
    return resp


@app.route("/confidentialite")
def confidentialite():
    """Politique de confidentialité (données personnelles, RGPD)."""
    resp = make_response(
        render_template(
            "confidentialite.html",
            **_site_layout_context(),
        )
    )
    _get_or_set_visitor_id(resp)
    return resp


@app.route("/confidentialite/")
@app.route("/confidentalite")
@app.route("/confidentalite/")
def confidentialite_alias():
    """Alias de compatibilité vers la politique de confidentialité."""
    return redirect(url_for("confidentialite"), code=301)


@app.route("/cookies")
def cookies():
    """Politique de cookies."""
    resp = make_response(
        render_template(
            "cookies.html",
            **_site_layout_context(),
        )
    )
    _get_or_set_visitor_id(resp)
    return resp


@app.route("/reading")
def page_reading():
    """Livres recommandés (finance & investissement)."""
    resp = make_response(
        render_template(
            "reading.html",
            books=RECOMMENDED_BOOKS,
            **_site_layout_context(),
        )
    )
    _get_or_set_visitor_id(resp)
    return resp


@app.route("/learning")
def page_learning_redirect():
    """Ancienne URL : redirige vers /reading."""
    return redirect(url_for("page_reading"), code=301)


@app.route("/copy-on-etoro")
def page_copy_on_etoro():
    """Page CopyOnEtoro (copy trading eToro)."""
    join_url = (os.getenv("ETORO_JOIN_URL") or "https://etoro.tw/46rrJQC").strip()
    resp = make_response(
        render_template(
            "copy_on_etoro.html",
            etoro_join_url=join_url,
            **_site_layout_context(),
        )
    )
    _get_or_set_visitor_id(resp)
    return resp


def _compute_posts_chart_data(traders: list[str], years: int = 1) -> tuple[list[str], list[dict]]:
    """Calcule les posts par mois par trader (dernière année). Même logique que _compute_chart_data."""
    from datetime import datetime, timezone, timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=years * 365)).strftime("%Y-%m")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m")
    all_months = []
    m = cutoff
    while m <= now_str:
        all_months.append(m)
        y, mo = int(m[:4]), int(m[5:7])
        mo += 1
        if mo > 12:
            mo, y = 1, y + 1
        m = f"{y:04d}-{mo:02d}"

    traders_data: dict[str, dict[str, int]] = {}
    for username in traders:
        if not username or username in traders_data:
            continue
        by_month = get_posts_per_month(username, years=years, max_pages=20)
        traders_data[username] = by_month

    colors = [
        "#58a6ff", "#3fb950", "#f0883e", "#a371f7", "#ff7b72",
        "#79c0ff", "#7ee787", "#d2a8ff", "#ffa657", "#56d4dd",
    ]
    datasets = []
    for i, (name, by_month) in enumerate(traders_data.items()):
        values = [by_month.get(m, 0) for m in all_months]
        datasets.append({
            "label": name,
            "data": values,
            "color": colors[i % len(colors)],
        })
    return all_months, datasets


@app.route("/api/posts-chart-data")
def api_posts_chart_data():
    """Retourne les posts par mois par trader. Même logique que chart-data (RomainRoth + traders ajoutés)."""
    traders = request.args.get("traders", "").strip().split(",")
    traders = [t.strip() for t in traders if t.strip()]
    if not traders:
        traders = [TRADER_USERNAME]
    if TRADER_USERNAME not in traders:
        traders = [TRADER_USERNAME] + traders
    try:
        labels, datasets = _compute_posts_chart_data(traders, years=1)
        return jsonify({"labels": labels, "datasets": datasets})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _ensure_romainroth_in_points(points: list[dict]) -> list[dict]:
    """Ajoute RomainRoth aux points si absent."""
    if any(p.get("userName") == TRADER_USERNAME for p in points):
        return points
    try:
        from etoro_client import get_current_copiers
        copiers = get_current_copiers(TRADER_USERNAME) or 0
        gain = get_user_gain(TRADER_USERNAME)
        by_month = _gain_to_by_month(_filter_gain_from_date(gain))
        perf = _compute_cumulative_index(by_month)
        if perf is not None and copiers is not None:
            return [{"userName": TRADER_USERNAME, "copiers": copiers, "gain": perf}] + points
    except Exception:
        pass
    return points


@app.route("/api/copiers-vs-performance")
def api_copiers_vs_performance():
    """Retourne les points (copiers, gain), romainroth_index et sp500_index pour les lignes de référence."""
    try:
        refresh = request.args.get("refresh", "").lower() in ("1", "true")
        points = _load_copiers_vs_performance_cached(refresh=refresh)
        points = _ensure_romainroth_in_points(points)
        romainroth_point = next((p for p in points if p.get("userName") == TRADER_USERNAME), None)
        romainroth_index = romainroth_point["gain"] if romainroth_point and romainroth_point.get("gain") else None
        sp500_returns = _get_sp500_monthly_returns()
        sp500_index = _compute_cumulative_index(sp500_returns) if sp500_returns else None
        points_no_main = [p for p in points if p.get("userName") != TRADER_USERNAME]
        return jsonify({
            "points": points_no_main,
            "romainroth_index": romainroth_index,
            "sp500_index": sp500_index,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chart-data")
def api_chart_data():
    """Retourne les données du graphique pour les traders sélectionnés."""
    traders = request.args.get("traders", "").strip().split(",")
    traders = [t.strip() for t in traders if t.strip()]
    include_sp500 = request.args.get("sp500", "true").lower() == "true"
    indices = request.args.get("indices", "").strip().split(",")
    indices = [i.strip() for i in indices if i.strip()]
    try:
        gain = get_user_gain(TRADER_USERNAME)
        gain = _filter_gain_from_date(gain)
        labels, datasets = _compute_chart_data(
            gain, traders, include_sp500=include_sp500, extra_indices=indices
        )
        return jsonify({"labels": labels, "datasets": datasets})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    """Route de diagnostic sans appel API externe."""
    _start_background_warmup_once()
    return "OK", 200


@app.route("/db-test")
def db_test():
    try:
        conn = get_pg_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        result = cur.fetchone()
        cur.close()
        conn.close()
        return f"DB OK: {result}"
    except Exception as e:
        return f"DB ERROR: {e}"


IMAGES_DIR = os.path.join(os.path.dirname(__file__), "images")


@app.route("/images/<path:filename>")
def serve_image(filename: str):
    """Sert une image du dossier images/ (ex: chatbot.png)."""
    if ".." in filename or "/" in filename:
        return jsonify({"error": "invalid"}), 400
    return send_from_directory(IMAGES_DIR, filename)


@app.route("/favicon.svg")
def favicon_round_svg():
    """Favicon circulaire : SVG avec la photo intégrée en data URI (pas de requête externe)."""
    path = _trader_avatar_disk_path()
    if not path:
        return Response("", 404)
    data_uri = (
        f"data:{_mime_for_image_path(path)};base64,"
        + base64.b64encode(path.read_bytes()).decode("ascii")
    )
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<defs><clipPath id="c"><circle cx="16" cy="16" r="16"/></clipPath></defs>'
        f'<image href="{data_uri}" x="0" y="0" width="32" height="32" '
        'preserveAspectRatio="xMidYMid slice" clip-path="url(#c)"/>'
        "</svg>"
    )
    return Response(svg, mimetype="image/svg+xml")


@app.route("/api/trader-post-image/<filename>")
def api_trader_post_image(filename: str):
    """Sert une image de post trader sauvegardée localement."""
    if ".." in filename or "/" in filename:
        return jsonify({"error": "invalid"}), 400
    return send_from_directory(TRADER_POST_IMAGES_DIR, filename)


@app.route("/api/post-to-etoro", methods=["POST"])
def api_post_to_etoro():
    """
    Crée un post sur le feed eToro (titre + résumé + image optionnelle).
    Body JSON: { "title": "...", "summary": "...", "image_url": "..." (optionnel) }
    L'image_url peut être relative ; elle est convertie en URL absolue.
    """
    auth_error = _require_mutation_auth_if_enabled()
    if auth_error is not None:
        return auth_error
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    summary = (data.get("summary") or "").strip()
    image_url = (data.get("image_url") or "").strip() or None
    # Forcer la date du jour en tête du résumé si une date YYYY-MM-DD y figure (évite cache/LLM obsolète)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if summary:
        first_line = summary.strip().split("\n")[0].strip()
        if len(first_line) == 10 and first_line[:4].isdigit() and first_line[4] == "-" and first_line[5:7].isdigit() and first_line[7] == "-" and first_line[8:10].isdigit():
            rest = summary.strip()[len(first_line):].lstrip("\n\r")
            summary = f"{today}\n{rest}" if rest else today
    message = f"{title}\n\n{summary}".strip()
    if not message:
        return jsonify({"success": False, "error": "title et summary requis"}), 400
    if image_url and image_url.startswith("data:"):
        image_url = None  # eToro attend une URL publique, pas une data URL
    if image_url and image_url.startswith("/"):
        base = request.host_url.rstrip("/")
        image_url = f"{base}{image_url}"
    # eToro ne peut pas charger une image en localhost → post sans image pour éviter image blanche
    if image_url and ("127.0.0.1" in image_url or "localhost" in image_url):
        image_url = None
    result = etoro_create_post(message, image_url=image_url)
    if result is None:
        return jsonify({"success": False, "error": "Échec de l’API eToro (vérifier clés et URL image publique)"}), 502
    if result.get("_api_error"):
        err = result.get("error") or {}
        msg = err.get("errorMessage", "")
        code = err.get("errorCode", "")
        if code and msg:
            err_msg = f"{code}: {msg}"
        else:
            err_msg = msg or str(err) or "Permission refusée par eToro"
        return jsonify({"success": False, "error": err_msg}), 502
    _append_etoro_published_post(title, summary, image_url)
    return jsonify({"success": True, "post": result, "image_url_sent": image_url})


def _append_etoro_published_post(title: str, summary: str, image_url: str | None) -> None:
    """Enregistre en mémoire les posts envoyés avec succès à l'API eToro (seule persistance des posts)."""
    try:
        os.makedirs(os.path.dirname(ETORO_PUBLISHED_POSTS_PATH), exist_ok=True)
        posts: list[dict] = []
        if os.path.exists(ETORO_PUBLISHED_POSTS_PATH):
            with open(ETORO_PUBLISHED_POSTS_PATH, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    posts = data
        posts.append({
            "published_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "title": (title or "").strip(),
            "summary": (summary or "").strip(),
            "image_url": (image_url or "").strip() or None,
        })
        with open(ETORO_PUBLISHED_POSTS_PATH, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _append_chat_question(question: str, reply: str) -> None:
    if not question.strip():
        return

    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_questions (created_at, question, reply)
                VALUES (NOW(), %s, %s)
                """,
                (
                    question.strip(),
                    (reply or "").strip(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _load_chat_questions() -> list[dict]:
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT created_at, question, reply
                FROM chat_questions
                ORDER BY created_at DESC
                LIMIT 1000
                """
            )
            rows = cur.fetchall()

        return [
            {
                "timestamp": r[0].isoformat(),
                "question": r[1],
                "reply": r[2],
            }
            for r in rows
        ]
    finally:
        conn.close()


def _load_trader_posts_local(limit: int | None = None) -> list[dict]:
    """Charge les posts du trader depuis le JSON local."""
    if not os.path.exists(TRADER_POSTS_PATH):
        return []
    try:
        with open(TRADER_POSTS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        posts = data.get("posts") if isinstance(data, dict) else None
        if not isinstance(posts, list):
            return []
        cleaned: list[dict] = []
        for p in posts:
            if not isinstance(p, dict):
                continue
            message = str(p.get("message") or "").strip()
            created = str(p.get("created") or "").strip()
            image_url = str(p.get("image_url") or "").strip() or None
            if image_url and image_url.startswith("/api/trader-post-image/") and not image_url.lower().endswith(".webp"):
                stem = Path(image_url).stem
                webp_name = f"{stem}.webp"
                if Path(TRADER_POST_IMAGES_DIR, webp_name).is_file():
                    image_url = f"/api/trader-post-image/{webp_name}"
            if not message:
                continue
            cleaned.append({
                "id": str(p.get("id") or ""),
                "created": created,
                "message": message,
                "image_url": image_url,
            })
        cleaned.sort(key=lambda x: x.get("created", ""), reverse=True)
        if limit is None:
            return cleaned
        return cleaned[: max(0, limit)]
    except Exception:
        return []


def _trader_posts_json_mtime() -> float | None:
    try:
        return os.path.getmtime(TRADER_POSTS_PATH)
    except OSError:
        return None


def _get_all_trader_posts_cached() -> list[dict]:
    global _trader_posts_cache, _trader_posts_loaded_mtime
    mtime = _trader_posts_json_mtime()
    if (
        _trader_posts_cache is not None
        and mtime == _trader_posts_loaded_mtime
    ):
        return _trader_posts_cache
    with _cache_lock:
        mtime = _trader_posts_json_mtime()
        if (
            _trader_posts_cache is not None
            and mtime == _trader_posts_loaded_mtime
        ):
            return _trader_posts_cache
        _trader_posts_cache = _load_trader_posts_local(limit=None)
        _trader_posts_loaded_mtime = mtime
    return _trader_posts_cache


def _infer_post_lang(message: str) -> str:
    """Même heuristique que le filtre langue côté client (profile.html)."""
    if not message:
        return "en"
    if re.search(r"Avertissement sur les risques", message, re.I):
        return "fr"
    if re.search(r"𝘙𝘪𝘴𝘬\s*𝘞𝘢𝘳𝘯𝘪𝘯𝘨|Risk Warning", message, re.I):
        return "en"
    head = message[:1200]
    if re.search(r"[äöüßÄÖÜ]", head) and re.search(
        r"\b(und |der |die |Das |für |nicht )\b", head, re.I
    ):
        return "de"
    sample = message[:4000]
    fr = 0
    en = 0
    if re.search(r"[àâäéèêëïîôùûüçœ]", sample, re.I):
        fr += 3
    fr += len(
        re.findall(
            r"\b(les|des|une|dans|pour|avec|sont|été|notre|votre|être|copieurs|mois|portefeuille|marchés|français|été)\b",
            sample,
            re.I,
        )
    )
    en += len(
        re.findall(
            r"\b(the|and|with|from|this|that|have|been|will|our|were|copiers|portfolio|markets|month|Hello)\b",
            sample,
            re.I,
        )
    )
    if fr > en + 2:
        return "fr"
    if en > fr + 2:
        return "en"
    return "fr" if fr >= en else "en"


def _post_title_line(message: str) -> str:
    if not message:
        return ""
    for line in message.splitlines():
        line = line.strip()
        if line:
            if len(line) > 160:
                return line[:157] + "…"
            return line
    return ""


def _filter_posts_by_ui_lang(posts: list[dict], ui_lang: str) -> list[dict]:
    if ui_lang not in ("fr", "en"):
        ui_lang = "en"
    out: list[dict] = []
    for p in posts:
        if not isinstance(p, dict):
            continue
        msg = str(p.get("message") or "")
        lang = _infer_post_lang(msg)
        if lang == "de":
            continue
        if ui_lang == "fr" and lang == "fr":
            out.append(p)
        elif ui_lang == "en" and lang == "en":
            out.append(p)
    return out


def _load_lexique_entries() -> list[dict]:
    global _lexique_json_cache
    if _lexique_json_cache is not None:
        return _lexique_json_cache
    if not os.path.exists(LEXIQUE_PATH):
        _lexique_json_cache = []
        return _lexique_json_cache
    try:
        with open(LEXIQUE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _lexique_json_cache = data if isinstance(data, list) else []
    except Exception:
        _lexique_json_cache = []
    return _lexique_json_cache


def _load_faq_entries() -> list[dict]:
    global _faq_json_cache
    if _faq_json_cache is not None:
        return _faq_json_cache
    if not os.path.exists(FAQ_PATH):
        _faq_json_cache = []
        return _faq_json_cache
    try:
        with open(FAQ_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _faq_json_cache = data if isinstance(data, list) else []
    except Exception:
        _faq_json_cache = []
    return _faq_json_cache


def _load_chatbot_resources(filename: str) -> str:
    """Charge une liste titre|URL depuis prompts/<filename> et retourne une chaîne formatée."""
    path = os.path.join(os.path.dirname(__file__), "prompts", filename)
    try:
        lines = []
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                lines.append(line)
        if lines:
            return "\n".join(f"- {line.split('|', 1)[0].strip()} : {line.split('|', 1)[1].strip()}" for line in lines)
    except Exception:
        pass
    return ""


def _load_chatbot_resource_pairs(filename: str) -> list[tuple[str, str]]:
    """Charge une liste titre|URL depuis prompts/<filename>."""
    path = os.path.join(os.path.dirname(__file__), "prompts", filename)
    pairs: list[tuple[str, str]] = []
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "|" not in line:
                continue
            title, url = line.split("|", 1)
            title = title.strip()
            url = url.strip()
            if title and url:
                pairs.append((title, url))
    except Exception:
        pass
    return pairs


def _load_chatbot_books() -> str:
    """Charge la liste des livres depuis prompts/chatbot_books.txt."""
    return _load_chatbot_resources("chatbot_books.txt")


def _load_chatbot_videos() -> str:
    """Charge la liste des vidéos YouTube depuis prompts/chatbot_videos.txt."""
    return _load_chatbot_resources("chatbot_videos.txt")


def _load_chatbot_citations() -> str:
    """Charge les citations Buffett depuis prompts/citation_buffet.txt."""
    path = os.path.join(os.path.dirname(__file__), "prompts", "citation_buffet.txt")
    try:
        quotes: list[str] = []
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Supprime un éventuel préfixe "12. "
            line = re.sub(r"^\d+\.\s*", "", line)
            if line:
                quotes.append(line)
        if quotes:
            return "\n".join(f"- {q}" for q in quotes)
    except Exception:
        pass
    return ""


def _load_chatbot_citation_list() -> list[str]:
    """Charge les citations Buffett en liste brute."""
    path = os.path.join(os.path.dirname(__file__), "prompts", "citation_buffet.txt")
    quotes: list[str] = []
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = re.sub(r"^\d+\.\s*", "", line).strip()
            if line:
                quotes.append(line)
    except Exception:
        pass
    return quotes


def _is_out_of_scope_finance_refusal(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized.startswith("je peux seulement répondre à des questions de finance")


def _ensure_risk_reminder(text: str) -> str:
    """Ajoute un rappel risque si absent dans une réponse finance."""
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned

    lower = cleaned.lower()
    risk_markers = [
        "risque de perte",
        "perte en capital",
        "performances passées",
        "ne constituent pas un conseil en investissement",
    ]
    if any(marker in lower for marker in risk_markers):
        return cleaned

    reminder = (
        "Rappel: les performances passées ne garantissent pas les performances futures "
        "et tout investissement comporte un risque de perte en capital."
    )
    return f"{cleaned}\n\n{reminder}"


def _append_alternating_chat_tail(reply: str, messages: list[dict]) -> str:
    """
    Alterne automatiquement les fins de réponse:
    - 1re réponse assistant: vidéo
    - 2e réponse assistant: citation Buffett
    - puis alternance stricte.
    """
    if not reply.strip() or _is_out_of_scope_finance_refusal(reply):
        return reply

    reply = _ensure_risk_reminder(reply)

    prior_assistant_count = sum(1 for m in messages if (m.get("role") or "").strip() == "assistant")
    turn_index = prior_assistant_count // 2

    if prior_assistant_count % 2 == 0:
        videos = _load_chatbot_resource_pairs("chatbot_videos.txt")
        if not videos:
            return reply
        title, url = videos[turn_index % len(videos)]
        tail = f"[{title}]({url})"
    else:
        quotes = _load_chatbot_citation_list()
        if not quotes:
            return reply
        quote = quotes[turn_index % len(quotes)]
        tail = f"\"{quote}\" - Warren Buffett"

    cleaned = reply.rstrip()
    if cleaned.endswith(tail):
        return cleaned
    return f"{cleaned}\n\n{tail}"


def _load_chatbot_prompt() -> str:
    """Charge le prompt système du chatbot depuis prompts/chatbot_system.txt + ressources."""
    base = os.path.join(os.path.dirname(__file__), "prompts")
    try:
        with open(os.path.join(base, "chatbot_system.txt"), encoding="utf-8") as f:
            prompt = f.read().strip()
    except Exception:
        prompt = (
            "Tu es un assistant financier. Réponds de façon concise, dans la même langue que la question de l'utilisateur."
        )
    books = _load_chatbot_books()
    if books:
        prompt += "\n\nLivres que tu peux recommander (propose le lien quand tu cites un livre) :\n" + books
    videos = _load_chatbot_videos()
    if videos:
        prompt += "\n\nVidéos YouTube que tu peux recommander (propose le lien quand tu cites une vidéo) :\n" + videos
    citations = _load_chatbot_citations()
    if citations:
        prompt += "\n\nCitations de Warren Buffett que tu peux utiliser :\n" + citations
    return prompt


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Chatbot OpenAI : envoie les messages et retourne la réponse. Rate limit par visitor_id. CAPTCHA si requis."""
    visitor_id = _get_or_set_visitor_id()
    data = request.get_json() or {}
    messages = data.get("messages") or []
    if not messages:
        return jsonify({"error": "messages requis"}), 400
    user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
    current_message = (user_msgs[-1] or "").strip() if user_msgs else ""

    abnormal = _detect_abnormal_behavior(messages, current_message)
    if abnormal:
        r = jsonify({"error": abnormal})
        _get_or_set_visitor_id(r)
        return r, 429

    if _should_require_captcha(visitor_id, current_message):
        secret = os.getenv("RECAPTCHA_SECRET_KEY")
        if secret:
            token = (data.get("captcha_token") or "").strip()
            if not _verify_recaptcha(token):
                r = jsonify({
                    "error": "Veuillez valider le CAPTCHA pour continuer.",
                    "require_captcha": True,
                })
                _get_or_set_visitor_id(r)
                return r, 429

    if not _check_chat_rate_limit(visitor_id):
        r = jsonify({"error": "Trop de requêtes. Limites : 5/min, 30/h, 100/j par visiteur."})
        _get_or_set_visitor_id(r)
        return r, 429
    from openai import APITimeoutError, OpenAI
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return jsonify({"error": "OPENAI_API_KEY manquante"}), 500
    system_prompt = _load_chatbot_prompt()
    try:
        client = OpenAI(api_key=key, timeout=CHAT_OPENAI_TIMEOUT_SEC)
        history = messages[-MAX_HISTORY_MESSAGES:] if len(messages) > MAX_HISTORY_MESSAGES else messages
        api_messages = [{"role": "system", "content": system_prompt}] + [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in history
        ]
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=api_messages,
            temperature=0.7,
            max_tokens=MAX_COMPLETION_TOKENS,
        )
        reply = (r.choices[0].message.content or "").strip()
        reply = _append_alternating_chat_tail(reply, messages)
        if len(reply) > MAX_REPLY_CHARS:
            reply = reply[: MAX_REPLY_CHARS - 3].rstrip() + "…"
        if user_msgs:
            _append_chat_question(user_msgs[-1], reply)
            if visitor_id not in _visitor_recent_messages:
                _visitor_recent_messages[visitor_id] = []
            _visitor_recent_messages[visitor_id] = (
                _visitor_recent_messages[visitor_id][-2:] + [current_message]
            )[:3]
        resp = jsonify({"reply": reply})
        _get_or_set_visitor_id(resp)
        return resp
    except APITimeoutError:
        err = jsonify({
            "error": "Délai dépassé (8 s). Réessayez ou reformulez une question plus courte.",
        })
        _get_or_set_visitor_id(err)
        return err, 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat-questions")
def api_chat_questions():
    """Exporte les questions du chatbot en JSON ou CSV."""
    fmt = (request.args.get("format") or "json").strip().lower()
    rows = _load_chat_questions()
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["timestamp", "question", "reply"])
        for r in rows:
            writer.writerow([
                r.get("timestamp", ""),
                r.get("question", ""),
                r.get("reply", ""),
            ])
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=chat_questions.csv"},
        )
    return jsonify(rows)


if __name__ == "__main__":
    app.run(debug=True, port=5001, host="127.0.0.1")
