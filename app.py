"""Application Flask pour visualiser le profil des traders eToro."""

import base64
import csv
import io
import json
import os
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import Flask, Response, g, make_response, jsonify, render_template, request, send_from_directory
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
)
from zone_bourse.news_fetcher import ZONEBOURSE_IMAGES_DIR, get_latest_news

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

app = Flask(__name__)
TRADER_USERNAME = "RomainRoth"
DATE_FROM = "2022-09"  # Données à partir de septembre 2022
COPIERS_VS_PERF_CACHE = os.path.join(os.path.dirname(__file__), "data", "copiers_vs_performance.json")
CHAT_QUESTIONS_LOG = os.path.join(os.path.dirname(__file__), "data", "chat_questions.jsonl")
NEWS_MEDIASTACK_PATH = os.path.join(os.path.dirname(__file__), "data", "news_mediastack.json")

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


def _best_keyword_for_instrument(instr: dict) -> str | None:
    """Retourne le meilleur mot-clé pour chercher des actualités sur cet instrument."""
    sym = (instr.get("symbol") or "").strip().upper()
    disp = (instr.get("displayname") or "").strip()
    # Priorité au symbole pour les actions (AAPL, TSLA, NVDA, SPY) — très fréquent dans les titres
    if sym and 2 <= len(sym) <= 6 and sym.isalpha():
        return sym
    # Fallback : premier mot significatif du displayname (ex. "Apple Inc" -> "Apple")
    if disp:
        words = [w for w in disp.split() if len(w) > 1 and w.lower() not in ("inc", "plc", "corp", "sa", "nv")]
        return words[0] if words else disp.split()[0] if disp.split() else None
    return None


def _fetch_mediastack_instrument_news(instruments: list[dict], limit: int = 3) -> list[dict]:
    """
    Récupère les N dernières actualités Mediastack pour les instruments du portefeuille.
    Priorité maximale : une requête par instrument (symbol ou displayname) pour des news ciblées.
    Retourne une liste de {title, description, url, source, published_at}.
    """
    key = os.getenv("MEDIASTACK_ACCESS_KEY")
    if not key:
        return []

    def _do_request(params: dict, use_https: bool = True) -> list[dict]:
        try:
            base = "https://api.mediastack.com" if use_https else "http://api.mediastack.com"
            r = requests.get(f"{base}/v1/news", params=params, timeout=10)
            if r.status_code != 200:
                return []
            data = r.json()
            err = data.get("error")
            if err:
                code = err.get("code", "") if isinstance(err, dict) else str(err)
                if use_https and code == "https_access_restricted":
                    return _do_request(params, use_https=False)
                return []
            items = data.get("data") or []
            return [
                {
                    "title": a.get("title") or "",
                    "description": a.get("description") or "",
                    "url": a.get("url") or "",
                    "source": a.get("source") or "",
                    "published_at": a.get("published_at") or "",
                }
                for a in items
            ]
        except Exception:
            return []

    base_params = {"access_key": key, "sort": "published_desc"}
    seen_urls: set[str] = set()
    collected: list[dict] = []

    # Priorité : une requête par instrument avec son meilleur mot-clé (symbol > displayname)
    if instruments:
        for instr in instruments[:5]:  # max 5 instruments
            kw = _best_keyword_for_instrument(instr)
            if not kw or len(collected) >= limit:
                continue
            items = _do_request({**base_params, "keywords": kw, "limit": 2})
            for a in items:
                url = (a.get("url") or "").strip()
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    collected.append(a)
                    if len(collected) >= limit:
                        break

    if collected:
        return _translate_instrument_news_to_french(collected[:limit])

    # Fallback : mots-clés combinés (displayname/symbol des 3 premiers)
    parts = []
    for i in instruments[:4]:
        kw = _best_keyword_for_instrument(i)
        if kw:
            parts.append(kw)
    if parts:
        items = _do_request({**base_params, "keywords": parts[0], "limit": limit})
        if items:
            return _translate_instrument_news_to_french(items)

    # Dernier recours : business
    items = _do_request({**base_params, "categories": "business", "limit": limit})
    if items:
        return _translate_instrument_news_to_french(items)
    return []


