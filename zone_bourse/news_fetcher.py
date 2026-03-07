"""Récupère les 3 dernières actualités Zonebourse (BeautifulSoup), puis résumé + titre via OpenAI."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
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

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")


def _load_summary_prompt() -> str:
    """Charge le prompt de résumé depuis prompts/zonebourse_summary.txt."""
    path = os.path.join(_PROMPTS_DIR, "zonebourse_summary.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return (
            "Tu es un rédacteur financier. Voici le texte d'un article boursier.\n\n"
            "Réponds UNIQUEMENT en JSON valide avec exactement deux clés :\n"
            '- "titre" : un titre court et percutant (une phrase).\n'
            '- "resume" : un résumé en exactement 5 lignes (5 phrases courtes, une par ligne, séparées par les retours à la ligne).\n\n'
            "Article :\n\n"
        )


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


def _summarize_with_openai(article_text: str) -> dict[str, str] | None:
    """Génère un titre et un résumé en 5 lignes via OpenAI. Retourne {"titre": ..., "resume": ...} ou None."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not article_text.strip():
        return None
    # Limiter la taille pour rester sous les limites de contexte
    text = article_text.strip()[:12000]
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Tu réponds uniquement en JSON valide, sans markdown ni commentaire."},
                {"role": "user", "content": _load_summary_prompt() + text},
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
        return {"titre": titre or "Sans titre", "resume": resume}
    except Exception:
        return None


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


_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
ZONEBOURSE_CACHE_PATH = os.path.join(_CACHE_DIR, "zonebourse_posts.json")
ZONEBOURSE_IMAGES_DIR = os.path.join(_CACHE_DIR, "zonebourse_images")


def _load_zonebourse_cache(cache_path: str) -> dict[str, dict[str, Any]]:
    """Charge le cache posts + images depuis data/zonebourse_posts.json."""
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_zonebourse_cache(cache_path: str, cache: dict[str, dict[str, Any]]) -> None:
    """Sauvegarde le cache posts + images."""
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=None)
    except Exception:
        pass


def _url_to_image_filename(url: str) -> str:
    """Génère un nom de fichier unique pour une URL (hash)."""
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"{h}.png"


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


def _build_image_prompt(title: str, summary: str) -> str:
    """Construit le prompt pour la génération d'image."""
    t = (title or "").strip()
    s = (summary or "").strip()
    first_line = s.split("\n")[0] if s else ""
    return (t + " " + first_line).strip()[:400]


def get_latest_news(
    limit: int = 3,
    cache_path: str | None = ZONEBOURSE_CACHE_PATH,
    generate_image_fn: Callable[[str, int], str | None] | None = None,
) -> dict[str, Any]:
    """
    Récupère les N dernières actualités Zonebourse (HTML + BeautifulSoup),
    puis génère pour chacune un titre et un résumé en 5 lignes via OpenAI.
    Si cache_path et generate_image_fn fournis, utilise le cache (url -> {title, summary, image_file}).
    Les images sont stockées en PNG dans data/zonebourse_images/. Retourne image_url (ou image_data_url en fallback).
    """
    urls, used_fallback = _fetch_article_links(limit=limit)
    cache = _load_zonebourse_cache(cache_path) if cache_path else {}
    use_cache = bool(cache_path and generate_image_fn)
    results: list[dict[str, Any]] = []
    for url in urls:
        if use_cache and url in cache:
            entry = cache[url].copy()
            if entry.get("image_file"):
                entry["image_url"] = f"/api/zonebourse-image/{entry['image_file']}"
                entry.pop("image_data_url", None)
            results.append(entry)
            continue
        try:
            resp = requests.get(url, headers=_get_headers(referer=ACTUALITES_URL), timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            try:
                body = extract_article_text(resp.text)
            except RuntimeError:
                body = _extract_any_text(soup)
            title = extract_article_title(soup)
            summary = ""
            if body and len(body.strip()) >= 50:
                summarized = _summarize_with_openai(body)
                if summarized:
                    title = summarized["titre"]
                    summary = summarized["resume"]
                else:
                    summary = body.strip()[:500] + ("…" if len(body) > 500 else "")
            else:
                summary = "Résumé non disponible (article inaccessible ou structure de page modifiée)."
            item: dict[str, Any] = {"title": title, "summary": summary}
            image_data_url: str | None = None
            image_file: str | None = None
            if generate_image_fn:
                prompt = _build_image_prompt(title, summary)
                style_index = random.randint(0, 5)
                image_data_url = generate_image_fn(prompt, style_index)
                if image_data_url and use_cache:
                    image_file = _url_to_image_filename(url)
                    img_path = os.path.join(ZONEBOURSE_IMAGES_DIR, image_file)
                    if _save_image_from_data_url(image_data_url, img_path):
                        item["image_url"] = f"/api/zonebourse-image/{image_file}"
                    else:
                        image_file = None
                        item["image_data_url"] = image_data_url
                elif image_data_url:
                    item["image_data_url"] = image_data_url
            if use_cache:
                cache[url] = {
                    "title": item["title"],
                    "summary": item["summary"],
                    "image_file": image_file,
                    "cached_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            results.append(item)
        except Exception:
            continue
    if use_cache and cache_path:
        _save_zonebourse_cache(cache_path, cache)
    if not results:
        results = [
            {"title": "Actualité 1 (exemple)", "summary": "Le chargement des articles Zonebourse a échoué (vérifier la connexion ou que zonebourse.com autorise les requêtes)."},
            {"title": "Actualité 2 (exemple)", "summary": "Vous pouvez tester avec des fichiers HTML locaux ou vérifier OPENAI_API_KEY dans .env pour les résumés."},
            {"title": "Actualité 3 (exemple)", "summary": "Consultez les logs du serveur (python app.py) pour voir les erreurs éventuelles."},
        ]
    return {"items": results, "used_fallback": used_fallback}
