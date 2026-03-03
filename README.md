# eToro Interface

Interface web pour visualiser le profil d'un trader eToro, comparer les performances avec des indices (S&P 500, NASDAQ 100, CAC 40 TR, MSCI World) et lister les instruments par place de marché.

## Fonctionnalités

- **Profil trader** : affichage du profil, des gains mensuels/annuels et du portefeuille
- **Comparaison des performances** : courbes comparatives (base 100) avec possibilité d'ajouter les 100 traders les plus copiés
- **Indices** : S&P 500, NASDAQ 100, CAC 40 TR, MSCI World
- **Simulation DCA** : 1 000 $ au départ + 100 $/mois, comparaison avec le S&P 500
- **Instruments par place de marché** : liste des actions et instruments disponibles sur eToro

## Prérequis

- Python 3.10+
- Compte eToro vérifié avec clés API

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

## Configuration

Créer un fichier `.env` à la racine :

```env
ETORO_API_KEY=ta_clé_api_publique
ETORO_USER_KEY=ta_clé_utilisateur
```

Les clés se génèrent dans **Paramètres > Trading > Gestion des clés API** sur eToro.

## Lancement

```bash
python app.py
```

Ouvrir [http://127.0.0.1:5000](http://127.0.0.1:5000) dans le navigateur.

## Structure

```
etoro_interface/
├── app.py              # Application Flask
├── etoro_client.py     # Client API eToro
├── requirements.txt
├── templates/
│   └── profile.html    # Interface
└── .env                # Clés API (à créer)
```

## Configuration du trader

Par défaut, le profil affiché est **RomainRoth**. Pour modifier, éditer dans `app.py` :

```python
TRADER_USERNAME = "NomDuTrader"
```

## API eToro

- [Documentation officielle](https://api-portal.etoro.com/)
- Base URL : `https://public-api.etoro.com/api/v1/`