def _translate_instrument_news_to_french(items: list[dict]) -> list[dict]:
    """Traduit titre et description des actualités en français via OpenAI."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not items:
        return items
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        texts = []
        for a in items:
            texts.append((a.get("title") or "").strip())
            texts.append((a.get("description") or "").strip()[:500])
        sep = "\n|||\n"
        batch = sep.join(texts)
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Tu traduis en français. Réponds uniquement par les traductions, dans le même ordre, séparées par ||| sur une ligne. Pas de numérotation ni commentaire."},
                {"role": "user", "content": batch},
            ],
            temperature=0.2,
        )
        out = (r.choices[0].message.content or "").strip()
        parts = [p.strip() for p in out.split("|||")]
        if len(parts) >= len(texts):
            for i, a in enumerate(items):
                updated = dict(a)
                idx = i * 2
                if idx < len(parts):
                    updated["title"] = parts[idx]
                if idx + 1 < len(parts):
                    updated["description"] = parts[idx + 1]
                items[i] = updated
    except Exception:
        pass
    return items


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
    Simule un DCA : investissement initial + 100 $/mois à la fin de chaque mois.
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


@app.route("/")
def index():
    try:
        profile = get_user_profile(TRADER_USERNAME)
    except Exception:
        profile = None
    try:
        gain = get_user_gain(TRADER_USERNAME)
        gain = _filter_gain_from_date(gain)
    except Exception:
        gain = None
    try:
        portfolio = get_user_portfolio(TRADER_USERNAME)
    except Exception:
        portfolio = None
    try:
        portfolio_instruments = get_portfolio_instruments(TRADER_USERNAME)
    except Exception:
        portfolio_instruments = []
    try:
        chart_labels, chart_datasets = _compute_chart_data(gain, [], include_sp500=True)
    except Exception:
        chart_labels, chart_datasets = [], []

    try:
        performance_yearly, performance_total = _build_performance_table(gain)
    except Exception:
        performance_yearly, performance_total = [], None

    try:
        dca_labels, dca_romainroth, dca_sp500 = _compute_dca_simulation(gain)
        dca_total_invested = 1000.0 + len(dca_labels) * 100.0 if dca_labels else None
    except Exception:
        dca_labels, dca_romainroth, dca_sp500 = [], [], []
        dca_total_invested = None

    try:
        most_copied = get_most_copied_traders(100)
    except Exception:
        most_copied = []

    try:
        zonebourse_result = get_latest_news(
            limit=3,
            cache_path=os.path.join(os.path.dirname(__file__), "data", "zonebourse_posts.json"),
            generate_image_fn=_gen_zonebourse_image,
        )
        zonebourse_news = zonebourse_result.get("items", [])
        zonebourse_used_fallback = zonebourse_result.get("used_fallback", False)
    except Exception:
        zonebourse_news = []
        zonebourse_used_fallback = False

    try:
        current_copiers = get_current_copiers(TRADER_USERNAME)
    except Exception:
        current_copiers = None

    resp = make_response(render_template(
        "profile.html",
        profile=profile,
        gain=gain,
        portfolio=portfolio,
        portfolio_instruments=portfolio_instruments,
        username=TRADER_USERNAME,
        chart_labels=chart_labels,
        chart_datasets=chart_datasets,
        performance_yearly=performance_yearly,
        performance_total=performance_total,
        most_copied_traders=most_copied,
        dca_labels=dca_labels,
        dca_romainroth=dca_romainroth,
        dca_sp500=dca_sp500,
        dca_total_invested=dca_total_invested,
        zonebourse_news=zonebourse_news,
        zonebourse_used_fallback=zonebourse_used_fallback,
        current_copiers=current_copiers,
        recaptcha_site_key=os.getenv("RECAPTCHA_SITE_KEY", ""),
    ))
    _get_or_set_visitor_id(resp)
    return resp


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
    return "OK", 200


@app.route("/api/zonebourse-image/<filename>")
def api_zonebourse_image(filename: str):
    """Sert une image cachée Zonebourse (PNG)."""
    if not filename.endswith(".png") or ".." in filename or "/" in filename:
        return jsonify({"error": "invalid"}), 400
    return send_from_directory(ZONEBOURSE_IMAGES_DIR, filename, mimetype="image/png")


@app.route("/api/zonebourse-news")
def api_zonebourse_news():
    """Retourne les dernières actualités Zonebourse (pour rafraîchissement dynamique)."""
    try:
        result = get_latest_news(
            limit=3,
            cache_path=os.path.join(os.path.dirname(__file__), "data", "zonebourse_posts.json"),
            generate_image_fn=_gen_zonebourse_image,
        )
        items = result.get("items", [])
        return jsonify({"count": len(items), "news": items, "used_fallback": result.get("used_fallback", False)})
    except Exception as e:
        return jsonify({"error": str(e), "count": 0, "news": []}), 500


def _fetch_mediastack_filtered(
    category: str | None = None,
    keywords: str | None = None,
    countries: str | None = None,
    languages: str | None = None,
    sources: str | None = None,
    date_str: str | None = None,
    limit: int = 5,
    translate: bool = True,
) -> list[dict]:
    """
    Récupère les actualités Mediastack avec les filtres choisis.
    Retourne une liste de {title, description, url, source, published_at}.
    """
    key = os.getenv("MEDIASTACK_ACCESS_KEY")
    if not key:
        return []

    def _do_request(params: dict, use_https: bool = True) -> list[dict]:
        try:
            base = "https://api.mediastack.com" if use_https else "http://api.mediastack.com"
            r = requests.get(f"{base}/v1/news", params=params, timeout=10)
            if r.status_code != 200:
                return []
            data = r.json()
            err = data.get("error")
            if err:
                code = err.get("code", "") if isinstance(err, dict) else str(err)
                if use_https and code == "https_access_restricted":
                    return _do_request(params, use_https=False)
                return []
            items = data.get("data") or []
            return [
                {
                    "title": a.get("title") or "",
                    "description": a.get("description") or "",
                    "url": a.get("url") or "",
                    "source": a.get("source") or "",
                    "published_at": a.get("published_at") or "",
                }
                for a in items
            ]
        except Exception:
            return []

    base_params: dict = {"access_key": key, "limit": limit, "sort": "published_desc"}
    if category:
        base_params["categories"] = category
    if keywords:
        base_params["keywords"] = keywords
    if countries:
        base_params["countries"] = countries
    if languages:
        base_params["languages"] = languages
    if sources:
        base_params["sources"] = sources

    from datetime import date, timedelta

    dates_to_try: list[str | None] = []
    if date_str == "today":
        dates_to_try = [date.today().strftime("%Y-%m-%d")]
    elif date_str == "yesterday":
        dates_to_try = [(date.today() - timedelta(days=1)).strftime("%Y-%m-%d")]
    elif date_str == "today_and_yesterday":
        dates_to_try = [
            date.today().strftime("%Y-%m-%d"),
            (date.today() - timedelta(days=1)).strftime("%Y-%m-%d"),
        ]
    else:
        dates_to_try = [None]

    seen_urls: set[str] = set()
    collected: list[dict] = []
    for d in dates_to_try:
        params = dict(base_params)
        if d:
            params["date"] = d
            params["limit"] = 5
        items = _do_request(params)
        for a in items:
            url = (a.get("url") or "").strip()
            if url and url not in seen_urls:
                seen_urls.add(url)
                collected.append(a)
        if len(collected) >= limit:
            break

    if not collected and dates_to_try != [None]:
        params = dict(base_params)
        params["limit"] = limit
        items = _do_request(params)
        for a in items:
            url = (a.get("url") or "").strip()
            if url and url not in seen_urls:
                seen_urls.add(url)
                collected.append(a)
                if len(collected) >= limit:
                    break

    collected = collected[:limit]
    if translate and collected:
        collected = _translate_instrument_news_to_french(collected)
    return collected


@app.route("/api/mediastack-news")
def api_mediastack_news():
    """
    Actualités Mediastack avec filtres (catégorie, thème, pays, langue, sources).
    Paramètres: category, theme, countries, languages, sources, date, limit (max 5).
    """
    category = request.args.get("category", "").strip() or None
    theme = request.args.get("theme", "").strip() or None
    countries = request.args.get("countries", "").strip() or None
    languages = request.args.get("languages", "").strip() or None
    sources = request.args.get("sources", "").strip() or None
    date_str = request.args.get("date", "today_and_yesterday").strip() or None
    limit = min(5, max(1, int(request.args.get("limit", 5) or 5)))
    try:
        items = _fetch_mediastack_filtered(
            category=category,
            keywords=theme,
            countries=countries,
            languages=languages,
            sources=sources,
            date_str=date_str,
            limit=limit,
            translate=True,
        )
        return jsonify({"news": items, "count": len(items)})
    except Exception as e:
        return jsonify({"error": str(e), "news": [], "count": 0}), 500


@app.route("/api/mediastack-saved", methods=["GET"])
def api_mediastack_saved_get():
    """Retourne les actualités Mediastack mémorisées (fichier data/news_mediastack.json)."""
    try:
        if not os.path.exists(NEWS_MEDIASTACK_PATH):
            return jsonify({"news": [], "count": 0})
        with open(NEWS_MEDIASTACK_PATH, encoding="utf-8") as f:
            data = json.load(f)
        news = data.get("news", data) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        if not isinstance(news, list):
            news = []
        return jsonify({"news": news, "count": len(news)})
    except Exception as e:
        return jsonify({"error": str(e), "news": [], "count": 0}), 500


@app.route("/api/mediastack-saved", methods=["POST"])
def api_mediastack_saved_post():
    """Ajoute des actualités aux mémorisées (fichier data/news_mediastack.json)."""
    try:
        payload = request.get_json(silent=True) or {}
        to_add = payload.get("news", [])
        if not isinstance(to_add, list) or not to_add:
            return jsonify({"error": "news requis (tableau non vide)"}), 400
        existing: list[dict] = []
        if os.path.exists(NEWS_MEDIASTACK_PATH):
            try:
                with open(NEWS_MEDIASTACK_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                existing = data.get("news", data) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        seen: set[str] = {a.get("url") or "" for a in existing}
        for a in to_add:
            if isinstance(a, dict):
                url = (a.get("url") or "").strip()
                if url and url not in seen:
                    seen.add(url)
                    existing.append(a)
        if len(existing) > 100:
            existing = existing[-100:]
        os.makedirs(os.path.dirname(NEWS_MEDIASTACK_PATH), exist_ok=True)
        with open(NEWS_MEDIASTACK_PATH, "w", encoding="utf-8") as f:
            json.dump({"news": existing, "updated": datetime.now(timezone.utc).isoformat()}, f, ensure_ascii=False, indent=2)
        return jsonify({"news": existing, "count": len(existing)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mediastack-saved", methods=["DELETE"])
def api_mediastack_saved_delete():
    """Efface les actualités Mediastack mémorisées (vide le fichier data/news_mediastack.json)."""
    try:
        if os.path.exists(NEWS_MEDIASTACK_PATH):
            os.remove(NEWS_MEDIASTACK_PATH)
        return jsonify({"news": [], "count": 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mediastack-debug")
def api_mediastack_debug():
    """Debug : test direct de l'API Mediastack. Retourne la réponse brute."""
    key = os.getenv("MEDIASTACK_ACCESS_KEY")
    if not key:
        return jsonify({"error": "MEDIASTACK_ACCESS_KEY absente", "key_loaded": False}), 200
    try:
        r = requests.get(
            "http://api.mediastack.com/v1/news",
            params={"access_key": key, "limit": 3, "sort": "published_desc"},
            timeout=10,
        )
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text[:500]}
        return jsonify({
            "status_code": r.status_code,
            "key_loaded": True,
            "response": data,
            "data_count": len(data.get("data") or []),
            "error": data.get("error"),
        })
    except Exception as e:
        return jsonify({"error": str(e), "key_loaded": True}), 500


