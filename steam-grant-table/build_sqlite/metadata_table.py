"""metadata 表：数据说明、工坊现状、缺详情页 browse 模组等。"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from data_paths import MODS_TABLE  # noqa: E402

METADATA_TABLE = "metadata"
BROWSE_INFO_TABLE = "mod_browse_info"

METADATA_DESCRIPTION = (
    "关于作者是否是国人，我们用的是启发式的方式，只判断用户名有没有中文"
    "关于mod_tag_ranks，我们做的是它在steam创意浏览页上的先后排名，而不是subscribers的先后排名。"
    "对于模组作者，我们用的是其在创意工坊浏览页的那一个用户名，也就是忽略了其他贡献者。"
    "关于mod_browse_info，其description是浏览页面的简介，有截断。"
)
_BEIJING_TZ = timezone(timedelta(hours=8))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1;",
        (name,),
    ).fetchone()
    return row is not None


def compute_browse_without_detail(conn: sqlite3.Connection) -> list[str]:
    if not _table_exists(conn, BROWSE_INFO_TABLE) or not _table_exists(conn, MODS_TABLE):
        return []
    rows = conn.execute(
        f"""
        SELECT b.mod_id
        FROM "{BROWSE_INFO_TABLE}" AS b
        LEFT JOIN "{MODS_TABLE}" AS m ON m.mod_id = b.mod_id
        WHERE m.mod_id IS NULL
        ORDER BY CAST(b.mod_id AS INTEGER), b.mod_id;
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def _format_beijing_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=_BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _file_timestamp(path: Path) -> float:
    stat = path.stat()
    birthtime = getattr(stat, "st_birthtime", None)
    return birthtime if birthtime not in (None, 0) else stat.st_mtime


def _detail_html_path(html_root: Path, row_id: int, *, use_mod_html_buckets: bool = True) -> Path:
    """与 resumable-batch-fetch fetch_via_id.html_path 一致；兼容 mod_{id}.html 命名。"""
    if use_mod_html_buckets:
        bucket = html_root / str(row_id)[:2]
        plain = bucket / f"{row_id}.html"
        if plain.is_file():
            return plain
        return bucket / f"mod_{row_id}.html"
    plain = html_root / f"{row_id}.html"
    if plain.is_file():
        return plain
    return html_root / f"mod_{row_id}.html"


def _created_time_from_detail_html(
    html_root: Path,
    *,
    use_mod_html_buckets: bool = True,
) -> tuple[str, Path] | None:
    """取 row_id 1..9 中第一个存在的详情页 HTML 文件时间；否则取目录内最早 HTML。"""
    root = html_root.expanduser().resolve()
    for row_id in range(1, 10):
        path = _detail_html_path(root, row_id, use_mod_html_buckets=use_mod_html_buckets)
        if path.is_file() and path.stat().st_size > 0:
            return _format_beijing_timestamp(_file_timestamp(path)), path

    html_files = sorted(
        (p for p in root.rglob("*.html") if p.is_file() and p.stat().st_size > 0),
        key=_file_timestamp,
    )
    if html_files:
        earliest = html_files[0]
        return _format_beijing_timestamp(_file_timestamp(earliest)), earliest
    return None


def _earliest_data_time_from_situation_file(path: Path | None) -> str | None:
    """取 current_situation.json 的文件时间（优先创建时间，否则最后修改时间）。"""
    if path is None or not path.is_file():
        return None
    return _format_beijing_timestamp(_file_timestamp(path))


def _load_current_situation_rows(path: Path | None) -> list[tuple[str, str]]:
    if path is None or not path.is_file():
        return []

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"current_situation.json must be a JSON object: {path}")

    rows: list[tuple[str, str]] = []
    if "APPID" in data:
        rows.append(("工坊 APPID", str(data["APPID"])))
    if "tag_count" in data:
        rows.append(("工坊标签数", str(data["tag_count"])))
    if "workshop_item_count" in data:
        rows.append(("工坊模组总数（hub）", str(data["workshop_item_count"])))
    if data.get("source_url"):
        rows.append(("工坊主页", str(data["source_url"])))

    tags = data.get("tags")
    if isinstance(tags, list) and tags:
        lines: list[str] = []
        for item in tags:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            count = item.get("count")
            if name is None or count is None:
                continue
            lines.append(f"{name}: {count}")
        if lines:
            rows.append(("标签统计", "\n".join(lines)))

    return rows


def create_metadata_table(
    conn: sqlite3.Connection,
    mod_count: int,
    *,
    detail_html_dir: Path | None = None,
    browse_without_detail: list[str] | None = None,
    current_situation_path: Path | None = None,
) -> None:
    if browse_without_detail is None:
        browse_without_detail = compute_browse_without_detail(conn)

    if detail_html_dir is None:
        created_at = datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    else:
        created = _created_time_from_detail_html(detail_html_dir)
        if created is None:
            root = detail_html_dir.expanduser().resolve()
            raise RuntimeError(
                f"无法在详情页 HTML 目录中找到 row_id 1..9 的有效页面: {root}"
            )
        created_at, _source_html = created

    gap_count = len(browse_without_detail)
    gap_list_text = "\n".join(browse_without_detail) if browse_without_detail else "无"
    earliest_data_time = _earliest_data_time_from_situation_file(current_situation_path)

    rows: list[tuple[str, str]] = [
        ("数据说明", METADATA_DESCRIPTION),
        ("创建时间", created_at),
    ]
    if earliest_data_time is not None:
        rows.append(("最早一条数据的获取时间", earliest_data_time))
    rows.extend(
        [
            ("模组总数量（详情页）", str(mod_count)),
            ("browse 有但缺详情页 HTML 的模组数", str(gap_count)),
            ("browse 有但缺详情页 HTML 的 mod_id 列表", gap_list_text),
        ]
    )
    rows.extend(_load_current_situation_rows(current_situation_path))

    conn.execute(f'DROP TABLE IF EXISTS "{METADATA_TABLE}"')
    conn.execute(
        f"""
        CREATE TABLE "{METADATA_TABLE}" (
            field TEXT NOT NULL,
            value TEXT NOT NULL
        );
        """
    )
    conn.executemany(
        f'INSERT INTO "{METADATA_TABLE}" (field, value) VALUES (?, ?);',
        rows,
    )
    conn.commit()
