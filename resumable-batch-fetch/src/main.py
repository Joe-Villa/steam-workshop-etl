#!/usr/bin/env python3
"""
Single entry point: init SQLite from URL list when missing, then run the fetch loop.

Usage::

    python src/main.py
    python src/main.py --config /path/to/job.json
    python src/main.py /path/to/job.json

With ``io`` in config, paths are relative to the config file's directory.
Without ``io``, uses the Paradox repo ``cfg/base.json`` data layout (stage 2).
"""

from __future__ import annotations

import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from app_config import (  # noqa: E402
    OutputDirNotCleanError,
    bootstrap_config_from_argv,
    ensure_io_dirs,
    get_config_path,
    load_app_config,
    validate_output_dir,
)


def run_with_config(config_path: str | Path) -> int:
    """Programmatic entry: load config, validate output dir, init if needed, crawl."""
    remaining = bootstrap_config_from_argv(["--config", str(config_path)])
    return _run_pipeline(remaining)


def _run_pipeline(_remaining_argv: list[str]) -> int:
    if _remaining_argv:
        print(
            f"WARNING: ignoring extra arguments: {' '.join(_remaining_argv)}",
            file=sys.stderr,
        )

    try:
        cfg = load_app_config()
        validate_output_dir(cfg.io.output_dir, cfg.io.sqlite_path)
        ensure_io_dirs(cfg.io)
    except (OutputDirNotCleanError, OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    cfg_path = get_config_path()
    print(f"Config: {cfg_path}")
    print(f"URLs:   {cfg.io.input_path}")
    print(f"Output: {cfg.io.output_dir}")

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
    return 0


def main() -> None:
    remaining = bootstrap_config_from_argv(sys.argv[1:])
    code = _run_pipeline(remaining)
    if code != 0:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
