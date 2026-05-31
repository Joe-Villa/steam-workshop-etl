"""Central game data paths from repo-root ``cfg/base.json``."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

_BASE_JSON_NAME = "base.json"
_CFG_DIR = "cfg"
_LIB_DIR = "lib"

# simple_info (appid-steamworkshop-table)
SIMPLE_CURRENT_SITUATION = "current_situation.json"
SIMPLE_BROWSE_URLS = "browse_urls.json"
SIMPLE_DETAIL_URLS = "urls.json"
SIMPLE_SQLITE = "name.sqlite"
SIMPLE_XLSX = "name.xlsx"
SIMPLE_HTML_DIR = "html"
SIMPLE_BROWSE_GAPS = "browse_html_gaps.json"

# concrete_html (resumable-batch-fetch)
CONCRETE_HTML_DIR = "html"
CONCRETE_FETCH_SQLITE = "mod_fetch.sqlite"
CONCRETE_FETCH_LOG = "output.log"

# concrete_info (steam-grant-table 建库产物)
CONCRETE_INFO_SQLITE = "name.sqlite"
CONCRETE_INFO_XLSX = "name.xlsx"
CONCRETE_INFO_META = "info"

# report（steam-mod-analysis 分析报告，与 simple_info 等平级）
DATA_REPORT_DIR = "report"


def find_repo_root(start: Path | None = None) -> Path:
    """Walk parents until ``cfg/base.json`` exists."""
    here = (start or Path.cwd()).resolve()
    if here.is_file():
        here = here.parent
    for parent in [here, *here.parents]:
        if (parent / _CFG_DIR / _BASE_JSON_NAME).is_file():
            return parent
    raise FileNotFoundError(
        f"Cannot find repo root (missing {_CFG_DIR}/{_BASE_JSON_NAME}); "
        f"started from {here}"
    )


def base_json_path(repo_root: Path | None = None) -> Path:
    root = repo_root or find_repo_root()
    return root / _CFG_DIR / _BASE_JSON_NAME


def load_base_json(repo_root: Path | None = None) -> dict:
    path = base_json_path(repo_root)
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: Invalid {path}: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    if not isinstance(data, dict):
        print(f"ERROR: {path} must be a JSON object.", file=sys.stderr)
        raise SystemExit(2)
    return data


def parse_appid(cfg: dict) -> int:
    raw = cfg.get("target-game-id", cfg.get("APPID"))
    if raw is None:
        print(
            'ERROR: cfg/base.json must contain "target-game-id" (or legacy "APPID").',
            file=sys.stderr,
        )
        raise SystemExit(2)
    if isinstance(raw, bool) or not isinstance(raw, int):
        if isinstance(raw, str) and raw.isdigit():
            raw = int(raw)
        else:
            print("ERROR: target-game-id must be a positive integer.", file=sys.stderr)
            raise SystemExit(2)
    if raw <= 0:
        print("ERROR: target-game-id must be a positive integer.", file=sys.stderr)
        raise SystemExit(2)
    return int(raw)


def resolve_data_root(repo_root: Path, cfg: dict) -> Path:
    """Default game data root: ``{repo}/data/{target-game-id}/``."""
    appid = parse_appid(cfg)
    return (repo_root / "data" / str(appid)).resolve()


@dataclass(frozen=True)
class ParadoxDataLayout:
    """``data/<APPID>/`` layout (see ``data/APPID/`` template)."""

    repo_root: Path
    appid: int
    root: Path

    @property
    def simple_info(self) -> Path:
        return self.root / "simple_info"

    @property
    def concrete_html(self) -> Path:
        return self.root / "concrete_html"

    @property
    def concrete_info(self) -> Path:
        return self.root / "concrete_info"

    @property
    def current_situation_json(self) -> Path:
        return self.simple_info / SIMPLE_CURRENT_SITUATION

    @property
    def browse_urls_json(self) -> Path:
        return self.simple_info / SIMPLE_BROWSE_URLS

    @property
    def detail_urls_json(self) -> Path:
        return self.simple_info / SIMPLE_DETAIL_URLS

    @property
    def simple_sqlite(self) -> Path:
        return self.simple_info / SIMPLE_SQLITE

    @property
    def simple_xlsx(self) -> Path:
        return self.simple_info / SIMPLE_XLSX

    @property
    def simple_html_root(self) -> Path:
        return self.simple_info / SIMPLE_HTML_DIR

    @property
    def browse_html_gaps_json(self) -> Path:
        return self.simple_info / SIMPLE_BROWSE_GAPS

    @property
    def concrete_html_root(self) -> Path:
        return self.concrete_html / CONCRETE_HTML_DIR

    @property
    def mod_fetch_sqlite(self) -> Path:
        return self.concrete_html / CONCRETE_FETCH_SQLITE

    @property
    def mod_fetch_log(self) -> Path:
        return self.concrete_html / CONCRETE_FETCH_LOG

    @property
    def analysis_sqlite(self) -> Path:
        return self.concrete_info / CONCRETE_INFO_SQLITE

    @property
    def analysis_xlsx(self) -> Path:
        return self.concrete_info / CONCRETE_INFO_XLSX

    @property
    def report_dir(self) -> Path:
        return self.root / DATA_REPORT_DIR

    @property
    def analysis_report_dir(self) -> Path:
        """Alias for :attr:`report_dir` (legacy name)."""
        return self.report_dir

    @property
    def analysis_info_dir(self) -> Path:
        return self.concrete_info / CONCRETE_INFO_META


def load_layout(
    repo_root: Path | None = None,
    *,
    cfg: dict | None = None,
    data_root: Path | None = None,
) -> ParadoxDataLayout:
    root = repo_root or find_repo_root()
    data = cfg if cfg is not None else load_base_json(root)
    appid = parse_appid(data)
    resolved = data_root.resolve() if data_root is not None else resolve_data_root(root, data)
    return ParadoxDataLayout(repo_root=root, appid=appid, root=resolved)


def ensure_layout_dirs(layout: ParadoxDataLayout) -> None:
    for d in (
        layout.simple_info,
        layout.simple_html_root,
        layout.concrete_html,
        layout.concrete_html_root,
        layout.concrete_info,
        layout.report_dir,
        layout.analysis_info_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)


def merge_write_base_config(
    appid: int,
    *,
    repo_root: Path | None = None,
) -> Path:
    """Update ``target-game-id`` in repo ``cfg/base.json`` (no ``data-folder``)."""
    root = repo_root or find_repo_root()
    path = base_json_path(root)
    cfg: dict = {}
    if path.is_file():
        cfg = load_base_json(root)
    cfg["target-game-id"] = appid
    cfg.pop("APPID", None)
    cfg.pop("data-folder", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    return path.resolve()


def merge_write_target_game(appid: int, *, repo_root: Path | None = None) -> None:
    merge_write_base_config(appid, repo_root=repo_root)
