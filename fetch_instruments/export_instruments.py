#!/usr/bin/env python3
"""
Récupère les instruments eToro par plage d'IDs et les exporte en CSV et JSON.
Colonnes : n°, id, symbole.

Usage (depuis la racine du projet) :
    python fetch_instruments/export_instruments.py              # 1001 à 1010 (défaut)
    python fetch_instruments/export_instruments.py 1001 1010   # id_min id_max
    python fetch_instruments/export_instruments.py 1011 1020   # étape suivante
"""

import argparse
import csv
import json
import os
import sys

# Racine du projet (parent de ce dossier)
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Charger .env depuis la racine du projet
from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

from etoro_client import get_stocks_by_id_range


def main():
    parser = argparse.ArgumentParser(description="Export instruments eToro (n°, id, symbole) en CSV et JSON.")
    parser.add_argument("id_min", type=int, nargs="?", default=1001, help="ID minimum (défaut: 1001)")
    parser.add_argument("id_max", type=int, nargs="?", default=1010, help="ID maximum (défaut: 1010)")
    args = parser.parse_args()
    id_min, id_max = args.id_min, args.id_max
    if id_min > id_max:
        id_min, id_max = id_max, id_min

    out_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(out_dir, f"instruments_{id_min}_{id_max}.csv")
    json_path = os.path.join(out_dir, f"instruments_{id_min}_{id_max}.json")

    print(f"Récupération des instruments eToro (ID {id_min} à {id_max})...")
    stocks = get_stocks_by_id_range(id_min, id_max)

    # Format : n°, id, symbole
    rows = [
        {"n": i + 1, "id": s["instrumentId"], "symbole": s.get("symbol") or str(s["instrumentId"])}
        for i, s in enumerate(stocks)
    ]

    # CSV : n°, id, symbole (UTF-8 avec BOM pour Excel)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["n°", "id", "symbole"])
        w.writerows([[r["n"], r["id"], r["symbole"]] for r in rows])

    # JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    print(f"{len(rows)} instruments exportés (ID {id_min}–{id_max}).")
    print(f"  CSV :  {csv_path}")
    print(f"  JSON : {json_path}")


if __name__ == "__main__":
    main()
