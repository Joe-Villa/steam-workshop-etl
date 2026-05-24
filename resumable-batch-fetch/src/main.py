#!/usr/bin/env python3
"""
Single entry point: create ``mod_fetch.sqlite`` when missing, then run the fetch loop.

If ``concrete_html/mod_fetch.sqlite`` already exists, skip initialization and
resume crawling (same behavior as ``fetch_via_id.py`` alone). Paths come from
repo ``cfg/base.json`` (see ``lib/paradox_paths.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from app_config import load_app_config


def main() -> None:
    cfg = load_app_config()
    sqlite_path = cfg.io.sqlite_path

    if sqlite_path.is_file():
        print(f"Resume: {sqlite_path} exists, continuing fetch.")
    else:
        print(
            f"Fresh start: {sqlite_path} not found, "
            f"initializing from {cfg.io.input_path}."
        )
        from init_mod_fetch_sqlite import main as init_main

        init_main()
        print("")

    from fetch_via_id import main as fetch_main

    fetch_main()


if __name__ == "__main__":
    main()
