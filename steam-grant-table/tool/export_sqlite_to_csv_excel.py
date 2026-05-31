import argparse
import re
import sqlite3
import sys
from pathlib import Path


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


def get_table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return [row[1] for row in rows]


def build_mod_rank_map(conn: sqlite3.Connection, main_table: str) -> dict:
    table_columns = get_table_columns(conn, main_table)
    required = {"mod_id", "subscribers"}
    if not required.issubset(set(table_columns)):
        return {}

    rows = conn.execute(
        f"""
        SELECT mod_id
        FROM "{main_table}"
        WHERE mod_id IS NOT NULL
        ORDER BY CAST(subscribers AS INTEGER) DESC
        """
    ).fetchall()

    rank_map: dict = {}
    for mod_id, in rows:
        if mod_id not in rank_map:
            rank_map[mod_id] = len(rank_map)
    return rank_map


def is_mod_id_primary_key(conn: sqlite3.Connection, table_name: str) -> bool:
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    pk_cols = [row[1] for row in rows if row[5] > 0]
    return pk_cols == ["mod_id"]


def fetch_table(
    conn: sqlite3.Connection,
    table_name: str,
    mod_rank_map: dict,
) -> tuple[list[str], list[tuple]]:
    cursor = conn.execute(f'SELECT * FROM "{table_name}"')
    rows = cursor.fetchall()
    columns = [col[0] for col in cursor.description]

    if mod_rank_map and is_mod_id_primary_key(conn, table_name) and "mod_id" in columns:
        mod_id_idx = columns.index("mod_id")
        unknown_rank = len(mod_rank_map) + 10**9
        rows = sorted(rows, key=lambda row: mod_rank_map.get(row[mod_id_idx], unknown_rank))

    return columns, rows


def normalize_sheet_name(raw_name: str, existing_names: set[str]) -> str:
    cleaned = re.sub(r'[\\/*?:\[\]]', "_", raw_name or "Sheet")
    base_name = cleaned[:31] if cleaned else "Sheet"
    candidate = base_name
    idx = 1
    while candidate in existing_names:
        suffix = f"_{idx}"
        candidate = f"{base_name[:31 - len(suffix)]}{suffix}"
        idx += 1
    existing_names.add(candidate)
    return candidate


def export_excel(
    conn: sqlite3.Connection,
    tables: list[str],
    mod_rank_map: dict,
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
        columns, rows = fetch_table(conn, table, mod_rank_map)
        sheet = workbook.create_sheet(title=normalize_sheet_name(table, existing_names))
        sheet.append(columns)
        for row in rows:
            sheet.append(list(row))
        print(f"[OK] {table}")

    workbook.save(output_file)
    return output_file


def export_database(db_path: Path, output_file: Path, main_table: str) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite 文件不存在: {db_path}")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        tables = get_table_names(conn)
        if not tables:
            raise RuntimeError("数据库中没有可导出的表。")

        mod_rank_map = build_mod_rank_map(conn, main_table) if main_table in tables else {}
        xlsx_path = export_excel(conn, tables, mod_rank_map, output_file)
        print(f"  XLSX -> {xlsx_path}")


def main() -> None:
    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from data_paths import DB_PATH, MODS_TABLE, XLSX_PATH, ensure_data_dirs  # noqa: E402

    ensure_data_dirs()
    default_db = DB_PATH
    default_output_file = XLSX_PATH

    parser = argparse.ArgumentParser(description="将 SQLite 所有表导出到一个 Excel 文件。")
    parser.add_argument("--db-path", type=Path, default=default_db, help="输入 sqlite 文件路径")
    parser.add_argument("--output-file", type=Path, default=default_output_file, help="导出的 Excel 文件路径")
    parser.add_argument(
        "--main-table",
        type=str,
        default=MODS_TABLE,
        help="用于构建 mod_id 排序基准的主表名",
    )
    args = parser.parse_args()

    export_database(args.db_path, args.output_file, args.main_table)


if __name__ == "__main__":
    main()
