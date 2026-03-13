"""Deux posts par jour en anglais : 1) portefeuille / instruments, 2) actualité marché via flux RSS Yahoo Finance (NASDAQ, S&P 500, CAC 40)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import calendar
from datetime import datetime, timezone
from html import unescape
from typing import Any, Callable

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://www.zonebourse.com"
ACTUALITES_URL = f"{BASE_URL}/actualite-bourse/"
REQUEST_TIMEOUT = 15
OPENAI_MODEL = "gpt-4o-mini"

# Flux RSS Yahoo Finance pour le post actualité marché (remplace Zonebourse)
YAHOO_FINANCE_RSS_URLS = [
    "https://finance.yahoo.com/rss/",
    "https://finance.yahoo.com/news/rssindex",
]
# Flux RSS par symbole (post 1 : actualités du jour par instrument)
YAHOO_FINANCE_HEADLINE_RSS = "https://finance.yahoo.com/rss/headline?s={symbol}"

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")


def _load_prompt(filename: str, fallback: str = "") -> str:
    """Charge un prompt depuis prompts/<filename>."""
    path = os.path.join(_PROMPTS_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return fallback


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _get_headers(referer: str | None = None) -> dict[str, str]:
    h = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }
    if referer:
        h["Referer"] = referer
    return h


def _extract_from_jsonld(soup: BeautifulSoup) -> str | None:
    """Tente d'extraire le texte via le JSON-LD (champ articleBody)."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if isinstance(obj, dict) and isinstance(obj.get("articleBody"), str):
                text = unescape(obj["articleBody"]).strip()
                if text:
                    return text
    return None


def _extract_from_dom(soup: BeautifulSoup) -> str | None:
    """Fallback : extrait le texte depuis le bloc HTML principal de l'article."""
    for selector in (
        "div.article-text.article-text--clear",
        "div.article-text",
        "article .content",
        "article",
        "[itemprop='articleBody']",
        "main",
        ".content",
        "#content",
    ):
        node = soup.select_one(selector)
        if not node:
            continue
        for br in node.find_all("br"):
            br.replace_with("\n")
        text = node.get_text("\n", strip=True)
        text = unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) > 100:  # évite les blocs vides ou nav
            return text
    return None


def _extract_any_text(soup: BeautifulSoup) -> str:
    """Dernier recours : concatène tous les paragraphes du corps de page."""
    parts = []
    for tag in soup.find_all(["p", "div"], class_=re.compile(r"article|content|body|text", re.I)):
        if tag.get("id") in ("comments", "sidebar", "nav", "header", "footer"):
            continue
        t = tag.get_text("\n", strip=True)
        if t and len(t) > 30:
            parts.append(t)
    if parts:
        return "\n\n".join(parts[:20])  # max 20 blocs
    for p in soup.find_all("p"):
        t = p.get_text(strip=True)
        if t and len(t) > 40:
            parts.append(t)
    return "\n\n".join(parts[:15]) if parts else ""


def _extract_title_from_jsonld(soup: BeautifulSoup) -> str | None:
    """Extrait le titre depuis le JSON-LD (champ headline)."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if isinstance(obj, dict) and isinstance(obj.get("headline"), str):
                title = unescape(obj["headline"]).strip()
                if title:
                    return title
    return None


def _extract_title_from_dom(soup: BeautifulSoup) -> str | None:
    """Fallback : titre depuis un h1 ou titre de l'article."""
    for sel in ("h1.article-headline", "h1", ".article-title"):
        node = soup.select_one(sel)
        if node:
            t = node.get_text(strip=True)
            if t:
                return unescape(t)
    return None


def extract_article_text(html: str) -> str:
    """Extrait le corps de l'article depuis le HTML (JSON-LD puis DOM, puis tout texte)."""
    soup = BeautifulSoup(html, "lxml")
    text = _extract_from_jsonld(soup)
    if text:
        return text
    text = _extract_from_dom(soup)
    if text:
        return text
    text = _extract_any_text(soup)
    if text and len(text.strip()) > 80:
        return text
    raise RuntimeError("Impossible de trouver le texte de l'article (sélecteurs/JSON-LD introuvables).")


def extract_article_title(soup: BeautifulSoup) -> str:
    """Extrait le titre de l'article."""
    title = _extract_title_from_jsonld(soup)
    if title:
        return title
    title = _extract_title_from_dom(soup)
    if title:
        return title
    return "Sans titre"


