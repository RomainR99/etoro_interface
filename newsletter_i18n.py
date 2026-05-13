"""Textes et gabarits newsletter (FR / EN) + lecture ui_lang stockée dans contact_messages.message."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone


def normalize_newsletter_lang(lang: str | None) -> str:
    return "fr" if (lang or "").strip().lower() == "fr" else "en"


def _newsletter_site_root(site_base_url: str) -> str:
    root = (site_base_url or "").strip().rstrip("/")
    return root if root else "https://romainroth.com"


def build_newsletter_site_legal_footer_html(lang: str, site_base_url: str) -> str:
    """Liens légaux + copyright (alignés sur le footer du site), hors carte blanche principale."""
    lang = normalize_newsletter_lang(lang)
    root = _newsletter_site_root(site_base_url)
    href_ml = html.escape(f"{root}/mentions-legales")
    href_pr = html.escape(f"{root}/confidentialite")
    href_ck = html.escape(f"{root}/cookies")
    year = datetime.now(timezone.utc).year
    copy_txt = f"© Romain Roth {year}"
    # Clients mail : <nav> + blocs multi-lignes peuvent rester alignés à gauche ; align="center" + <p> centré aide.
    outer_wrap = (
        "margin-top:20px;padding-top:18px;border-top:1px solid #e5e7eb;"
        "width:100%;max-width:100%;text-align:center;"
    )
    legal_line = (
        "margin:0 0 8px;padding:0;line-height:1.6;"
        "font-size:11.5px;color:#8b949e;text-align:center;"
    )
    link_a = "color:#8b949e;text-decoration:none;"
    sep = '<span style="margin:0 5px;color:#8b949e;opacity:0.55;" aria-hidden="true">·</span>'
    copy_p = "text-align:center;margin:0;padding:0.75rem 0 0;font-size:12px;color:#8b949e;"
    if lang == "fr":
        l1, l2, l3 = "Mentions légales", "Politique de confidentialité", "Politique de cookies"
        nav_aria = "Informations légales"
    else:
        l1, l2, l3 = "Legal notice", "Privacy policy", "Cookie policy"
        nav_aria = "Legal"
    nav_aria_esc = html.escape(nav_aria)
    links_row = (
        f'<a href="{href_ml}" target="_blank" rel="noopener noreferrer" style="{link_a}">{html.escape(l1)}</a>'
        f"{sep}"
        f'<a href="{href_pr}" target="_blank" rel="noopener noreferrer" style="{link_a}">{html.escape(l2)}</a>'
        f"{sep}"
        f'<a href="{href_ck}" target="_blank" rel="noopener noreferrer" style="{link_a}">{html.escape(l3)}</a>'
    )
    return (
        f'      <div align="center" style="{outer_wrap}">\n'
        f'        <div role="navigation" aria-label="{nav_aria_esc}" style="text-align:center;width:100%;">\n'
        f'          <p style="{legal_line}">{links_row}</p>\n'
        f'        </div>\n'
        f'        <p style="{copy_p}">{html.escape(copy_txt)}</p>\n'
        "      </div>"
    )


def build_newsletter_site_legal_footer_plain(lang: str, site_base_url: str) -> str:
    lang = normalize_newsletter_lang(lang)
    root = _newsletter_site_root(site_base_url)
    year = datetime.now(timezone.utc).year
    if lang == "fr":
        return (
            f"Mentions légales : {root}/mentions-legales\n"
            f"Politique de confidentialité : {root}/confidentialite\n"
            f"Politique de cookies : {root}/cookies\n"
            f"© Romain Roth {year}\n"
        )
    return (
        f"Legal notice: {root}/mentions-legales\n"
        f"Privacy policy: {root}/confidentialite\n"
        f"Cookie policy: {root}/cookies\n"
        f"© Romain Roth {year}\n"
    )


def parse_newsletter_ui_lang_from_message(message: str | None) -> str:
    if not message:
        return "fr"
    m = re.search(r"\bui_lang=(fr|en)\b", message)
    if m:
        return m.group(1)
    return "fr"


def newsletter_subscribe_message_body(ui_lang: str) -> str:
    """Corps enregistré pour subject=Newsletter ; parse_newsletter_ui_lang_from_message en extrait la langue."""
    return (
        f"Newsletter opt-in; ui_lang={normalize_newsletter_lang(ui_lang)}; "
        "user requested regular updates by email; "
        "privacy respected; details not shared with third parties."
    )


def newsletter_lang_sync_message_body(ui_lang: str) -> str:
    """Nouvelle ligne Newsletter après changement de drapeau sur le site (même parsing ui_lang=)."""
    return (
        f"Newsletter language preference update; ui_lang={normalize_newsletter_lang(ui_lang)}; "
        "synced from site language control."
    )


def welcome_email_subject(lang: str) -> str:
    return (
        "Bienvenue — Newsletter Romain Roth"
        if normalize_newsletter_lang(lang) == "fr"
        else "Welcome — Romain Roth newsletter"
    )


def format_new_posts_hello_html(lang: str, recipient_name: str) -> str:
    """Ligne de salutation HTML ; le nom est échappé."""
    lang = normalize_newsletter_lang(lang)
    safe = html.escape((recipient_name or "").strip())
    if lang == "fr":
        return f"Bonjour {safe}," if safe else "Bonjour,"
    return f"Hello {safe}," if safe else "Hello,"


def new_posts_image_alt(lang: str) -> str:
    return "Illustration du post eToro" if normalize_newsletter_lang(lang) == "fr" else "eToro post illustration"


def new_posts_empty_posts_html(lang: str) -> str:
    return (
        "<p style='margin:0 0 22px;font-size:15px;line-height:1.6;'>"
        "Nouveau contenu disponible sur le profil."
        "</p>"
        if normalize_newsletter_lang(lang) == "fr"
        else "<p style='margin:0 0 22px;font-size:15px;line-height:1.6;'>"
        "New content is available on the profile."
        "</p>"
    )


def new_posts_email_subject(lang: str, count: int, post_title: str | None = None) -> str:
    lang = normalize_newsletter_lang(lang)
    title = str(post_title or "").strip()
    if title:
        return title
    if lang == "fr":
        return f"[RomainRoth] {count} nouveau(x) post(s) disponible(s)"
    if count == 1:
        return "[RomainRoth] 1 new post available"
    return f"[RomainRoth] {count} new posts available"


def _shared_footer_html(
    lang: str,
    profile_href: str,
    copy_href: str,
    posts_href: str,
    *,
    calendar_year_perf_pct: float | None = None,
    calendar_year: int | None = None,
) -> str:
    perf_snap_fr = ""
    perf_snap_en = ""
    if calendar_year_perf_pct is not None and calendar_year is not None:
        p = calendar_year_perf_pct
        y = calendar_year
        perf_snap_fr = (
            f'        <p style="font-size:15px;line-height:1.6;margin:0 0 8px;">\n'
            f"          Le portefeuille affiche actuellement une performance de {p:+.2f} % en {y}.\n"
            f"        </p>\n"
        )
        perf_snap_en = (
            f'        <p style="font-size:15px;line-height:1.6;margin:0 0 8px;">\n'
            f"          The portfolio currently shows a performance of {p:+.2f}% in {y}.\n"
            f"        </p>\n"
        )
    if normalize_newsletter_lang(lang) == "fr":
        return f"""
        <p style="font-size:15px;line-height:1.6;">
          À partir de maintenant, vous recevrez directement par email
          <strong>tous les posts que je publie sur eToro</strong>
          - sans avoir besoin de vous connecter à la plateforme.
        </p>
        <p style="font-size:15px;line-height:1.6;">Cela vous permet de :</p>
        <ul style="font-size:15px;line-height:1.6;margin:0 0 8px;padding-left:1.25rem;">
          <li>suivre mes analyses en temps réel</li>
          <li>comprendre mes décisions</li>
          <li>rester informé sans effort</li>
        </ul>
{perf_snap_fr}        <p style="font-size:15px;line-height:1.6;margin:0 0 16px;">
          Suivi actuellement par plus de 800 investisseurs sur eToro.
        </p>
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:22px 0;" />
        <p style="font-size:15px;line-height:1.6;"><strong>Accéder au portefeuille en direct</strong></p>
        <p style="font-size:15px;line-height:1.6;">
          Vous pouvez consulter et suivre mon portefeuille eToro ici :
        </p>
        <p style="text-align:center;margin:18px 0 6px;">
          <a
            href="{profile_href}"
            target="_blank"
            rel="noopener noreferrer"
            style="display:inline-block;padding:8px 14px;border-radius:4px;text-decoration:none;background:#ffffff;border:1px solid #3fb950;color:#3fb950;font-weight:600;font-size:12.5px;line-height:1.25;text-align:center;box-sizing:border-box;"
          >Mon profil sur eToro</a>
        </p>
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:22px 0;" />
        <p style="font-size:15px;line-height:1.6;">
          Si vous souhaitez aller plus loin, eToro permet également de
          <strong>copier automatiquement un portefeuille</strong>, afin de reproduire les positions en temps réel.
        </p>
        <p style="text-align:center;margin:18px 0 6px;">
          <a
            href="{copy_href}"
            target="_blank"
            rel="noopener noreferrer"
            style="display:inline-block;padding:10px 18px;border-radius:6px;text-decoration:none;background:#3fb950;border:1px solid #2ea043;color:#ffffff;font-weight:700;font-size:14px;line-height:1.25;text-align:center;box-sizing:border-box;"
          >Me rejoindre</a>
        </p>
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:22px 0;" />
        <p style="font-size:15px;line-height:1.6;"><strong>Mon approche</strong></p>
        <p style="font-size:15px;line-height:1.6;">
          Je partage uniquement des analyses sur des entreprises que je comprends, avec une approche simple :
        </p>
        <ul style="font-size:15px;line-height:1.6;margin:0 0 16px;padding-left:1.25rem;">
          <li>Pas de levier</li>
          <li>Vision long terme</li>
          <li>Gestion du risque prioritaire</li>
          <li>Peu d’actions</li>
          <li>Pas de crypto</li>
        </ul>
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:22px 0;" />
        <p style="font-size:15px;line-height:1.6;">⚠️ <strong>Avertissement sur les risques :</strong></p>
        <p style="font-size:15px;line-height:1.6;font-style:italic;color:#444;">
          C’est une stratégie personnelle, non un conseil. L’appliquer ou non reste votre choix. Les performances passées ne garantissent pas les résultats futurs.
        </p>
        <p style="font-size:15px;line-height:1.6;margin-top:22px;margin-bottom:0;">À bientôt,<br>Romain Roth</p>
