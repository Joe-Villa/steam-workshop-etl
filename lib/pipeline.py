"""Paradox analysis pipeline: stage detection, state file, data protection."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from paradox_paths import ParadoxDataLayout, ensure_layout_dirs

PIPELINE_STATE_NAME = "pipeline_state.json"
STATE_VERSION = 1

# Legacy sub-package status (under package dir, not data-folder)
LEGACY_APPID_STATUS_DIR = "status"


class Stage(StrEnum):
    SIMPLE_INFO = "simple_info"
    DETAIL_FETCH = "detail_fetch"
    GRANT_TABLE = "grant_table"
    MOD_ANALYSIS = "mod_analysis"
    DONE = "done"

    # legacy alias
    ANALYSIS = "mod_analysis"


STAGE_ORDER: tuple[Stage, ...] = (
    Stage.SIMPLE_INFO,
    Stage.DETAIL_FETCH,
    Stage.GRANT_TABLE,
    Stage.MOD_ANALYSIS,
    Stage.DONE,
)

_STEP_ID_TO_STAGE: dict[str, Stage] = {
    "1": Stage.SIMPLE_INFO,
    "2": Stage.DETAIL_FETCH,
    "3": Stage.GRANT_TABLE,
    "4": Stage.MOD_ANALYSIS,
}

_STAGE_TO_STEP_ID: dict[Stage, str] = {v: k for k, v in _STEP_ID_TO_STAGE.items()}


@dataclass(frozen=True)
class PipelineErrorInfo:
    code: str
    title: str
    cause: str
    solution: str
    exit_code: int = 1


class PipelineError(Exception):
    def __init__(self, info: PipelineErrorInfo, *, detail: str | None = None) -> None:
        self.info = info
        self.detail = detail
        super().__init__(detail or info.title)

    def format_message(self) -> str:
        lines = [
            f"[{self.info.code}] {self.info.title}",
            f"原因: {self.info.cause}",
            f"处理: {self.info.solution}",
        ]
        if self.detail:
            lines.append(f"详情: {self.detail}")
        return "\n".join(lines)


ERR_DATA_DIR_NOT_EMPTY = PipelineErrorInfo(
    "DATA_DIR_NOT_EMPTY",
    "数据目录已存在且非空，拒绝作为全新流水线启动",
    "数据根目录（manifest output 或 data/<APPID>/）里已有爬取产物（HTML/SQLite/JSON 等）。",
    "换一个新的空目录作为 output，或确认可以覆盖后使用 --force-fresh；"
    "若只是断点续跑，不要加 --fresh，直接运行 python main.py。",
    exit_code=3,
)

ERR_APPID_MISMATCH = PipelineErrorInfo(
    "APPID_MISMATCH",
    "配置中的 APPID 与数据目录内快照不一致",
    "current_situation.json 里的 APPID 与 cfg/base.json 的 target-game-id 不同。",
    "检查是否误用了别的游戏数据目录；修正 manifest output 或 target-game-id 后重试。",
    exit_code=3,
)

ERR_MISSING_PREREQUISITE = PipelineErrorInfo(
    "MISSING_PREREQUISITE",
    "上一阶段产物缺失，无法进入当前阶段",
    "流水线要求按 simple_info → detail_fetch → grant_table → mod_analysis 顺序产出文件。",
    "先完成上一阶段（python main.py 会自动从断点阶段继续），或用 --from 指定更早阶段。",
    exit_code=4,
)

ERR_STAGE_FAILED = PipelineErrorInfo(
    "STAGE_FAILED",
    "子包执行失败",
    "某个阶段的 Python 子进程以非零退出码结束。",
    "查看上方子包日志；修复网络/代理/磁盘问题后重新运行 python main.py（会从失败阶段重试）。",
    exit_code=5,
)

ERR_SIMPLE_INFO_INCOMPLETE = PipelineErrorInfo(
    "SIMPLE_INFO_INCOMPLETE",
    "简略信息阶段未完整结束",
    "浏览页 HTML 仍有缺口，或 urls.json / name.sqlite 未就绪。",
    "在 appid-steamworkshop-table 目录运行 test/check_browse_html_coverage.py；"
    "或重新运行 python main.py --only simple_info。",
    exit_code=6,
)

ERR_DETAIL_FETCH_INCOMPLETE = PipelineErrorInfo(
    "DETAIL_FETCH_INCOMPLETE",
    "详情页爬取未全部完成",
    "mod_fetch.sqlite 中仍有 pending 或可重试的 fail 行。",
    "重新运行 python main.py（会续爬）；检查 resumable-batch-fetch 的代理端口与 output.log。",
    exit_code=6,
)


@dataclass
class StageRecord:
    status: str  # pending | running | completed | failed
    started_at: str | None = None
    completed_at: str | None = None
    last_error: str | None = None
    subprocess_exit: int | None = None


@dataclass
class PipelineState:
    version: int
    appid: int
    data_folder: str
    current_stage: str
    stages: dict[str, StageRecord] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "appid": self.appid,
            "data_folder": self.data_folder,
            "current_stage": self.current_stage,
            "stages": {k: asdict(v) for k, v in self.stages.items()},
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineState:
        stages_raw = data.get("stages") or {}
        stages: dict[str, StageRecord] = {}
        if isinstance(stages_raw, dict):
            for name, rec in stages_raw.items():
                if isinstance(rec, dict):
                    stages[name] = StageRecord(
                        status=str(rec.get("status", "pending")),
                        started_at=rec.get("started_at"),
                        completed_at=rec.get("completed_at"),
                        last_error=rec.get("last_error"),
                        subprocess_exit=rec.get("subprocess_exit"),
                    )
        current = str(data.get("current_stage", Stage.SIMPLE_INFO.value))
        if current == "analysis":
            current = Stage.GRANT_TABLE.value
        legacy_analysis = stages.pop("analysis", None)
        if legacy_analysis and Stage.GRANT_TABLE.value not in stages:
            stages[Stage.GRANT_TABLE.value] = legacy_analysis
        if legacy_analysis and Stage.MOD_ANALYSIS.value not in stages:
            stages[Stage.MOD_ANALYSIS.value] = StageRecord(
                status="pending",
            )
        return cls(
            version=int(data.get("version", 1)),
            appid=int(data["appid"]),
            data_folder=str(data["data_folder"]),
            current_stage=current,
            stages=stages,
            updated_at=str(data.get("updated_at", "")),
        )


def pipeline_state_path(layout: ParadoxDataLayout) -> Path:
    return layout.root / PIPELINE_STATE_NAME


def pipeline_state_path_at(state_file: Path) -> Path:
    return state_file.resolve()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_has_content(path: Path, *, min_bytes: int = 1) -> bool:
    return path.is_file() and path.stat().st_size >= min_bytes


def _json_array_len(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(raw) if isinstance(raw, list) else 0


def _count_html_files(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("*.html") if p.is_file())


def _appid_in_situation(layout: ParadoxDataLayout) -> int | None:
    path = layout.current_situation_json
    if not _file_has_content(path, min_bytes=2):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get("APPID")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def validate_appid_consistency(layout: ParadoxDataLayout) -> None:
    on_disk = _appid_in_situation(layout)
    if on_disk is not None and on_disk != layout.appid:
        raise PipelineError(
            ERR_APPID_MISMATCH,
            detail=f"配置 APPID={layout.appid}，current_situation.json APPID={on_disk}",
        )


def data_root_has_meaningful_content(layout: ParadoxDataLayout) -> bool:
    """True if directory looks like an in-progress or finished crawl (protect from overwrite)."""
    if _count_html_files(layout.simple_html_root) > 0:
        return True
    if _count_html_files(layout.concrete_html_root) > 0:
        return True
    for p in (
        layout.simple_sqlite,
        layout.detail_urls_json,
        layout.mod_fetch_sqlite,
        layout.analysis_sqlite,
    ):
        if _file_has_content(p, min_bytes=32):
            return True
    if _json_array_len(layout.detail_urls_json) > 0:
        return True
    if pipeline_state_path(layout).is_file():
        return True
    return False


def is_data_root_empty(layout: ParadoxDataLayout) -> bool:
    return not data_root_has_meaningful_content(layout)


def simple_info_complete(layout: ParadoxDataLayout, *, strict_browse: bool = False) -> bool:
    if _json_array_len(layout.detail_urls_json) < 1:
        return False
    if not _file_has_content(layout.simple_sqlite, min_bytes=100):
        return False
    if not _file_has_content(layout.current_situation_json, min_bytes=10):
        return False
    if strict_browse and layout.browse_urls_json.is_file():
        n_urls = _json_array_len(layout.browse_urls_json)
        if n_urls > 0:
            n_html = _count_html_files(layout.simple_html_root)
            if n_html < n_urls:
                return False
            if layout.browse_html_gaps_json.is_file():
                try:
                    gaps = json.loads(layout.browse_html_gaps_json.read_text(encoding="utf-8"))
                    if int(gaps.get("gap_count", 0)) > 0:
                        return False
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    pass
    return True


def detail_fetch_stats(layout: ParadoxDataLayout) -> dict[str, int] | None:
    db = layout.mod_fetch_sqlite
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(str(db))
        try:
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mod_fetch'"
            ).fetchone():
                return None
            total = int(conn.execute("SELECT COUNT(*) FROM mod_fetch").fetchone()[0])
            pending = int(
                conn.execute(
                    "SELECT COUNT(*) FROM mod_fetch WHERE status='pending'"
                ).fetchone()[0]
            )
            fail = int(
                conn.execute(
                    "SELECT COUNT(*) FROM mod_fetch WHERE status='fail'"
                ).fetchone()[0]
            )
            success = int(
                conn.execute(
                    "SELECT COUNT(*) FROM mod_fetch WHERE status='success'"
                ).fetchone()[0]
            )
            invalid = int(
                conn.execute(
                    "SELECT COUNT(*) FROM mod_fetch WHERE status='invalid'"
                ).fetchone()[0]
            )
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return {
        "total": total,
        "pending": pending,
        "fail": fail,
        "success": success,
        "invalid": invalid,
    }


def detail_fetch_complete(layout: ParadoxDataLayout) -> bool:
    stats = detail_fetch_stats(layout)
    if not stats or stats["total"] < 1:
        return False
    return stats["pending"] == 0 and stats["fail"] == 0


def grant_table_complete(layout: ParadoxDataLayout) -> bool:
    return _file_has_content(layout.analysis_sqlite, min_bytes=100)


def mod_analysis_complete(layout: ParadoxDataLayout) -> bool:
    report = layout.report_dir
    if not report.is_dir():
        return False
    state = report / "stage_state.sqlite"
    if state.is_file():
        return True
    return any(report.glob("*.md")) or any(report.rglob("*.csv"))


def analysis_complete(layout: ParadoxDataLayout) -> bool:
    return grant_table_complete(layout) and mod_analysis_complete(layout)


def infer_stage(layout: ParadoxDataLayout) -> Stage:
    validate_appid_consistency(layout)
    if mod_analysis_complete(layout):
        return Stage.DONE
    if grant_table_complete(layout):
        return Stage.MOD_ANALYSIS
    if detail_fetch_complete(layout):
        return Stage.GRANT_TABLE
    if simple_info_complete(layout):
        return Stage.DETAIL_FETCH
    if data_root_has_meaningful_content(layout):
        return Stage.SIMPLE_INFO
    return Stage.SIMPLE_INFO


def stage_for_step_id(step_id: str) -> Stage | None:
    return _STEP_ID_TO_STAGE.get(str(step_id))


def step_id_for_stage(stage: Stage) -> str | None:
    if stage == Stage.DONE:
        return None
    return _STAGE_TO_STEP_ID.get(stage)


def load_pipeline_state(
    layout: ParadoxDataLayout,
    *,
    state_path: Path | None = None,
) -> PipelineState | None:
    path = state_path or pipeline_state_path(layout)
    path = path.resolve()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    state = PipelineState.from_dict(data)
    if state.appid != layout.appid:
        return None
    if Path(state.data_folder).resolve() != layout.root.resolve():
        return None
    return state


def save_pipeline_state(
    layout: ParadoxDataLayout,
    state: PipelineState,
    *,
    state_path: Path | None = None,
) -> None:
    state.updated_at = _utc_now()
    state.data_folder = str(layout.root)
    state.appid = layout.appid
    path = (state_path or pipeline_state_path(layout)).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def new_pipeline_state(layout: ParadoxDataLayout, start: Stage) -> PipelineState:
    stages = {
        s.value: StageRecord(status="pending") for s in STAGE_ORDER if s != Stage.DONE
    }
    return PipelineState(
        version=STATE_VERSION,
        appid=layout.appid,
        data_folder=str(layout.root),
        current_stage=start.value,
        stages=stages,
        updated_at=_utc_now(),
    )


def resolve_run_stage(
    layout: ParadoxDataLayout,
    *,
    state: PipelineState | None,
    from_stage: Stage | None,
    fresh: bool,
    force_fresh: bool = False,
) -> Stage:
    if fresh:
        if not is_data_root_empty(layout) and not force_fresh:
            raise PipelineError(
                ERR_DATA_DIR_NOT_EMPTY,
                detail=str(layout.root),
            )
        return Stage.SIMPLE_INFO
    if from_stage is not None:
        return from_stage
    if state is not None:
        cur = Stage(state.current_stage)
        if cur in STAGE_ORDER:
            if cur == Stage.DONE:
                return Stage.DONE
            if cur == Stage.SIMPLE_INFO and simple_info_complete(layout):
                return Stage.DETAIL_FETCH
            if cur == Stage.DETAIL_FETCH and detail_fetch_complete(layout):
                return Stage.GRANT_TABLE
            if cur == Stage.GRANT_TABLE and grant_table_complete(layout):
                return Stage.MOD_ANALYSIS
            return cur
    return infer_stage(layout)


def assert_prerequisites(layout: ParadoxDataLayout, stage: Stage) -> None:
    if stage == Stage.SIMPLE_INFO:
        return
    if stage == Stage.DETAIL_FETCH:
        if not simple_info_complete(layout):
            raise PipelineError(
                ERR_MISSING_PREREQUISITE,
                detail="需要 simple_info/urls.json 与 simple_info/name.sqlite",
            )
        return
    if stage in (Stage.GRANT_TABLE, Stage.ANALYSIS):
        if not simple_info_complete(layout):
            raise PipelineError(ERR_MISSING_PREREQUISITE, detail="缺少简略表")
        if not detail_fetch_complete(layout):
            stats = detail_fetch_stats(layout) or {}
            raise PipelineError(
                ERR_MISSING_PREREQUISITE,
                detail=f"详情爬取未完成: pending={stats.get('pending', '?')}, "
                f"fail={stats.get('fail', '?')}",
            )
        return
    if stage == Stage.MOD_ANALYSIS:
        if not grant_table_complete(layout):
            raise PipelineError(
                ERR_MISSING_PREREQUISITE,
                detail="缺少 concrete_info/name.sqlite（请先运行 steam-grant-table）",
            )


def clear_intermediate_status_files(
    layout: ParadoxDataLayout, repo_root: Path, *, include_fetch_db: bool = False
) -> list[str]:
    """Remove resumable status artifacts (not bulk HTML). Returns removed paths."""
    removed: list[str] = []
    candidates = [
        layout.browse_html_gaps_json,
        layout.mod_fetch_log,
        layout.concrete_html_root / "id_collection_state.json",
        layout.concrete_html_root / "mod_html_fetch_state.json",
        layout.concrete_html_root / "mod_index.json",
    ]
    if include_fetch_db:
        candidates.append(layout.mod_fetch_sqlite)
    legacy = repo_root / "appid-steamworkshop-table" / LEGACY_APPID_STATUS_DIR
    if legacy.is_dir():
        for p in legacy.iterdir():
            candidates.append(p)
    for path in candidates:
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    return removed


def format_status_report(layout: ParadoxDataLayout, state: PipelineState | None) -> str:
    inferred = infer_stage(layout)
    lines = [
        f"数据目录: {layout.root}",
        f"APPID: {layout.appid}",
        f"推断阶段: {inferred.value}",
    ]
    if state:
        lines.append(f"状态文件阶段: {state.current_stage} (更新于 {state.updated_at})")
    else:
        lines.append("状态文件: 无")
    lines.append("")
    lines.append("阶段检查:")
    lines.append(f"  simple_info 完成: {simple_info_complete(layout)}")
    si_strict = simple_info_complete(layout, strict_browse=True)
    lines.append(f"  simple_info 严格(浏览页齐全): {si_strict}")
    stats = detail_fetch_stats(layout)
    if stats:
        lines.append(
            f"  detail_fetch: total={stats['total']} ok={stats['success']} "
            f"pending={stats['pending']} fail={stats['fail']} invalid={stats['invalid']} "
            f"完成={detail_fetch_complete(layout)}"
        )
    else:
        lines.append("  detail_fetch: (无 mod_fetch.sqlite)")
    lines.append(f"  grant_table 完成: {grant_table_complete(layout)}")
    lines.append(f"  mod_analysis 完成: {mod_analysis_complete(layout)}")
    lines.append(f"  详情 HTML 文件数: {_count_html_files(layout.concrete_html_root)}")
    lines.append(f"  简略 HTML 文件数: {_count_html_files(layout.simple_html_root)}")
    return "\n".join(lines)


def mark_stage_running(state: PipelineState, stage: Stage) -> None:
    rec = state.stages.setdefault(stage.value, StageRecord(status="pending"))
    rec.status = "running"
    rec.started_at = _utc_now()
    rec.last_error = None
    rec.subprocess_exit = None
    state.current_stage = stage.value


def mark_stage_completed(state: PipelineState, stage: Stage) -> None:
    rec = state.stages.setdefault(stage.value, StageRecord(status="pending"))
    rec.status = "completed"
    rec.completed_at = _utc_now()
    idx = STAGE_ORDER.index(stage)
    if idx + 1 < len(STAGE_ORDER):
        state.current_stage = STAGE_ORDER[idx + 1].value
    else:
        state.current_stage = Stage.DONE.value


def mark_stage_failed(state: PipelineState, stage: Stage, *, message: str, exit_code: int) -> None:
    rec = state.stages.setdefault(stage.value, StageRecord(status="pending"))
    rec.status = "failed"
    rec.last_error = message
    rec.subprocess_exit = exit_code
    state.current_stage = stage.value
