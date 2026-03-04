# eToro Interface
## Présentation du projet

---

# 1. Contexte et objectif

- **Interface web** pour suivre un profil de trader eToro.
- Visualiser les **performances** (gains mensuels/annuels, portefeuille).
- Comparer avec des **indices** (S&P 500, NASDAQ 100, CAC 40 TR, MSCI World).
- Outils complémentaires : actualités bourse, assistant conversationnel.

---

# 2. Fonctionnalités principales

- **Profil trader** : avatar, gains, portefeuille.
- **Graphiques** : comparaison des performances (base 100), ajout de traders populaires.
- **Simulation DCA** : 1 000 $ + 100 $/mois vs S&P 500.
- **Instruments** : liste des actions par place de marché (API eToro).
- **Actualités Zonebourse** : 3 derniers articles, résumés en 5 lignes (IA).
- **Chatbot** : conseils vidéos/livres et objectifs financiers (OpenAI).

---

# 3. Stack technique

| Couche        | Techno                    |
|---------------|---------------------------|
| Backend       | **Flask** (Python)        |
| Client API    | **requests**, **etoro_client** |
| Données       | **API eToro**, **yfinance** |
| Actualités    | **BeautifulSoup**, **Zonebourse** |
| Résumés / Chat| **OpenAI** (gpt-4o-mini)  |
| Front         | **HTML/CSS/JS**, **Jinja2**, **Chart.js** |

---

# 4. Architecture : monolithique

- **Backend et frontend** dans le même projet et le même processus.
- **Flask** sert les pages (`render_template`) et les APIs (`/api/chat`, `/api/chart-data`, etc.).
- Pas de SPA ni de front séparé : tout dans `templates/profile.html`.
- Pour une architecture découplée : backend API uniquement + front (React/Vue) à part.

---

# 5. API eToro

- **Documentation** : [api-portal.etoro.com](https://api-portal.etoro.com/)
- **Base URL** : `https://public-api.etoro.com/api/v1/`
- **Clés** : `ETORO_API_KEY` et `ETORO_USER_KEY` dans le fichier `.env`.
- Endpoints utilisés : profil, gains, portefeuille, traders les plus copiés, instruments par place de marché.

---

# 6. Actualités Zonebourse — source des données

- **Page listing** : `https://www.zonebourse.com/actualite-bourse/`
- **Articles** : `https://www.zonebourse.com/actualite-bourse/{slug}-{id}`
- Récupération du **texte** : BeautifulSoup (JSON-LD `articleBody` ou sélecteurs DOM).
- **Limite** : 3 actualités pour ne pas surcharger le site (rate limit, 403).

---

# 7. Actualités Zonebourse — flux

1. Requête sur la page **listing** Zonebourse.
2. Extraction des **liens** vers les 3 derniers articles (ou URLs de secours).
3. Pour chaque article : **fetch HTML** → extraction du texte (BeautifulSoup).
4. Envoi du texte à **OpenAI** avec un prompt (titre + résumé en 5 lignes).
5. Affichage **titre + résumé** dans l’interface (pas de lien externe).

---

# 8. Chatbot OpenAI

- **Fenêtre** en bas à droite, message d’accueil : *« Besoin d’aide ? »*.
- **Prompt système** : fichier `prompts/chatbot_system.txt` (assistant financier, vidéos/livres, objectifs).
- **API** : `POST /api/chat` avec `{ "messages": [...] }`, réponse `{ "reply": "..." }`.
- Modèle : **gpt-4o-mini** ; clé : `OPENAI_API_KEY` dans `.env`.

---

# 9. Avertissements et bonnes pratiques

- **Risques** : stratégie personnelle, pas un conseil ; performances passées ne garantissent pas les résultats futurs (affiché en bas de page).
- **Zonebourse** : limiter le nombre de requêtes (3 articles), privilégier un cache ou des délais si besoin.
- **Backend/frontend** : architecture monolithique ; séparer si équipes ou déploiements distincts.

---

# 10. Démo et suite

- **Lancement** : `./run.sh` ou `./venv/bin/python3 app.py` → [http://127.0.0.1:5001](http://127.0.0.1:5001).
- **Debug actualités** : [http://127.0.0.1:5001/api/zonebourse-news-debug](http://127.0.0.1:5001/api/zonebourse-news-debug).
- **Repo** : structure `app.py`, `etoro_client.py`, `zone_bourse/`, `prompts/`, `templates/`, `images/`.
- **Pistes** : cache Zonebourse, séparation backend/frontend, tests automatisés.
