"""
从 data/table 的 SQLite 运行全部分析，输出到 data/result。

用法（在 vic3analysis 目录下）：
  python analysis_src/main.py
  python analysis_src/main.py --game civ6   # 可选：输出文件名带游戏前缀
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _path in (_PROJECT_ROOT, _SCRIPT_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from data_paths import DB_PATH, RESULT_DIR, ensure_data_dirs  # noqa: E402
from game_profile import figures_subdir, get_profile, output_basename  # noqa: E402


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
    include_private_like: bool = False,
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
                    *(
                        ["--include-private-like"]
                        if include_private_like
                        else []
                    ),
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
    print(f"完成。输入: {db}")
    print(f"      输出目录: {out}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="读取 data/table 中的 SQLite，将全部分析结果写入 data/result。"
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help="SQLite 路径（默认 data/table/mods.sqlite3）",
    )
    parser.add_argument(
        "--result-dir",
        type=Path,
        default=RESULT_DIR,
        help="分析输出目录（默认 data/result）",
    )
    parser.add_argument(
        "--game",
        choices=("workshop", "civ6", "vic3"),
        default="workshop",
        help="workshop=通用文件名与标题；civ6/vic3=输出带游戏前缀",
    )
    parser.add_argument(
        "--include-private-like",
        action="store_true",
        help="订阅不平等分析中包含自用 mod（默认排除）",
    )
    return parser.parse_args()


def main() -> None:
    ensure_data_dirs()
    args = parse_args()
    run_all_analyses(
        args.db_path,
        args.result_dir,
        game=args.game,
        include_private_like=args.include_private_like,
    )


if __name__ == "__main__":
    main()
