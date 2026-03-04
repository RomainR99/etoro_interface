"""Application Flask pour visualiser le profil des traders eToro."""

import os
from datetime import datetime
from flask import Flask, jsonify, render_template, request
from etoro_client import (
    get_user_profile,
    get_user_gain,
    get_user_portfolio,
    get_most_copied_traders,
    get_instruments_by_exchange,
    get_all_stocks,
)
from zone_bourse.news_fetcher import get_latest_news

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

app = Flask(__name__)
TRADER_USERNAME = "RomainRoth"
DATE_FROM = "2022-09"  # Données à partir de septembre 2022


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
        chart_labels, chart_datasets = _compute_chart_data(gain, [], include_sp500=True)
    except Exception:
        chart_labels, chart_datasets = [], []

    try:
        dca_labels, dca_romainroth, dca_sp500 = _compute_dca_simulation(gain)
    except Exception:
        dca_labels, dca_romainroth, dca_sp500 = [], [], []

    try:
        most_copied = get_most_copied_traders(100)
    except Exception:
        most_copied = []

    try:
        zonebourse_news = get_latest_news(limit=3)
    except Exception:
        zonebourse_news = []

    return render_template(
        "profile.html",
        profile=profile,
        gain=gain,
        portfolio=portfolio,
        username=TRADER_USERNAME,
        chart_labels=chart_labels,
        chart_datasets=chart_datasets,
        most_copied_traders=most_copied,
        dca_labels=dca_labels,
        dca_romainroth=dca_romainroth,
        dca_sp500=dca_sp500,
        zonebourse_news=zonebourse_news,
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


@app.route("/api/zonebourse-news-debug")
def api_zonebourse_debug():
    """Debug : retourne le résultat de get_latest_news pour diagnostiquer les actualités Zonebourse."""
    try:
        from zone_bourse.news_fetcher import get_latest_news
        news = get_latest_news(limit=3)
        return jsonify({"count": len(news), "news": news})
    except Exception as e:
        return jsonify({"error": str(e), "count": 0, "news": []}), 500


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
    """Chatbot OpenAI : envoie les messages et retourne la réponse du modèle."""
    from openai import OpenAI
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return jsonify({"error": "OPENAI_API_KEY manquante"}), 500
    data = request.get_json() or {}
    messages = data.get("messages") or []
    if not messages:
        return jsonify({"error": "messages requis"}), 400
    system_prompt = _load_chatbot_prompt()
    try:
        client = OpenAI(api_key=key)
        api_messages = [{"role": "system", "content": system_prompt}] + [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in messages
        ]
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=api_messages,
            temperature=0.7,
        )
        reply = (r.choices[0].message.content or "").strip()
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5001, host="127.0.0.1")
