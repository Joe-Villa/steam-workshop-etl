"""
一次运行：从工坊 HTML 构建 SQLite 全表，并导出为单个 Excel 文件。

HTML 根目录可任意指定（不必放在 data/html），例如：
  python build_all_tables.py /path/to/html
  python build_all_tables.py --html-dir /path/to/html

步骤：
  0. 检查依赖（详情页 HTML、browse SQLite 等）
  1. aaa_mods（解析详情页 HTML）
  2. mod_private_like_flags
  3. mod_subscriber_exposure_ratio_with_mod_name
  4. mod_tag_flags
  5. 合并 browse 表
  6. authors（来源 mod_browse_info.author）
  7. metadata
  8. statistic
  9. 导出 Excel
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_TOOL_DIR = _PROJECT_ROOT / "tool"
for _path in (_SCRIPT_DIR, _TOOL_DIR, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from data_paths import (  # noqa: E402
    BROWSE_SQLITE_PATH,
    CURRENT_SITUATION_JSON,
    DB_PATH,
    DEFAULT_DB_NAME,
    DEFAULT_XLSX_NAME,
    HTML_DIR,
    MODS_TABLE,
    XLSX_PATH,
    ensure_data_dirs,
)
from export_sqlite_to_csv_excel import export_database  # noqa: E402
from metadata_table import METADATA_TABLE, create_metadata_table  # noqa: E402
from statistics_table import STATISTIC_TABLE, create_statistic_table  # noqa: E402
from validate_build_inputs import browse_sqlite_available, fail, validate_build_inputs  # noqa: E402
PRIVATE_FLAGS_TABLE = "mod_private_like_flags"
RATIO_WITH_NAME_TABLE = "mod_subscriber_exposure_ratio_with_mod_name"
TAG_FLAGS_TABLE = "mod_tag_flags"
BROWSE_INFO_TABLE = "mod_browse_info"
AUTHORS_TABLE = "authors"

# Victoria 3 workshop canonical tags (build_mod_tag_flags.py)
CANONICAL_TAGS = [
    "AI",
    "Alternative History",
    "Balance",
    "Cultures and Religions",
    "Diplomacy",
    "Economy and Buildings",
    "Events",
    "Expansion",
    "Fixes",
    "Flags",
    "Gameplay",
    "Graphics",
    "Historical",
    "Journal Entries",
    "Laws",
    "Map",
    "Military",
    "New Nations",
    "Politics",
    "Pops",
    "Technologies",
    "Total Conversion",
    "Trade",
    "Utilities",
]

TAG_ALIASES: dict[str, str] = {
    "alternate history": "Alternative History",
    "alternete history": "Alternative History",
    "alternative": "Alternative History",
    "culture and religions": "Cultures and Religions",
    "cultures and religions": "Cultures and Religions",
    "economy": "Economy and Buildings",
    "economy and buildings": "Economy and Buildings",
    "fixed": "Fixes",
    "fix": "Fixes",
    "history": "Historical",
    "historicall": "Historical",
    "journal_entries": "Journal Entries",
    "new nations": "New Nations",
    "technology": "Technologies",
    "utilities": "Utilities",
    "utility": "Utilities",
    "warfare": "Military",
    "warefare": "Military",
}

TAG_LINK_RE = re.compile(r"requiredtags%5B%5D=[^\"&>]+\">([^<]+)</a>", flags=re.IGNORECASE)
MOD_ID_FROM_HTML_RE = re.compile(r"var\s+publishedfileid\s*=\s*'(\d+)'", flags=re.IGNORECASE)


# ---------------------------------------------------------------------------
# mods + authors（原 build_mods_sqlite.py）
# ---------------------------------------------------------------------------

def _extract_first(pattern: str, text: str, flags: int = 0) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def _extract_int_from_labeled_row(text: str, label: str) -> int | None:
    escaped_label = re.escape(label)
    pattern = rf"<tr>\s*<td>\s*([\d,]+)\s*</td>\s*<td>\s*{escaped_label}\s*</td>\s*</tr>"
    value = _extract_first(pattern, text, flags=re.IGNORECASE)
    if value is None:
        return None
    return int(value.replace(",", ""))


def _is_chinese_name(username: str) -> int:
    return 1 if re.search(r"[\u4e00-\u9fff]", username) else 0


def _load_author_classification(classification_json_path: Path | None) -> dict[str, int]:
    if classification_json_path is None or not classification_json_path.exists():
        return {}

    with classification_json_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    mapping: dict[str, int] = {}
    for username in payload.get("is_chinese", []):
        mapping[str(username)] = 1
    for username in payload.get("not_chinese", []):
        mapping[str(username)] = 0
    return mapping


def _normalize_username(raw: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", raw.strip()))


def parse_mod_html(html_text: str) -> dict[str, object] | None:
    mod_id = _extract_first(r"var\s+publishedfileid\s*=\s*'(\d+)'", html_text)
    mod_name = _extract_first(r'<div class="workshopItemTitle">\s*(.*?)\s*</div>', html_text, flags=re.DOTALL)

    if not mod_id or not mod_name:
        return None

    mod_name = html.unescape(re.sub(r"\s+", " ", mod_name).strip())

    visitors = _extract_int_from_labeled_row(html_text, "Unique Visitors")
    subscribers = _extract_int_from_labeled_row(html_text, "Current Subscribers")
    favorites = _extract_int_from_labeled_row(html_text, "Current Favorites")

    return {
        "mod_id": mod_id,
        "mod_name": mod_name,
        "exposure": visitors if visitors is not None else 0,
        "subscribers": subscribers if subscribers is not None else 0,
        "favorites": favorites if favorites is not None else 0,
    }


def create_mods_tables(conn: sqlite3.Connection) -> None:
    conn.execute('DROP TABLE IF EXISTS mods;')
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{MODS_TABLE}" (
            mod_id TEXT NOT NULL,
            mod_name TEXT NOT NULL,
            exposure INTEGER NOT NULL,
            subscribers INTEGER NOT NULL,
            favorites INTEGER NOT NULL,
            PRIMARY KEY (mod_id)
        );
        """
    )