@app.route("/api/zonebourse-news-debug")
def api_zonebourse_debug():
    """Debug : retourne le résultat de get_latest_news pour diagnostiquer les actualités Zonebourse."""
    try:
        from zone_bourse.news_fetcher import get_latest_news
        result = get_latest_news(limit=3)
        items = result.get("items", [])
        return jsonify({"count": len(items), "news": items, "used_fallback": result.get("used_fallback", False)})
    except Exception as e:
        return jsonify({"error": str(e), "count": 0, "news": []}), 500


OPENAI_IMAGE_MODEL = "dall-e-3"


def _load_image_news_prompt(style_index: int = 0) -> str:
    """Charge le template du prompt image. style_index 0-5 : 6 styles (éditorial, fintech, réaliste, Bloomberg, Economist, cartoon)."""
    style_index = max(0, min(5, int(style_index)))
    filename = f"image_news_style{style_index + 1}.txt"
    path = os.path.join(os.path.dirname(__file__), "prompts", filename)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "Professional financial news illustration, clean and modern style:"


def _generate_image_openai(prompt: str, style_index: int = 0) -> tuple[str | None, str | None]:
    """Génère une image via l'API Images OpenAI (DALL·E). Retourne (data_url_base64, None) ou (None, erreur)."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, "OPENAI_API_KEY manquant dans .env"
    if not (prompt or "").strip():
        return None, "Prompt vide"
    template = _load_image_news_prompt(style_index)
    full_prompt = f"{template} {prompt.strip()}".strip()[:4000]
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        r = client.images.generate(
            model=OPENAI_IMAGE_MODEL,
            prompt=full_prompt,
            n=1,
            size="1024x1024",
            response_format="b64_json",
            quality="standard",
        )
        if not r.data or len(r.data) == 0:
            return None, "Réponse OpenAI vide"
        b64 = getattr(r.data[0], "b64_json", None)
        if not b64:
            return None, "Image non renvoyée en base64"
        return f"data:image/png;base64,{b64}", None
    except Exception as e:
        return None, str(e).strip()[:300] or "Génération impossible"


def _gen_zonebourse_image(prompt: str, style_index: int) -> str | None:
    """Wrapper pour générer une image Zonebourse (retourne data_url ou None)."""
    data_url, _ = _generate_image_openai(prompt, style_index=style_index)
    return data_url


@app.route("/api/generate-news-image", methods=["POST"])
def api_generate_news_image():
    """Génère une image à partir d'un prompt (actualité) via OpenAI DALL·E. style_index 0-5 = 6 styles (choix aléatoire côté client)."""
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or "").strip()
    style_index = data.get("style_index", 0)
    if not prompt:
        return jsonify({"error": "prompt manquant"}), 400
    data_url, err = _generate_image_openai(prompt, style_index=style_index)
    if not data_url:
        return jsonify({"error": err or "Génération d'image impossible"}), 502
    return jsonify({"image_data_url": data_url})


