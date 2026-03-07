"""Client API eToro pour récupérer les données des traders."""

import os
import time
import uuid
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://public-api.etoro.com/api/v1"


def _get_headers():
    return {
        "x-api-key": os.getenv("ETORO_API_KEY"),
        "x-user-key": os.getenv("ETORO_USER_KEY"),
        "x-request-id": str(uuid.uuid4()),
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }


def get_user_profile(username: str) -> dict | None:
    """Récupère le profil complet d'un trader."""
    url = f"{BASE_URL}/user-info/people"
    params = {"usernames": username}
    resp = requests.get(url, headers=_get_headers(), params=params)
    if resp.status_code != 200:
        return None
    data = resp.json()
    users = data.get("Users") or data.get("users") or []
    return users[0] if users else None


def get_user_gain(username: str) -> dict | None:
    """Récupère les métriques de performance (gain mensuel/annuel)."""
    url = f"{BASE_URL}/user-info/people/{username}/gain"
    resp = requests.get(url, headers=_get_headers())
    if resp.status_code != 200:
        return None
    return resp.json()


# Instruments pour le feed — utilisés en fallback quand l'API user feed renvoie 0
# (symbole pour recherche API, label affiché)
FEED_INSTRUMENTS = [
    ("NSDQ100", "NSDQ100"),
    ("SPX500", "SPX500"),
    ("CAC40", "CAC40"),
    ("XAUUSD", "Or"),
    ("BTC", "Bitcoin"),
    ("ETH", "Ethereum"),
]


