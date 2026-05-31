"""Parse paths ``input`` (object or array) with order-independent role assignment."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Recognized input roles (array items are classified into these).
INPUT_ROLES = frozenset(
    {
        "urls",
        "detail_html",
        "browse_sqlite",
        "grant_sqlite",
        "workshop_status",
    }
)


def classify_input_path(path: Path) -> str:
    """Map a path to a role; does not depend on array order."""
    p = path.resolve()
    name = p.name.lower()
    parts_lower = {part.lower() for part in p.parts}

    if name == "current_situation.json":
        return "workshop_status"
    if name == "urls.json" or (p.suffix == ".json" and "url" in name):
        return "urls"
    if p.suffix == ".sqlite":
        if "simple_info" in parts_lower:
            return "browse_sqlite"
        if "concrete_info" in parts_lower:
            return "grant_sqlite"
        print(
            f"ERROR: cannot classify SQLite input (expected under simple_info/ or concrete_info/): {p}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if p.is_dir():
        if name == "html" or "concrete_html" in parts_lower:
            return "detail_html"
    print(f"ERROR: cannot classify input path: {p}", file=sys.stderr)
    raise SystemExit(2)


def _resolve_path(repo_root: Path, raw: str, *, field: str) -> Path:
    if not raw.strip():
        print(f'ERROR: manifest field "{field}" must be a non-empty path string.', file=sys.stderr)
        raise SystemExit(2)
    p = Path(raw.strip()).expanduser()
    if not p.is_absolute():
        p = repo_root / p
    return p.resolve()


def parse_input_block(repo_root: Path, raw: Any) -> dict[str, Path]:
    """
    ``input`` may be:
    - object: ``{"browse_sqlite": "...", "detail_html": "..."}`` (order-free)
    - array: paths classified by type/location (order-free)
    - null / omitted: empty
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        out: dict[str, Path] = {}
        for key, val in raw.items():
            if not isinstance(key, str) or not key.strip():
                print("ERROR: input object keys must be non-empty strings.", file=sys.stderr)
                raise SystemExit(2)
            if val is None:
                continue
            if not isinstance(val, str):
                print(f"ERROR: input.{key} must be a path string.", file=sys.stderr)
                raise SystemExit(2)
            role = key.strip()
            if role in out:
                print(f'ERROR: duplicate input role "{role}".', file=sys.stderr)
                raise SystemExit(2)
            out[role] = _resolve_path(repo_root, val, field=f"input.{role}")
        return out

    if isinstance(raw, list):
        out: dict[str, Path] = {}
        for i, item in enumerate(raw):
            if item is None:
                continue
            if not isinstance(item, str):
                print(f"ERROR: manifest input[{i}] must be null or a path string.", file=sys.stderr)
                raise SystemExit(2)
            path = _resolve_path(repo_root, item, field=f"input[{i}]")
            role = classify_input_path(path)
            if role in out:
                print(
                    f"ERROR: multiple input paths map to role {role!r}:\n"
                    f"  {out[role]}\n"
                    f"  {path}",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            out[role] = path
        return out

    print('ERROR: manifest "input" must be null, an object, or an array.', file=sys.stderr)
    raise SystemExit(2)


def require_input(input_map: dict[str, Path], role: str) -> Path:
    path = input_map.get(role)
    if path is None:
        print(
            f'ERROR: paths JSON must define input "{role}" (object key or inferrable path).',
            file=sys.stderr,
        )
        raise SystemExit(2)
    return path