""".strip()
    return f"""
        <p style="font-size:15px;line-height:1.6;">
          From now on, you will receive by email
          <strong>every post I publish on eToro</strong>
          - without having to log in to the platform.
        </p>
        <p style="font-size:15px;line-height:1.6;">This lets you:</p>
        <ul style="font-size:15px;line-height:1.6;margin:0 0 8px;padding-left:1.25rem;">
          <li>follow my analyses in real time</li>
          <li>understand my decisions</li>
          <li>stay informed effortlessly</li>
        </ul>
{perf_snap_en}        <hr style="border:none;border-top:1px solid #e5e7eb;margin:22px 0;" />
        <p style="font-size:15px;line-height:1.6;"><strong>View the portfolio live</strong></p>
        <p style="font-size:15px;line-height:1.6;">
          You can view and follow my eToro portfolio here:
        </p>
        <p style="text-align:center;margin:18px 0 6px;">
          <a
            href="{profile_href}"
            target="_blank"
            rel="noopener noreferrer"
            style="display:inline-block;padding:8px 14px;border-radius:4px;text-decoration:none;background:#ffffff;border:1px solid #3fb950;color:#3fb950;font-weight:600;font-size:12.5px;line-height:1.25;text-align:center;box-sizing:border-box;"
          >My profile on eToro</a>
        </p>
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:22px 0;" />
        <p style="font-size:15px;line-height:1.6;">
          If you want to go further, eToro also lets you
          <strong>automatically copy a portfolio</strong> to mirror positions in real time.
        </p>
        <p style="text-align:center;margin:18px 0 6px;">
          <a
            href="{copy_href}"
            target="_blank"
            rel="noopener noreferrer"
            style="display:inline-block;padding:10px 18px;border-radius:6px;text-decoration:none;background:#3fb950;border:1px solid #2ea043;color:#ffffff;font-weight:700;font-size:14px;line-height:1.25;text-align:center;box-sizing:border-box;"
          >Join me</a>
        </p>
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:22px 0;" />
        <p style="font-size:15px;line-height:1.6;"><strong>My approach</strong></p>
        <p style="font-size:15px;line-height:1.6;">
          I only share analysis on companies I understand, with a simple approach:
        </p>
        <ul style="font-size:15px;line-height:1.6;margin:0 0 16px;padding-left:1.25rem;">
          <li>No leverage</li>
          <li>Long-term view</li>
          <li>Risk management first</li>
          <li>Few stocks</li>
          <li>No crypto</li>
        </ul>
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:22px 0;" />
        <p style="font-size:15px;line-height:1.6;">⚠️ <strong>Risk warning:</strong></p>
        <p style="font-size:15px;line-height:1.6;font-style:italic;color:#444;">
          This is a personal strategy, not advice. Whether or not to follow it is your choice. Past performance does not guarantee future results.
        </p>
        <p style="font-size:15px;line-height:1.6;margin-top:22px;margin-bottom:0;">Best regards,<br>Romain Roth</p>