def build_database(mods_dir: Path, db_path: Path) -> tuple[int, int]:
    html_files = list(mods_dir.rglob("*.html"))
    parsed_rows: list[dict[str, object]] = []
    skipped = 0

    for html_file in html_files:
        text = html_file.read_text(encoding="utf-8", errors="ignore")
        row = parse_mod_html(text)
        if row is None:
            skipped += 1
            continue
        parsed_rows.append(row)

    if db_path.exists():
        db_path.unlink()

    with sqlite3.connect(db_path) as conn:
        create_mods_tables(conn)
        conn.executemany(
            f"""
            INSERT INTO "{MODS_TABLE}" (
                mod_id,
                mod_name,
                exposure,
                subscribers,
                favorites
            ) VALUES (?, ?, ?, ?, ?);
            """,
            [
                (
                    str(row["mod_id"]),
                    str(row["mod_name"]),
                    int(row["exposure"]),
                    int(row["subscribers"]),
                    int(row["favorites"]),
                )
                for row in parsed_rows
            ],
        )
        conn.commit()

    return len(parsed_rows), skipped



# ---------------------------------------------------------------------------
# mod_private_like_flags（原 build_private_like_flags.py）
# ---------------------------------------------------------------------------

PRIVATE_NAME_PATTERN = re.compile(
    r"(?:^|\b)(test|debug|tmp|temp|private|for me|my |mine|backup|demo|自用|测试|临时)(?:\b|$)",
    flags=re.IGNORECASE,
)


def looks_private_like(mod_name: str, exposure: int, subscribers: int, favorites: int) -> int:
    if subscribers >= 100 or favorites >= 20 or exposure >= 2000:
        return 0
    if subscribers <= 10 and favorites <= 1 and exposure <= 80:
        return 1
    if subscribers <= 50 and PRIVATE_NAME_PATTERN.search(mod_name):
        return 1
    return 0