def _summarize_with_prompt(user_content: str, prompt_prefix: str) -> dict[str, str] | None:
    """Génère titre + résumé via OpenAI. prompt_prefix + user_content = message user. Retourne {"titre", "resume"} ou None."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not (prompt_prefix.strip() or user_content.strip()):
        return None
    text = (prompt_prefix + user_content).strip()[:14000]
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You answer only with valid JSON, no markdown or commentary."},
                {"role": "user", "content": text},
            ],
            temperature=0.3,
        )
        raw = response.choices[0].message.content
        if not raw:
            return None
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?", "", raw).rstrip("`")
        data = json.loads(raw)
        titre = (data.get("titre") or "").strip()
        resume = (data.get("resume") or "").strip()
        if isinstance(resume, list):
            resume = "\n".join(str(l).strip() for l in resume)
        return {"titre": titre or "Untitled", "resume": resume}
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("OpenAI summary failed: %s", e)
        return None


def _generate_instruments_post(instruments: list[dict[str, Any]]) -> dict[str, str] | None:
    """Génère un post en anglais à partir des flux RSS Yahoo Finance du jour pour chaque instrument du portefeuille. Retourne {"titre", "resume"} ou None."""
    content = _build_portfolio_rss_content(
        instruments or [],
        entries_per_instrument=3,
        only_today=True,
    )
    prompt_prefix = _load_prompt(
        "post_instruments.txt",
        "Write a short daily post in English based on the RSS headlines below. Reply with JSON: titre, resume (5 lines).\n\n",
    )
    return _summarize_with_prompt(content, prompt_prefix)


def _summarize_market_news(article_text: str) -> dict[str, str] | None:
    """Résumé en anglais d'un article marché + contexte NASDAQ/S&P 500/CAC 40. Retourne {"titre", "resume"} ou None."""
    prompt_prefix = _load_prompt("post_market_news.txt", "Summarize in English as JSON: titre, resume (5 lines). Mention NASDAQ, S&P 500, CAC 40.\n\n")
    text = (article_text or "").strip()[:12000]
    return _summarize_with_prompt(text, prompt_prefix)


def _normalize_article_url(href: str) -> str | None:
    """Retourne l'URL absolue d'un article si elle pointe vers une page d'article Zonebourse.
    Format attendu: .../actualite-bourse/slug-titre-abc123def
    """
    if not href or not href.strip():
        return None
    href = href.strip()
    if href.startswith("/"):
        href = BASE_URL + href
    # Zonebourse utilise /actualite-bourse/ (singulier) pour les articles
    if BASE_URL not in href or "/actualite-bourse/" not in href:
        return None
    # Exclure la page listing (pas de slug après actualite-bourse/)
    path = href.split("zonebourse.com", 1)[-1].rstrip("/")
    if path in ("/actualite-bourse", "/actualite-bourse/"):
        return None
    # Doit avoir un slug après actualite-bourse/ (ex: titre-article-ce7e5cd3df81f627)
    if path.count("/") < 2:
        return None
    return href


# Fallback si la page listing ne renvoie pas de liens (contenu chargé en JS)
FALLBACK_ARTICLE_URLS = [
    f"{BASE_URL}/actualite-bourse/le-retour-de-l-inflationa-et-des-hausses-de-taux--ce7e5cd2da8cf321",
    f"{BASE_URL}/actualite-bourse/les-bourses-europeennes-rebondissent-apres-deux-seances-dans-le-rouge-ce7e5cd3df81f627",
    f"{BASE_URL}/actualite-bourse/marches-prudents-mais-haussiers-le-mib-repart-de-46-200-ce7e5ddad888f52d",
]


