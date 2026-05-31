"""steam-grant-table：从 HTML 建库路径（由 repo layout + manifest output 决定）。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = PROJECT_ROOT.parent
_LIB = _REPO_ROOT / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from paradox_paths import (  # noqa: E402
    CONCRETE_INFO_SQLITE,
    CONCRETE_INFO_XLSX,
    ensure_layout_dirs,
    load_layout,
)

_layout = load_layout(_REPO_ROOT)
DATA_DIR = _layout.root
HTML_DIR = _layout.concrete_html_root
BROWSE_SQLITE_PATH = _layout.simple_sqlite
CURRENT_SITUATION_JSON = _layout.current_situation_json
TABLE_DIR = _layout.concrete_info
INFO_DIR = _layout.analysis_info_dir

DEFAULT_DB_NAME = CONCRETE_INFO_SQLITE
DEFAULT_XLSX_NAME = CONCRETE_INFO_XLSX
MODS_TABLE = "aaa_mods"
DEFAULT_RATIO_CSV_NAME = "mod_subscriber_exposure_ratio_with_name.csv"

DB_PATH = _layout.analysis_sqlite
XLSX_PATH = _layout.analysis_xlsx
RATIO_WITH_NAME_CSV_PATH = TABLE_DIR / DEFAULT_RATIO_CSV_NAME

AUTHOR_CLASSIFICATION_JSON = INFO_DIR / "author_classification_result.json"
AUTHOR_CLASSIFICATION_UNCERTAIN_JSON = INFO_DIR / "author_classification_uncertain.json"
AUTHOR_CLASSIFICATION_CHECKPOINT_JSON = INFO_DIR / "author_classification_checkpoint.json"
AUTHOR_MOD_TITLES_JSON = INFO_DIR / "author_mod_titles_map.json"


def ensure_data_dirs() -> None:
    ensure_layout_dirs(_layout)


def get_layout():
    return _layout


def reload_layout(*, data_root=None, repo_root=None) -> None:
    """Refresh module-level paths after manifest resolution."""
    global _layout, DATA_DIR, HTML_DIR, BROWSE_SQLITE_PATH, CURRENT_SITUATION_JSON
    global TABLE_DIR, INFO_DIR, DB_PATH, XLSX_PATH, RATIO_WITH_NAME_CSV_PATH
    global AUTHOR_CLASSIFICATION_JSON, AUTHOR_CLASSIFICATION_UNCERTAIN_JSON
    global AUTHOR_CLASSIFICATION_CHECKPOINT_JSON, AUTHOR_MOD_TITLES_JSON

    root = repo_root or _REPO_ROOT
    _layout = load_layout(root, data_root=data_root)
    DATA_DIR = _layout.root
    HTML_DIR = _layout.concrete_html_root
    BROWSE_SQLITE_PATH = _layout.simple_sqlite
    CURRENT_SITUATION_JSON = _layout.current_situation_json
    TABLE_DIR = _layout.concrete_info
    INFO_DIR = _layout.analysis_info_dir
    DB_PATH = _layout.analysis_sqlite
    XLSX_PATH = _layout.analysis_xlsx
    RATIO_WITH_NAME_CSV_PATH = TABLE_DIR / DEFAULT_RATIO_CSV_NAME
    AUTHOR_CLASSIFICATION_JSON = INFO_DIR / "author_classification_result.json"
    AUTHOR_CLASSIFICATION_UNCERTAIN_JSON = INFO_DIR / "author_classification_uncertain.json"
    AUTHOR_CLASSIFICATION_CHECKPOINT_JSON = INFO_DIR / "author_classification_checkpoint.json"
    AUTHOR_MOD_TITLES_JSON = INFO_DIR / "author_mod_titles_map.json"
