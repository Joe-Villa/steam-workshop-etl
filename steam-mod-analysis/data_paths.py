"""Steam 创意工坊分析：数据目录约定（由 repo ``cfg/base.json`` 决定）。"""

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
RESULT_DIR = _layout.analysis_report_dir
INFO_DIR = _layout.analysis_info_dir

DEFAULT_DB_NAME = CONCRETE_INFO_SQLITE
DEFAULT_XLSX_NAME = CONCRETE_INFO_XLSX
DEFAULT_RATIO_CSV_NAME = "mod_subscriber_exposure_ratio_with_name.csv"

DB_PATH = _layout.analysis_sqlite
XLSX_PATH = _layout.analysis_xlsx
RATIO_WITH_NAME_CSV_PATH = TABLE_DIR / DEFAULT_RATIO_CSV_NAME

AUTHOR_CLASSIFICATION_JSON = INFO_DIR / "author_classification_result.json"
AUTHOR_CLASSIFICATION_UNCERTAIN_JSON = INFO_DIR / "author_classification_uncertain.json"
AUTHOR_CLASSIFICATION_CHECKPOINT_JSON = INFO_DIR / "author_classification_checkpoint.json"
AUTHOR_MOD_TITLES_JSON = INFO_DIR / "author_mod_titles_map.json"

ID_COLLECTION_STATE_JSON = HTML_DIR / "id_collection_state.json"
MOD_HTML_FETCH_STATE_JSON = HTML_DIR / "mod_html_fetch_state.json"
MOD_INDEX_JSON = HTML_DIR / "mod_index.json"


def ensure_data_dirs() -> None:
    ensure_layout_dirs(_layout)
