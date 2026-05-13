"""Métriques performance trader (même logique que le tableau « Performances » de la home)."""

from __future__ import annotations

from datetime import datetime, timezone

from etoro_client import get_user_gain

# Aligné sur app.DATE_FROM — à garder synchronisé si la date de départ des séries change.
DATE_FROM = "2022-09"


def filter_gain_from_date(gain_data: dict | None) -> dict | None:
    """Ne garde que les entrées à partir de septembre 2022 (identique à app._filter_gain_from_date)."""
    if not gain_data:
        return gain_data
    filtered: dict = {}
    if gain_data.get("monthly"):
        filtered["monthly"] = [
            e
            for e in gain_data["monthly"]
            if e.get("timestamp") and e["timestamp"][:7] >= DATE_FROM
        ]
    if gain_data.get("yearly"):
        filtered["yearly"] = [
            e
            for e in gain_data["yearly"]
            if e.get("timestamp") and e["timestamp"][:4] >= DATE_FROM[:4]
        ]
    return filtered if filtered else gain_data


def gain_to_by_month(gain: dict | None) -> dict[str, float]:
    """Convertit les gains API en dict {YYYY-MM: gain_pct} (identique à app._gain_to_by_month)."""
    out: dict[str, float] = {}
    if gain and gain.get("monthly"):
        for e in gain["monthly"]:
            ts = e.get("timestamp")
            g = e.get("gain")
            if ts and ts[:7] >= DATE_FROM:
                out[ts[:7]] = float(g) if g is not None else 0.0
    return out


def monthly_to_yearly_returns(by_month: dict[str, float]) -> dict[str, float]:
    """Rendement annuel composé par année civile (identique à app._monthly_to_yearly_returns)."""
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


def get_trader_calendar_year_return_pct(username: str, year: int | None = None) -> float | None:
    """
    Pourcentage de la colonne « Total » de la ligne d’année du tableau home
    (performance cumulée sur les mois de cette année civile dans les données eToro).
    """
    y = year if year is not None else datetime.now(timezone.utc).year
    gain = get_user_gain(username)
    filtered = filter_gain_from_date(gain)
    by_month = gain_to_by_month(filtered)
    yearly = monthly_to_yearly_returns(by_month)
    raw = yearly.get(str(y))
    if raw is None:
        return None
    return round(float(raw), 2)