def create_private_flags_table(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            mod_id TEXT PRIMARY KEY,
            is_private_like INTEGER NOT NULL CHECK (is_private_like IN (0, 1))
        );
        """
    )


def rebuild_flags(db_path: Path, table_name: str = PRIVATE_FLAGS_TABLE) -> tuple[int, int]:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        create_private_flags_table(conn, table_name)

        rows = conn.execute(
            f"""
            SELECT mod_id, mod_name, exposure, subscribers, favorites
            FROM "{MODS_TABLE}";
            """
        ).fetchall()

        flags: list[tuple[str, int]] = []
        private_count = 0
        for mod_id, mod_name, exposure, subscribers, favorites in rows:
            is_private = looks_private_like(
                mod_name=str(mod_name),
                exposure=int(exposure),
                subscribers=int(subscribers),
                favorites=int(favorites),
            )
            private_count += is_private
            flags.append((str(mod_id), is_private))

        conn.execute(f'DELETE FROM "{table_name}";')
        conn.executemany(
            f'INSERT INTO "{table_name}" (mod_id, is_private_like) VALUES (?, ?);',
            flags,
        )
        conn.commit()

    return len(flags), private_count



def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1;",
        (name,),
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# mod_subscriber_exposure_ratio_with_mod_name
# （原 export_subscriber_exposure_ratio_with_mod_name.py）
# ---------------------------------------------------------------------------

def create_ratio_with_mod_name_table(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            mod_id TEXT PRIMARY KEY,
            mod_name TEXT NOT NULL,
            subscribers INTEGER NOT NULL,
            subscriber_exposure_ratio REAL NOT NULL
        );
        """
    )


def rebuild_subscriber_exposure_ratio_with_mod_name(
    db_path: Path,
    output_table: str = RATIO_WITH_NAME_TABLE,
    flags_table: str = PRIVATE_FLAGS_TABLE,
) -> tuple[int, int]:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        if not _table_exists(conn, MODS_TABLE):
            raise RuntimeError(f'Required table "{MODS_TABLE}" is missing in the database.')
        if not _table_exists(conn, flags_table):
            raise RuntimeError(
                f'Flags table "{flags_table}" not found. Run private-like flags step first.'
            )

        conn.execute('DROP TABLE IF EXISTS "mod_subscriber_exposure_ratio";')
        conn.execute(f'DROP TABLE IF EXISTS "{output_table}";')
        create_ratio_with_mod_name_table(conn, output_table)
        flags_join = f'LEFT JOIN "{flags_table}" f ON f.mod_id = m.mod_id'
        flags_filter = "AND COALESCE(f.is_private_like, 0) = 0"
        conn.execute(
            f"""
            INSERT INTO "{output_table}" (
                mod_id,
                mod_name,
                subscribers,
                subscriber_exposure_ratio
            )
            SELECT
                m.mod_id,
                m.mod_name,
                m.subscribers,
                ROUND(CAST(m.subscribers AS REAL) / m.exposure, 3)
            FROM "{MODS_TABLE}" m
            {flags_join}
            WHERE m.exposure > 0
              {flags_filter}
            ORDER BY ROUND(CAST(m.subscribers AS REAL) / m.exposure, 3) DESC, m.mod_id;
            """
        )
        conn.commit()
        inserted = conn.execute(f'SELECT COUNT(*) FROM "{output_table}";').fetchone()[0]
        gt_one = conn.execute(
            f"""
            SELECT COUNT(*) FROM "{output_table}"
            WHERE subscriber_exposure_ratio > 1.0;
            """
        ).fetchone()[0]

    return int(inserted), int(gt_one)



# ---------------------------------------------------------------------------
# mod_tag_flags（原 build_mod_tag_flags.py）
# ---------------------------------------------------------------------------

