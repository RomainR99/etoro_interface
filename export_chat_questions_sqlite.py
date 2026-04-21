"""Convertit data/chat_questions.jsonl en data/chat_questions.sqlite.

Usage:
    python3 export_chat_questions_sqlite.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
JSONL_PATH = BASE_DIR / "data" / "chat_questions.jsonl"
SQLITE_PATH = BASE_DIR / "data" / "chat_questions.sqlite"


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def export_to_sqlite(rows: list[dict], sqlite_path: Path) -> int:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(sqlite_path)
    try:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS chat_questions")
        cur.execute(
            """
            CREATE TABLE chat_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                question TEXT,
                reply TEXT
            )
            """
        )
        cur.executemany(
            "INSERT INTO chat_questions (timestamp, question, reply) VALUES (?, ?, ?)",
            [
                (
                    str(r.get("timestamp") or ""),
                    str(r.get("question") or ""),
                    str(r.get("reply") or ""),
                )
                for r in rows
            ],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def main() -> None:
    rows = load_rows(JSONL_PATH)
    count = export_to_sqlite(rows, SQLITE_PATH)
    print(f"OK: {count} lignes exportees vers {SQLITE_PATH}")


if __name__ == "__main__":
    main()
