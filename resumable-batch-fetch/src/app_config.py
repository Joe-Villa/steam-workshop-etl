"""Load crawler job config: ``io`` paths in JSON, or repo ``cfg/base.json`` layout."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = PROJECT_ROOT.parent
_LIB = _REPO_ROOT / "lib"

_DEFAULT_SQLITE_FILE = "mod_fetch.sqlite"
_DEFAULT_LOG_FILE = "output.log"
_HTML_SUBDIR = "html"

_ACTIVE_CONFIG_PATH: Path | None = None
_STAGE_IO: IoPaths | None = None

_DEFAULT_CRAWLER = {
    "max_fail": 5,
    "wait_min": 0.1,
    "wait_max": 0.3,
    "success_gap_s": 4.0,
    "concurrency": 8,
    "Ports": [-1, 26561],
}
_DEFAULT_SAFETY = {
    "26561_ignore_tsl": False,
}
_DEFAULT_EGRESS_HEALTH = {
    "transport_fail_to_degraded": 2,
    "transport_fail_to_quarantine": 5,
    "quarantine_s": 600.0,
}

STEAMPP_LOCAL_PROXY_PORT = 26561


class OutputDirNotCleanError(ValueError):
    """Output directory has files but no SQLite state (unsafe to start)."""


@dataclass(frozen=True)
class IoPaths:
    input_path: Path
    output_dir: Path
    html_root: Path
    sqlite_path: Path
    log_path: Path | None
    use_mod_html_buckets: bool


@dataclass(frozen=True)
class CrawlerConfig:
    max_fail: int
    wait_min: float
    wait_max: float
    success_gap_s: float
    concurrency: int
    ports: list[int]


@dataclass(frozen=True)
class SafetyConfig:
    """When ``ignore_tls_for_26561`` is true, egress on port 26561 skips TLS verification."""

    ignore_tls_for_26561: bool


@dataclass(frozen=True)
class EgressHealthConfig:
    """Per-port circuit breaker thresholds (see ``Port`` in ``crawler.py``)."""

    transport_fail_to_degraded: int
    transport_fail_to_quarantine: int
    quarantine_s: float


@dataclass(frozen=True)
class AppConfig:
    config_path: Path | None
    io: IoPaths
    crawler: CrawlerConfig
    safety: SafetyConfig
    egress_health: EgressHealthConfig
    test_urls: list[str]


def configure(config_path: Path | str | None) -> None:
    """Set the active config file for subsequent ``load_app_config()`` calls."""
    global _ACTIVE_CONFIG_PATH
    if config_path is None:
        _ACTIVE_CONFIG_PATH = None
        return
    _ACTIVE_CONFIG_PATH = Path(config_path).expanduser().resolve()


def set_stage_io(io: IoPaths | None) -> None:
    """When set, ``load_app_config()`` uses these paths (from pipeline paths JSON)."""
    global _STAGE_IO
    _STAGE_IO = io


def get_active_config_path() -> Path | None:
    return _ACTIVE_CONFIG_PATH


def default_config_path() -> Path:
    """Package default when not inside a Paradox pipeline repo."""
    return PROJECT_ROOT / "cfg" / "config.json"


def discover_config_path() -> Path:
    """Prefer explicit configure(); else repo ``cfg/crawler.json``; else package default."""
    if _ACTIVE_CONFIG_PATH is not None:
        return _ACTIVE_CONFIG_PATH
    try:
        if str(_LIB) not in sys.path:
            sys.path.insert(0, str(_LIB))
        from paradox_paths import find_repo_root  # noqa: WPS433

        return find_repo_root(PROJECT_ROOT) / "cfg" / "crawler.json"
    except FileNotFoundError:
        return default_config_path()


def get_config_path() -> Path:
    return discover_config_path()


def resolve_path(path: str | Path, *, base: Path | None = None) -> Path:
    """Resolve relative paths against ``base`` (config dir or package root)."""
    p = Path(path)
    if p.is_absolute():
        return p.resolve()
    root = base if base is not None else (
        _ACTIVE_CONFIG_PATH.parent if _ACTIVE_CONFIG_PATH else PROJECT_ROOT
    )
    return (root / p).resolve()


def resolve_under_root(path: str | Path) -> Path:
    """Legacy alias: relative paths use active config dir or ``PROJECT_ROOT``."""
    return resolve_path(path)


def _unexpected_output_entries(output_dir: Path) -> list[Path]:
    """Entries that block a fresh start (excluding an empty ``html/`` scaffold)."""
    unexpected: list[Path] = []
    for entry in output_dir.iterdir():
        if entry.name == _HTML_SUBDIR and entry.is_dir() and not any(entry.iterdir()):
            continue
        unexpected.append(entry)
    return unexpected


def validate_output_dir(output_dir: Path, sqlite_path: Path) -> None:
    """
    Allow empty output_dir or resume when ``sqlite_path`` exists.

    Reject directories that contain any entry but no SQLite (orphan HTML / mixed runs).
    An empty ``html/`` subdirectory is allowed (pipeline or config may create it first).
    """
    if not output_dir.exists():
        return
    if sqlite_path.is_file():
        return
    if not _unexpected_output_entries(output_dir):
        return
    rel_sqlite = sqlite_path.name
    raise OutputDirNotCleanError(
        f"输出目录不干净: {output_dir}\n"
        f"目录里已有文件，但未找到状态库 {rel_sqlite}。\n"
        f"请换空目录、删除无关文件，或保留已有 SQLite 以断点续跑。"
    )


def ensure_io_dirs(io: IoPaths) -> None:
    """Create output/html directories after cleanliness checks pass."""
    io.output_dir.mkdir(parents=True, exist_ok=True)
    io.html_root.mkdir(parents=True, exist_ok=True)


def _parse_ports(raw: object) -> list[int]:
    if not isinstance(raw, list) or not raw:
        raise ValueError('config crawler."Ports" must be a non-empty array of integers')
    out: list[int] = []
    for i, item in enumerate(raw):
        if isinstance(item, bool) or not isinstance(item, int):
            if isinstance(item, str) and item.lstrip("-").isdigit():
                out.append(int(item))
                continue
            raise ValueError(f"crawler.Ports[{i}] must be an integer")
        if item == -1 or 1 <= item <= 65535:
            out.append(item)
        else:
            raise ValueError(
                f"crawler.Ports[{i}] must be -1 (direct) or 1..65535, got {item}"
            )
    return out


def _io_paths_from_job(io_raw: dict, *, config_dir: Path, use_mod_html_buckets: bool) -> IoPaths:
    urls_raw = io_raw.get("urls_path")
    out_raw = io_raw.get("output_dir")
    if not isinstance(urls_raw, str) or not urls_raw.strip():
        raise ValueError('config io."urls_path" must be a non-empty string')
    if not isinstance(out_raw, str) or not out_raw.strip():
        raise ValueError('config io."output_dir" must be a non-empty string')

    sqlite_file = io_raw.get("sqlite_file", _DEFAULT_SQLITE_FILE)
    log_file = io_raw.get("log_file", _DEFAULT_LOG_FILE)
    if not isinstance(sqlite_file, str) or not sqlite_file.strip():
        raise ValueError('config io."sqlite_file" must be a non-empty string')
    if log_file is not None and not isinstance(log_file, str):
        raise ValueError('config io."log_file" must be a string or null')

    output_dir = resolve_path(out_raw.strip(), base=config_dir)
    sqlite_path = (output_dir / sqlite_file.strip()).resolve()
    log_path = (
        (output_dir / log_file.strip()).resolve()
        if isinstance(log_file, str) and log_file.strip()
        else None
    )
    html_root = (output_dir / _HTML_SUBDIR).resolve()
    input_path = resolve_path(urls_raw.strip(), base=config_dir)

    return IoPaths(
        input_path=input_path,
        output_dir=output_dir,
        html_root=html_root,
        sqlite_path=sqlite_path,
        log_path=log_path,
        use_mod_html_buckets=use_mod_html_buckets,
    )


def io_paths_from_repo_layout(*, use_mod_html_buckets: bool = True) -> IoPaths:
    if str(_LIB) not in sys.path:
        sys.path.insert(0, str(_LIB))
    from paradox_paths import load_layout  # noqa: WPS433

    layout = load_layout(_REPO_ROOT)
    return IoPaths(
        input_path=layout.detail_urls_json,
        output_dir=layout.concrete_html,
        html_root=layout.concrete_html_root,
        sqlite_path=layout.mod_fetch_sqlite,
        log_path=layout.mod_fetch_log,
        use_mod_html_buckets=use_mod_html_buckets,
    )


def config_template() -> dict:
    return {
        "io": {
            "urls_path": "../data_example/urls.json",
            "output_dir": "../data_example",
            "sqlite_file": "state.sqlite",
            "log_file": "output.log",
        },
        "test_url": ["https://steamcommunity.com/"],
        "crawler": dict(_DEFAULT_CRAWLER),
        "safety": dict(_DEFAULT_SAFETY),
        "egress_health": dict(_DEFAULT_EGRESS_HEALTH),
        "use_mod_html_buckets": True,
    }


def load_config_dict(config_path: Path | None = None) -> dict:
    path = config_path or discover_config_path()
    if not path.is_file():
        if path == default_config_path():
            return config_template()
        raise FileNotFoundError(f"Config not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return raw


def _resolve_io(
    data: dict,
    *,
    config_path: Path | None,
    use_mod_html_buckets: bool,
) -> IoPaths:
    if _STAGE_IO is not None:
        return _STAGE_IO
    io_raw = data.get("io")
    if isinstance(io_raw, dict) and io_raw.get("urls_path") and io_raw.get("output_dir"):
        base = (config_path or discover_config_path()).parent
        return _io_paths_from_job(
            io_raw, config_dir=base, use_mod_html_buckets=use_mod_html_buckets
        )
    try:
        if str(_LIB) not in sys.path:
            sys.path.insert(0, str(_LIB))
        from paradox_paths import find_repo_root  # noqa: WPS433

        find_repo_root(PROJECT_ROOT)
        return io_paths_from_repo_layout(use_mod_html_buckets=use_mod_html_buckets)
    except FileNotFoundError as e:
        raise ValueError(
            'Config must include io."urls_path" and io."output_dir" '
            "when not run inside a Paradox pipeline repo (cfg/base.json)."
        ) from e


def load_app_config(
    config_path: Path | str | None = None,
    raw: dict | None = None,
) -> AppConfig:
    if config_path is not None:
        configure(config_path)

    resolved_path = discover_config_path() if raw is None else get_active_config_path()
    data = load_config_dict(resolved_path) if raw is None else raw

    buckets = data.get("use_mod_html_buckets", True)
    if not isinstance(buckets, bool):
        io_raw = data.get("io")
        if isinstance(io_raw, dict):
            b2 = io_raw.get("use_mod_html_buckets")
            if isinstance(b2, bool):
                buckets = b2
    if not isinstance(buckets, bool):
        raise ValueError("use_mod_html_buckets must be a boolean")

    cfg_file = resolved_path if raw is None else get_active_config_path()
    io = _resolve_io(data, config_path=cfg_file, use_mod_html_buckets=buckets)

    crawler_raw = data.get("crawler")
    if not isinstance(crawler_raw, dict):
        crawler_raw = dict(_DEFAULT_CRAWLER)
    merged_crawler = {**_DEFAULT_CRAWLER, **crawler_raw}

    max_fail = int(merged_crawler.get("max_fail", _DEFAULT_CRAWLER["max_fail"]))
    if max_fail < 1:
        raise ValueError("crawler.max_fail must be >= 1")

    wait_min = float(merged_crawler.get("wait_min", _DEFAULT_CRAWLER["wait_min"]))
    wait_max = float(merged_crawler.get("wait_max", _DEFAULT_CRAWLER["wait_max"]))
    if wait_min < 0 or wait_max < 0 or wait_min > wait_max:
        raise ValueError("crawler.wait_min and wait_max must satisfy 0 <= wait_min <= wait_max")

    success_gap_s = float(
        merged_crawler.get("success_gap_s", _DEFAULT_CRAWLER["success_gap_s"])
    )
    if success_gap_s < 0:
        raise ValueError("crawler.success_gap_s must be >= 0")

    concurrency = int(merged_crawler.get("concurrency", _DEFAULT_CRAWLER["concurrency"]))
    if concurrency < 1:
        raise ValueError("crawler.concurrency must be >= 1")

    ports = _parse_ports(merged_crawler.get("Ports"))

    safety_raw = data.get("safety")
    if not isinstance(safety_raw, dict):
        safety_raw = dict(_DEFAULT_SAFETY)
    merged_safety = {**_DEFAULT_SAFETY, **safety_raw}
    ignore_tls = merged_safety.get("26561_ignore_tsl", _DEFAULT_SAFETY["26561_ignore_tsl"])
    if not isinstance(ignore_tls, bool):
        raise ValueError('config safety."26561_ignore_tsl" must be a boolean')

    test_raw = data.get("test_url", [])
    if test_raw is None:
        test_raw = []
    if not isinstance(test_raw, list):
        raise ValueError('config "test_url" must be an array of URL strings')
    test_urls: list[str] = []
    for i, item in enumerate(test_raw):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"test_url[{i}] must be a non-empty string URL")
        test_urls.append(item.strip())

    health_raw = data.get("egress_health")
    if not isinstance(health_raw, dict):
        health_raw = dict(_DEFAULT_EGRESS_HEALTH)
    merged_health = {**_DEFAULT_EGRESS_HEALTH, **health_raw}

    fail_degraded = int(
        merged_health.get(
            "transport_fail_to_degraded",
            _DEFAULT_EGRESS_HEALTH["transport_fail_to_degraded"],
        )
    )
    fail_quarantine = int(
        merged_health.get(
            "transport_fail_to_quarantine",
            _DEFAULT_EGRESS_HEALTH["transport_fail_to_quarantine"],
        )
    )
    quarantine_s = float(
        merged_health.get("quarantine_s", _DEFAULT_EGRESS_HEALTH["quarantine_s"])
    )
    if fail_degraded < 1:
        raise ValueError("egress_health.transport_fail_to_degraded must be >= 1")
    if fail_quarantine < fail_degraded:
        raise ValueError(
            "egress_health.transport_fail_to_quarantine must be >= "
            "transport_fail_to_degraded"
        )
    if quarantine_s <= 0:
        raise ValueError("egress_health.quarantine_s must be > 0")

    return AppConfig(
        config_path=cfg_file,
        io=io,
        crawler=CrawlerConfig(
            max_fail=max_fail,
            wait_min=wait_min,
            wait_max=wait_max,
            success_gap_s=success_gap_s,
            concurrency=concurrency,
            ports=ports,
        ),
        safety=SafetyConfig(ignore_tls_for_26561=ignore_tls),
        egress_health=EgressHealthConfig(
            transport_fail_to_degraded=fail_degraded,
            transport_fail_to_quarantine=fail_quarantine,
            quarantine_s=quarantine_s,
        ),
        test_urls=test_urls,
    )


def parse_config_argv(argv: list[str]) -> tuple[Path | None, list[str]]:
    """
    Extract ``--config`` / ``-c`` or a lone ``*.json`` positional.

    Returns ``(config_path, remaining_argv)``.
    """
    remaining: list[str] = []
    config_path: Path | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--config", "-c"):
            if i + 1 >= len(argv):
                raise SystemExit(f"ERROR: {arg} requires a path argument.")
            config_path = Path(argv[i + 1]).expanduser()
            i += 2
            continue
        if arg.startswith("--config="):
            config_path = Path(arg.split("=", 1)[1]).expanduser()
            i += 1
            continue
        remaining.append(arg)
        i += 1

    if config_path is None and len(remaining) == 1:
        cand = Path(remaining[0]).expanduser()
        if cand.suffix.lower() == ".json" and cand.exists():
            config_path = cand
            remaining = []

    return config_path, remaining


def bootstrap_config_from_argv(argv: list[str]) -> list[str]:
    """Apply config path from CLI and return unconsumed arguments."""
    config_path, remaining = parse_config_argv(argv)
    if config_path is not None:
        configure(config_path.resolve())
    return remaining