def _tag_to_column_name(tag: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", tag.lower()).strip("_")
    return f"has_{normalized}"


def _canonicalize_workshop_tag(tag: str) -> str | None:
    raw = " ".join(tag.split()).strip()
    if not raw:
        return None

    lowered = raw.lower()
    if lowered in TAG_ALIASES:
        return TAG_ALIASES[lowered]

    for canonical in CANONICAL_TAGS:
        if lowered == canonical.lower():
            return canonical
    return None


def parse_mod_id_from_html(html_text: str) -> str | None:
    match = MOD_ID_FROM_HTML_RE.search(html_text)
    if not match:
        return None
    return match.group(1)


def parse_mod_tags_from_html(html_text: str) -> set[str]:
    found: set[str] = set()
    for match in TAG_LINK_RE.finditer(html_text):
        canonical = _canonicalize_workshop_tag(match.group(1))
        if canonical is not None:
            found.add(canonical)
    return found


def create_tag_flags_table(conn: sqlite3.Connection, table_name: str) -> None:
    columns_sql = ",\n            ".join(
        f'"{_tag_to_column_name(tag)}" INTEGER NOT NULL CHECK ("{_tag_to_column_name(tag)}" IN (0, 1))'
        for tag in CANONICAL_TAGS
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            mod_id TEXT PRIMARY KEY,
            {columns_sql}
        );
        """
    )


def rebuild_tag_flags(
    mods_dir: Path,
    db_path: Path,
    table_name: str = TAG_FLAGS_TABLE,
) -> tuple[int, int]:
    if not mods_dir.exists():
        raise FileNotFoundError(f"Mods directory not found: {mods_dir}")
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    html_files = list(mods_dir.rglob("*.html"))
    rows: list[tuple[int | str, ...]] = []
    skipped = 0

    column_names = [_tag_to_column_name(tag) for tag in CANONICAL_TAGS]

    for html_file in html_files:
        html_text = html_file.read_text(encoding="utf-8", errors="ignore")
        mod_id = parse_mod_id_from_html(html_text)
        if mod_id is None:
            skipped += 1
            continue

        tags = parse_mod_tags_from_html(html_text)
        row_values = [1 if tag in tags else 0 for tag in CANONICAL_TAGS]
        rows.append((mod_id, *row_values))

    with sqlite3.connect(db_path) as conn:
        create_tag_flags_table(conn, table_name)
        conn.execute(f'DELETE FROM "{table_name}";')

        insert_columns = ", ".join(["mod_id", *[f'"{name}"' for name in column_names]])
        placeholders = ", ".join(["?"] * (1 + len(column_names)))

        conn.executemany(
            f'INSERT INTO "{table_name}" ({insert_columns}) VALUES ({placeholders});',
            rows,
        )
        conn.commit()

    return len(rows), skipped



# ---------------------------------------------------------------------------
# browse 表合并 + authors（来源 mod_browse_info.author）
# ---------------------------------------------------------------------------

def merge_browse_tables(conn: sqlite3.Connection, browse_db_path: Path) -> list[str]:
    src_path = browse_db_path.expanduser().resolve()
    if not src_path.is_file():
        raise FileNotFoundError(f"Browse SQLite not found: {src_path}")

    with sqlite3.connect(f"file:{src_path}?mode=ro", uri=True) as src_conn:
        tables = [
            row[0]
            for row in src_conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name;
                """
            ).fetchall()
        ]
        if not tables:
            raise RuntimeError(f"No tables found in browse SQLite: {src_path}")

        for table in tables:
            ddl_row = src_conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?;",
                (table,),
            ).fetchone()
            if ddl_row is None or not ddl_row[0]:
                raise RuntimeError(f"Missing DDL for browse table: {table}")

            conn.execute(f'DROP TABLE IF EXISTS "{table}"')
            conn.execute(ddl_row[0])
            columns = [
                col[1] for col in src_conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            ]
            quoted_cols = ", ".join(f'"{col}"' for col in columns)
            placeholders = ", ".join("?" for _ in columns)
            rows = src_conn.execute(f'SELECT {quoted_cols} FROM "{table}"').fetchall()
            conn.executemany(
                f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders});',
                rows,
            )
        conn.commit()
    return tables


