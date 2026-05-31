"""Validate stage ``paths`` JSON before running a package ``main.py``."""

from __future__ import annotations

import sys
from pathlib import Path

from pipeline_manifest import StageManifest


class StageOutputNotCleanError(ValueError):
    """Output directory has content but no SQLite state file."""


def _unexpected_entries(output_dir: Path, *, state_path: Path) -> list[Path]:
    unexpected: list[Path] = []
    for entry in output_dir.iterdir():
        if entry.resolve() == state_path.resolve():
            continue
        if entry.name == "html" and entry.is_dir() and not any(entry.iterdir()):
            continue
        unexpected.append(entry)
    return unexpected


def validate_stage_output(output_dir: Path, state_sqlite: str) -> None:
    """
    Output must be empty, or contain the stage SQLite state (resume).

    Reject: non-empty output without the state file.
    """
    if not state_sqlite or not str(state_sqlite).strip():
        print('ERROR: paths JSON must define non-empty "state" (sqlite filename).', file=sys.stderr)
        raise SystemExit(2)

    state_path = (output_dir / state_sqlite.strip()).resolve()
    if not output_dir.exists():
        return
    if state_path.is_file():
        return
    if not _unexpected_entries(output_dir, state_path=state_path):
        return
    raise StageOutputNotCleanError(
        f"输出目录不干净: {output_dir}\n"
        f"目录里已有文件，但未找到状态库 {state_sqlite}。\n"
        f"请换空目录、删除无关文件，或保留已有 SQLite 以断点续跑。"
    )


def validate_stage_inputs(spec: StageManifest) -> None:
    for role, inp in spec.input_map.items():
        if not inp.exists():
            print(f"ERROR: input.{role} not found: {inp}", file=sys.stderr)
            raise SystemExit(2)


def validate_stage_paths(spec: StageManifest) -> None:
    """Run all pre-flight checks for a stage paths file."""
    validate_stage_inputs(spec)
    try:
        validate_stage_output(spec.output_root, spec.state_sqlite)
    except StageOutputNotCleanError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2) from e
