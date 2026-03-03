# Export des instruments eToro

Script pour récupérer les instruments (stocks/ETF, ID ≥ 1001) et les exporter en CSV et JSON.

## Prérequis

- Python 3 avec les dépendances du projet (`python-dotenv`, `requests`).
- Fichier `.env` à la racine du projet avec `ETORO_API_KEY` et `ETORO_USER_KEY`.

## Usage

Depuis la racine du projet :

```bash
python fetch_instruments/export_instruments.py
```

Ou depuis ce dossier :

```bash
cd fetch_instruments && python export_instruments.py
```

## Fichiers générés

- **instruments.csv** : colonnes `n°`, `id`, `symbole` (UTF-8 avec BOM).
- **instruments.json** : tableau d’objets `{ "n", "id", "symbole" }`.

Les fichiers sont créés dans le dossier `fetch_instruments/`.
