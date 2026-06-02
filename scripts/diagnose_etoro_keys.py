#!/usr/bin/env python3
"""Diagnostic clés eToro (prod: sudo + source /etc/etoro/interface.env avant)."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from env_load import load_app_dotenv

load_app_dotenv(ROOT)

BASE = "https://public-api.etoro.com/api/v1"
TRADER = "RomainRoth"


def main() -> int:
    api = (os.getenv("ETORO_API_KEY") or "").strip()
    user = (os.getenv("ETORO_USER_KEY") or "").strip()
    print("ETORO_API_KEY length:", len(api))
    print("ETORO_USER_KEY length:", len(user))
    if not api or not user:
        print("ERREUR: ETORO_API_KEY ou ETORO_USER_KEY vide.")
        return 1

    def hdr() -> dict[str, str]:
        return {
            "x-api-key": api,
            "x-user-key": user,
            "x-request-id": str(uuid.uuid4()),
        }

    r = requests.get(f"{BASE}/me", headers=hdr(), timeout=30)
    print(f"\nGET /me -> {r.status_code}")
    print(r.text[:400])
    me = r.json() if r.status_code == 200 else {}

    r2 = requests.get(
        f"{BASE}/user-info/people",
        headers=hdr(),
        params={"usernames": TRADER},
        timeout=30,
    )
    print(f"\nGET user-info/people ({TRADER}) -> {r2.status_code}")
    prof: dict = {}
    if r2.status_code == 200:
        users = r2.json().get("Users") or r2.json().get("users") or []
        prof = users[0] if users else {}
        print("  gcid:", prof.get("gcid"), "| realCID:", prof.get("realCID"))

    me_gcid = me.get("gcid")
    prof_gcid = prof.get("gcid")
    print(f"\n/me gcid={me_gcid!r}  |  {TRADER} gcid={prof_gcid!r}")
    if me_gcid and prof_gcid and str(me_gcid) != str(prof_gcid):
        print(
            "\n>>> ETORO_USER_KEY est liée à un AUTRE compte que "
            f"{TRADER}. Recrée la clé connecté sur etoro.com comme {TRADER}."
        )

    uid = prof_gcid or me_gcid
    if uid:
        r3 = requests.get(
            f"{BASE}/feeds/users/{uid}",
            headers=hdr(),
            params={"take": 5, "offset": 0},
            timeout=30,
        )
        print(f"\nGET /feeds/users/{uid} -> {r3.status_code}")
        print(r3.text[:400])

    r4 = requests.get(f"{BASE}/watchlists", headers=hdr(), timeout=30)
    print(f"\nGET /watchlists -> {r4.status_code}")
    print(r4.text[:200])

    if r.status_code == 200 and uid and r3.status_code == 200:
        disc = (r3.json() or {}).get("discussions") or []
        print(f"\nOK: feed accessible ({len(disc)} discussions dans l'échantillon).")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
