#!/usr/bin/env python3
"""
Create a fresh mod_fetch SQLite DB from the URL list in cfg/config.json ``io.input_path``.

All rows: status=pending, retry_count=0. Row id is AUTOINCREMENT.
Run once before fetch_via_id (or use an existing SQLite state file instead).
"""

from __future__ import annotations

import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from app_config import load_app_config, resolve_under_root
from mod_fetch_db import init_db_from_urls, load_status_info_path, load_urls_from_json


def main() -> None:
    cfg = load_app_config()

    if len(sys.argv) >= 2:
        urls_path = resolve_under_root(sys.argv[1])
    else:
        urls_path = cfg.io.input_path

    if len(sys.argv) >= 3:
        sqlite_path = resolve_under_root(sys.argv[2])
    else:
        sqlite_path = load_status_info_path()

    print(f"URL list: {urls_path}")
    print(f"SQLite:   {sqlite_path}")

    try:
        urls = load_urls_from_json(urls_path)
        n = init_db_from_urls(sqlite_path, urls)
    except FileExistsError as e:
        raise SystemExit(str(e)) from e
    except (FileNotFoundError, ValueError) as e:
        raise SystemExit(str(e)) from e

    print(f"Created {sqlite_path} with {n} rows (all pending).")


if __name__ == "__main__":
    main()
