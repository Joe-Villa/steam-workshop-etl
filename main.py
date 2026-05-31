#!/usr/bin/env python3
"""
Paradox 工坊流水线总控。

用法:
  python3 main.py pipeline/pipeline.json
  python3 main.py pipeline/pipeline.json --status
  python3 main.py pipeline/pipeline.json --dry-run
  python3 main.py pipeline/pipeline.json --from 3
  python3 main.py pipeline/pipeline.json --only 2
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

from paradox_paths import ensure_layout_dirs, load_base_json  # noqa: E402
from pipeline_manifest import load_stage_manifest  # noqa: E402
from pipeline_definition import PipelineDefinition, PipelineStep, load_pipeline_definition  # noqa: E402
from stage_layout import layout_from_manifest  # noqa: E402
from subprocess_python import resolve_package_python  # noqa: E402
from pipeline import (  # noqa: E402
    ERR_DATA_DIR_NOT_EMPTY,
    ERR_DETAIL_FETCH_INCOMPLETE,
    ERR_MISSING_PREREQUISITE,
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
    grant_table_complete,
    infer_stage,
    is_data_root_empty,
    load_pipeline_state,
    mark_stage_completed,
    mark_stage_failed,
    mark_stage_running,
    mod_analysis_complete,
    new_pipeline_state,
    save_pipeline_state,
    simple_info_complete,
    stage_for_step_id,
    step_id_for_stage,
)


def _run_subprocess(argv: list[str], *, cwd: Path, label: str) -> int:
    print(f"\n{'=' * 60}", flush=True)
    print(f"▶ {label}", flush=True)
    print(f"  cwd: {cwd}", flush=True)
    print(f"  cmd: {' '.join(argv)}", flush=True)
    print("=" * 60, flush=True)
    return int(subprocess.run(argv, cwd=str(cwd)).returncode)


def _layout_for_definition(defn: PipelineDefinition):
    if not defn.steps:
        raise PipelineError(ERR_STAGE_FAILED, detail="pipeline.json process 为空")
    spec = load_stage_manifest(defn.steps[0].paths_file)
    cfg = load_base_json(defn.repo_root)
    layout = layout_from_manifest(spec, cfg)
    return layout


def _run_step(step: PipelineStep, *, defn: PipelineDefinition) -> int:
    paths_arg = step.paths_file
    if not step.execute.is_file():
        print(f"ERROR: execute not found: {step.execute}", file=sys.stderr)
        return 2
    cwd = step.execute.parent
    if step.execute.parent.name == "resumable-batch-fetch":
        try:
            py = resolve_package_python(
                cwd, required_imports=("aiohttp", "requests")
            )
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        argv = [str(py), str(step.execute.name), str(paths_arg)]
    else:
        argv = [sys.executable, str(step.execute.name), str(paths_arg)]
    return _run_subprocess(
        argv,
        cwd=cwd,
        label=f"步骤 {step.step_id}: {step.execute.parent.name}",
    )


def _check_step_after(stage: Stage, layout) -> None:
    if stage == Stage.SIMPLE_INFO and not simple_info_complete(layout, strict_browse=False):
        raise PipelineError(ERR_SIMPLE_INFO_INCOMPLETE)
    if stage == Stage.DETAIL_FETCH and not detail_fetch_complete(layout):
        raise PipelineError(ERR_DETAIL_FETCH_INCOMPLETE)
    if stage == Stage.GRANT_TABLE and not grant_table_complete(layout):
        raise PipelineError(
            ERR_STAGE_FAILED,
            detail="grant_table 产物检查未通过（concrete_info/name.sqlite）",
        )
    if stage == Stage.MOD_ANALYSIS and not mod_analysis_complete(layout):
        raise PipelineError(
            ERR_STAGE_FAILED,
            detail="mod_analysis 产物检查未通过（report/）",
        )


def _steps_from(
    defn: PipelineDefinition,
    *,
    start_step_id: str | None,
    only_step_id: str | None,
) -> list[PipelineStep]:
    if only_step_id is not None:
        for s in defn.steps:
            if s.step_id == only_step_id:
                return [s]
        raise PipelineError(ERR_STAGE_FAILED, detail=f"未知步骤 id: {only_step_id}")
    if start_step_id is None:
        return list(defn.steps)
    out: list[PipelineStep] = []
    found = False
    for s in defn.steps:
        if s.step_id == start_step_id:
            found = True
        if found:
            out.append(s)
    if not found:
        raise PipelineError(ERR_STAGE_FAILED, detail=f"未知起始步骤 id: {start_step_id}")
    return out


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Paradox 工坊流水线（由 pipeline.json 驱动）")
    ap.add_argument(
        "pipeline",
        type=Path,
        help="流水线定义，如 pipeline/pipeline.json",
    )
    ap.add_argument("--status", action="store_true", help="仅打印状态")
    ap.add_argument("--dry-run", action="store_true", help="只列出将执行的步骤")
    ap.add_argument("--fresh", action="store_true", help="要求数据根为空后从步骤 1 开始")
    ap.add_argument("--force-fresh", action="store_true", help="与 --fresh 联用：允许非空数据根")
    ap.add_argument("--from", dest="from_step", metavar="ID", help="从步骤 id 开始（1–4）")
    ap.add_argument("--only", dest="only_step", metavar="ID", help="只运行指定步骤 id")
    ap.add_argument(
        "--doctor",
        action="store_true",
        help="运行 test/smoke.py 后退出",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    defn = load_pipeline_definition(args.pipeline)
    layout = _layout_for_definition(defn)
    ensure_layout_dirs(layout)

    print(f"仓库根: {defn.repo_root}", flush=True)
    print(f"状态文件: {defn.state_path}", flush=True)
    print(f"数据根: {layout.root}", flush=True)
    print(f"APPID: {layout.appid}", flush=True)

    state = load_pipeline_state(layout, state_path=defn.state_path)

    if args.status:
        print(format_status_report(layout, state))
        raise SystemExit(0)

    if args.doctor:
        smoke = _REPO / "test" / "smoke.py"
        raise SystemExit(subprocess.run([sys.executable, str(smoke)], cwd=str(_REPO)).returncode)

    if args.fresh and not args.force_fresh and not is_data_root_empty(layout):
        raise PipelineError(ERR_DATA_DIR_NOT_EMPTY, detail=str(layout.root))

    if args.fresh or args.force_fresh:
        removed = clear_intermediate_status_files(
            layout, defn.repo_root, include_fetch_db=args.fresh
        )
        if defn.state_path.is_file():
            defn.state_path.unlink()
            removed.append(str(defn.state_path))
        if removed:
            print("已清除状态文件:", flush=True)
            for p in removed:
                print(f"  - {p}", flush=True)
        state = None

    start_step = args.from_step
    if start_step is None and state is None and not args.fresh:
        start_step = step_id_for_stage(infer_stage(layout)) or "1"
    elif start_step is None and state is not None:
        start_step = step_id_for_stage(Stage(state.current_stage)) or state.current_stage

    steps = _steps_from(defn, start_step_id=start_step, only_step_id=args.only_step)

    if args.dry_run:
        print("将执行步骤:")
        for s in steps:
            print(f"  {s.step_id}: {s.execute.name} {s.paths_file}")
        raise SystemExit(0)

    if state is None and steps:
        first = stage_for_step_id(steps[0].step_id) or Stage.SIMPLE_INFO
        state = new_pipeline_state(layout, first)

    for step in steps:
        stage = stage_for_step_id(step.step_id)
        if stage is None:
            continue
        assert_prerequisites(layout, stage)
        mark_stage_running(state, stage)
        save_pipeline_state(layout, state, state_path=defn.state_path)

        code = _run_step(step, defn=defn)
        if code != 0:
            mark_stage_failed(
                state,
                stage,
                message=f"退出码 {code}",
                exit_code=code,
            )
            save_pipeline_state(layout, state, state_path=defn.state_path)
            raise PipelineError(ERR_STAGE_FAILED, detail=f"步骤 {step.step_id} 退出码 {code}")

        _check_step_after(stage, layout)
        mark_stage_completed(state, stage)
        save_pipeline_state(layout, state, state_path=defn.state_path)
        print(f"\n✓ 步骤 {step.step_id} 完成 ({stage.value})", flush=True)

    if mod_analysis_complete(layout):
        state.current_stage = Stage.DONE.value
        save_pipeline_state(layout, state, state_path=defn.state_path)

    print("\n" + format_status_report(layout, state))
    print("\n流水线本轮执行结束。", flush=True)


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        print(f"\n{e.format_message()}\n", file=sys.stderr)
        raise SystemExit(e.info.exit_code) from e
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        raise SystemExit(130) from None
