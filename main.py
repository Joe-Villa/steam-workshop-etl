#!/usr/bin/env python3
"""
Paradox 工坊数据流水线总控。

- 路径由仓库根 cfg/base.json 决定（可设绝对路径 data-folder）
- pipeline_state.json 记录断点；各子包内部另有细粒度状态
- 全新启动要求数据目录为空，防止误覆盖已有爬取数据
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent
_LIB = _REPO / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from paradox_paths import (  # noqa: E402
    ensure_layout_dirs,
    load_base_json,
    load_layout,
    merge_write_base_config,
    parse_appid,
)
from subprocess_python import resolve_package_python  # noqa: E402
from pipeline import (  # noqa: E402
    ERR_DATA_DIR_NOT_EMPTY,
    ERR_DETAIL_FETCH_INCOMPLETE,
    ERR_SIMPLE_INFO_INCOMPLETE,
    ERR_STAGE_FAILED,
    PipelineError,
    PipelineState,
    Stage,
    STAGE_ORDER,
    assert_prerequisites,
    clear_intermediate_status_files,
    detail_fetch_complete,
    format_status_report,
    infer_stage,
    is_data_root_empty,
    load_pipeline_state,
    mark_stage_completed,
    mark_stage_failed,
    mark_stage_running,
    new_pipeline_state,
    resolve_run_stage,
    save_pipeline_state,
    simple_info_complete,
    analysis_complete,
)

_PKG_APPID = _REPO / "appid-steamworkshop-table"
_PKG_FETCH = _REPO / "resumable-batch-fetch"
_PKG_ANALYSIS = _REPO / "steam-mod-analysis"


def _run_subprocess(
    argv: list[str],
    *,
    cwd: Path,
    label: str,
) -> int:
    print(f"\n{'=' * 60}", flush=True)
    print(f"▶ {label}", flush=True)
    print(f"  cwd: {cwd}", flush=True)
    print(f"  cmd: {' '.join(argv)}", flush=True)
    print("=" * 60, flush=True)
    proc = subprocess.run(argv, cwd=str(cwd))
    return int(proc.returncode)


def run_simple_info(*, extra_argv: list[str]) -> int:
    argv = [sys.executable, "main.py", *extra_argv]
    return _run_subprocess(argv, cwd=_PKG_APPID, label="阶段 1/3：简略信息表 (appid-steamworkshop-table)")


def run_detail_fetch() -> int:
    try:
        py = resolve_package_python(
            _PKG_FETCH, required_imports=("aiohttp", "requests")
        )
    except RuntimeError as e:
        print(f"\n阶段 2/3 无法启动：{e}", file=sys.stderr, flush=True)
        return 2
    argv = [str(py), "src/main.py"]
    return _run_subprocess(argv, cwd=_PKG_FETCH, label="阶段 2/3：详情页爬取 (resumable-batch-fetch)")


def run_analysis(*, game: str) -> int:
    html = load_layout(_REPO).concrete_html_root
    db = load_layout(_REPO).analysis_sqlite
    xlsx = load_layout(_REPO).analysis_xlsx

    code = _run_subprocess(
        [
            sys.executable,
            "build_sqlite/build_all_tables.py",
            "--html-dir",
            str(html),
            "--db-path",
            str(db),
            "--output-file",
            str(xlsx),
        ],
        cwd=_PKG_ANALYSIS,
        label="阶段 3a/3：建库 (steam-mod-analysis/build_all_tables)",
    )
    if code != 0:
        return code

    return _run_subprocess(
        [
            sys.executable,
            "analysis_src/main.py",
            "--db-path",
            str(db),
            "--result-dir",
            str(load_layout(_REPO).analysis_report_dir),
            "--game",
            game,
        ],
        cwd=_PKG_ANALYSIS,
        label="阶段 3b/3：统计分析 (steam-mod-analysis/analysis_src)",
    )


def run_stage(stage: Stage, *, tls_argv: list[str], game: str) -> int:
    if stage == Stage.SIMPLE_INFO:
        return run_simple_info(extra_argv=tls_argv)
    if stage == Stage.DETAIL_FETCH:
        return run_detail_fetch()
    if stage == Stage.ANALYSIS:
        return run_analysis(game=game)
    return 0


def _stages_to_run(start: Stage, *, only: Stage | None) -> list[Stage]:
    if only is not None:
        return [only]
    if start == Stage.DONE:
        return []
    idx = STAGE_ORDER.index(start)
    return [s for s in STAGE_ORDER[idx:] if s != Stage.DONE]


def _guard_new_pipeline_without_fresh(layout, fresh: bool, force_fresh: bool) -> None:
    if fresh or force_fresh:
        return
    if load_pipeline_state(layout) is not None:
        return
    if infer_stage(layout) != Stage.SIMPLE_INFO:
        return
    if not is_data_root_empty(layout):
        raise PipelineError(
            ERR_DATA_DIR_NOT_EMPTY,
            detail=(
                f"{layout.root} 中已有数据，但未找到 pipeline_state.json。"
                " 若确为断点续跑，请先运行 python main.py --status 查看推断阶段；"
                " 不要用 --fresh。"
            ),
        )


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Paradox 工坊流水线总控（断点续跑 / 数据保护 / 分阶段执行）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py --status
  python main.py
  python main.py 529340 --data-folder /path/to/storage/529340
  python main.py --fresh
  python main.py --only detail_fetch
  python main.py --from analysis
        """,
    )
    ap.add_argument(
        "appid",
        nargs="?",
        type=int,
        help="Steam APPID；写入 cfg/base.json 后执行流水线",
    )
    ap.add_argument(
        "--data-folder",
        type=Path,
        help="数据根目录（可绝对路径）；写入 cfg/base.json 的 data-folder",
    )
    ap.add_argument(
        "--status",
        action="store_true",
        help="仅打印流水线与产物检查，不执行",
    )
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="全新流水线：要求数据目录为空，并清除中间状态文件后从 simple_info 开始",
    )
    ap.add_argument(
        "--force-fresh",
        action="store_true",
        help="与 --fresh 联用：数据目录非空也允许启动（不删除已有 HTML，只清状态文件）",
    )
    ap.add_argument(
        "--from",
        dest="from_stage",
        choices=[s.value for s in STAGE_ORDER if s != Stage.DONE],
        metavar="STAGE",
        help="从指定阶段开始（simple_info | detail_fetch | analysis）",
    )
    ap.add_argument(
        "--only",
        choices=[s.value for s in STAGE_ORDER if s != Stage.DONE],
        metavar="STAGE",
        help="只运行单个阶段",
    )
    ap.add_argument(
        "--game",
        choices=("workshop", "civ6", "vic3"),
        default="workshop",
        help="分析阶段输出命名（传给 steam-mod-analysis）",
    )
    ap.add_argument(
        "--no-tls-verify",
        action="store_true",
        help="阶段 1 访问 Steam 时跳过 TLS 证书校验（代理场景）",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只显示将执行的阶段，不调用子包",
    )
    ap.add_argument(
        "--doctor",
        action="store_true",
        help="运行 test/smoke.py 冒烟测试后退出（不执行流水线）",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_base_json(_REPO)

    if args.appid is not None:
        if args.appid <= 0:
            print("ERROR: APPID 必须为正整数", file=sys.stderr)
            raise SystemExit(2)
        merge_write_base_config(
            args.appid,
            data_folder=args.data_folder,
            repo_root=_REPO,
        )
        cfg = load_base_json(_REPO)
    elif args.data_folder is not None:
        appid = parse_appid(cfg)
        merge_write_base_config(appid, data_folder=args.data_folder, repo_root=_REPO)
        cfg = load_base_json(_REPO)

    layout = load_layout(_REPO, cfg=cfg)
    ensure_layout_dirs(layout)

    print(f"配置: {_REPO / 'cfg' / 'base.json'}", flush=True)
    print(f"数据目录: {layout.root}", flush=True)
    print(f"APPID: {layout.appid}", flush=True)

    state = load_pipeline_state(layout)

    if args.status:
        print(format_status_report(layout, state))
        raise SystemExit(0)

    if args.doctor:
        smoke = _REPO / "test" / "smoke.py"
        code = subprocess.run([sys.executable, str(smoke)], cwd=str(_REPO)).returncode
        raise SystemExit(int(code))

    from_stage = Stage(args.from_stage) if args.from_stage else None
    only = Stage(args.only) if args.only else None

    if args.fresh and not args.force_fresh and not is_data_root_empty(layout):
        raise PipelineError(ERR_DATA_DIR_NOT_EMPTY, detail=str(layout.root))

    if args.fresh or args.force_fresh:
        removed = clear_intermediate_status_files(
            layout, _REPO, include_fetch_db=args.fresh
        )
        if removed:
            print("已清除中间状态文件:", flush=True)
            for p in removed:
                print(f"  - {p}", flush=True)
        state = None

    if not args.dry_run:
        _guard_new_pipeline_without_fresh(layout, args.fresh, args.force_fresh)

    start = resolve_run_stage(
        layout,
        state=state,
        from_stage=from_stage,
        fresh=args.fresh,
        force_fresh=args.force_fresh,
    )

    if start == Stage.DONE and only is None:
        print("流水线已完成（analysis 产物齐全）。", flush=True)
        print(format_status_report(layout, state))
        raise SystemExit(0)

    stages = _stages_to_run(start, only=only)
    if not stages:
        print("没有需要执行的阶段。", flush=True)
        raise SystemExit(0)

    if args.dry_run:
        print("将执行阶段:", ", ".join(s.value for s in stages))
        raise SystemExit(0)

    if state is None:
        state = new_pipeline_state(layout, stages[0])

    tls_argv: list[str] = []
    if args.no_tls_verify:
        tls_argv.append("--no-tls-verify")

    for stage in stages:
        assert_prerequisites(layout, stage)
        mark_stage_running(state, stage)
        save_pipeline_state(layout, state)

        code = run_stage(stage, tls_argv=tls_argv, game=args.game)
        if code != 0:
            mark_stage_failed(
                state,
                stage,
                message=f"子进程退出码 {code}",
                exit_code=code,
            )
            save_pipeline_state(layout, state)
            raise PipelineError(
                ERR_STAGE_FAILED,
                detail=f"阶段 {stage.value} 退出码 {code}",
            )

        if stage == Stage.SIMPLE_INFO and not simple_info_complete(layout, strict_browse=False):
            mark_stage_failed(
                state,
                stage,
                message="产物检查未通过",
                exit_code=0,
            )
            save_pipeline_state(layout, state)
            raise PipelineError(ERR_SIMPLE_INFO_INCOMPLETE)

        if stage == Stage.DETAIL_FETCH and not detail_fetch_complete(layout):
            mark_stage_failed(
                state,
                stage,
                message="mod_fetch 仍有未完成行",
                exit_code=0,
            )
            save_pipeline_state(layout, state)
            raise PipelineError(ERR_DETAIL_FETCH_INCOMPLETE)

        mark_stage_completed(state, stage)
        save_pipeline_state(layout, state)
        print(f"\n✓ 阶段完成: {stage.value}", flush=True)

    if analysis_complete(layout):
        state.current_stage = Stage.DONE.value
        save_pipeline_state(layout, state)

    print("\n" + format_status_report(layout, state))
    print("\n流水线本轮执行结束。", flush=True)


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        print(f"\n{e.format_message()}\n", file=sys.stderr)
        raise SystemExit(e.info.exit_code) from e
    except KeyboardInterrupt:
        print("\n已中断。重新运行 python main.py 可从断点继续。", file=sys.stderr)
        raise SystemExit(130) from None
