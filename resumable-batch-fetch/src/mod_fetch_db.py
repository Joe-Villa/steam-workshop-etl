#It is not an Executable file
"""Shared SQLite schema and helpers for mod URL fetch pipeline."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from app_config import load_app_config

TABLE_NAME = "mod_fetch"

CREATE_TABLE_SQL = f"""
CREATE TABLE {TABLE_NAME} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'success', 'fail', 'invalid')),
    retry_count INTEGER NOT NULL DEFAULT 0
        CHECK (retry_count >= 0)
)
"""

_MOD_ID_RE = re.compile(r"[?&]id=(\d+)")


def load_status_info_path() -> Path:
    """SQLite path: ``io.status_info`` relative to the directory of ``io.input_path``."""
    return load_app_config().io.sqlite_path


def mod_id_from_url(url: str) -> str | None:
    m = _MOD_ID_RE.search(url)
    return m.group(1) if m else None


def connect_db(sqlite_path: Path) -> sqlite3.Connection:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (TABLE_NAME,),
    ).fetchone()
    return row is not None


def rowcount(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    cur = conn.execute(sql, params)
    return int(cur.fetchone()[0])


def load_urls_from_json(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"URL list not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path.name} must be a JSON array of URL strings")
    urls: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{path.name}[{i}] must be a non-empty string URL")
        urls.append(item.strip())
    return urls


def init_db_from_urls(sqlite_path: Path, urls: list[str]) -> int:
    """
    Create a fresh ``mod_fetch`` database (all rows pending).

    Raises ``FileExistsError`` if ``sqlite_path`` already exists.
  """
    if sqlite_path.is_file():
        raise FileExistsError(
            f"Refusing to overwrite existing database: {sqlite_path}\n"
            "Remove it manually if you intend to re-initialize."
        )
    if not urls:
        raise ValueError("No URLs in input list.")

    conn = connect_db(sqlite_path)
    try:
        conn.execute(CREATE_TABLE_SQL)
        conn.executemany(
            f"INSERT INTO {TABLE_NAME} (url, status, retry_count) VALUES (?, 'pending', 0)",
            [(u,) for u in urls],
        )
        conn.commit()
        return rowcount(conn, f"SELECT COUNT(*) FROM {TABLE_NAME}")
    finally:
        conn.close()