def _fetch_article_links(limit: int = 3) -> tuple[list[str], bool]:
    """Récupère les URLs des N derniers articles depuis la section Hot News de la page actualités.
    Zonebourse n'a pas d'API : on utilise BeautifulSoup pour extraire les liens Hot News.
    Retourne (urls, used_fallback) où used_fallback=True si la section Hot News n'a pas fourni de liens."""
    urls: list[str] = []
    try:
        resp = requests.get(ACTUALITES_URL, headers=_get_headers(), timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return (FALLBACK_ARTICLE_URLS[:limit], True)
        soup = BeautifulSoup(resp.text, "lxml")
        # Trouver l'élément contenant "Hot News" puis le tableau suivant
        hot_news_table = None
        for elem in soup.find_all(string=re.compile(r"Hot\s*News", re.I)):
            tag = elem.parent
            if tag:
                hot_news_table = tag.find_next("table")
                if hot_news_table:
                    break
        if hot_news_table:
            seen: set[str] = set()
            for a in hot_news_table.find_all("a", href=True):
                if len(urls) >= limit:
                    break
                url = _normalize_article_url(a["href"])
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
        # Fallback : chercher tous les liens actualite-bourse dans la page (ordre d'apparition)
        if not urls:
            seen = set()
            for a in soup.find_all("a", href=True):
                if len(urls) >= limit:
                    break
                url = _normalize_article_url(a["href"])
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
    except Exception:
        pass
    if not urls:
        urls = FALLBACK_ARTICLE_URLS[:limit]
        return (urls[:limit], True)
    return (urls[:limit], False)


def _fetch_yahoo_rss_entries(limit: int = 1) -> list[dict[str, str]]:
    """Récupère les N derniers articles depuis le flux RSS Yahoo Finance.
    Retourne une liste de dicts avec 'title', 'description', 'link'."""
    try:
        import feedparser
    except ImportError:
        return []
    entries: list[dict[str, str]] = []
    for url in YAHOO_FINANCE_RSS_URLS:
        if len(entries) >= limit:
            break
        try:
            feed = feedparser.parse(url, request_headers=_get_headers(), request_timeout=REQUEST_TIMEOUT)
            if not getattr(feed, "entries", None):
                continue
            for entry in feed.entries:
                if len(entries) >= limit:
                    break
                title = (entry.get("title") or "").strip()
                desc = (entry.get("description") or entry.get("summary") or "").strip()
                link = (entry.get("link") or "").strip()
                if title or desc:
                    entries.append({"title": title, "description": desc, "link": link})
        except Exception:
            continue
    return entries[:limit]


def _fetch_yahoo_rss_for_symbol(
    symbol: str,
    limit: int = 5,
    only_today: bool = True,
) -> list[dict[str, Any]]:
    """Récupère les entrées RSS Yahoo Finance pour un symbole (headline feed).
    Retourne une liste de dicts avec 'title', 'description', 'published_parsed'.
    Si only_today=True, ne garde que les entrées datées du jour (UTC)."""
    if not (symbol or "").strip():
        return []
    try:
        import feedparser
    except ImportError:
        return []
    url = YAHOO_FINANCE_HEADLINE_RSS.format(symbol=symbol.strip())
    today_date = datetime.now(timezone.utc).date()
    entries: list[dict[str, Any]] = []
    try:
        feed = feedparser.parse(url, request_headers=_get_headers(), request_timeout=REQUEST_TIMEOUT)
        for entry in getattr(feed, "entries", [])[: limit + 10]:
            if len(entries) >= limit:
                break
            if only_today:
                pub = entry.get("published_parsed")
                if pub:
                    try:
                        # published_parsed is UTC struct_time
                        ts = calendar.timegm(pub)
                        entry_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                        if entry_date != today_date:
                            continue
                    except (TypeError, OSError, ValueError):
                        pass
            title = (entry.get("title") or "").strip()
            desc = (entry.get("description") or entry.get("summary") or "").strip()
            entries.append({"title": title, "description": desc})
    except Exception:
        pass
    return entries[:limit]


def _build_portfolio_rss_content(
    instruments: list[dict[str, Any]],
    entries_per_instrument: int = 3,
    only_today: bool = True,
) -> str:
    """Construit un bloc texte : pour chaque instrument, les titres/descriptions RSS du jour.
    Utilisé comme entrée au prompt du post 1."""
    if not instruments:
        return "No instruments in portfolio."
    parts: list[str] = []
    for inv in instruments[:25]:
        sym = (inv.get("symbol") or inv.get("displayName") or "").strip()
        name = (inv.get("displayname") or inv.get("displayName") or inv.get("symbol") or "").strip()
        label = f"{sym} ({name})" if (sym and name) else (name or sym or str(inv.get("instrumentId", "")))
        if not label:
            continue
        entries = _fetch_yahoo_rss_for_symbol(sym or name, limit=entries_per_instrument, only_today=only_today)
        if not entries and only_today:
            entries = _fetch_yahoo_rss_for_symbol(sym or name, limit=entries_per_instrument, only_today=False)
        if not entries:
            parts.append(f"Instrument: {label}\n(No RSS headlines for today.)")
            continue
        lines = [f"Instrument: {label}"]
        for e in entries:
            t = (e.get("title") or "").strip()
            d = (e.get("description") or "").strip()
            if d:
                soup = BeautifulSoup(d, "lxml")
                d = soup.get_text(" ", strip=True)[:400]
            lines.append(f"- {t}" + (f"\n  {d}" if d else ""))
        parts.append("\n".join(lines))
    return "\n\n".join(parts) if parts else "No instruments in portfolio."


_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
ZONEBOURSE_CACHE_PATH = os.path.join(_CACHE_DIR, "zonebourse_posts.json")
ZONEBOURSE_IMAGES_DIR = os.path.join(_CACHE_DIR, "zonebourse_images")


def _load_date_cache(cache_path: str) -> dict[str, Any]:
    """Charge le cache par date : { "date": "YYYY-MM-DD", "items": [ { type, title, summary, image_file }, ... ] }."""
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "items" in data and "date" in data:
            return data
    except Exception:
        pass
    return {}


def _save_date_cache(cache_path: str, cache: dict[str, Any]) -> None:
    """Sauvegarde le cache par date."""
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _day_image_filename(day: str, kind: str) -> str:
    """Nom de fichier image par jour et type (instruments / news)."""
    h = hashlib.sha256(f"{day}:{kind}".encode("utf-8")).hexdigest()[:12]
    return f"{kind}_{day}_{h}.png"


def _save_image_from_data_url(data_url: str, filepath: str) -> bool:
    """Extrait le base64 d'un data URL et sauvegarde en PNG. Retourne True si succès."""
    if not data_url or not data_url.startswith("data:image"):
        return False
    try:
        _, b64 = data_url.split(",", 1)
        raw = base64.b64decode(b64)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(raw)
        return True
    except Exception:
        return False


def _build_instruments_list_prompt(instruments: list[str]) -> str:
    """Construit un prompt textuel intermédiaire à partir de mentioned_instruments.

    Exemple de rendu :

    Show only these instruments on the screens:
    - Netflix
    - Verizon
    - Siemens Energy
    - 2CRSi
    - FDJ
    """
    cleaned = [str(x).strip() for x in instruments if str(x).strip()]
    if not cleaned:
        return ""
    lines = ["Show only these instruments on the screens:"]
    # Limiter pour garder un prompt compact
    for name in cleaned[:30]:
        lines.append(f"- {name}")
    return "\n".join(lines)


def _build_image_prompt(title: str, summary: str, instruments: list[str] | None = None) -> str:
    """Construit le prompt intermédiaire pour la génération d'image.

    Étape 1 : contexte texte (titre + 1ère ligne du résumé).
    Étape 2 : texte intermédiaire listant explicitement les instruments à afficher,
              construit à partir de mentioned_instruments.
    Ce prompt sera ensuite injecté dans le template principal image_instruments.txt
    par la couche d'appel d'image (dans app.py).
    """
    t = (title or "").strip()
    s = (summary or "").strip()
    first_line = s.split("\n")[0] if s else ""
    base = (t + " " + first_line).strip()
    if instruments:
        list_prompt = _build_instruments_list_prompt(instruments)
        if list_prompt:
            base = (base + "\n\n" + list_prompt).strip()
    return base[:800]


def get_latest_news(
    limit: int = 2,
    cache_path: str | None = ZONEBOURSE_CACHE_PATH,
    generate_image_fn: Callable[[str, int, str], str | None] | None = None,
    portfolio_instruments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Deux posts par jour en anglais :
    1) Post sur l'ensemble des instruments du portefeuille.
    2) Post sur l'actualité du jour (flux RSS Yahoo Finance) + NASDAQ / S&P 500 / CAC 40.
    Cache par date (YYYY-MM-DD). generate_image_fn(prompt, style_index, image_kind) avec image_kind "instruments" ou "news".
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cache = _load_date_cache(cache_path) if cache_path else {}
    use_cache = bool(cache_path and generate_image_fn)
    instruments = portfolio_instruments or []

    if use_cache and cache.get("date") == today and len(cache.get("items", [])) >= 2:
        results = []
        for entry in cache["items"][:2]:
            item = {"title": entry.get("title", ""), "summary": entry.get("summary", ""), "date": today}
            if entry.get("image_file"):
                item["image_url"] = f"/api/zonebourse-image/{entry['image_file']}"
            results.append(item)
        return {"items": results, "used_fallback": False}

    results: list[dict[str, Any]] = []
    cache_items: list[dict[str, Any]] = []

    # Post 1 : instruments du portefeuille
    instruments_post = _generate_instruments_post(instruments)
    # Construire une liste explicite d'instruments à afficher (nom lisible ou symbole)
    mentioned_instruments: list[str] = []
    for inv in instruments:
        name = (inv.get("displayname") or inv.get("displayName") or "").strip()
        sym = (inv.get("symbol") or "").strip()
        if name and sym:
            label = f"{sym} ({name})"
        else:
            label = name or sym
        if label:
            mentioned_instruments.append(label)

    if instruments_post:
        title1 = instruments_post["titre"]
        summary1 = instruments_post["resume"]
        # Ne pas afficher de date dans le post : supprimer toute ligne de date (YYYY-MM-DD) en tête du résumé
        if summary1:
            lines = summary1.strip().splitlines()
            while lines and re.match(r"^\d{4}-\d{2}-\d{2}\s*$", lines[0].strip()):
                lines.pop(0)
            summary1 = "\n".join(lines).strip() if lines else summary1
    else:
        title1 = "Portfolio overview"
        summary1 = "Unable to generate instruments summary. Check OPENAI_API_KEY and portfolio data."
    item1: dict[str, Any] = {
        "title": title1,
        "summary": summary1,
        "date": today,
        "mentioned_instruments": mentioned_instruments,
    }
    image_file1: str | None = None
    if generate_image_fn:
        prompt1 = _build_image_prompt(title1, summary1, mentioned_instruments)
        data_url1 = generate_image_fn(prompt1, 0, "instruments")
        if data_url1 and use_cache:
            image_file1 = _day_image_filename(today, "instruments")
            img_path = os.path.join(ZONEBOURSE_IMAGES_DIR, image_file1)
            if _save_image_from_data_url(data_url1, img_path):
                item1["image_url"] = f"/api/zonebourse-image/{image_file1}"
            else:
                image_file1 = None
                item1["image_data_url"] = data_url1
        elif data_url1:
                item1["image_data_url"] = data_url1
    results.append(item1)
    cache_items.append(
        {
            "type": "instruments",
            "title": title1,
            "summary": summary1,
            "image_file": image_file1,
            "mentioned_instruments": mentioned_instruments,
        }
    )

    # Post 2 : actualité marché (flux RSS Yahoo Finance + NASDAQ/SP500/CAC40)
    used_fallback = True
    rss_entries = _fetch_yahoo_rss_entries(limit=1)
    title2 = "Market news"
    summary2 = "No market article available. Check Yahoo Finance RSS."
    if rss_entries:
        entry = rss_entries[0]
        article_text = (entry.get("title") or "").strip()
        desc = (entry.get("description") or "").strip()
        if desc:
            # Décoder le HTML en texte brut pour le prompt
            soup_desc = BeautifulSoup(desc, "lxml")
            desc_plain = soup_desc.get_text("\n", strip=True)
            desc_plain = re.sub(r"\n{3,}", "\n\n", desc_plain).strip()
            article_text = (article_text + "\n\n" + desc_plain).strip() if desc_plain else article_text
        if article_text and len(article_text) >= 30:
            summarized = _summarize_market_news(article_text)
            if summarized:
                used_fallback = False
                title2 = summarized["titre"]
                summary2 = summarized["resume"]
                # Supprimer toute ligne de date (YYYY-MM-DD) en tête du résumé
                if summary2:
                    lines = summary2.strip().splitlines()
                    while lines and re.match(r"^\d{4}-\d{2}-\d{2}\s*$", lines[0].strip()):
                        lines.pop(0)
                    summary2 = "\n".join(lines).strip() if lines else summary2
    item2: dict[str, Any] = {"title": title2, "summary": summary2, "date": today}
    image_file2: str | None = None
    if generate_image_fn:
        prompt2 = _build_image_prompt(title2, summary2)
        data_url2 = generate_image_fn(prompt2, 0, "news")
        if data_url2 and use_cache:
            image_file2 = _day_image_filename(today, "news")
            img_path = os.path.join(ZONEBOURSE_IMAGES_DIR, image_file2)
            if _save_image_from_data_url(data_url2, img_path):
                item2["image_url"] = f"/api/zonebourse-image/{image_file2}"
            else:
                image_file2 = None
                item2["image_data_url"] = data_url2
        elif data_url2:
            item2["image_data_url"] = data_url2
    results.append(item2)
    cache_items.append({"type": "market_news", "title": title2, "summary": summary2, "image_file": image_file2})

    if use_cache and cache_path:
        _save_date_cache(cache_path, {"date": today, "items": cache_items})

    return {"items": results, "used_fallback": used_fallback}
