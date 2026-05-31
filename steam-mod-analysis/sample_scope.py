"""分析样本口径：统计数据默认仅包含非自用模组。"""

from __future__ import annotations

import sqlite3

PRIVATE_FLAGS_TABLE = "mod_private_like_flags"

SAMPLE_SCOPE_NOTE = (
    "样本口径：仅非自用模组（`mod_private_like_flags.is_private_like = 0`）。"
)


def join_private_like_flags(mod_alias: str = "m") -> str:
    return (
        f'LEFT JOIN "{PRIVATE_FLAGS_TABLE}" f ON f.mod_id = {mod_alias}.mod_id'
    )


def non_private_like_predicate() -> str:
    return "COALESCE(f.is_private_like, 0) = 0"


def fetch_subscribers_non_private(
    conn: sqlite3.Connection,
    *,
    mods_table: str,
    order_by: str = "m.subscribers DESC, m.mod_id ASC",
) -> list[int]:
    sql = f"""
        SELECT m.subscribers
        FROM "{mods_table}" m
        {join_private_like_flags("m")}
        WHERE {non_private_like_predicate()}
          AND m.subscribers IS NOT NULL
        ORDER BY {order_by}
    """
    rows = conn.execute(sql).fetchall()
    return [int(row[0]) for row in rows]
