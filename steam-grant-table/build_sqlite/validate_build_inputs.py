"""建库前检查：详情页 HTML、browse SQLite 等依赖是否齐全。"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REQUIRED_BROWSE_TABLES = ("mod_browse_info", "mod_detail_url", "mod_tag_ranks")
BROWSE_INFO_TABLE = "mod_browse_info"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate_detail_html_dir(mods_dir: Path) -> int:
    path = mods_dir.expanduser().resolve()
    if not path.is_dir():
        fail(f"详情页 HTML 目录不存在或不是目录: {path}")

    html_files = list(path.rglob("*.html"))
    if not html_files:
        fail(f"详情页 HTML 目录下没有 *.html 文件: {path}")
    return len(html_files)


def browse_sqlite_available(browse_db_path: Path) -> bool:
    path = browse_db_path.expanduser().resolve()
    if not path.is_file():
        return False

    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            table_rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%';
                """
            ).fetchall()
            tables = {row[0] for row in table_rows}
            if not tables:
                return False

            missing = [name for name in REQUIRED_BROWSE_TABLES if name not in tables]
            if missing:
                return False

            for table in REQUIRED_BROWSE_TABLES:
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}";').fetchone()[0]
                if int(count) <= 0:
                    return False

            browse_cols = {
                row[1]
                for row in conn.execute(f'PRAGMA table_info("{BROWSE_INFO_TABLE}")').fetchall()
            }
            for required_col in ("mod_id", "author"):
                if required_col not in browse_cols:
                    return False
    except sqlite3.Error:
        return False

    return True


def validate_build_inputs(mods_dir: Path, browse_db_path: Path) -> tuple[int, bool]:
    """返回 (详情页 HTML 文件数量, browse SQLite 是否可用)。"""
    html_count = validate_detail_html_dir(mods_dir)
    has_browse = browse_sqlite_available(browse_db_path)
    return html_count, has_browse
