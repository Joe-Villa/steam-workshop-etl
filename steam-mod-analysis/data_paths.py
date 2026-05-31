"""steam-mod-analysis：分析报告路径（由 repo layout 决定）。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = PROJECT_ROOT.parent
_LIB = _REPO_ROOT / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from paradox_paths import ensure_layout_dirs, load_layout  # noqa: E402

_layout = load_layout(_REPO_ROOT)
DATA_DIR = _layout.root
RESULT_DIR = _layout.report_dir
DB_PATH = _layout.analysis_sqlite
MODS_TABLE = "aaa_mods"
INFO_DIR = _layout.analysis_info_dir
AUTHOR_CLASSIFICATION_JSON = INFO_DIR / "author_classification_result.json"


def ensure_data_dirs() -> None:
    ensure_layout_dirs(_layout)


def reload_layout(*, data_root=None, repo_root=None) -> None:
    global _layout, DATA_DIR, RESULT_DIR, DB_PATH, INFO_DIR, AUTHOR_CLASSIFICATION_JSON
    root = repo_root or _REPO_ROOT
    _layout = load_layout(root, data_root=data_root)
    DATA_DIR = _layout.root
    RESULT_DIR = _layout.report_dir
    DB_PATH = _layout.analysis_sqlite
    INFO_DIR = _layout.analysis_info_dir
    AUTHOR_CLASSIFICATION_JSON = INFO_DIR / "author_classification_result.json"


def get_layout():
    return _layout
