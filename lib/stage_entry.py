"""Shared ``main.py`` entry: single positional paths JSON argument."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from pipeline_manifest import StageManifest, load_cfg_from_manifest, load_stage_manifest
from stage_validate import validate_stage_paths


def parse_paths_argv(argv: list[str] | None = None) -> Path:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 1:
        print(
            "用法: python3 main.py <paths.json>\n"
            "  paths.json 定义 input / cfg / output / state",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return Path(args[0])


def bootstrap_stage(
    paths_file: Path,
    *,
    argv: list[str] | None = None,
) -> tuple[StageManifest, dict]:
    _ = argv
    spec = load_stage_manifest(paths_file)
    validate_stage_paths(spec)
    cfg = load_cfg_from_manifest(spec)
    return spec, cfg


def run_stage_main(
    run_fn: Callable[[StageManifest, dict], None],
    *,
    argv: list[str] | None = None,
) -> None:
    paths_file = parse_paths_argv(argv)
    spec, cfg = bootstrap_stage(paths_file, argv=argv)
    run_fn(spec, cfg)
