"""Cache disque des gains eToro (tableau Performances, graphiques, newsletter)."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

from etoro_client import get_user_gain

ROOT = Path(__file__).resolve().parent
DEFAULT_GAIN_CACHE_PATH = ROOT / "data" / "trader_gain_romainroth.json"
DEFAULT_USERNAME = "RomainRoth"


def gain_has_monthly_data(gain: dict | None) -> bool:
    if not gain or not isinstance(gain, dict):
        return False
    monthly = gain.get("monthly")
    return isinstance(monthly, list) and len(monthly) > 0


def _read_cache_file(path: Path | str) -> dict | None:
    json_path = Path(path)
    if not json_path.is_file():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"trader_gain_cache: JSON invalide ({json_path}): {exc}", file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def load_cached_gain(
    path: Path | str = DEFAULT_GAIN_CACHE_PATH,
    *,
    username: str | None = None,
) -> dict | None:
    """Charge la dernière réponse gain API sauvegardée (sans appel réseau)."""
    data = _read_cache_file(path)
    if not data:
        return None
    if username and str(data.get("username") or "").strip() != username:
        return None
    gain = data.get("gain")
    return gain if gain_has_monthly_data(gain) else None


def load_cached_perf_since_sep2022(
    path: Path | str = DEFAULT_GAIN_CACHE_PATH,
    *,
    username: str | None = None,
) -> dict | None:
    """Bloc « Portefeuille depuis sept. 2022 » (perf cumulée + annualisée) depuis le cache disque."""
    data = _read_cache_file(path)
    if not data:
        return None
    if username and str(data.get("username") or "").strip() != username:
        return None
    summary = data.get("perf_since_sep2022")
    if not isinstance(summary, dict):
        return None
    if summary.get("trader_pct") is None:
        return None
    return dict(summary)


def save_cached_gain(
    gain: dict,
    path: Path | str = DEFAULT_GAIN_CACHE_PATH,
    *,
    username: str = DEFAULT_USERNAME,
    perf_since_sep2022: dict | None = None,
) -> None:
    """Persiste les gains après un fetch API réussi."""
    if not gain_has_monthly_data(gain):
        return
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_cache_file(path) or {}
    payload = {
        "username": username,
        "updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "gain": gain,
    }
    ps = perf_since_sep2022 if isinstance(perf_since_sep2022, dict) else None
    if ps and ps.get("trader_pct") is not None:
        payload["perf_since_sep2022"] = ps
    elif isinstance(existing.get("perf_since_sep2022"), dict):
        payload["perf_since_sep2022"] = existing["perf_since_sep2022"]
    try:
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        _log.warning("save_cached_gain: impossible d'écrire %s (%s)", json_path, exc)


def fetch_trader_gain_with_cache(
    username: str = DEFAULT_USERNAME,
    path: Path | str = DEFAULT_GAIN_CACHE_PATH,
) -> dict | None:
    """
    Appelle l'API gain ; en cas d'échec ou réponse vide, retourne le cache disque.
    Met à jour le cache uniquement quand l'API renvoie des données monthly.
    """
    gain: dict | None = None
    try:
        gain = get_user_gain(username)
    except Exception as exc:
        print(f"fetch_trader_gain_with_cache({username!r}): {exc}", file=sys.stderr)

    if gain_has_monthly_data(gain):
        save_cached_gain(gain, path, username=username, perf_since_sep2022=None)
        return gain

    cached = load_cached_gain(path, username=username)
    if cached:
        print(
            f"Gain eToro indisponible pour {username!r}, utilisation du cache ({path}).",
            file=sys.stderr,
        )
        return cached
    return gain
