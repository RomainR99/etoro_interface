"""Client API eToro pour récupérer les données des traders."""

import os
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
