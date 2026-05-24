#!/usr/bin/env python3
"""Export mod detail URLs from workshop_mods.sqlite as a JSON string array."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_TABLE_DETAIL = "mod_detail_url"
_COL_DETAIL_URL = "detail_url"


def fetch_detail_urls(conn: sqlite3.Connection) -> list[str]:
    tables = {
        row[0]
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%';
            """
        ).fetchall()
    }
    if _TABLE_DETAIL not in tables:
        raise RuntimeError(f'数据库中缺少表 "{_TABLE_DETAIL}"。')

    columns = [
        row[1]
        for row in conn.execute(f'PRAGMA table_info("{_TABLE_DETAIL}")').fetchall()
    ]
    if _COL_DETAIL_URL not in columns:
        raise RuntimeError(
            f'表 "{_TABLE_DETAIL}" 中缺少列 "{_COL_DETAIL_URL}"。'
        )

    rows = conn.execute(
        f'SELECT "{_COL_DETAIL_URL}" FROM "{_TABLE_DETAIL}" '
        f'WHERE "{_COL_DETAIL_URL}" IS NOT NULL AND TRIM("{_COL_DETAIL_URL}") != "" '
        f'ORDER BY mod_id'
    ).fetchall()
    return [row[0].strip() for row in rows]


def export_detail_urls(db_path: Path, output_file: Path) -> int:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite 文件不存在: {db_path}")

    with sqlite3.connect(db_path) as conn:
        urls = fetch_detail_urls(conn)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(urls, f, ensure_ascii=False, indent=2)
        f.write("\n")

    return len(urls)


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from paths import get_layout

    layout = get_layout()
    default_db = layout.simple_sqlite
    default_output = layout.detail_urls_json

    parser = argparse.ArgumentParser(
        description=(
            "从 workshop_mods.sqlite 的 mod_detail_url 表导出全部 detail_url，"
            "写入 JSON 字符串数组（供后续爬取）。"
        )
    )
    parser.add_argument("--db-path", type=Path, default=default_db, help="输入 sqlite 路径")
    parser.add_argument(
        "--output-file",
        type=Path,
        default=default_output,
        help="输出 JSON 路径",
    )
    args = parser.parse_args()

    try:
        n = export_detail_urls(args.db_path, args.output_file)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1) from e

    print(f"Wrote {args.output_file} ({n} URLs)")


if __name__ == "__main__":
    main()
