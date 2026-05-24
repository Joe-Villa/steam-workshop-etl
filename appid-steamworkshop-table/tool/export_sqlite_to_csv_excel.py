#!/usr/bin/env python3
"""Export workshop_mods.sqlite (3 tables) to one Excel workbook."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

# openpyxl rejects XML 1.0 control chars in cell text
_ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_EXCEL_CELL_MAX_LEN = 32767

_TABLE_DETAIL = "mod_detail_url"
_TABLE_RANKS = "mod_tag_ranks"
_TABLE_BROWSE = "mod_browse_info"
_EXPORT_TABLES = (_TABLE_DETAIL, _TABLE_RANKS, _TABLE_BROWSE)


def get_table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name;
        """
    ).fetchall()
    return [row[0] for row in rows]


def order_tables_for_export(tables: list[str]) -> list[str]:
    ordered = [t for t in _EXPORT_TABLES if t in tables]
    extra = sorted(t for t in tables if t not in _EXPORT_TABLES)
    return ordered + extra


def get_table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return [row[1] for row in rows]


def sanitize_cell(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    text = _ILLEGAL_XML_RE.sub("", str(value))
    if len(text) > _EXCEL_CELL_MAX_LEN:
        text = text[: _EXCEL_CELL_MAX_LEN - 3] + "..."
    return text


def _best_rank_key(row: tuple, columns: list[str]) -> tuple:
    mod_id_idx = columns.index("mod_id")
    rank_vals = [
        int(v)
        for i, v in enumerate(row)
        if i != mod_id_idx and v is not None
    ]
    best = min(rank_vals) if rank_vals else 10**9
    return (best, row[mod_id_idx])


def fetch_table(conn: sqlite3.Connection, table_name: str) -> tuple[list[str], list[tuple]]:
    cursor = conn.execute(f'SELECT * FROM "{table_name}"')
    rows = cursor.fetchall()
    columns = [col[0] for col in cursor.description]

    if table_name == _TABLE_RANKS and "mod_id" in columns:
        rows = sorted(rows, key=lambda r: _best_rank_key(r, columns))

    return columns, rows


def normalize_sheet_name(raw_name: str, existing_names: set[str]) -> str:
    cleaned = re.sub(r'[\\/*?:\[\]]', "_", raw_name or "Sheet")
    base_name = cleaned[:31] if cleaned else "Sheet"
    candidate = base_name
    idx = 1
    while candidate in existing_names:
        suffix = f"_{idx}"
        candidate = f"{base_name[: 31 - len(suffix)]}{suffix}"
        idx += 1
    existing_names.add(candidate)
    return candidate


def export_excel(
    conn: sqlite3.Connection,
    tables: list[str],
    output_file: Path,
) -> Path:
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise RuntimeError(
            "缺少 openpyxl 依赖，请先安装：python3 -m pip install openpyxl"
        ) from exc

    workbook = Workbook()
    workbook.remove(workbook.active)
    existing_names: set[str] = set()

    for table in tables:
        columns, rows = fetch_table(conn, table)
        sheet = workbook.create_sheet(title=normalize_sheet_name(table, existing_names))
        sheet.append([sanitize_cell(c) for c in columns])
        for row in rows:
            sheet.append([sanitize_cell(v) for v in row])
        print(f"[OK] {table} ({len(rows)} rows)")

    workbook.save(output_file)
    return output_file


def export_database(db_path: Path, output_file: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite 文件不存在: {db_path}")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        tables = order_tables_for_export(get_table_names(conn))
        if not tables:
            raise RuntimeError("数据库中没有可导出的表。")
        xlsx_path = export_excel(conn, tables, output_file)
        print(f"XLSX -> {xlsx_path}")


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root / "src"))
    from paths import get_layout

    layout = get_layout()
    default_db = layout.simple_sqlite
    default_output = layout.simple_xlsx

    parser = argparse.ArgumentParser(
        description="将 workshop_mods.sqlite 三张表导出到一个 Excel 文件。"
    )
    parser.add_argument("--db-path", type=Path, default=default_db, help="输入 sqlite 路径")
    parser.add_argument(
        "--output-file",
        type=Path,
        default=default_output,
        help="输出 .xlsx 路径",
    )
    args = parser.parse_args()

    try:
        export_database(args.db_path, args.output_file)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=__import__("sys").stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
