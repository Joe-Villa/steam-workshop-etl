"""Resolve which Python executable to use for a package subprocess."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def resolve_package_python(
    package_root: Path,
    *,
    required_imports: tuple[str, ...] = (),
) -> Path:
    """
    Prefer ``package_root/.venv/bin/python3`` when it satisfies ``required_imports``;
    otherwise fall back to ``sys.executable``.
    """
    candidates: list[Path] = []
    venv_py = package_root / ".venv" / "bin" / "python3"
    if venv_py.is_file():
        candidates.append(venv_py)
    candidates.append(Path(sys.executable))

    last_missing: str | None = None
    for py in candidates:
        missing = _first_missing_import(py, required_imports)
        if missing is None:
            return py
        last_missing = missing

    req = ", ".join(required_imports) if required_imports else "(none)"
    raise RuntimeError(
        f"缺少 Python 依赖（{last_missing or req}）。\n"
        f"在 {package_root} 下执行：\n"
        f"  python3 -m venv .venv\n"
        f"  .venv/bin/pip install -r requirements.txt\n"
        f"或：python3 -m pip install -r {package_root / 'requirements.txt'}"
    )


def _first_missing_import(py: Path, names: tuple[str, ...]) -> str | None:
    for mod in names:
        proc = subprocess.run(
            [str(py), "-c", f"import {mod}"],
            capture_output=True,
        )
        if proc.returncode != 0:
            return mod
    return None
