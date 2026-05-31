#!/usr/bin/env python3
"""
Print total URL count and pending/success/fail/invalid breakdown to stdout.
"""

from __future__ import annotations

import sys
from pathlib import Path

_here = Path(__file__).resolve().parent.parent / "src"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from app_config import bootstrap_config_from_argv, load_app_config, resolve_path
from mod_fetch_db import (
    TABLE_NAME,
    connect_db,
    rowcount,
    table_exists,
)


def status_counts(conn) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT status, COUNT(*) AS n FROM {TABLE_NAME} GROUP BY status"
    ).fetchall()
    return {str(r["status"]): int(r["n"]) for r in rows}


def pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return 100.0 * part / total


def main() -> None:
    remaining = bootstrap_config_from_argv(sys.argv[1:])
    cfg = load_app_config()
    if len(remaining) >= 1:
        sqlite_path = resolve_path(remaining[0])
    else:
        sqlite_path = cfg.io.sqlite_path

    if not sqlite_path.is_file():
        print(f"ERROR: database not found: {sqlite_path}", file=sys.stderr)
        raise SystemExit(2)

    conn = connect_db(sqlite_path)
    try:
        if not table_exists(conn):
            print(f"ERROR: table `{TABLE_NAME}` missing in {sqlite_path}", file=sys.stderr)
            raise SystemExit(2)

        total = rowcount(conn, f"SELECT COUNT(*) FROM {TABLE_NAME}")
        counts = status_counts(conn)
    finally:
        conn.close()

    print(f"Database: {sqlite_path}")
    print(f"Total URLs: {total}")
    print("")
    for status in ("pending", "success", "fail", "invalid"):
        n = counts.get(status, 0)
        print(f"  {status:8s}: {n:6d}  ({pct(n, total):6.2f}%)")


if __name__ == "__main__":
    main()
