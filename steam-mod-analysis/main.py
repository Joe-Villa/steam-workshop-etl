#!/usr/bin/env python3
"""
统计分析：仅接受一个 paths JSON 参数。

用法:
  python3 main.py pipeline/run_analysis.json
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent
_REPO = _PKG_ROOT.parent
_LIB = _REPO / "lib"
for _p in (_PKG_ROOT, _LIB):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import data_paths  # noqa: E402
from game_profile import figures_subdir, get_profile, output_basename  # noqa: E402
from pipeline_manifest import StageManifest  # noqa: E402
from stage_entry import run_stage_main  # noqa: E402
from stage_inputs import require_input  # noqa: E402
from stage_layout import layout_from_manifest  # noqa: E402


def _run_module_main(module_name: str, argv_tail: list[str]) -> None:
    old_argv = sys.argv
    sys.argv = [module_name, *argv_tail]
    try:
        mod = importlib.import_module(module_name)
        mod.main()
    finally:
        sys.argv = old_argv


def run_all_analyses(
    db_path: Path,
    result_dir: Path,
    *,
    game: str = "workshop",
) -> None:
    if not db_path.is_file():
        raise FileNotFoundError(f"SQLite 不存在: {db_path}")

    result_dir.mkdir(parents=True, exist_ok=True)
    profile = get_profile(game)
    slug = profile.slug
    db = db_path.resolve()
    out = result_dir.resolve()

    steps: list[tuple[str, Callable[[], None]]] = [
        (
            "订阅宏观仪表盘",
            lambda: _run_module_main(
                "generate_domestic_reports",
                ["--db-path", str(db), "--output-md", str(out / "subscribers_stats.md")],
            ),
        ),
        (
            "国模订阅排名 CSV",
            lambda: _run_module_main(
                "export_domestic_ranking_csv",
                ["--db-path", str(db), "--output-csv", str(out / "domestic_mods_ranking.csv")],
            ),
        ),
        (
            "订阅不平等分析",
            lambda: _run_module_main(
                "analyze_subscribers_inequality",
                [
                    "--game",
                    game,
                    "--db",
                    str(db),
                    "--output",
                    str(out / output_basename(slug, "subscribers_inequality_report.md")),
                ],
            ),
        ),
        (
            "订阅/曝光比分析",
            lambda: _run_module_main(
                "analyze_subscriber_exposure_ratio",
                [
                    "--game",
                    game,
                    "--db",
                    str(db),
                    "--output",
                    str(out / output_basename(slug, "subscriber_exposure_ratio_report.md")),
                    "--excel",
                    str(
                        out
                        / output_basename(slug, "subscriber_exposure_ratio_by_subscribers_tier.xlsx")
                    ),
                    "--figures-dir",
                    str(out / figures_subdir(slug)),
                ],
            ),
        ),
    ]

    total = len(steps)
    for i, (label, run_step) in enumerate(steps, start=1):
        print(f"== {i}/{total} {label} ==")
        run_step()

    state_file = result_dir / "stage_state.sqlite"
    if not state_file.is_file():
        sqlite3.connect(str(state_file)).close()
    print(f"完成。输入: {db}")
    print(f"      输出目录: {out}")


def _run_stage(spec: StageManifest, cfg: dict) -> None:
    layout = layout_from_manifest(spec, cfg)
    data_paths.reload_layout(data_root=layout.root, repo_root=spec.repo_root)
    data_paths.ensure_data_dirs()
    layout = data_paths.get_layout()

    game = str(spec.options.get("game") or "workshop")
    db_path = require_input(spec.input_map, "grant_sqlite")
    result_dir = spec.output_root

    print(
        f"steam-mod-analysis — output={result_dir}, db={db_path}, game={game}",
        flush=True,
    )
    run_all_analyses(db_path, result_dir, game=game)


def main() -> None:
    run_stage_main(_run_stage)


if __name__ == "__main__":
    main()
