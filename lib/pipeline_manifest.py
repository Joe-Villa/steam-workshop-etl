"""Load per-stage pipeline manifests (input / cfg / output)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paradox_paths import find_repo_root
from stage_inputs import parse_input_block


_DEFAULT_STATE_BY_STEM: dict[str, str] = {
    "collect_simple_info": "name.sqlite",
    "collect_detail_fetch": "mod_fetch.sqlite",
    "build_grant_table": "name.sqlite",
    "run_analysis": "stage_state.sqlite",
}


@dataclass(frozen=True)
class StageManifest:
    """Resolved paths for one pipeline stage."""

    repo_root: Path
    manifest_path: Path
    input_map: dict[str, Path]
    cfg_paths: dict[str, Path]
    output_root: Path
    state_sqlite: str
    options: dict[str, object]


def repo_root_for_manifest(manifest_path: Path) -> Path:
    """Prefer ``pipeline/pipeline.json`` ``root``, else walk up to ``cfg/base.json``."""
    manifest_path = manifest_path.resolve()
    pipeline_json = manifest_path.parent / "pipeline.json"
    if pipeline_json.is_file():
        try:
            data = json.loads(pipeline_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(
                f"WARNING: ignoring invalid {pipeline_json}: {e}",
                file=sys.stderr,
            )
        else:
            if isinstance(data, dict):
                raw = data.get("root")
                if isinstance(raw, str) and raw.strip():
                    return Path(raw.strip()).expanduser().resolve()
    return find_repo_root(manifest_path)


def _resolve_path(repo_root: Path, raw: str, *, field: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        print(f'ERROR: manifest field "{field}" must be a non-empty path string.', file=sys.stderr)
        raise SystemExit(2)
    p = Path(raw.strip()).expanduser()
    if not p.is_absolute():
        p = repo_root / p
    return p.resolve()


def _parse_cfg_entries(repo_root: Path, raw: Any) -> dict[str, Path]:
    if raw is None:
        print('ERROR: manifest must contain "cfg".', file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(raw, list) or not raw:
        print('ERROR: manifest "cfg" must be a non-empty array.', file=sys.stderr)
        raise SystemExit(2)
    out: dict[str, Path] = {}
    for i, item in enumerate(raw):
        if isinstance(item, str):
            if "base" in out:
                print(f"ERROR: duplicate cfg base at index {i}.", file=sys.stderr)
                raise SystemExit(2)
            out["base"] = _resolve_path(repo_root, item, field=f"cfg[{i}]")
            continue
        if not isinstance(item, dict):
            print(f"ERROR: manifest cfg[{i}] must be a string or object.", file=sys.stderr)
            raise SystemExit(2)
        for key, path_raw in item.items():
            if not isinstance(key, str) or not key.strip():
                print(f"ERROR: invalid cfg key at index {i}.", file=sys.stderr)
                raise SystemExit(2)
            if key in out:
                print(f'ERROR: duplicate cfg key "{key}".', file=sys.stderr)
                raise SystemExit(2)
            out[key.strip()] = _resolve_path(
                repo_root, str(path_raw), field=f"cfg[{i}].{key}"
            )
    if "base" not in out:
        print('ERROR: manifest "cfg" must include a "base" entry.', file=sys.stderr)
        raise SystemExit(2)
    return out


def load_stage_manifest(manifest_path: Path) -> StageManifest:
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_file():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        raise SystemExit(2)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: Invalid {manifest_path}: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    if not isinstance(data, dict):
        print(f"ERROR: {manifest_path} must be a JSON object.", file=sys.stderr)
        raise SystemExit(2)

    repo_root = repo_root_for_manifest(manifest_path)
    cfg_paths = _parse_cfg_entries(repo_root, data.get("cfg"))
    input_map = parse_input_block(repo_root, data.get("input"))

    raw_out = data.get("output")
    if raw_out is None:
        print('ERROR: manifest must contain "output" (data root directory).', file=sys.stderr)
        raise SystemExit(2)
    output_root = _resolve_path(repo_root, str(raw_out), field="output")

    raw_state = data.get("state")
    if isinstance(raw_state, str) and raw_state.strip():
        state_sqlite = raw_state.strip()
    else:
        state_sqlite = _DEFAULT_STATE_BY_STEM.get(
            manifest_path.stem, "stage_state.sqlite"
        )

    raw_opts = data.get("options")
    options: dict[str, object] = raw_opts if isinstance(raw_opts, dict) else {}
    if "game" in data and isinstance(data["game"], str):
        options = {**options, "game": data["game"]}

    return StageManifest(
        repo_root=repo_root,
        manifest_path=manifest_path,
        input_map=input_map,
        cfg_paths=cfg_paths,
        output_root=output_root,
        state_sqlite=state_sqlite,
        options=options,
    )


def load_cfg_from_manifest(spec: StageManifest) -> dict:
    base_path = spec.cfg_paths["base"]
    try:
        with base_path.open(encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: Invalid {base_path}: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    if not isinstance(cfg, dict):
        print(f"ERROR: {base_path} must be a JSON object.", file=sys.stderr)
        raise SystemExit(2)
    return cfg


def write_manifest_output(manifest_path: Path, output: Path, *, repo_root: Path | None = None) -> Path:
    """Update manifest ``output`` (relative to repo root when possible)."""
    manifest_path = manifest_path.resolve()
    root = repo_root or repo_root_for_manifest(manifest_path)
    output = output.expanduser().resolve()
    try:
        rel = output.relative_to(root)
        stored = rel.as_posix()
    except ValueError:
        stored = output.as_posix()

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print(f"ERROR: {manifest_path} must be a JSON object.", file=sys.stderr)
        raise SystemExit(2)
    data["output"] = stored
    manifest_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    return manifest_path
