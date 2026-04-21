"""Charge les secrets pour l'API eToro et l'app.

- Les variables déjà présentes (ex. injectées par systemd EnvironmentFile) ne sont pas écrasées.
- Fichier optionnel : ETORO_ENV_FILE ou ENV_FILE (ex. /etc/etoro/interface.env).
- Sinon : .env à la racine du projet s'il existe (développement).
"""

from __future__ import annotations

import os
from pathlib import Path


def load_app_dotenv(project_root: Path | None = None) -> None:
    from dotenv import load_dotenv

    root = project_root or Path(__file__).resolve().parent
    override = False
    extra = (os.getenv("ETORO_ENV_FILE") or os.getenv("ENV_FILE") or "").strip()
    if extra:
        p = Path(extra).expanduser()
        if p.is_file():
            load_dotenv(p, override=override)
    local = root / ".env"
    if local.is_file():
        load_dotenv(local, override=override)
