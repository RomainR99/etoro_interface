# eToro Interface

Interface web pour visualiser le profil d'un trader eToro, comparer les performances avec des indices (S&P 500, NASDAQ 100, CAC 40 TR, MSCI World) et lister les instruments par place de marché.

## Sommaire

- [Fonctionnalités](#fonctionnalités)
  - [Limiter les requêtes (rate limit par visiteur)](#limiter-les-requêtes-rate-limit-par-visiteur)
  - [CAPTCHA (anti-bots)](#captcha-anti-bots)
  - [Récupérer les données du chatbot](#récupérer-les-données-du-chatbot)
  - [Consentements cookies (SQLite)](#consentements-cookies-sqlite)
  - [Inscriptions newsletter / messages (`contact_messages`)](#inscriptions-newsletter--messages-contact_messages)
- [Prérequis](#prérequis)
- [Dépendances principales](#dépendances-principales)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Trouver et configurer `DATABASE_URL`](#trouver-et-configurer-database_url)
  - [Configurer `REDIS_URL` (local et production)](#configurer-redis_url-local-et-production)
  - [Redis à faire](#redis-à-faire)
  - [C'est quoi Celery ?](#cest-quoi-celery)
  - [Frontend séparé (Next.js)](#frontend-séparé-nextjs)
  - [Note performance (première visite)](#note-performance-première-visite)
  - [Checklist production (rapide)](#checklist-production-rapide)
  - [Déploiement production](#déploiement-production)
    - [Cron serveur pour le sync eToro](#cron-serveur-pour-le-sync-etoro)
- [Lancement](#lancement)
  - [Gunicorn (3 façons équivalentes)](#gunicorn-3-façons-équivalentes)
- [Structure](#structure)
- [Configuration du trader](#configuration-du-trader)
- [Avatar (header) et favicon](#avatar-header-et-favicon)
- [Export des messages chatbot vers SQLite (DB Browser)](#export-des-messages-chatbot-vers-sqlite-db-browser)
- [API eToro](#api-etoro)
- [Actualités Zonebourse](#actualités-zonebourse)
- [Sécurité et déploiement](#sécurité-et-déploiement)
  - [C'est quoi le MFA ?](#cest-quoi-le-mfa-)
- [Apple / iOS](#apple--ios)
- [Corrections à apporter](#corrections-à-apporter)
- [Notes Werkzeug](#1️⃣-à-quoi-sert-werkzeug)

## Fonctionnalités

- **Profil trader** : affichage du profil, des gains mensuels/annuels et du portefeuille
- **Comparaison des performances** : courbes comparatives (base 100) avec possibilité d'ajouter les 100 traders les plus copiés. La liste déroulante est fermée par défaut (affichage « — Choisir un trader — ») ; après sélection et ajout, `blur()` est appliqué pour refermer la liste.
- **Indices** : S&P 500, NASDAQ 100, CAC 40 TR, MSCI World
- **Simulation DCA** : 1 000 $ au départ + 100 $/mois, comparaison avec le S&P 500
- **Posts par mois** (graphique 3) : comparaison du nombre de posts. Priorité au feed utilisateur (tous les posts) ; si vide, fallback sur plusieurs instruments (NSDQ100, SPX500, CAC40, Or, BTC, ETH) en filtrant par auteur
- **Performance vs copieurs** (graphique 5) : nuage de points pour les 2000 traders les plus copiés (abscisse = copieurs, ordonnée = performance % sur 2 ans)
- **Actualités Zonebourse** : résumés des articles générés par IA (5 lignes par article) et illustration sous chaque actualité (génération d’image via OpenAI DALL·E 3, même clé `OPENAI_API_KEY`). Pour le stockage et le web, privilégier un format **moins lourd que le PNG** (ex. **WebP** ou **JPEG**) afin de réduire poids disque et bande passante.
- **Actualités par instrument** : sous les posts Zonebourse, les 3 dernières actualités Mediastack pour les instruments du portefeuille du trader (RomainRoth). Traduction automatique en français via OpenAI.
- **Chatbot agent IA** : assistant pédagogique en éducation financière pour poser des questions sur les données eToro et Zonebourse. Elle ajoute :
  - conformité AMF / régulation financière
  - prévention promesses de rendement
  - interdiction fraude / évasion fiscale
  - gestion risques utilisateurs
  - positionnement éducation financière plutôt que conseil

Avec ce prompt ton chatbot devient : **conforme fintech**, **compatible AMF / MiFID II**, **safe juridiquement**, **utilisable dans un produit SaaS**.

### Limiter les requêtes (rate limit par visiteur)

Les appels à `/api/chat` sont limités par **visiteur anonyme** (identifiant stocké côté navigateur) :

| Fenêtre | Limite |
|---------|--------|
| 1 minute | 5 messages |
| 1 heure | 30 messages |
| 24 heures | 100 messages |

Si la limite est dépassée, l'API renvoie `429 Too Many Requests`. Les limites sont configurables dans `app.py` (`CHAT_RATE_LIMIT`).

> **Pas de cooldown** : le temps de réponse de l’IA est déjà suffisant pour espacer naturellement les requêtes.

> **Possibilité : limiter par session courte** — Par exemple : 10 messages gratuits par session, puis blocage temporaire pendant 1 heure. C'est simple pour un chatbot public.

**Identifiant visiteur** : à la première visite, le backend génère un `visitor_id` aléatoire (UUID) et le stocke dans un **cookie HTTP-only** (durée 1 an). Le rate limit s'applique par `visitor_id`. Un visiteur derrière un NAT a son propre quota, distinct des autres. Supprimer le cookie ou utiliser un autre navigateur/onglet privé réinitialise le compteur.

### CAPTCHA (anti-bots)

Un CAPTCHA (reCAPTCHA v2) est demandé **uniquement au moment opportun** pour ne pas gêner les vrais utilisateurs :

| Condition | Déclenchement |
|-----------|---------------|
| Après 5 messages | Une fois 5 messages envoyés (fenêtre 24 h), le CAPTCHA est demandé pour le suivant |
| Rythme trop rapide | Si 3 messages ou plus en 1 minute |
| Requêtes similaires | Si le message est identique ou très proche d'un des 2 derniers |

*C'est très efficace contre les bots* : les utilisateurs légitimes ne voient généralement pas de CAPTCHA sur leurs premiers échanges ; les scripts automatisés qui envoient des messages en rafale ou répétés sont bloqués.

**Configuration** : ajouter dans `.env` les clés reCAPTCHA v2 (checkbox) :
- `RECAPTCHA_SITE_KEY` – clé publique
- `RECAPTCHA_SECRET_KEY` – clé secrète

Sans ces clés, le CAPTCHA n'est pas activé. Obtenir les clés : [Google reCAPTCHA Admin](https://www.google.com/recaptcha/admin).

**Erreur affichée par Google : « ERREUR pour le propriétaire du site : Type de clé non valide »**  
Cela signifie que la **clé du site** (`RECAPTCHA_SITE_KEY`) n’est pas du bon **type** pour ce widget :

| À utiliser | À ne pas utiliser |
|------------|-------------------|
| **reCAPTCHA v2** → type **« Je ne suis pas un robot » Case à cocher** | reCAPTCHA **v3** (score) |
| Clés classiques **non-Enterprise** avec l’API `siteverify` | Clés **Enterprise** sans configuration adaptée |

À la [création des clés](https://www.google.com/recaptcha/admin), choisir explicitement **v2** et **Case à cocher**, puis recopier la **clé du site** et la **clé secrète** dans `.env`. Les clés v3 ne fonctionnent pas avec `grecaptcha.render` en mode case à cocher.

**Erreur : « L'hôte local ne figure pas dans la liste des domaines acceptés pour la clé de ce site »**  
Dans [Google reCAPTCHA Admin](https://www.google.com/recaptcha/admin), ouvrir la clé concernée → section **Domaines** → ajouter **`localhost`** et, si tu ouvres le site via l’IP, **`127.0.0.1`** → enregistrer. Les changements peuvent prendre une ou deux minutes.  
**En développement local**, tu peux aussi laisser `RECAPTCHA_SITE_KEY` et `RECAPTCHA_SECRET_KEY` vides dans `.env` : le CAPTCHA est alors désactivé (voir ci-dessus).

> **Note** : Une même clé peut couvrir plusieurs domaines (ex. `romainroth.com`, `localhost`). Pour un domaine de production différent, ajoute-le dans la liste ou crée une autre inscription reCAPTCHA.

> **Détecter les comportements anormaux** — Vous pouvez refuser ou ralentir les requêtes si : requêtes trop rapides, copier-coller de prompts énormes, mêmes messages répétés, user-agent étrange, ou trop de tokens demandés. Constantes dans `app.py` :
> - `MAX_USER_MESSAGE_CHARS = 2000`
> - `MAX_ESTIMATED_TOKENS = 12000`
> - `SUSPICIOUS_UA_SUBSTRINGS = ("curl", "python", "wget", "httpie", "bot", "scrapy", "requests/")`

> **Limiter la taille des messages** : `MAX_USER_MESSAGE_CHARS` (message utilisateur), `MAX_REPLY_CHARS` (troncature réponse), `MAX_HISTORY_MESSAGES` (historique envoyé au modèle), `MAX_COMPLETION_TOKENS` (max_tokens API).

### Récupérer les données du chatbot

Chaque question posée et chaque réponse sont enregistrées dans `data/chat_questions.jsonl` (format JSONL : une ligne par échange, avec `timestamp`, `question`, `reply`).

**API :**

| URL | Format | Description |
|-----|--------|-------------|
| `GET /api/chat-questions` | JSON | Retourne la liste des échanges (tableau d'objets `{timestamp, question, reply}`) |
| `GET /api/chat-questions?format=csv` | CSV | Télécharge un fichier CSV `chat_questions.csv` (colonnes : timestamp, question, reply) |

Exemple : `curl http://127.0.0.1:5001/api/chat-questions` ou ouvrir l’URL dans le navigateur pour le JSON. Pour l’export CSV : `http://127.0.0.1:5001/api/chat-questions?format=csv`.

### Consentements cookies (SQLite)

#### Base SQLite — `data/cookie_consent.sqlite`

Table **`cookie_consent_log`** :

| Colonne | Contenu |
|---------|---------|
| `id` | Identifiant auto |
| `created_at` | Horodatage UTC (ISO) |
| `choice` | `accepted` ou `necessary` |
| `visitor_id` | Cookie `visitor_id` (lien avec le reste du site) |
| `lang` | Langue envoyée par le front (`page_lang`) |
| `user_agent` | User-Agent (tronqué à 512 car.) |
| `client_ip` | IP client (`X-Forwarded-For` ou `remote_addr`) |

Le fichier est créé au premier consentement. Il est listé dans **`.gitignore`** pour ne pas versionner les données.

#### API — `POST /api/cookie-consent`

- **Corps JSON** : `{"choice": "accepted"|"necessary", "lang": "fr"|"en"}`
- **Comportement** : enregistre une ligne en base, pose le cookie **`visitor_id`** si besoin (comme sur le profil).
- **Réponse** : `{"ok": true}` ou erreur **400** / **500**.

#### Front — `templates/partials/cookie_banner.html`

Après enregistrement du consentement côté navigateur (cookie HTTP `cookieConsent`), un **`fetch`** envoie le même choix au serveur **sans bloquer l’UI** si la requête échoue.

**Consulter les traces** (exemple) :

```bash
sqlite3 data/cookie_consent.sqlite "SELECT * FROM cookie_consent_log ORDER BY id DESC LIMIT 20;"
```

**Mode interactif** (depuis la racine du projet, avec `sqlite3` installé — souvent déjà présent sur macOS/Linux) :

```bash
sqlite3 data/cookie_consent.sqlite
```

Puis dans le shell SQLite :

```sql
.tables
.schema cookie_consent_log
SELECT id, created_at, choice, visitor_id, lang FROM cookie_consent_log ORDER BY id DESC;
.quit
```

### Inscriptions newsletter / messages (`contact_messages`)

Les inscriptions via le bloc **« Never Miss an Opportunity »** (au-dessus du pied de page) sont enregistrées en **SQLite** dans `data/contact_messages.sqlite` (fichier ignoré par git), table **`contact_messages`** :

| Colonne | Contenu |
|---------|---------|
| `id` | Identifiant auto (SQLite `AUTOINCREMENT`) |
| `first_name`, `last_name` | Dérivés du champ « Name » |
| `email` | Adresse (obligatoire) |
| `subject` | Ex. `Newsletter` |
| `message` | Texte décrivant l’opt-in et le respect de la vie privée |
| `created_at` | Horodatage UTC (ISO) |

Pour **PostgreSQL**, le schéma équivalent est dans `data/schema_contact_messages.postgresql.sql` (`SERIAL`, `TIMESTAMP`, etc.).

- **API** : `POST /api/newsletter-subscribe` avec corps JSON `{"name":"…","email":"…","newsletter_opt_in":true}` (champ anti-bot optionnel `company`, à laisser vide).
- **Limite** : nombre d’inscriptions par IP sur une fenêtre glissante d’une heure.
- **Stockage** : au clic sur **« S’inscrire »** (bloc *Never Miss an Opportunity*), le backend enregistre bien le **nom** et l’**email** dans SQLite (`data/contact_messages.sqlite`, table `contact_messages`).

```bash
sqlite3 data/contact_messages.sqlite "SELECT id, first_name, last_name, email, subject, created_at FROM contact_messages ORDER BY id DESC LIMIT 20;"
```

> **Note** : conserver l’IP et le user-agent relève du **traitement de données** ; prévois l’information dans ta **politique de confidentialité** / **RGPD** si besoin.

## Corrections à apporter

- **Graphique 4** : à améliorer (supprimé dans la version actuelle)
- **Barre de recherche** : ajouter une barre de recherche pour filtrer rapidement les traders, instruments et actualités dans l’interface.
- **Amélioration PNG → WebP** : convertir les images PNG (captures d’écran, assets statiques, illustrations) en **WebP** pour réduire le poids des fichiers et la bande passante, tout en conservant une qualité visuelle correcte ; fournir un fallback ou `<picture>` si besoin de compatibilité navigateurs anciens.

## Sécurité et déploiement

- **Secrets et automatisation** : voir [Déploiement production](#déploiement-production) et le guide [`deploy/README.md`](deploy/README.md) (fichier `/etc/etoro/interface.env`, systemd, sync quotidien des posts).
- **Firewall** : mettre en place un firewall pour limiter les accès réseau au strict nécessaire.
- **Reverse proxy** : placer l’application derrière un reverse proxy (ex. Nginx) pour mieux filtrer et sécuriser le trafic entrant.
- **Bots Internet et SSH** : ajouter une protection contre les bots Internet, surtout sur SSH (durcissement SSH, blocage brute force, filtrage d’IP, etc.).

### C'est quoi le MFA ?

👉 **MFA** = *Multi-Factor Authentication*  
(en français : **authentification multi-facteurs**)

➡️ Ça veut dire : tu dois prouver ton identité avec **plusieurs** éléments.

#### Exemple concret

**Sans MFA**

- login + mot de passe  
- ➡️ si quelqu’un le vole → accès total ❌

**Avec MFA**

- login + mot de passe  
- code sur téléphone  
- ➡️ même si le mot de passe est volé → bloqué ✅

#### Les 3 types de facteurs

1. **Ce que tu sais** — mot de passe  
2. **Ce que tu possèdes** — téléphone, app (Google Authenticator, etc.)  
3. **Ce que tu es** — empreinte, Face ID  

#### Exemple simple (le plus courant)

👉 Tu te connectes :

1. tu mets ton mot de passe  
2. tu reçois un code (6 chiffres)  
3. tu le rentres  

➡️ accès autorisé

#### Pourquoi c’est crucial pour toi

Pour ton projet **microcrédit**, tu vas gérer :

- données personnelles  
- KYC  
- potentiellement des infos financières  

➡️ **sans MFA** :

- ❌ énorme risque de piratage  
- ❌ problème légal (RGPD)  

#### Cas concret — accès admin (toi)

**Sans MFA** : quelqu’un devine ton mot de passe → il contrôle tout ❌  

**Avec MFA** : il lui faut ton téléphone → quasi impossible ✅  

#### Où mettre le MFA

- **Ton serveur (SSH)** — ultra important (ex. clé SSH + code)  
- **Ton app admin** — panneau admin Flask  
- **AWS / OVH** — accès cloud  

#### Outils populaires

- Google Authenticator  
- Microsoft Authenticator  
- Authy  

#### Résumé simple

👉 MFA = **double sécurité** : mot de passe + code téléphone (ou autre second facteur).

👉 À considérer comme **obligatoire** pour : admin, infra, microcrédit.

#### Conseil pour toi

👉 Fais-le **dès le début** pour : SSH, ton serveur, ton compte cloud.

## Apple / iOS

- Pour une application Apple, le développement natif se fait en **Swift**.
- Budget à prévoir : **100 $/an par application**.


## Prérequis

- Python 3.10+
- Compte eToro vérifié avec clés API

## Dépendances principales

| Package | Usage |
|---------|--------|
| `flask`, `werkzeug` | Application web |
| `requests` | Appels API (eToro, Zonebourse, etc.) |
| `beautifulsoup4`, `lxml` | Parsing HTML Zonebourse |
| `openai` | Résumés des actualités (titre + 5 lignes) et génération d’images (DALL·E 3) |
| `python-dotenv` | Chargement du fichier `.env` |
| `yfinance` | Données indices (S&P 500, CAC 40, etc.) |

Toutes les dépendances sont listées dans `requirements.txt`.

## Installation

```bash
# Cloner le projet
cd etoro_interface

# Créer l'environnement virtuel
python3 -m venv venv

# Activer l'environnement
source venv/bin/activate   # macOS/Linux
# ou : venv\Scripts\activate   # Windows

# Installer les dépendances
pip install -r requirements.txt
```

## Avatar (header) et favicon

La photo du header et le favicon utilisent un fichier **local** sous `images/` (aucune URL eToro exposée au navigateur pour l’avatar). Le favicon est en outre servi en **SVG circulaire** sur `/favicon.svg` (découpe en cercle à partir de la même image).

Pour régénérer la photo à partir de l’API eToro (clés `ETORO_API_KEY` et `ETORO_USER_KEY` dans `.env`) :

```bash
python sync_trader_avatar.py
```

**`sync_trader_avatar.py`** : appelle encore `get_user_profile` côté serveur, récupère l’URL d’avatar, télécharge l’image et l’enregistre sous `images/trader_avatar.<ext>` (les anciens `trader_avatar.*` sont remplacés). Exemple après exécution : `images/trader_avatar.jpg` (~quelques ko). Pense à versionner ce fichier après sync ou à relancer le script après déploiement.

## Export des messages chatbot vers SQLite (DB Browser)

Les échanges du chatbot sont enregistrés en **JSONL** dans `data/chat_questions.jsonl`.

Pour les consulter dans **DB Browser for SQLite**, un script d’export est disponible :

```bash
python3 export_chat_questions_sqlite.py
```

Ce script recrée `data/chat_questions.sqlite` avec une table `chat_questions` (`id`, `timestamp`, `question`, `reply`) à partir du JSONL.

> Si la table est vide, vérifie d’abord que `data/chat_questions.jsonl` contient des lignes, puis relance l’export.

### Ouvrir les bases dans DB Browser for SQLite

1. Ouvrir **DB Browser for SQLite**.
2. Cliquer sur **Open Database**.
3. Sélectionner un fichier dans `data/` :
   - `cookie_consent.sqlite` (table `cookie_consent_log`)
   - `contact_messages.sqlite` (table `contact_messages`)
   - `chat_questions.sqlite` (table `chat_questions`, générée par l’export JSONL)
4. Aller dans l’onglet **Browse Data** puis choisir la table dans la liste.

Si des nouvelles lignes n’apparaissent pas, cliquer sur **Refresh** (ou rouvrir la base).

## Configuration

Créer un fichier `.env` à la racine :

```env
ETORO_API_KEY=ta_clé_api_publique
ETORO_USER_KEY=ta_clé_utilisateur
OPENAI_API_KEY=sk-...          # Résumés des actualités + génération d’images (DALL·E 3) sous chaque actualité
TWELVEDATA_API_KEY=...        # Optionnel
RECAPTCHA_SITE_KEY=...        # Optionnel : reCAPTCHA v2 « case à cocher » uniquement (pas v3)
RECAPTCHA_SECRET_KEY=...      # Optionnel : clé secrète associée (même paire que la clé du site)
MEDIASTACK_ACCESS_KEY=...     # Optionnel : actualités par instrument (plan gratuit : 100 req/mois)
REDIS_URL=redis://...         # Optionnel (recommandé en production) : cache partagé inter-workers
WARMUP_TOKEN=...              # Optionnel : protège /internal/warmup
AUTO_WARMUP_ON_START=1        # Optionnel : warmup auto au 1er hit (/ ou /health), 1 par défaut
STARTUP_WARMUP_MAX_SECONDS=10 # Optionnel : budget max du warmup auto (en secondes)
FRONTEND_ORIGINS=http://localhost:3000  # Origines autorisées en CORS (séparées par virgule)
API_AUTH_PASSWORD=...         # Mot de passe de session pour endpoints de mutation
ENFORCE_MUTATION_AUTH=1       # 1 = protège les routes POST/DELETE sensibles
FLASK_SECRET_KEY=...          # Secret de session Flask (obligatoire en production)
SESSION_COOKIE_SECURE=1       # 1 en HTTPS (production)
SESSION_COOKIE_SAMESITE=Lax   # Lax ou Strict selon ton flux
```

- Les clés eToro se génèrent dans **Paramètres > Trading > Gestion des clés API** sur eToro.
- **OPENAI_API_KEY** : résumés Zonebourse, illustrations, chatbot.
- **RECAPTCHA_*** : optionnel. Si absent, le CAPTCHA est désactivé.
- **MEDIASTACK_ACCESS_KEY** : actualités par instrument. Si absent, la section affiche « Aucune actualité chargée ».
- **REDIS_URL** : recommandé en production pour partager le cache entre workers/containers et réduire les "cold starts".
- **FRONTEND_ORIGINS** : whitelist CORS stricte pour le frontend séparé.
- **API_AUTH_PASSWORD** : authentification de session pour les actions sensibles (mutations API).

### Trouver et configurer `DATABASE_URL`

Question frequente : comment trouver la bonne valeur pour :

```env
DATABASE_URL=postgresql://romain:monpass@127.0.0.1:5432/etoro
```

Tu ne l'inventes pas : elle depend de l'endroit ou tourne PostgreSQL.

#### Cas 1 - PostgreSQL local (machine/VPS)

Construire l'URL avec :

- `USER` : utilisateur PostgreSQL
- `PASSWORD` : mot de passe PostgreSQL
- `HOST` : `127.0.0.1` (ou l'IP/hostname du serveur DB)
- `PORT` : `5432` (souvent)
- `DBNAME` : nom de la base

Format :

```env
DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1:5432/DBNAME
```

Exemple :

```env
DATABASE_URL=postgresql://romain:MonSuperPass@127.0.0.1:5432/etoro
```

#### Cas 2 - PostgreSQL heberge (Neon, Supabase, Railway, Render, etc.)

Dans le dashboard du provider, copier la valeur **Connection string** ou **DATABASE_URL** et la coller telle quelle dans `.env`.

#### Cas 3 - Verifier avec `psql`

Verifier les bases disponibles :

```bash
psql -l
```

Tester une connexion :

```bash
psql -h 127.0.0.1 -U <user> -d <dbname>
```

Si la connexion fonctionne, utiliser les memes parametres dans `DATABASE_URL`.

### Configurer `REDIS_URL` (local et production)

Tu peux le configurer comme n’importe quelle variable d’environnement.

Le plus simple en local :

1. Lance Redis (exemple Docker) :

```bash
docker run -d --name myredis -p 6379:6379 redis:7
```

2. Mets la variable dans ton `.env` :

```env
REDIS_URL=redis://localhost:6379/0
```

3. Redémarre ton app (gunicorn/flask) pour recharger la variable.

Format possible de `REDIS_URL` :

- Sans mot de passe : `redis://host:6379/0`
- Avec mot de passe : `redis://:PASSWORD@host:6379/0`
- Avec TLS (souvent cloud) : `rediss://:PASSWORD@host:6380/0`

Vérification rapide :

- Au démarrage, l’app doit indiquer Redis actif dans les logs (ou via l’info `redis_enabled` dans `app.py`).

### Redis à faire

On va l'integrer proprement dans ton projet Flask `etoro_interface` avec :

- Redis pour les sessions
- Redis pour le cache des appels eToro
- une structure simple a brancher sans casser ton app actuelle

Je te donne le chemin le plus direct.

#### 1. Installer les dependances

Dans ton venv :

```bash
pip install redis flask-session
```

Si tu es sur un VPS Ubuntu :

```bash
sudo apt update
sudo apt install redis-server -y
sudo systemctl enable redis
sudo systemctl start redis
redis-cli ping
```

Tu dois voir :

```text
PONG
```

#### 2. Ajouter une config Redis dans ton projet

Dans ton fichier principal Flask, probablement `app.py`, ajoute :

```python
import os
import redis
from flask import Flask
from flask_session import Session

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY", "change-me-in-production")

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

app.config["SESSION_TYPE"] = "redis"
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True
app.config["SESSION_REDIS"] = redis_client

Session(app)
```

#### 3. Utiliser Redis pour la session utilisateur

Si aujourd'hui tu fais deja un login Flask avec `session`, c'est simple : tu gardes presque le meme code.

Exemple :

```python
from flask import request, session, redirect, url_for

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        # Remplace ca par ta vraie logique d'auth
        if username == "romain" and password == "test123":
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("profile"))

        return "Identifiants invalides", 401

    return """
    <form method="post">
        <input name="username" placeholder="Username">
        <input name="password" type="password" placeholder="Password">
        <button type="submit">Connexion</button>
    </form>
    """
```

Et pour proteger une route :

```python
@app.route("/profile")
def profile():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    username = session.get("username")
    return f"Bonjour {username}"
```

Pour logout :

```python
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))
```

Ca, Redis le stocke cote serveur, donc c'est plus propre que de tout dependre du navigateur.

#### 4. Ajouter un cache Redis pour tes donnees eToro

L'idee : au lieu d'appeler l'API eToro a chaque requete, tu mets le resultat en cache pendant 30 a 120 secondes.

Ajoute une petite fonction utilitaire :

```python
import json
import requests

def get_cached_json(key: str):
    raw = redis_client.get(key)
    if raw:
        return json.loads(raw)
    return None

def set_cached_json(key: str, value, ttl: int = 60):
    redis_client.setex(key, ttl, json.dumps(value))
```

#### 5. Brancher ca sur ton appel eToro

Exemple generique :

```python
def fetch_etoro_portfolio():
    # Remplace par ton vrai appel API / scraping / lecture locale
    # Exemple fictif :
    response = requests.get("https://api.example.com/etoro/portfolio", timeout=10)
    response.raise_for_status()
    return response.json()
```

Puis dans ta route Flask :

```python
from flask import jsonify

@app.route("/api/portfolio")
def api_portfolio():
    cache_key = "etoro:portfolio"
    cached_data = get_cached_json(cache_key)

    if cached_data:
        return jsonify({
            "source": "cache",
            "data": cached_data
        })

    fresh_data = fetch_etoro_portfolio()
    set_cached_json(cache_key, fresh_data, ttl=60)

    return jsonify({
        "source": "api",
        "data": fresh_data
    })
```

Comme ca :

- premier appel -> API reelle
- appels suivants pendant 60 sec -> Redis
- ton site est plus rapide
- tu reduis la charge

#### 6. Si tu as plusieurs profils eToro

Si tu affiches plusieurs profils, fais une cle par utilisateur :

```python
def get_portfolio_data(username: str):
    cache_key = f"etoro:portfolio:{username}"
    cached_data = get_cached_json(cache_key)

    if cached_data:
        return cached_data

    # remplace par ton vrai fetch
    response = requests.get(f"https://api.example.com/etoro/{username}", timeout=10)
    response.raise_for_status()
    data = response.json()

    set_cached_json(cache_key, data, ttl=60)
    return data
```

Et la route :

```python
@app.route("/api/portfolio/<username>")
def api_portfolio_user(username):
    data = get_portfolio_data(username)
    return jsonify(data)
```

#### 7. Ajouter un decorateur de cache reutilisable

Comme ca tu peux le brancher partout :

```python
import json
from functools import wraps
from flask import request

def redis_cache(ttl=60, key_prefix="view"):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f"{key_prefix}:{request.full_path}"
            cached = redis_client.get(cache_key)

            if cached:
                return json.loads(cached)

            result = func(*args, **kwargs)
            redis_client.setex(cache_key, ttl, json.dumps(result))
            return result
        return wrapper
    return decorator
```

Exemple :

```python
@app.route("/api/stats")
@redis_cache(ttl=120, key_prefix="stats")
def stats():
    return {
        "followers": 26,
        "performance": "+142%"
    }
```

Ca marche bien si la route renvoie deja un dict JSON simple.

#### 8. Prevoir un fallback si Redis tombe

Tres important en prod. Ton site ne doit pas casser juste parce que Redis est indisponible.

Version securisee :

```python
def safe_get_cached_json(key: str):
    try:
        raw = redis_client.get(key)
        if raw:
            return json.loads(raw)
    except Exception as e:
        print(f"Redis GET error: {e}")
    return None

def safe_set_cached_json(key: str, value, ttl: int = 60):
    try:
        redis_client.setex(key, ttl, json.dumps(value))
    except Exception as e:
        print(f"Redis SET error: {e}")
```

Et dans la route :

```python
@app.route("/api/portfolio")
def api_portfolio():
    cache_key = "etoro:portfolio"
    cached_data = safe_get_cached_json(cache_key)

    if cached_data:
        return jsonify({"source": "cache", "data": cached_data})

    fresh_data = fetch_etoro_portfolio()
    safe_set_cached_json(cache_key, fresh_data, ttl=60)

    return jsonify({"source": "api", "data": fresh_data})
```

#### 9. Variables d'environnement a ajouter

Dans ton `.env` :

```env
SECRET_KEY=une_vraie_cle_longue_et_random
REDIS_URL=redis://127.0.0.1:6379/0
```

Si plus tard Redis tourne dans Docker ou ailleurs, tu changes juste cette URL.

#### 10. Exemple de structure propre pour ton projet

Tu peux viser quelque chose comme :

```text
etoro_interface/
├── app.py
├── .env
├── templates/
│   └── profile.html
├── static/
├── services/
│   └── etoro_service.py
└── utils/
    └── cache.py
```

Par exemple :

`utils/cache.py`

```python
import json
import os
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

def get_cached_json(key: str):
    try:
        raw = redis_client.get(key)
        if raw:
            return json.loads(raw)
    except Exception as e:
        print(f"Redis GET error: {e}")
    return None

def set_cached_json(key: str, value, ttl: int = 60):
    try:
        redis_client.setex(key, ttl, json.dumps(value))
    except Exception as e:
        print(f"Redis SET error: {e}")
```

`services/etoro_service.py`

```python
import requests
from utils.cache import get_cached_json, set_cached_json

def fetch_portfolio(username: str):
    cache_key = f"etoro:portfolio:{username}"
    cached = get_cached_json(cache_key)
    if cached:
        return cached

    response = requests.get(f"https://api.example.com/etoro/{username}", timeout=10)
    response.raise_for_status()
    data = response.json()

    set_cached_json(cache_key, data, ttl=60)
    return data
```

`app.py`

```python
import os
import redis
from flask import Flask, jsonify, session, redirect, url_for
from flask_session import Session
from services.etoro_service import fetch_portfolio

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me")

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
session_redis = redis.from_url(REDIS_URL, decode_responses=True)

app.config["SESSION_TYPE"] = "redis"
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True
app.config["SESSION_REDIS"] = session_redis

Session(app)

@app.route("/")
def home():
    return "OK"

@app.route("/login")
def login():
    session["logged_in"] = True
    session["username"] = "Romain"
    return redirect(url_for("dashboard"))

@app.route("/dashboard")
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return f"Bienvenue {session.get('username')}"

@app.route("/api/portfolio/<username>")
def api_portfolio(username):
    data = fetch_portfolio(username)
    return jsonify(data)
```

#### 11. Ce que ca t'apporte immediatement

Pour `etoro_interface`, Redis va te servir a deux choses tres concretes :

Sessions :

- garder l'utilisateur connecte
- partager l'etat entre plusieurs workers Gunicorn
- eviter les soucis si l'app redemarre

Cache :

- eviter de refaire les appels eToro a chaque chargement
- accelerer l'affichage
- reduire le risque de blocage ou de timeout

#### 12. Ce que je te conseille pour commencer

Fais simple :

- branche Redis pour les sessions
- ajoute un cache 60 secondes sur ta route portfolio
- teste localement
- ensuite on le met sur ton VPS avec Gunicorn + Nginx

#### 13. Test rapide a faire

Une fois branche :

```bash
redis-cli keys "*"
```

Tu verras apparaitre des cles du style :

```text
session:...
etoro:portfolio:...
```

Et pour tester le cache :

- premier refresh -> source `"api"`
- deuxieme refresh dans la minute -> source `"cache"`

### C'est quoi Celery ?

**Celery** — c’est un système qui permet de lancer des **tâches en arrière-plan** (asynchrones), **sans bloquer** ton app Flask.

#### Problème sans Celery

Imagine ton site : un utilisateur clique sur « analyser mon dossier » et derrière tu enchaînes appel API bancaire, analyse, envoi d’email — ça prend par exemple **5 secondes**.

**Résultat** : page qui freeze, utilisateur qui attend, serveur bloqué.

#### Avec Celery

Tu fais : utilisateur clique → Flask envoie la tâche à Celery → Celery traite en arrière-plan → Flask répond tout de suite.

**Résultat** : site réactif, tâches longues séparées du request/response, architecture plus scalable.

#### Exemple concret (futur site microcrédit)

**Sans Celery** — tout dans la requête HTTP :

```python
def analyse_credit():
    call_api_bank()
    calcul_score()
    envoyer_email()
```

→ l’utilisateur attend longtemps (ex. 10 secondes).

**Avec Celery** — délégation :

```python
analyse_credit.delay()
```

→ réponse immédiate du type : « Votre demande est en cours de traitement ».

#### Cas d’usage typiques

Celery sert notamment pour :

| Domaine | Exemples |
|--------|----------|
| **Emails** | confirmations, relances |
| **Calculs** | scoring crédit, stats, ML |
| **Vérifications** | KYC, fraude, appels API externes |
| **Fichiers** | PDF, OCR, images |

#### Comment ça marche (architecture)

1. **Flask** envoie la tâche.
2. **Redis** (ou autre broker) fait office de **file d’attente** — comme une « boîte aux lettres ».
3. **Worker Celery** exécute la tâche hors du processus web.

**Image simple** : Flask = réceptionniste, Redis = liste des tâches, Celery = employé qui travaille en coulisse.

#### Important

Celery **n’est pas obligatoire** au début d’un projet. Il devient utile quand :

- les tâches dépassent ~**1 seconde**,
- tu as **beaucoup d’utilisateurs**,
- la **logique métier** devient lourde ou complexe.

**Dans ton cas** :

- **Projet eToro (ce dépôt)** : pas indispensable tout de suite.
- **Projet microcrédit** : quasi indispensable à terme pour rester pro et scalable.

#### Résumé

Celery = **tâches en arrière-plan** pour garder l’app rapide, scalable et « prod ».

#### Conseil

Commence **sans** Celery ; ajoute-le quand ça devient **lent** ou **trop complexe** à traiter dans la requête HTTP.

### Frontend séparé (Next.js)

Une base frontend séparée est disponible dans `frontend/` et consomme l'API Flask via `api/v1`.

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Variables frontend :

- `NEXT_PUBLIC_API_BASE_URL` (ex: `http://127.0.0.1:5001`)

### Note performance (première visite)

La première ouverture de la page (`/`) peut être plus lente si les APIs externes sont lentes. Les rafraîchissements sont ensuite rapides grâce au cache.

- **Cache mémoire local** : actif par défaut (rapide, mais non partagé entre workers).
- **Cache Redis** : activé si `REDIS_URL` est défini (partagé, recommandé en production).
- **Warmup manuel** : `GET/POST /internal/warmup` (protégeable via `WARMUP_TOKEN`).
- **Warmup auto** : activé par défaut (`AUTO_WARMUP_ON_START=1`) avec limite de temps (`STARTUP_WARMUP_MAX_SECONDS`).

Exemple de warmup après déploiement :

```bash
curl -H "X-Warmup-Token: <ton_token>" https://ton-domaine/internal/warmup
```

### Checklist production (rapide)

- Configurer `REDIS_URL` pour un cache partagé entre workers/instances.
- Définir `WARMUP_TOKEN` et garder `/internal/warmup` protégé.
- Laisser `AUTO_WARMUP_ON_START=1`.
- Régler `STARTUP_WARMUP_MAX_SECONDS=10` (ajuster 8-15 selon ton infra).
- Après chaque déploiement, lancer :

```bash
curl -H "X-Warmup-Token: <token>" https://ton-domaine/internal/warmup
```

- Vérifier les logs au boot : `startup warmup: started` puis `startup warmup: done ...`.

### Déploiement production

En production, les clés API eToro n’ont pas besoin d’être dans un `.env` du dépôt : place-les dans un fichier système lisible uniquement par root (ex. `/etc/etoro/interface.env`, `chmod 600`) et référence ce fichier avec **`EnvironmentFile=`** dans les unités systemd (voir les exemples dans le dossier **`deploy/`**).

- **`env_load.py`** : charge un fichier optionnel pointé par `ETORO_ENV_FILE` ou `ENV_FILE`, puis le `.env` local s’il existe. Les variables **déjà présentes** dans l’environnement (injectées par systemd) **ne sont pas écrasées**.
- **Gunicorn** : exemple d’unité dans `deploy/gunicorn-etoro.service.example` (même `EnvironmentFile` que le job de sync).
- **Sync des posts trader** : script `sync_romain_posts.py` (JSON + images WebP dans `data/`). Pour l’exécuter **tous les jours** sans intervention, utiliser le timer systemd `deploy/sync-trader-posts.timer` + `deploy/sync-trader-posts.service` (adapter chemins et utilisateur). L’application **recharge** `data/trader_posts_romainroth.json` lorsque le fichier change sur disque ; **inutile de redémarrer Gunicorn** après le sync.
- **Installation pas à pas** : [`deploy/README.md`](deploy/README.md).

#### Variables serveur minimales (`/etc/etoro/interface.env`)

Pour la récupération des posts eToro + newsletter automatique, définir au minimum :

- `ETORO_API_KEY`
- `ETORO_USER_KEY`
- `DATABASE_URL`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM`
- `SMTP_USE_TLS=1`
- `SITE_BASE_URL`
- `NEWSLETTER_UNSUBSCRIBE_SECRET`

Exemple :

```env
ETORO_API_KEY=...
ETORO_USER_KEY=...
DATABASE_URL=postgresql://user:pass@127.0.0.1:5432/etoro
SMTP_HOST=smtp.mail.ovh.net
SMTP_PORT=587
SMTP_USER=contact@romainroth.com
SMTP_PASSWORD=...
SMTP_FROM=Romain Roth <contact@romainroth.com>
SMTP_USE_TLS=1
SITE_BASE_URL=https://romainroth.com
NEWSLETTER_UNSUBSCRIBE_SECRET=...
```

À quoi servent les variables SMTP / URL :

- `SMTP_HOST` : serveur SMTP de ton fournisseur mail (ex. `smtp.mail.ovh.net`).
- `SMTP_PORT` : port SMTP (souvent `587` avec TLS, parfois `465` en SSL).
- `SMTP_USER` : identifiant du compte mail SMTP.
- `SMTP_PASSWORD` : mot de passe (ou mot de passe applicatif) du compte SMTP.
- `SMTP_FROM` : expéditeur affiché dans les emails (ex. `Romain Roth <contact@...>`).
- `SMTP_USE_TLS=1` : active le chiffrement TLS (recommandé avec port `587`).
- `SITE_BASE_URL` : URL publique du site, utilisée pour les liens email (ex. désinscription).

#### Cron serveur pour le sync eToro

Si ton cron tourne sur un serveur séparé, la configuration doit etre faite directement sur ce serveur (le `crontab` local de ton Mac ne s'applique pas en production).

1. Se connecter en SSH :

```bash
ssh <user>@<ip-ou-domaine>
```

2. Ouvrir le crontab du bon utilisateur :

```bash
crontab -e
```

3. Ajouter la tache quotidienne (6h00) :

```bash
0 6 * * * cd /chemin/vers/etoro_interface && /chemin/vers/etoro_interface/venv/bin/python sync_romain_posts.py >> /chemin/vers/etoro_interface/logs/sync.log 2>&1
```

4. Creer le dossier de logs (une fois) :

```bash
mkdir -p /chemin/vers/etoro_interface/logs
```

5. Verifier l'installation du cron :

```bash
crontab -l
```

6. Tester sans attendre 6h00 :

```bash
cd /chemin/vers/etoro_interface
/chemin/vers/etoro_interface/venv/bin/python sync_romain_posts.py
tail -n 100 /chemin/vers/etoro_interface/logs/sync.log
```

## Lancement

```bash
python app.py
```

Ouvrir [http://127.0.0.1:5001](http://127.0.0.1:5001) dans le navigateur.

### Gunicorn (3 façons équivalentes)

En production ou pour tester avec plusieurs workers, préférer **l’exécutable du venv** : si tu lances seulement la commande `gunicorn`, macOS peut prendre le binaire du **Python système** (sans Flask dans le venv) et provoquer `ModuleNotFoundError: No module named 'flask'`.

1. **Chemin explicite vers le binaire du venv** (recommandé) :

```bash
./venv/bin/gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

2. **Module Python du venv** :

```bash
./venv/bin/python -m gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

3. **Script** `run-gunicorn.sh` (même chose que l’option 1, depuis la racine du projet) :

```bash
./run-gunicorn.sh
```

Puis ouvrir [http://127.0.0.1:8000](http://127.0.0.1:8000) (port différent du serveur de dev).

## Structure

```
etoro_interface/
├── app.py                 # Application Flask
├── env_load.py            # Chargement .env / ETORO_ENV_FILE (vars systemd préservées)
├── etoro_client.py        # Client API eToro
├── sync_romain_posts.py   # Sync posts + images (cron ou systemd timer en prod)
├── run-gunicorn.sh        # Gunicorn via venv (voir section Lancement)
├── requirements.txt
├── deploy/                # Exemples systemd + env (voir Déploiement production)
├── templates/
│   └── profile.html       # Interface
└── .env                   # Clés API (dev local ; optionnel en prod si systemd)
```

> **⚠️ DANGER — Backend et frontend non séparés**  
> Ce projet est une **application monolithique** : le backend (Flask, `app.py`) et le frontend (HTML/CSS/JS dans `templates/profile.html`) sont dans le même dépôt et le même processus. Flask sert à la fois les pages et les APIs (`/api/chat`, `/api/chart-data`, etc.).  
> Pour **séparer** backend et frontend, il faudrait : un backend qui n’expose que des routes JSON (sans `render_template` pour l’UI), et une application frontend distincte (React, Vue, Svelte ou HTML/JS) sur un autre port, qui appelle ce backend. À faire si vous visez une architecture découplée (équipes différentes, déploiements indépendants, SPA).

## Configuration du trader

Par défaut, le profil affiché est **RomainRoth**. Pour modifier, éditer dans `app.py` :

```python
TRADER_USERNAME = "NomDuTrader"
```

## API eToro

- [Documentation officielle](https://api-portal.etoro.com/)
- Base URL : `https://public-api.etoro.com/api/v1/`

### Publication de posts (feed eToro)

1. **Doc API eToro**

L’endpoint utilisé est bien `POST /api/v1/feeds/post` (**Create a new discussion post**).  
Le corps attendu est `DiscussionCreateRequest` : `message` (obligatoire), `attachments` (optionnel, avec `url`, `mediaType: "Image"`, `media.image`).

2. **`etoro_client.py`**

Nouvelle fonction `create_post(message, image_url=None, image_width=630, image_height=315)` qui envoie un `POST` vers `https://public-api.etoro.com/api/v1/feeds/post` avec les en-têtes existants (`x-api-key`, `x-user-key`, `x-request-id`).  
Si `image_url` est fourni, un attachment image est ajouté au body.

3. **`app.py`**

Nouvelle route `POST /api/post-to-etoro` qui reçoit en JSON : `title`, `summary`, `image_url` (optionnel).  
Construit le texte du post : `title + "\n\n" + summary`.  
Si `image_url` est relatif (commence par `/`), il est transformé en URL absolue avec `request.host_url`.  
Les `data:` URLs sont ignorées (eToro attend une URL publique).  
En cas de succès (`201`), renvoie `{ "success": true, "post": ... }` ; sinon `502` avec un message d’erreur.

4. **Template (`Dernières actualités`)**

Un bouton « Poster sur eToro » a été ajouté sous chaque actualité (rendu Jinja + rendu dynamique après rafraîchissement).  
Au clic : envoi des `data-title`, `data-summary`, `data-image-url` du bouton vers `/api/post-to-etoro`, puis affichage d’une alerte succès/erreur.  
Quand l’image est générée côté client, `data-image-url` du bouton est mis à jour pour inclure l’URL de l’image.  
Style du bouton : `.btn-post-etoro` (petit bouton secondaire avec hover).

**À noter**

- Pour que l’image soit visible sur eToro, son URL doit être **accessible publiquement**. En local (`localhost`), eToro ne pourra pas la charger ; en production (ou avec un tunnel type ngrok), utiliser l’URL absolue de l’image.
- Les clés `ETORO_API_KEY` et `ETORO_USER_KEY` dans `.env` doivent être valides pour que le post soit créé.
- Les avertissements du linter sur le template viennent du mélange Jinja/JS (ex. `{{ ... }}`) et ne concernent pas les nouveaux bouts de code.

**Exposer l’app en local avec ngrok (pour que les URLs d’images soient publiques)**

Installer ngrok (macOS avec Homebrew) :

```bash
brew install ngrok
```

Exemple :

```bash
ngrok http 5001
```

Si ton serveur local expose par exemple :

`http://127.0.0.1:5001/static/image.png`

ngrok te donnera une URL publique du type :

`https://abc123.ngrok-free.app/static/image.png`

Cette URL est alors testable publiquement (et utilisable par l’API eToro pour les pièces jointes des posts).

## Actualités Zonebourse

L’interface affiche les **3 dernières actualités Zonebourse** : le texte des articles est récupéré (BeautifulSoup), puis OpenAI génère un **titre** et un **résumé en 5 lignes** pour chaque article.

### Source des données (URLs)

Les actualités sont récupérées depuis Zonebourse via les URLs suivantes (définies dans `zone_bourse/news_fetcher.py`) :

| Usage | URL |
|-------|-----|
| **Page listing** (liste des derniers articles) | `https://www.zonebourse.com/actualite-bourse/` |
| **Un article** (format) | `https://www.zonebourse.com/actualite-bourse/{slug-titre}-{id}` |

Exemple d’URL d’article :  
`https://www.zonebourse.com/actualite-bourse/les-bourses-europeennes-rebondissent-apres-deux-seances-dans-le-rouge-ce7e5cd3df81f627`

Le code parse le HTML de la page listing pour extraire les liens vers les 3 derniers articles, puis charge chaque page d’article pour en extraire le texte (JSON-LD `articleBody` ou sélecteurs DOM). Si la page listing ne renvoie pas de liens, des URLs d’articles de secours (fallback) sont utilisées.

> **📌 Note — Limiter les requêtes Zonebourse**  
> Pour éviter de surcharger Zonebourse et limiter les risques de blocage (rate limit, 403), le nombre d’actualités récupérées est fixé à **3** (`get_latest_news(limit=3)` dans `app.py`). Ne pas augmenter abusivement ce nombre ; en cas de besoin (ex. cache, file d’attente), mettre en place un cache côté serveur ou un délai entre les requêtes plutôt que d’enchaîner beaucoup d’appels.

### Résumé avec OpenAI

Une fois le texte de l’article extrait, il est envoyé à l’API OpenAI avec un **prompt** pour générer un titre et un résumé en 5 lignes. Le prompt utilisé est la constante `SUMMARY_PROMPT` dans `zone_bourse/news_fetcher.py`, **lignes 21-29** :

```python
SUMMARY_PROMPT = """Tu es un rédacteur financier. Voici le texte d'un article boursier.

Réponds UNIQUEMENT en JSON valide avec exactement deux clés :
- "titre" : un titre court et percutant (une phrase).
- "resume" : un résumé en exactement 5 lignes (5 phrases courtes, une par ligne, séparées par des retours à la ligne).

Article :

"""
```

Le modèle utilisé est **gpt-4o-mini**. La réponse JSON est parsée pour afficher le titre et le résumé dans l’interface. La clé API est lue depuis la variable d’environnement `OPENAI_API_KEY` (fichier `.env`).

> **📌 Cache** — Les posts et images sont mis en cache dans `data/zonebourse_posts.json` (métadonnées) et `data/zonebourse_images/` (images). Gain de place et de mémoire par rapport au stockage base64 en JSON.  
> **Format d’image** : préférer **WebP** ou **JPEG** au **PNG** pour des fichiers moins gourmands en taille ; le PNG reste possible si besoin de transparence sans perte.

### Actualités par instrument (Mediastack)

Juste **sous les 3 posts Zonebourse**, une section affiche les **3 dernières actualités** liées aux instruments en portefeuille du trader (RomainRoth). Les actualités proviennent de l’[API Mediastack](https://mediastack.com/documentation) et sont **traduites en français** via OpenAI (gpt-4o-mini).

| Élément | Description |
|--------|-------------|
| **Source** | Mediastack (paramètre `MEDIASTACK_ACCESS_KEY` dans `.env`) |
| **Mots-clés** | Symboles/noms des instruments du portefeuille |
| **Fallback** | Si aucun résultat : catégorie `business`, puis sans filtre |
| **Traduction** | Titre et description traduits en français via `OPENAI_API_KEY` |

Sans `MEDIASTACK_ACCESS_KEY`, la section affiche « Aucune actualité chargée ». Le plan gratuit Mediastack limite à 100 requêtes/mois.

![Capture des actualités Zonebourse](images/actualité.png)

![Capture des actualités Zonebourse 2](images/actualité2.png)

![Chatbot - poser une question](images/ask_chatbot.png)

![Chatbot - réponse avec lien cliquable](images/answer_chatbot.png)

![Avertissement sur les risques](images/risk.png)

### Quand ça marche

Tu vois des titres comme « Analyse des tendances du marché financier », « Les tendances du marché financier en 2023 », « Les cryptomonnaies en pleine effervescence », avec des résumés en 5 lignes sur des thèmes boursiers / marchés / crypto. Dans ce cas, le flux a bien :

1. Récupéré 3 pages Zonebourse (ou les URLs de secours)
2. Extrait le texte avec BeautifulSoup
3. Envoyé le texte à OpenAI avec le prompt configuré
4. Affiché les titres et résumés en 5 lignes renvoyés par l’API

Si le contenu te paraît un peu générique, c’est soit parce que les articles scrapés étaient courts / peu détaillés, soit parce que le modèle a un peu « lissé » le texte.

### Quand ça échoue (message par défaut)

Les textes **par défaut** (quand tout échoue) sont :

- **Titres** : « Actualité 1 (exemple) », « Actualité 2 (exemple) », « Actualité 3 (exemple) »
- **Résumés** : des phrases du type « Le chargement des articles Zonebourse a échoué… », « Vous pouvez tester avec des fichiers HTML locaux… », etc.

### Vérifier la source

Pour vérifier que les données viennent bien de l’API (et non des placeholders), ouvre :

**http://127.0.0.1:5001/api/zonebourse-news-debug**

et regarde si les champs `title` / `summary` correspondent à ce que tu vois sur la page (et qu’il n’y a pas « (exemple) » dans les titres).

---

## 1️⃣ À quoi sert Werkzeug

Werkzeug fournit les briques techniques bas niveau pour un serveur web Python.

Par exemple :

- gérer les requêtes HTTP
- gérer les réponses HTTP
- gérer les cookies
- parser les formulaires
- router les URLs
- gérer les headers

En résumé :

```
navigateur
     ↓
requête HTTP
     ↓
Werkzeug analyse la requête
     ↓
ton application Python
     ↓
Werkzeug renvoie la réponse HTTP
```

## 2️⃣ Exemple simple avec Werkzeug

```python
from werkzeug.wrappers import Request, Response
from werkzeug.serving import run_simple

@Request.application
def application(request):
    return Response("Hello World")

run_simple("localhost", 5000, application)
```

Quand tu vas sur `http://localhost:5000`, le navigateur reçoit : **Hello World**.

## 3️⃣ Pourquoi Flask utilise Werkzeug

Flask est construit au-dessus de Werkzeug.

Structure simplifiée :

```
Flask
   ↓
Werkzeug
   ↓
WSGI
   ↓
serveur web
```

Donc Flask utilise Werkzeug pour :

- analyser les requêtes
- gérer les routes
- créer les réponses HTTP

## 4️⃣ Ce que contient Werkzeug

| Module      | Fonction                    |
|------------|-----------------------------|
| routing    | gestion des routes          |
| wrappers   | objets Request / Response   |
| serving    | serveur de développement    |
| exceptions | erreurs HTTP                |
| utils      | fonctions utiles            |

## 5️⃣ Werkzeug et WSGI

Werkzeug implémente WSGI. WSGI est une norme qui relie un serveur web et une application Python.

Architecture :

```
Nginx / Apache
       ↓
WSGI
       ↓
Werkzeug
       ↓
Application Python
```

## 6️⃣ Pourquoi utiliser Werkzeug directement

Les développeurs l’utilisent quand ils veulent :

- créer leur propre framework web
- comprendre comment fonctionne Flask
- faire des outils HTTP personnalisés

## ✅ Résumé

| Question            | Réponse                    |
|---------------------|----------------------------|
| Qu'est-ce que Werkzeug | bibliothèque web Python  |
| À quoi ça sert      | gérer requêtes et réponses HTTP |
| Framework complet   | non                        |
| Utilisé par         | Flask                      |
| Niveau              | bas niveau                 |


