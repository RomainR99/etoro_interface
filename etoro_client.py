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
            if sym or disp:
                result[iid] = {"symbol": sym or "", "displayname": disp or ""}
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


def get_all_stocks(max_pages: int = 50) -> list:
    """
    Récupère toutes les actions disponibles sur eToro.
    Combine search API + instruments par place de marché pour maximiser le nombre.
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

    # 2. Compléter avec l'API search (au cas où des instruments manquent)
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
            total = data.get("totalItems") or data.get("totalitems") or 0
            if total and page * page_size >= total:
                break
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
