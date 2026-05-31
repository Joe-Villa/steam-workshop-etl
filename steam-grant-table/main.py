#!/usr/bin/env python3
"""
建库：仅接受一个 paths JSON 参数。

input（对象或数组，顺序无关）:
  detail_html, browse_sqlite, workshop_status（可选）
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent
_BUILD = _PKG_ROOT / "build_sqlite"
_TOOL = _PKG_ROOT / "tool"
_REPO = _PKG_ROOT.parent
_LIB = _REPO / "lib"
for _p in (_BUILD, _TOOL, _PKG_ROOT, _LIB):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import data_paths  # noqa: E402
from build_all_tables import main as build_main  # noqa: E402
from pipeline_manifest import StageManifest  # noqa: E402
from stage_entry import run_stage_main  # noqa: E402
from stage_inputs import require_input  # noqa: E402
from stage_layout import layout_from_manifest  # noqa: E402
from validate_build_inputs import browse_sqlite_available, fail  # noqa: E402


def _resolve_grant_inputs(
    spec: StageManifest, layout
) -> tuple[Path, Path, Path | None]:
    html_dir = require_input(spec.input_map, "detail_html")
    browse_db = require_input(spec.input_map, "browse_sqlite")
    situation = spec.input_map.get("workshop_status")
    if situation is None and layout.current_situation_json.is_file():
        situation = layout.current_situation_json
    return html_dir, browse_db, situation


def _run_stage(spec: StageManifest, cfg: dict) -> None:
    layout = layout_from_manifest(spec, cfg)
    data_paths.reload_layout(data_root=layout.root, repo_root=spec.repo_root)
    data_paths.ensure_data_dirs()
    layout = data_paths.get_layout()

    html_dir, browse_db, situation = _resolve_grant_inputs(spec, layout)
    skip_excel = bool(spec.options.get("skip_excel"))

    if not html_dir.is_dir():
        fail(f"详情页 HTML 目录不存在: {html_dir}")
    if not browse_db.is_file():
        fail(f"步骤一简略表 SQLite 不存在: {browse_db}")
    if not browse_sqlite_available(browse_db):
        fail(f"步骤一 browse SQLite 不可用: {browse_db}")

    print(f"HTML 输入: {html_dir}", flush=True)
    print(f"Browse SQLite: {browse_db}", flush=True)
    if situation is not None:
        print(f"current_situation: {situation}", flush=True)

    argv = [
        "build_all_tables.py",
        "--html-dir",
        str(html_dir),
        "--db-path",
        str(layout.analysis_sqlite),
        "--output-file",
        str(layout.analysis_xlsx),
        "--browse-db-path",
        str(browse_db),
    ]
    if situation is not None:
        argv.extend(["--current-situation-json", str(situation)])
    if skip_excel:
        argv.append("--skip-excel")

    old_argv = sys.argv
    sys.argv = argv
    try:
        build_main()
    finally:
        sys.argv = old_argv


def main() -> None:
    run_stage_main(_run_stage)


if __name__ == "__main__":
    main()