def rebuild_authors_table(conn: sqlite3.Connection) -> tuple[int, int]:
    if not _table_exists(conn, BROWSE_INFO_TABLE):
        raise RuntimeError(
            f'Required table "{BROWSE_INFO_TABLE}" is missing. Run browse merge step first.'
        )

    rows = conn.execute(
        f"""
        SELECT author
        FROM "{BROWSE_INFO_TABLE}"
        WHERE author IS NOT NULL AND TRIM(author) != '';
        """
    ).fetchall()

    author_seen: dict[str, int] = {}
    for (raw_author,) in rows:
        username = _normalize_username(str(raw_author))
        if not username:
            continue
        author_seen[username] = _is_chinese_name(username)

    conn.execute(f'DROP TABLE IF EXISTS "{AUTHORS_TABLE}"')
    conn.execute(
        f"""
        CREATE TABLE "{AUTHORS_TABLE}" (
            username TEXT PRIMARY KEY,
            is_chinese INTEGER NOT NULL CHECK (is_chinese IN (0, 1))
        );
        """
    )
    conn.executemany(
        f'INSERT INTO "{AUTHORS_TABLE}" (username, is_chinese) VALUES (?, ?);',
        list(author_seen.items()),
    )
    conn.commit()
    chinese_count = sum(is_chinese for is_chinese in author_seen.values())
    return len(author_seen), chinese_count



# ---------------------------------------------------------------------------
# 主流水线
# ---------------------------------------------------------------------------

def resolve_html_dir(html_dir: Path | None, html_dir_opt: Path | None) -> Path:
    """位置参数 > --html-dir/--mods-dir > 默认 data/html。"""
    chosen = html_dir_opt or html_dir or HTML_DIR
    return chosen.expanduser().resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从工坊 HTML 构建 SQLite 各表，并导出到一个 Excel 文件。",
        epilog="未指定 HTML 目录时使用 data/html。",
    )
    parser.add_argument(
        "html_dir",
        nargs="?",
        type=Path,
        default=None,
        help="工坊 HTML 根目录（递归扫描 *.html；可省略）",
    )
    parser.add_argument(
        "--html-dir",
        "--mods-dir",
        dest="html_dir_opt",
        type=Path,
        default=None,
        metavar="DIR",
        help="同上；与位置参数二选一，同时给出时以本选项为准",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help="SQLite 输出路径（默认 data/table/mods.sqlite3）",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=XLSX_PATH,
        help="导出的 Excel 路径（默认 data/table/all_tables.xlsx）",
    )
    parser.add_argument(
        "--browse-db-path",
        type=Path,
        default=BROWSE_SQLITE_PATH,
        help="步骤一 browse SQLite（默认 simple_info/name.sqlite）",
    )
    parser.add_argument(
        "--current-situation-json",
        type=Path,
        default=CURRENT_SITUATION_JSON,
        help="步骤一工坊现状 JSON（默认 simple_info/current_situation.json）",
    )
    parser.add_argument(
        "--skip-excel",
        action="store_true",
        help="只建 SQLite，不导出 Excel",
    )
    args = parser.parse_args()
    args.mods_dir = resolve_html_dir(args.html_dir, args.html_dir_opt)
    return args


