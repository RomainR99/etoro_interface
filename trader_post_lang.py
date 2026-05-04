"""Heuristique de langue des posts (alignée sur le filtre côté client)."""

from __future__ import annotations

import re


def infer_post_lang(message: str) -> str:
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


def filter_posts_by_ui_lang(posts: list[dict], ui_lang: str) -> list[dict]:
    if ui_lang not in ("fr", "en"):
        ui_lang = "en"
    out: list[dict] = []
    for p in posts:
        if not isinstance(p, dict):
            continue
        msg = str(p.get("message") or "")
        lang = infer_post_lang(msg)
        if lang == "de":
            continue
        if ui_lang == "fr" and lang == "fr":
            out.append(p)
        elif ui_lang == "en" and lang == "en":
            out.append(p)
    return out