def _search_instrument_id(symbol: str) -> int | None:
    """Recherche l'ID d'un instrument par symbole (ex: AAPL, TSLA)."""
    url = f"{BASE_URL}/market-data/search"
    params = {"internalSymbolFull": symbol, "pageSize": 5}
    try:
        resp = requests.get(url, headers=_get_headers(), params=params, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        items = data.get("items") or data.get("Items") or []
        for item in items:
            if (item.get("internalSymbolFull") or item.get("symbol") or "").upper() == symbol.upper():
                iid = item.get("instrumentId") or item.get("InstrumentID")
                if iid is not None:
                    return int(iid)
        if items:
            return int(items[0].get("instrumentId") or items[0].get("InstrumentID") or 0)
    except Exception:
        pass
    return None


def get_instrument_feed_posts(
    market_id: str | int, take: int = 100, offset: int = 0
) -> dict | None:
    """Récupère les posts du feed d'un instrument (API Feeds).
    market_id : ID de l'instrument (ex: 100000 pour BTC).
    Retourne {"discussions": [...], "paging": {...}} ou None."""
    url = f"{BASE_URL}/feeds/instrument/{market_id}"
    params = {"take": min(take, 100), "offset": offset}
    try:
        resp = requests.get(url, headers=_get_headers(), params=params, timeout=30)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _extract_posts_from_feed_response(data: dict) -> list[dict]:
    """Extrait les posts d'une réponse feed (discussions ou posts)."""
    posts = []
    # Format discussions (user feed, parfois instrument feed)
    for d in data.get("discussions") or []:
        p = d.get("post") if isinstance(d.get("post"), dict) else {}
        if p:
            posts.append(p)
    # Format posts direct (instrument feed)
    for p in data.get("posts") or []:
        if isinstance(p, dict):
            posts.append(p)
    return posts


def _post_matches_user(post: dict, username: str) -> bool:
    """Vérifie si le post appartient au trader."""
    owner = post.get("owner") if isinstance(post.get("owner"), dict) else {}
    post_username = (owner.get("username") or owner.get("userName") or "").strip()
    return post_username.lower() == (username or "").lower()


def get_posts_per_month_by_instrument(
    instrument_id: int, years: int = 5, username: str | None = None
) -> dict[str, int]:
    """
    Récupère les posts du feed d'un instrument et les agrège par mois.
    Si username fourni, ne compte que les posts de ce trader.
    Retourne {"YYYY-MM": count, ...}.
    """
    from datetime import datetime, timedelta, timezone, timezone

    cutoff_str = (datetime.now(timezone.utc) - timedelta(days=years * 365)).strftime("%Y-%m")
    by_month: dict[str, int] = {}
    offset = 0
    take = 100
    stop_old = False
    max_pages = 15

    while offset < max_pages * take:
        data = get_instrument_feed_posts(instrument_id, take=take, offset=offset)
        if not data:
            break
        posts = _extract_posts_from_feed_response(data)
        if not posts:
            break

        for post in posts:
            if username and not _post_matches_user(post, username):
                continue
            created = post.get("created")
            if not created or len(str(created)) < 7:
                continue
            key = str(created)[:7]
            if key < cutoff_str:
                stop_old = True
                continue
            by_month[key] = by_month.get(key, 0) + 1

        if stop_old or len(posts) < take:
            break
        offset += take
        time.sleep(0.15)

    return dict(sorted(by_month.items()))


def get_posts_per_month_from_instruments(username: str, years: int = 1) -> dict[str, int]:
    """
    Fallback : agrège les posts du trader depuis plusieurs instruments (NSDQ, SPX, CAC, Or, etc.).
    Déduplique par post id (même post peut apparaître dans plusieurs feeds).
    """
    from datetime import datetime, timedelta, timezone
    from collections import defaultdict

    cutoff_str = (datetime.now(timezone.utc) - timedelta(days=years * 365)).strftime("%Y-%m")
    seen_ids: set[str] = set()
    by_month: dict[str, int] = defaultdict(int)

    for symbol, _ in FEED_INSTRUMENTS:
        iid = _search_instrument_id(symbol)
        if iid is None:
            continue
        offset = 0
        take = 100
        max_pages = 8
        stop_old = False

        while offset < max_pages * take:
            data = get_instrument_feed_posts(iid, take=take, offset=offset)
            if not data:
                break
            posts = _extract_posts_from_feed_response(data)
            if not posts:
                break

            for post in posts:
                if not _post_matches_user(post, username):
                    continue
                post_id = post.get("id") or post.get("obsoleteId") or ""
                if post_id and post_id in seen_ids:
                    continue
                if post_id:
                    seen_ids.add(post_id)
                created = post.get("created")
                if not created or len(str(created)) < 7:
                    continue
                key = str(created)[:7]
                if key < cutoff_str:
                    stop_old = True
                    continue
                by_month[key] += 1

            if stop_old or len(posts) < take:
                break
            offset += take
            time.sleep(0.15)

    return dict(sorted(by_month.items()))


def get_user_feed_posts(
    user_id: str | int, take: int = 100, offset: int = 0, requester_user_id: str | int | None = None
) -> dict | None:
    """Récupère les posts du feed d'un utilisateur (API Feeds).
    user_id : ID numérique du user (pas le username).
    requester_user_id : optionnel, ID du demandeur (pour personnalisation).
    Retourne {"discussions": [...], "paging": {...}} ou None."""
    url = f"{BASE_URL}/feeds/user/{user_id}"
    params = {"take": min(take, 100), "offset": offset}
    if requester_user_id is not None:
        params["requesterUserId"] = str(requester_user_id)
    try:
        resp = requests.get(url, headers=_get_headers(), params=params, timeout=30)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def get_posts_per_month(username: str, years: int = 1, max_pages: int = 10) -> dict[str, int]:
    """
    Récupère les posts d'un trader et les agrège par mois sur les N dernières années.
    Utilise l'API user feed en priorité (tous les posts). Si vide, fallback sur
    plusieurs instruments (NSDQ100, SPX500, CAC40, Or, BTC, ETH) en filtrant par auteur.
    Retourne {"YYYY-MM": count, ...}.
    """
    from datetime import datetime, timedelta, timezone

    profile = get_user_profile(username)
    if not profile:
        return {}

    user_id = (
        profile.get("gcid")
        or profile.get("UserID")
        or profile.get("userID")
        or profile.get("id")
        or profile.get("realCID")
        or profile.get("demoCID")
    )
    user_id = str(user_id) if user_id is not None else None
    if not user_id:
        return get_posts_per_month_from_instruments(username, years)

    cutoff_str = (datetime.now(timezone.utc) - timedelta(days=years * 365)).strftime("%Y-%m")
    by_month: dict[str, int] = {}
    offset = 0
    take = 100
    stop_old = False

    while offset < max_pages * take:
        data = get_user_feed_posts(user_id, take=take, offset=offset, requester_user_id=user_id)
        if not data:
            break
        discussions = data.get("discussions") or []
        if not discussions:
            break

        for d in discussions:
            post = d.get("post") if isinstance(d.get("post"), dict) else {}
            created = post.get("created")
            if not created or len(str(created)) < 7:
                continue
            key = str(created)[:7]
            if key < cutoff_str:
                stop_old = True
                continue
            by_month[key] = by_month.get(key, 0) + 1

        if stop_old or len(discussions) < take:
            break
        offset += take
        time.sleep(0.2)

    result = dict(sorted(by_month.items()))

    # Fallback : si user feed vide, utiliser les feeds instruments filtrés par auteur
    if not result or sum(result.values()) < 2:
        result = get_posts_per_month_from_instruments(username, years)

    return result


# Traders populaires eToro en repli si l'API search ne retourne rien
FALLBACK_TRADERS = [
    {"userName": "jaynemesis", "copiers": 0, "gain": None},
    {"userName": "daviddem", "copiers": 0, "gain": None},
    {"userName": "benztrades", "copiers": 0, "gain": None},
    {"userName": "TradingGranny", "copiers": 0, "gain": None},
    {"userName": "sirile", "copiers": 0, "gain": None},
    {"userName": "CharlyTrader", "copiers": 0, "gain": None},
    {"userName": "Mixtredefx", "copiers": 0, "gain": None},
    {"userName": "InvestorParadis", "copiers": 0, "gain": None},
    {"userName": "david_2104", "copiers": 0, "gain": None},
    {"userName": "RanBeckenstein", "copiers": 0, "gain": None},
]


def get_most_copied_traders(limit: int = 10) -> list[dict]:
    """Récupère les traders les plus copiés (popular investors triés par nombre de copieurs)."""
    url = f"{BASE_URL}/user-info/people/search"
    for params in [
        {"period": "LastYear", "sort": "-copiers", "pageSize": limit},
        {"period": "CurrYear", "sort": "-copiers", "pageSize": limit},
        {"period": "LastTwoYears", "sort": "-copiers", "pageSize": limit},
    ]:
        try:
            resp = requests.get(url, headers=_get_headers(), params=params)
            if resp.status_code != 200:
                continue
            data = resp.json()
            items = data.get("items") or data.get("Items") or []
            result = [
                {"userName": item.get("userName"), "copiers": item.get("copiers", 0), "gain": item.get("gain")}
                for item in items
                if item.get("userName")
            ]
            if result:
                return result[:limit]
        except Exception:
            continue
    return FALLBACK_TRADERS[:limit]


# Périodes pour l'évolution des copieurs (snapshots API)
# Ordre chronologique : passé → présent. L'API ne fournit pas d'historique mensuel.
COPIERS_PERIODS = [
    ("LastTwoYears", "Il y a 2 ans"),
    ("OneYearAgo", "Il y a 1 an"),
    ("SixMonthsAgo", "Il y a 6 mois"),
    ("ThreeMonthsAgo", "Il y a 3 mois"),
    ("TwoMonthsAgo", "Il y a 2 mois"),
    ("OneMonthAgo", "Il y a 1 mois"),
    ("CurrMonth", "Actuel"),
]


def get_copiers_by_period(period: str, page_size: int = 2000) -> dict[str, int]:
    """
    Récupère le nombre de copieurs par trader pour une période donnée.
    Retourne {userName: copiers}.
    """
    url = f"{BASE_URL}/user-info/people/search"
    params = {"period": period, "sort": "-copiers", "pageSize": page_size}
    try:
        resp = requests.get(url, headers=_get_headers(), params=params, timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        items = data.get("items") or data.get("Items") or []
        return {item["userName"]: item.get("copiers", 0) for item in items if item.get("userName")}
    except Exception:
        return {}


def get_copiers_vs_performance(limit: int = 2000, period: str = "LastTwoYears") -> list[dict]:
    """
    Retourne les N traders les plus copiés avec copiers et gain (performance).
    Pour le graphique : abscisse = copiers, ordonnée = gain (gain %).
    period=LastTwoYears = performance sur 2 ans (max disponible via search).
    """
    url = f"{BASE_URL}/user-info/people/search"
    params = {"period": period, "sort": "-copiers", "pageSize": min(limit, 2000)}
    try:
        resp = requests.get(url, headers=_get_headers(), params=params, timeout=30)
        if resp.status_code != 200:
            return []
        data = resp.json()
        items = data.get("items") or data.get("Items") or []
        result = [
            {
                "userName": item.get("userName"),
                "copiers": item.get("copiers", 0) or 0,
                "gain": item.get("gain") if item.get("gain") is not None else None,
            }
            for item in items
            if item.get("userName")
        ]
        return [p for p in result if p["gain"] is None or p["gain"] <= 500]
    except Exception:
        return []


def get_current_copiers(username: str) -> int | None:
    """
    Retourne le nombre de copieurs actuels du trader (période CurrMonth).
    Retourne None si le trader n'est pas dans le top 2000.
    """
    by_user = get_copiers_by_period("CurrMonth", page_size=2000)
    return by_user.get(username)


def get_copiers_evolution(traders: list[str]) -> dict[str, dict[str, int]]:
    """
    Récupère l'évolution du nombre de copieurs pour chaque trader.
    Retourne {userName: {period_label: copiers}}.
    """
    result: dict[str, dict[str, int]] = {u: {} for u in traders if u}
    for period, label in COPIERS_PERIODS:
        by_user = get_copiers_by_period(period)
        for username in traders:
            if username:
                result.setdefault(username, {})[label] = by_user.get(username, 0)
        time.sleep(0.2)
    return result


def get_exchanges() -> list[dict]:
    """Récupère la liste des places de marché supportées."""
    url = f"{BASE_URL}/market-data/exchanges"
    try:
        resp = requests.get(url, headers=_get_headers())
        if resp.status_code != 200:
            return []
        data = resp.json()
        info = data.get("exchangeInfo") or data.get("ExchangeInfo") or []
        return [
            {"exchangeId": e.get("exchangeId"), "name": e.get("exchangeDescription")}
            for e in info
            if e.get("exchangeId") is not None
        ]
    except Exception:
        return []


def _fetch_all_instrument_ids_from_closing_prices() -> list[int]:
    """
    Doc eToro : GET /api/v1/market-data/instruments/history/closing-price
    Retourne la liste de TOUS les instruments (avec instrumentId).
    """
    url = f"{BASE_URL}/market-data/instruments/history/closing-price"
    try:
        resp = requests.get(url, headers=_get_headers(), timeout=60)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        return [int(item["instrumentId"]) for item in data if item.get("instrumentId") is not None]
    except Exception:
        return []


def _fetch_instruments_by_exchange(exchange_id, exchange_name: str) -> list[dict]:
    """Récupère les instruments d'une place de marché."""
    url = f"{BASE_URL}/market-data/exchanges/{exchange_id}/instruments"
    exclude_keywords = ("crypto", "forex", "fx", "commodity", "commodities", "currency", "index")
    out = []
    try:
        page = 1
        page_size = 500
        while True:
            resp = requests.get(
                url,
                headers=_get_headers(),
                params={"pageSize": page_size, "pageNumber": page},
                timeout=20,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get("items") or data.get("Items") or []
            if not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                asset_class = (item.get("internalAssetClassName") or item.get("instrumentType") or "").lower()
                if asset_class and any(kw in asset_class for kw in exclude_keywords):
                    continue
                iid = item.get("instrumentId") or item.get("instrumentID") or item.get("internalInstrumentId")
                if iid is None:
                    continue
                sym = (
                    item.get("symbol")
                    or item.get("internalSymbolFull")
                    or item.get("symbolFull")
                    or str(iid)
                )
                disp = (
                    item.get("displayName")
                    or item.get("instrumentDisplayName")
                    or item.get("internalInstrumentDisplayName")
                    or sym
                )
                out.append({
                    "instrumentId": iid,
                    "symbol": sym,
                    "displayname": disp,
                    "exchange": exchange_name,
                })
            total = data.get("totalItems") or data.get("totalitems") or 0
            if total and page * page_size >= total:
                break
            page += 1
            if page > 100:
                break
    except Exception:
        pass
    return out


def _get_instruments_metadata(instrument_ids: list) -> dict:
    """Récupère les métadonnées (nom, symbole) pour une liste d'IDs."""
    if not instrument_ids:
        return {}
    url = f"{BASE_URL}/market-data/instruments"
    try:
        ids_str = ",".join(str(i) for i in instrument_ids[:200])
        resp = requests.get(url, headers=_get_headers(), params={"instrumentIds": ids_str}, timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        items = (
            data.get("instrumentDisplayDatas")
            or data.get("InstrumentDisplayDatas")
            or data.get("items")
            or data.get("Items")
            or []
        )
        # Gérer InstrumentDisplayData (objet unique) comme dans l'OpenAPI eToro
        if isinstance(data.get("InstrumentDisplayData"), dict):
            d = data["InstrumentDisplayData"]
            iid = d.get("InstrumentID") or d.get("instrumentId")
            if iid is not None:
                items = [d]
        result = {}
        for item in items if isinstance(items, list) else [items]:
            if not isinstance(item, dict):
                continue
            iid = item.get("instrumentId") or item.get("InstrumentID") or item.get("instrumentID")
            if iid is None:
                continue
            sym = (
                item.get("symbolFull")
                or item.get("SymbolFull")
                or item.get("symbol")
                or item.get("internalSymbolFull")
            )
            disp = (
                item.get("instrumentDisplayName")
                or item.get("InstrumentDisplayName")
                or item.get("displayName")
                or item.get("displayname")
            )
            result[iid] = {
                "symbol": sym or "",
                "displayname": disp or "",
                "instrumentTypeId": item.get("instrumentTypeId") or item.get("instrumentTypeID"),
                "exchangeId": item.get("exchangeId") or item.get("exchangeID"),
            }
        return result
    except Exception:
        return {}


def _get_single_instrument_legacy(instrument_id) -> dict | None:
    """
    Fallback: récupère les métadonnées via l'API legacy eToro (un instrument à la fois).
    Utilisé quand l'API market-data/instruments ne retourne pas symbol/displayname.
    """
    url = f"https://www.etoro.com/sapi/instrumentsmetadata/V1.1/instruments/{instrument_id}"
    try:
        resp = requests.get(url, headers=_get_headers(), timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        d = data.get("InstrumentDisplayData")
        if not isinstance(d, dict):
            return None
        sym = d.get("SymbolFull") or d.get("symbolFull") or d.get("symbol")
        disp = d.get("InstrumentDisplayName") or d.get("instrumentDisplayName") or d.get("displayName")
        if sym or disp:
            return {"symbol": sym or "", "displayname": disp or ""}
        return None
    except Exception:
        return None


# Type IDs eToro (doc) : Stocks=-5, ETF=-6. Exclure : Crypto=-10, Currencies=-1, Commodities=-2, Indices=-4
_STOCK_ETF_TYPE_IDS = {-5, -6}


def get_stocks_by_id_range(id_min: int, id_max: int) -> list[dict]:
    """
    Récupère les instruments eToro (stocks/ETF) dont l'ID est entre id_min et id_max (inclus).
    Utile pour récupérer en plusieurs étapes (ex. 1001-1010, puis 1011-1020...).
    """
    stocks = []
    ids = list(range(id_min, id_max + 1))
    if not ids:
        return stocks
    try:
        exchange_map = {e["exchangeId"]: (e.get("name") or f"Exchange {e['exchangeId']}") for e in get_exchanges()}
        for i in range(0, len(ids), 200):
            batch = ids[i : i + 200]
            meta = _get_instruments_metadata(batch)
            for iid in batch:
                m = meta.get(iid)
                if not m:
                    stocks.append({"instrumentId": iid, "symbol": str(iid), "displayname": str(iid), "exchange": "N/A"})
                    continue
                type_id = m.get("instrumentTypeId")
                if type_id is not None and type_id not in _STOCK_ETF_TYPE_IDS:
                    continue
                sym = m.get("symbol") or str(iid)
                disp = m.get("displayname") or sym
                ex_id = m.get("exchangeId")
                exchange = exchange_map.get(ex_id, "N/A") if ex_id is not None else "N/A"
                stocks.append({"instrumentId": iid, "symbol": sym, "displayname": disp, "exchange": exchange})
        # Legacy pour symbol/nom manquants
        for s in stocks:
            iid = s["instrumentId"]
            if str(s.get("symbol", "")) == str(iid) or str(s.get("displayname", "")) == str(iid):
                m = _get_single_instrument_legacy(iid)
                if m:
                    if m.get("symbol"):
                        s["symbol"] = m["symbol"]
                    if m.get("displayname"):
                        s["displayname"] = m["displayname"]
                time.sleep(0.05)
    except Exception:
        pass
    return stocks


def get_all_stocks(max_pages: int = 50) -> list:
    """
    Récupère toutes les actions disponibles sur eToro.
    Combine : closing-price (tous les instruments), search (pageSize/pageNumber), exchanges.
    Inclut stocks/equities, exclut crypto, forex, commodities.
    """
    stocks = []
    seen_ids = set()

    def _add_item(iid, symbol, displayname, exchange):
        if iid in seen_ids:
            return
        seen_ids.add(iid)
        stocks.append({
            "instrumentId": iid,
            "symbol": symbol,
            "displayname": displayname,
            "exchange": exchange,
        })

    # 0. Doc eToro : closing-price retourne TOUS les instruments → IDs >= 1001, enrichis par metadata
    try:
        all_ids = _fetch_all_instrument_ids_from_closing_prices()
        ids_ge_1001 = sorted(i for i in all_ids if i >= 1001)
        exchange_map = {e["exchangeId"]: (e.get("name") or f"Exchange {e['exchangeId']}") for e in get_exchanges()}
        for i in range(0, len(ids_ge_1001), 200):
            batch = ids_ge_1001[i : i + 200]
            meta = _get_instruments_metadata(batch)
            for iid in batch:
                m = meta.get(iid)
                if not m:
                    _add_item(iid, str(iid), str(iid), "N/A")
                    continue
                type_id = m.get("instrumentTypeId")
                if type_id is not None and type_id not in _STOCK_ETF_TYPE_IDS:
                    continue
                sym = m.get("symbol") or str(iid)
                disp = m.get("displayname") or sym
                ex_id = m.get("exchangeId")
                exchange = exchange_map.get(ex_id, "N/A") if ex_id is not None else "N/A"
                _add_item(iid, sym, disp, exchange)
    except Exception:
        pass

    # 1. Récupérer par place de marché (donne beaucoup plus d'instruments)
    try:
        exchanges = get_exchanges()
        for ex in exchanges:
            eid = ex.get("exchangeId")
            ename = ex.get("name") or f"Exchange {eid}"
            if eid is None:
                continue
            for s in _fetch_instruments_by_exchange(eid, ename):
                _add_item(s["instrumentId"], s["symbol"], s["displayname"], s["exchange"])
    except Exception:
        pass

    # 2. Compléter avec l'API search (pagination jusqu'à plus de résultats)
    url = f"{BASE_URL}/market-data/search"
    exclude_keywords = ("crypto", "forex", "fx", "commodity", "commodities", "currency", "index")
    try:
        page = 1
        page_size = 500
        while page <= max_pages:
            resp = requests.get(
                url,
                headers=_get_headers(),
                params={
                    "fields": "instrumentId,displayName,internalInstrumentDisplayName,symbol,internalSymbolFull,internalExchangeName,internalAssetClassName,instrumentType",
                    "pageSize": page_size,
                    "pageNumber": page,
                },
                timeout=20,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get("items") or data.get("Items") or []
            if not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                asset_class = (item.get("internalAssetClassName") or item.get("instrumentType") or "").lower()
                if asset_class and any(kw in asset_class for kw in exclude_keywords):
                    continue
                iid = item.get("instrumentId") or item.get("instrumentID") or item.get("internalInstrumentId")
                if iid is None:
                    continue

                def _first(d, *keys):
                    for k in keys:
                        v = d.get(k)
                        if v is not None and str(v).strip():
                            return str(v).strip()
                    return None

                def _find_key(d, *substrings):
                    for k, v in d.items():
                        if not (v and isinstance(v, str) and str(v).strip()):
                            continue
                        if any(s in k.lower() for s in substrings):
                            return str(v).strip()
                    return None

                symbol = _first(item, "symbol", "internalSymbolFull", "Symbol") or _find_key(item, "symbol") or str(iid)
                displayname = _first(item, "displayName", "displayname", "internalInstrumentDisplayName") or _find_key(item, "display", "name") or symbol
                exchange = (
                    item.get("internalExchangeName")
                    or item.get("InternalExchangeName")
                    or item.get("exchangeDescription")
                    or "N/A"
                )
                _add_item(iid, symbol, displayname, exchange)
            # Ne pas s'arrêter sur totalItems : continuer tant qu'on reçoit des items (plus d'instruments, dont ID > 1001)
            page += 1
    except Exception:
        pass

    if stocks:
        for i in range(0, len(stocks), 200):
            batch = [s["instrumentId"] for s in stocks[i : i + 200]]
            meta = _get_instruments_metadata(batch)
            for s in stocks[i : i + 200]:
                m = meta.get(s["instrumentId"])
                if m:
                    if m.get("symbol"):
                        s["symbol"] = m["symbol"]
                    if m.get("displayname"):
                        s["displayname"] = m["displayname"]

        # Fallback API legacy : enrichir les stocks où symbole/nom == ID
        for s in stocks:
            iid = s["instrumentId"]
            need_fallback = (
                str(s.get("symbol", "")) == str(iid)
                or str(s.get("displayname", "")) == str(iid)
            )
            if need_fallback:
                m = _get_single_instrument_legacy(iid)
                if m:
                    if m.get("symbol"):
                        s["symbol"] = m["symbol"]
                    if m.get("displayname"):
                        s["displayname"] = m["displayname"]
                time.sleep(0.05)

    return stocks


def get_instruments_by_exchange(max_pages: int = 5) -> dict:
    """
    Récupère les instruments (actions, ETF...) groupés par place de marché.
    Retourne {nom_exchange: [{symbol, displayname, instrumentId}, ...]}
    """
    url = f"{BASE_URL}/market-data/search"
    by_exchange = {}
    page = 1
    page_size = 200
    try:
        while page <= max_pages:
            resp = requests.get(
                url,
                headers=_get_headers(),
                params={
                    "fields": "instrumentId,displayname,symbol,exchangeID,internalExchangeName,internalAssetClassName",
                    "pageSize": page_size,
                    "pageNumber": page,
                },
                timeout=15,
            )
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get("items") or data.get("Items") or []
            if not items:
                break
            for item in items:
                exchange_name = (
                    item.get("internalExchangeName")
                    or item.get("exchangeDescription")
                    or "Exchange %s" % item.get("exchangeID", "?")
                )
                if exchange_name not in by_exchange:
                    by_exchange[exchange_name] = []
                by_exchange[exchange_name].append({
                    "instrumentId": item.get("instrumentId"),
                    "symbol": item.get("symbol") or item.get("internalSymbolFull"),
                    "displayname": item.get("displayname") or item.get("internalInstrumentDisplayName"),
                    "assetClass": item.get("internalAssetClassName"),
                })
            total = data.get("totalItems") or data.get("totalitems") or 0
            if total and page * page_size >= total:
                break
            page += 1
    except Exception:
        pass
    return dict(sorted(by_exchange.items(), key=lambda x: x[0]))


def get_user_portfolio(username: str) -> dict | None:
    """Récupère le portefeuille en direct d'un trader."""
    url = f"{BASE_URL}/user-info/people/{username}/portfolio/live"
    resp = requests.get(url, headers=_get_headers())
    if resp.status_code != 200:
        return None
    return resp.json()


def get_portfolio_instruments(username: str) -> list[dict]:
    """
    Retourne la liste des instruments en portefeuille du trader (positions directes),
    enrichis avec symbole et nom d'affichage.
    """
    portfolio = get_user_portfolio(username)
    if not portfolio:
        return []
    positions = portfolio.get("positions") or portfolio.get("Positions") or []
    ids_seen: set[int] = set()
    instrument_ids: list[int] = []
    for p in positions:
        if not isinstance(p, dict):
            continue
        iid = p.get("instrumentId") or p.get("InstrumentID")
        if iid is not None and iid not in ids_seen:
            ids_seen.add(iid)
            instrument_ids.append(iid)
    if not instrument_ids:
        return []
    meta = _get_instruments_metadata(instrument_ids)
    result: list[dict] = []
    for iid in instrument_ids:
        m = meta.get(iid) or {}
        sym = m.get("symbol") or ""
        disp = m.get("displayname") or ""
        if not sym and not disp:
            fallback = _get_single_instrument_legacy(iid)
            if fallback:
                sym = fallback.get("symbol", "") or fallback.get("symbolFull", "")
                disp = fallback.get("displayname", "") or fallback.get("displayName", "")
        if not sym and not disp:
            sym = str(iid)
        result.append({
            "instrumentId": iid,
            "symbol": sym,
            "displayname": disp or sym,
        })
    return sorted(result, key=lambda x: (x.get("displayname") or x.get("symbol") or "").lower())