def main() -> None:
    ensure_data_dirs()
    args = parse_args()

    print(f"HTML 输入: {args.mods_dir}")
    print(f"Browse SQLite: {args.browse_db_path}")

    print("== 0/9 检查依赖 ==")
    html_count, has_browse = validate_build_inputs(args.mods_dir, args.browse_db_path)
    print(f"  detail html files: {html_count}")
    if has_browse:
        print(f"  browse sqlite: {args.browse_db_path.resolve()}")
    else:
        print(
            "[WARN] 未找到可用的步骤一 browse SQLite，将跳过 browse 表合并、authors 及依赖 browse 的 metadata 字段。",
            file=sys.stderr,
        )

    situation_path: Path | None = args.current_situation_json
    if situation_path is not None and not situation_path.is_file():
        print(
            f"[WARN] 未找到 current_situation.json，metadata 将跳过工坊现状: "
            f"{situation_path.resolve()}",
            file=sys.stderr,
        )
        situation_path = None
    elif situation_path is not None:
        print(f"  current_situation: {situation_path.resolve()}")

    print(f"== 1/9 {MODS_TABLE} ==")
    inserted, skipped = build_database(args.mods_dir, args.db_path)
    print(f"  mods: {inserted}, skipped html: {skipped}")
    if inserted == 0:
        fail(
            f"详情页 HTML 共 {html_count} 个文件，但未解析出任何模组。"
            f"请检查 {args.mods_dir.resolve()} 下的 HTML 是否为有效的工坊详情页。"
        )

    print("== 2/9 mod_private_like_flags ==")
    total_flags, private_count = rebuild_flags(args.db_path)
    print(f"  rows: {total_flags}, private-like: {private_count}")

    print(f"== 3/9 {RATIO_WITH_NAME_TABLE} ==")
    ratio_name_rows, ratio_gt_one = rebuild_subscriber_exposure_ratio_with_mod_name(args.db_path)
    print(f"  rows: {ratio_name_rows}, ratio > 1: {ratio_gt_one}")

    print(f"== 4/9 {TAG_FLAGS_TABLE} ==")
    tag_rows, tag_skipped = rebuild_tag_flags(args.mods_dir, args.db_path)
    print(f"  rows: {tag_rows}, skipped html: {tag_skipped}")

    if has_browse:
        print("== 5/9 merge browse tables ==")
        with sqlite3.connect(args.db_path) as conn:
            browse_tables = merge_browse_tables(conn, args.browse_db_path)
        print(f"  tables: {', '.join(browse_tables)}")

        print(f"== 6/9 {AUTHORS_TABLE} ==")
        with sqlite3.connect(args.db_path) as conn:
            author_count, chinese_count = rebuild_authors_table(conn)
        print(f"  authors: {author_count}, is_chinese=1: {chinese_count}")
    else:
        print("== 5/9 merge browse tables (skipped) ==")
        print("== 6/9 authors (skipped) ==")

    print(f"== 7/9 {METADATA_TABLE} ==")
    with sqlite3.connect(args.db_path) as conn:
        mod_count = conn.execute(f'SELECT COUNT(*) FROM "{MODS_TABLE}";').fetchone()[0]
        create_metadata_table(
            conn,
            int(mod_count),
            detail_html_dir=args.mods_dir,
            current_situation_path=situation_path,
        )
        gap_count = 0
        if has_browse and _table_exists(conn, BROWSE_INFO_TABLE):
            gap_count = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM "{BROWSE_INFO_TABLE}" AS b
                LEFT JOIN "{MODS_TABLE}" AS m ON m.mod_id = b.mod_id
                WHERE m.mod_id IS NULL;
                """
            ).fetchone()[0]
    print(f"  mod_count: {mod_count}, browse_only: {gap_count}")

    print("== 8/9 statistic ==")
    with sqlite3.connect(args.db_path) as conn:
        stat_rows = create_statistic_table(conn)
    print(f"  rows: {stat_rows}")

    if args.skip_excel:
        print("== 9/9 Excel export (skipped) ==")
        print(f"SQLite: {args.db_path}")
        return

    print("== 9/9 Excel export ==")
    export_database(args.db_path, args.output_file, main_table=MODS_TABLE)
    print(f"完成。SQLite: {args.db_path}")
    print(f"       Excel: {args.output_file}")


if __name__ == "__main__":
    main()
