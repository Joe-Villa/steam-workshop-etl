"""Load ``cfg/config.json`` (crawler) and repo ``cfg/base.json`` (I/O paths)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = PROJECT_ROOT.parent
_LIB = _REPO_ROOT / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from paradox_paths import find_repo_root, load_layout  # noqa: E402


def crawler_config_path() -> Path:
    """Prefer repo ``cfg/crawler.json``; fallback to package ``cfg/config.json``."""
    try:
        return find_repo_root(PROJECT_ROOT) / "cfg" / "crawler.json"
    except FileNotFoundError:
        return PROJECT_ROOT / "cfg" / "config.json"


CONFIG_PATH = crawler_config_path()

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
    io: IoPaths
    crawler: CrawlerConfig
    safety: SafetyConfig
    egress_health: EgressHealthConfig
    test_urls: list[str]


def resolve_under_root(path: str | Path) -> Path:
    """Relative paths are under resumable-batch-fetch/; absolute paths are unchanged."""
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


def io_paths_from_repo_layout(*, use_mod_html_buckets: bool = True) -> IoPaths:
    layout = load_layout(_REPO_ROOT)
    layout.concrete_html.mkdir(parents=True, exist_ok=True)
    layout.concrete_html_root.mkdir(parents=True, exist_ok=True)
    return IoPaths(
        input_path=layout.detail_urls_json,
        output_dir=layout.concrete_html,
        html_root=layout.concrete_html_root,
        sqlite_path=layout.mod_fetch_sqlite,
        log_path=layout.mod_fetch_log,
        use_mod_html_buckets=use_mod_html_buckets,
    )


def config_template() -> dict:
    """Canonical ``config.json`` shape (I/O paths come from repo ``cfg/base.json``)."""
    return {
        "test_url": ["https://steamcommunity.com/"],
        "crawler": dict(_DEFAULT_CRAWLER),
        "safety": dict(_DEFAULT_SAFETY),
        "egress_health": dict(_DEFAULT_EGRESS_HEALTH),
        "use_mod_html_buckets": True,
    }


def load_config_dict() -> dict:
    if not CONFIG_PATH.is_file():
        return config_template()
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{CONFIG_PATH.name} must be a JSON object")
    return raw


def _parse_ports(raw: object) -> list[int]:
    if not isinstance(raw, list) or not raw:
        raise ValueError('config.json crawler."Ports" must be a non-empty array of integers')
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


def load_app_config(raw: dict | None = None) -> AppConfig:
    data = load_config_dict() if raw is None else raw

    buckets = data.get("use_mod_html_buckets", True)
    if not isinstance(buckets, bool):
        io_raw = data.get("io")
        if isinstance(io_raw, dict):
            buckets = io_raw.get("use_mod_html_buckets", True)
    if not isinstance(buckets, bool):
        raise ValueError("use_mod_html_buckets must be a boolean")

    io = io_paths_from_repo_layout(use_mod_html_buckets=buckets)

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
        raise ValueError('config.json safety."26561_ignore_tsl" must be a boolean')

    test_raw = data.get("test_url", [])
    if test_raw is None:
        test_raw = []
    if not isinstance(test_raw, list):
        raise ValueError('config.json "test_url" must be an array of URL strings')
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