def _append_chat_question(question: str, reply: str) -> None:
    """Enregistre une question utilisateur et la réponse dans le log JSONL."""
    if not question.strip():
        return
    try:
        os.makedirs(os.path.dirname(CHAT_QUESTIONS_LOG), exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "question": question.strip(),
            "reply": (reply or "").strip(),
        }
        with open(CHAT_QUESTIONS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_chat_questions() -> list[dict]:
    """Charge la liste des questions/réponses depuis le log JSONL."""
    rows: list[dict] = []
    if not os.path.exists(CHAT_QUESTIONS_LOG):
        return rows
    try:
        with open(CHAT_QUESTIONS_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return rows


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


def _load_chatbot_books() -> str:
    """Charge la liste des livres depuis prompts/chatbot_books.txt."""
    return _load_chatbot_resources("chatbot_books.txt")


def _load_chatbot_videos() -> str:
    """Charge la liste des vidéos YouTube depuis prompts/chatbot_videos.txt."""
    return _load_chatbot_resources("chatbot_videos.txt")


def _load_chatbot_prompt() -> str:
    """Charge le prompt système du chatbot depuis prompts/chatbot_system.txt + livres + vidéos."""
    base = os.path.join(os.path.dirname(__file__), "prompts")
    try:
        with open(os.path.join(base, "chatbot_system.txt"), encoding="utf-8") as f:
            prompt = f.read().strip()
    except Exception:
        prompt = "Tu es un assistant financier. Réponds de façon concise en français."
    books = _load_chatbot_books()
    if books:
        prompt += "\n\nLivres que tu peux recommander (propose le lien quand tu cites un livre) :\n" + books
    videos = _load_chatbot_videos()
    if videos:
        prompt += "\n\nVidéos YouTube que tu peux recommander (propose le lien quand tu cites une vidéo) :\n" + videos
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
    from openai import OpenAI
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return jsonify({"error": "OPENAI_API_KEY manquante"}), 500
    system_prompt = _load_chatbot_prompt()
    try:
        client = OpenAI(api_key=key)
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