""".strip()


def build_newsletter_welcome_html(
    lang: str,
    profile_href: str,
    copy_href: str,
    posts_href: str,
    unsub_href: str,
    site_base_url: str,
    *,
    calendar_year_perf_pct: float | None = None,
    calendar_year: int | None = None,
) -> str:
    lang = normalize_newsletter_lang(lang)
    footer_common = _shared_footer_html(
        lang,
        profile_href,
        copy_href,
        posts_href,
        calendar_year_perf_pct=calendar_year_perf_pct,
        calendar_year=calendar_year,
    )
    legal_site = build_newsletter_site_legal_footer_html(lang, site_base_url)
    if lang == "fr":
        top = """
        <p style="margin-top:0;font-size:16px;">Bonjour,</p>
        <p style="font-size:15px;line-height:1.6;">
          Merci encore pour votre inscription.
        </p>
""".strip()
    else:
        top = """
        <p style="margin-top:0;font-size:16px;">Hello,</p>
        <p style="font-size:15px;line-height:1.6;">
          Thank you again for subscribing.
        </p>
""".strip()
    if lang == "fr":
        outer_footer = f"""
      <p style="font-size:12px;color:#777;text-align:center;line-height:1.5;margin-top:18px;">
        Vous recevez cet email car vous vous êtes inscrit aux analyses de Romain Roth.<br>
        <a href="{unsub_href}" style="color:#555;text-decoration:underline;">
          Se désabonner
        </a>
      </p>
