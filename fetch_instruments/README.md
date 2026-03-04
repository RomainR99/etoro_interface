# Export des instruments eToro

Script pour récupérer les instruments (stocks/ETF, ID ≥ 1001) et les exporter en CSV et JSON.

## Prérequis

- Python 3 avec les dépendances du projet (`python-dotenv`, `requests`).
- Fichier `.env` à la racine du projet avec `ETORO_API_KEY` et `ETORO_USER_KEY`.

## Usage

Depuis la racine du projet :

```bash
python fetch_instruments/export_instruments.py              # 1001 à 1010 (défaut)
python fetch_instruments/export_instruments.py 1001 1010   # plage personnalisée
python fetch_instruments/export_instruments.py 9601 9800   # ex. ID 9601 à 9800
```

Exemple de sortie :

```
Récupération des instruments eToro (ID 9601 à 9800)...
200 instruments exportés (ID 9601–9800).
  CSV :  fetch_instruments/instruments_9601_9800.csv
  JSON : fetch_instruments/instruments_9601_9800.json
```

## Fichiers générés

- **instruments_{id_min}_{id_max}.csv** : colonnes `n°`, `id`, `symbole`, `nom` (UTF-8 avec BOM).
- **instruments_{id_min}_{id_max}.json** : tableau d'objets `{ "n", "id", "symbole", "nom" }`.

Les fichiers sont créés dans le dossier `fetch_instruments/`.
