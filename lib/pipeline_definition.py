"""Load ``pipeline/pipeline.json`` (orchestrator definition)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineStep:
    step_id: str
    paths_file: Path
    execute: Path


@dataclass(frozen=True)
class PipelineDefinition:
    definition_path: Path
    repo_root: Path
    state_path: Path
    steps: tuple[PipelineStep, ...]


def _resolve(repo_root: Path, raw: str, *, field: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        print(f'ERROR: pipeline "{field}" must be a non-empty path.', file=sys.stderr)
        raise SystemExit(2)
    p = Path(raw.strip()).expanduser()
    if not p.is_absolute():
        p = repo_root / p
    return p.resolve()


def load_pipeline_definition(definition_path: Path) -> PipelineDefinition:
    definition_path = definition_path.resolve()
    if not definition_path.is_file():
        print(f"ERROR: pipeline definition not found: {definition_path}", file=sys.stderr)
        raise SystemExit(2)
    try:
        data = json.loads(definition_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: invalid {definition_path}: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    if not isinstance(data, dict):
        print(f"ERROR: {definition_path} must be a JSON object.", file=sys.stderr)
        raise SystemExit(2)

    raw_root = data.get("root")
    if isinstance(raw_root, str) and raw_root.strip():
        repo_root = Path(raw_root.strip()).expanduser()
        if not repo_root.is_absolute():
            repo_root = (definition_path.parent / repo_root).resolve()
        else:
            repo_root = repo_root.resolve()
    elif definition_path.parent.name == "pipeline":
        repo_root = definition_path.parent.parent.resolve()
    else:
        repo_root = definition_path.parent.resolve()

    raw_state = data.get("state")
    if not isinstance(raw_state, str) or not raw_state.strip():
        print('ERROR: pipeline.json must contain "state" (pipeline_state.json path).', file=sys.stderr)
        raise SystemExit(2)
    state_path = _resolve(repo_root, raw_state, field="state")

    process = data.get("process")
    if not isinstance(process, dict) or not process:
        print('ERROR: pipeline.json must contain non-empty "process".', file=sys.stderr)
        raise SystemExit(2)

    steps: list[PipelineStep] = []
    for key in sorted(process.keys(), key=lambda k: (len(str(k)), str(k))):
        entry = process[key]
        if not isinstance(entry, dict):
            print(f"ERROR: process[{key!r}] must be an object.", file=sys.stderr)
            raise SystemExit(2)
        raw_paths = entry.get("paths")
        raw_exec = entry.get("execute")
        if not isinstance(raw_paths, str) or not raw_paths.strip():
            print(f"ERROR: process[{key!r}].paths missing.", file=sys.stderr)
            raise SystemExit(2)
        if not isinstance(raw_exec, str) or not raw_exec.strip():
            print(f"ERROR: process[{key!r}].execute missing.", file=sys.stderr)
            raise SystemExit(2)
        steps.append(
            PipelineStep(
                step_id=str(key),
                paths_file=_resolve(repo_root, raw_paths, field=f"process[{key}].paths"),
                execute=_resolve(repo_root, raw_exec, field=f"process[{key}].execute"),
            )
        )

    return PipelineDefinition(
        definition_path=definition_path,
        repo_root=repo_root,
        state_path=state_path,
        steps=tuple(steps),
    )