""".strip()
    else:
        outer_footer = f"""
      <p style="font-size:12px;color:#777;text-align:center;line-height:1.5;margin-top:18px;">
        You are receiving this email because you opted in to the newsletter.<br>
        <a href="{unsub_href}" style="color:#555;text-decoration:underline;">
          Unsubscribe
        </a>
      </p>
""".strip()
    return f"""
<html lang="{lang}">
  <body style="margin:0;padding:0;background:#f5f6f8;font-family:Arial,sans-serif;color:#111;">
    <div style="max-width:640px;margin:0 auto;padding:24px;">
      <div style="background:#ffffff;border-radius:14px;padding:28px;border:1px solid #e5e7eb;">
        {top}
        {footer_common}
      </div>
      {outer_footer}
      {legal_site}
    </div>
  </body>
</html>
""".strip()


def build_newsletter_welcome_plain(
    lang: str,
    posts_page_url: str,
    etoro_profile_url: str,
    etoro_copy_invite_url: str,
    one_click_url: str,
    site_base_url: str,
    *,
    calendar_year_perf_pct: float | None = None,
    calendar_year: int | None = None,
) -> str:
    lang = normalize_newsletter_lang(lang)
    perf_fr = ""
    perf_en = ""
    if calendar_year_perf_pct is not None and calendar_year is not None:
        p = calendar_year_perf_pct
        y = calendar_year
        perf_fr = f"Le portefeuille affiche actuellement une performance de {p:+.2f} % en {y}.\n\n"
        perf_en = f"The portfolio currently shows a performance of {p:+.2f}% in {y}.\n\n"
    if lang == "fr":
        return (
            "Bonjour,\n\n"
            "Merci encore pour votre inscription.\n\n"
            "À partir de maintenant, vous recevrez par email les posts publiés sur eToro.\n\n"
            f"{perf_fr}"
            "Suivi actuellement par plus de 800 investisseurs sur eToro.\n\n"
            f"Profil eToro : {etoro_profile_url}\n\n"
            "eToro permet aussi de copier automatiquement un portefeuille (positions en temps réel).\n"
            f"Me rejoindre : {etoro_copy_invite_url}\n\n"
            f"Voir tous les posts : {posts_page_url}\n\n"
            "⚠️ Avertissement sur les risques :\n"
            "C’est une stratégie personnelle, non un conseil. L’appliquer ou non reste votre choix. "
            "Les performances passées ne garantissent pas les résultats futurs.\n\n"
            "Vous recevez cet email car vous vous êtes inscrit aux analyses de Romain Roth.\n"
            f"Se désabonner : {one_click_url}\n\n"
            "À bientôt,\nRomain Roth\n\n"
            f"{build_newsletter_site_legal_footer_plain('fr', site_base_url)}"
        )
    return (
        "Hello,\n\n"
        "Thank you again for subscribing.\n\n"
        "From now on, you will receive my eToro posts by email.\n\n"
        f"{perf_en}"
        f"eToro profile: {etoro_profile_url}\n\n"
        "eToro also lets you automatically copy a portfolio (positions in real time).\n"
        f"Join me: {etoro_copy_invite_url}\n\n"
        f"See all posts: {posts_page_url}\n\n"
        "⚠️ Risk warning:\n"
        "This is a personal strategy, not advice. Whether or not to follow it is your choice. "
        "Past performance does not guarantee future results.\n\n"
        "You are receiving this email because you opted in to the newsletter.\n"
        f"Unsubscribe: {one_click_url}\n\n"
        "Best regards,\nRomain Roth\n\n"
        f"{build_newsletter_site_legal_footer_plain('en', site_base_url)}"
    )


def build_new_posts_newsletter_html(
    lang: str,
    trader_username: str,
    hello_line: str,
    posts_block: str,
    profile_href: str,
    copy_href: str,
    posts_href: str,
    unsub_href: str,
    site_base_url: str,
    *,
    calendar_year_perf_pct: float | None = None,
    calendar_year: int | None = None,
) -> str:
    lang = normalize_newsletter_lang(lang)
    footer_common = _shared_footer_html(
        lang,
        profile_href,
        copy_href,
        posts_href,
        calendar_year_perf_pct=calendar_year_perf_pct,
        calendar_year=calendar_year,
    )
    legal_site = build_newsletter_site_legal_footer_html(lang, site_base_url)
    if lang == "fr":
        outer_footer = f"""
      <p style="font-size:12px;color:#777;text-align:center;line-height:1.5;margin-top:18px;">
        Vous recevez cet email car vous vous êtes inscrit aux analyses de Romain Roth.<br>
        <a href="{unsub_href}" style="color:#555;text-decoration:underline;">
          Se désabonner
        </a>
      </p>
