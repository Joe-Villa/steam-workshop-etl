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

from app_config import bootstrap_config_from_argv, load_app_config, resolve_path
from mod_fetch_db import init_db_from_urls, load_urls_from_json


def main() -> None:
    remaining = bootstrap_config_from_argv(sys.argv[1:])
    cfg = load_app_config()

    if len(remaining) >= 1:
        urls_path = resolve_path(remaining[0])
    else:
        urls_path = cfg.io.input_path

    if len(remaining) >= 2:
        sqlite_path = resolve_path(remaining[1])
    else:
        sqlite_path = cfg.io.sqlite_path

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
