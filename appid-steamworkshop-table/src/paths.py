"""appid-steamworkshop-table I/O paths (from stage manifest output root)."""

from __future__ import annotations

import sys
from pathlib import Path

_MOD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _MOD_ROOT.parent
_LIB = _REPO_ROOT / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from paradox_paths import ParadoxDataLayout, load_layout  # noqa: E402

_active_layout: ParadoxDataLayout | None = None


def set_active_layout(layout: ParadoxDataLayout) -> None:
    global _active_layout
    _active_layout = layout


def get_layout() -> ParadoxDataLayout:
    if _active_layout is not None:
        return _active_layout
    return load_layout(_REPO_ROOT)


def project_root_for_logs() -> Path:
    """Relative paths in logs use the game data root."""
    return get_layout().root