""".strip()
    else:
        outer_footer = f"""
      <p style="font-size:12px;color:#777;text-align:center;line-height:1.5;margin-top:18px;">
        You are receiving this email because you opted in to the newsletter.<br>
        <a href="{unsub_href}" style="color:#555;text-decoration:underline;">
          Unsubscribe
        </a>
      </p>
""".strip()
    return f"""
<html lang="{lang}">
  <body style="margin:0;padding:0;background:#f5f6f8;font-family:Arial,sans-serif;color:#111;">
    <div style="max-width:640px;margin:0 auto;padding:24px;">
      <div style="background:#ffffff;border-radius:14px;padding:28px;border:1px solid #e5e7eb;">
        <p style="margin-top:0;font-size:16px;">{hello_line}</p>
        {posts_block}
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:22px 0;" />
        {footer_common}
      </div>
      {outer_footer}
      {legal_site}
    </div>
  </body>
</html>
""".strip()


def build_new_posts_newsletter_plain(
    lang: str,
    posts_plain: str,
    etoro_profile_plain: str,
    etoro_copy_plain: str,
    count: int,
    site_base_url: str,
    *,
    calendar_year_perf_pct: float | None = None,
    calendar_year: int | None = None,
) -> str:
    lang = normalize_newsletter_lang(lang)
    perf_line = ""
    if calendar_year_perf_pct is not None and calendar_year is not None:
        p = calendar_year_perf_pct
        y = calendar_year
        if lang == "fr":
            perf_line = f"Le portefeuille affiche actuellement une performance de {p:+.2f} % en {y}.\n\n"
        else:
            perf_line = f"The portfolio currently shows a performance of {p:+.2f}% in {y}.\n\n"
    if lang == "fr":
        head = f"De nouveaux posts eToro sont disponibles ({count}).\n\n{perf_line}"
        risk = (
            "⚠️ Avertissement sur les risques : stratégie personnelle, pas un conseil. "
            "Les performances passées ne garantissent pas les résultats futurs.\n"
        )
    else:
        head = f"New eToro posts are available ({count}).\n\n{perf_line}"
        risk = (
            "⚠️ Risk warning: personal strategy, not advice. "
            "Past performance does not guarantee future results.\n"
        )
    if lang == "fr":
        links = (
            f"Voir tous les posts : {posts_plain}\n\n"
            f"Mon profil eToro : {etoro_profile_plain}\n"
            f"Me rejoindre : {etoro_copy_plain}\n\n"
        )
        legal = build_newsletter_site_legal_footer_plain("fr", site_base_url)
    else:
        links = (
            f"See all posts: {posts_plain}\n\n"
            f"eToro profile: {etoro_profile_plain}\n"
            f"Join me: {etoro_copy_plain}\n\n"
        )
        legal = build_newsletter_site_legal_footer_plain("en", site_base_url)
    return f"{head}{links}{risk}\n{legal}"
