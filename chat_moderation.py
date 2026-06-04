"""Modération éthique / légale du chatbot (refus avant appel LLM)."""

from __future__ import annotations

import re

# Contexte criminel / immoral (FR + EN) — pas une liste exhaustive, filet de sécurité serveur.
_UNETHICAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"marché\s+noir|black\s+market",
        r"trafic\s+d['\u2019]?\s*organes?|organ\s+traffick",
        r"vend(re|u|ant)\s+.{0,30}organes?",
        r"vente\s+d['\u2019]?\s*organes?",
        r"blanchiment|money\s+launder",
        r"trafic\s+(de\s+)?drogues?|drug\s+traffick|narco",
        r"trafic\s+d['\u2019]?\s*êtres|human\s+traffick",
        r"proxénétisme|escort\s+mineur",
        r"pump\s*[&]\s*dump|délit\s+d['\u2019]?\s*initié",
        r"évasion\s+fiscale\s+illégal|tax\s+fraud",
        r"argent\s+(sale|illégal)|illegal\s+(money|funds|proceeds)",
        r"provenant\s+(d['\u2019]?\s*)?(un\s+)?crime|from\s+(a\s+)?crime",
        r"meurtre|assassinat|kidnap|terroris",
    )
)


def message_needs_ethical_refusal(text: str) -> bool:
    """True si le message décrit une activité illégale / immorale (y compris « comment investir »)."""
    normalized = (text or "").strip()
    if len(normalized) < 8:
        return False
    return any(pat.search(normalized) for pat in _UNETHICAL_PATTERNS)


def ethical_refusal_reply(ui_lang: str) -> str:
    """Réponse fixe, sans ressource vidéo/citation."""
    if ui_lang == "en":
        return (
            "I can't help with illegal or unethical activities (e.g. organ trafficking or black markets). "
            "Money from crime must not be invested through this chat. "
            "Ask a question about lawful saving or investing instead."
        )
    return (
        "Je ne peux pas aider avec des activités illégales ou contraires à l'éthique "
        "(ex. trafic d'organes, marché noir). L'argent issu de crimes ne doit pas être « investi » ici. "
        "Pose une question d'éducation financière sur des revenus licites."
    )


def is_ethics_refusal_reply(text: str) -> bool:
    """Détecte une réponse de refus éthique (pour ne pas ajouter vidéo/citation)."""
    lower = (text or "").strip().lower()
    markers = (
        "activités illégales",
        "illegal or unethical",
        "trafic d'organes",
        "organ trafficking",
        "argent issu de crimes",
        "money from crime",
    )
    return any(m in lower for m in markers)
