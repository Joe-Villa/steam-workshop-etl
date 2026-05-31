#!/usr/bin/env python3
"""详情页爬取：仅接受一个 paths JSON 参数。"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent
_SRC = _PKG / "src"
_REPO = _PKG.parent
_LIB = _REPO / "lib"
for _p in (_SRC, _LIB):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from pipeline_manifest import StageManifest  # noqa: E402
from stage_entry import run_stage_main  # noqa: E402
from stage_inputs import require_input  # noqa: E402

from app_config import (  # noqa: E402
    IoPaths,
    configure,
    discover_config_path,
    ensure_io_dirs,
    load_app_config,
    set_stage_io,
    validate_output_dir,
)


def _io_from_spec(spec: StageManifest) -> IoPaths:
    input_path = require_input(spec.input_map, "urls")
    out = spec.output_root
    return IoPaths(
        input_path=input_path,
        output_dir=out,
        html_root=out / "html",
        sqlite_path=out / spec.state_sqlite,
        log_path=out / "output.log",
        use_mod_html_buckets=True,
    )


def _run_pipeline_body() -> int:
    from fetch_via_id import main as fetch_main
    from init_mod_fetch_sqlite import main as init_main

    cfg = load_app_config()
    validate_output_dir(cfg.io.output_dir, cfg.io.sqlite_path)
    ensure_io_dirs(cfg.io)

    sqlite_path = cfg.io.sqlite_path
    if sqlite_path.is_file():
        print(f"Resume: {sqlite_path} exists, continuing fetch.")
    else:
        print(f"Fresh start: initializing from {cfg.io.input_path}.")
        init_main()
        print("")

    fetch_main()
    return 0


def _run_stage(spec: StageManifest, cfg: dict) -> None:  # noqa: ARG001
    crawler = spec.cfg_paths.get("crawler")
    configure(crawler if crawler else discover_config_path())
    set_stage_io(_io_from_spec(spec))
    try:
        code = _run_pipeline_body()
    finally:
        set_stage_io(None)
    if code != 0:
        raise SystemExit(code)


def main() -> None:
    run_stage_main(_run_stage)


if __name__ == "__main__":
    main()
