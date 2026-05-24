"""
Grade each cfg ``Ports`` entry by probing a Steam workshop detail URL from ``urls.json``.
Uses aiohttp (same stack as production crawl). Does not download page bodies: on HTTP 200
only the response is released without reading content.
Writes a text report under ``test/`` (default: ``test/port_health_report.txt``).
Grades:
  A healthy   — ok_rate >= 80%, low transport/429, proxy up, p95 latency OK
  B degraded  — ok_rate >= 40%, proxy up, not dead
  C unstable  — some success but poor reliability
  F dead      — no success, or proxy refused, or all transport failures
"""
from __future__ import annotations
import argparse
import asyncio
import json
import ssl
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import aiohttp
_TEST_DIR = Path(__file__).resolve().parent
_SRC = _TEST_DIR.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
from app_config import CONFIG_PATH, STEAMPP_LOCAL_PROXY_PORT, load_app_config  # noqa: E402
from egress import clear_proxy_env  # noqa: E402
URLS_JSON = _TEST_DIR / "urls.json"
DEFAULT_REPORT = _TEST_DIR / "port_health_report.txt"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
_GATEWAY_CODES = frozenset({502, 503, 504})
# Grade thresholds (aligned with crawler egress_health semantics).
_MIN_OK_A = 0.8
_MAX_TRANSPORT_A = 0.2
_MAX_429_A = 0.2
_P95_MAX_S = 30.0
_MIN_OK_B = 0.4
_MAX_CONSEC_TRANSPORT_WARN = 3
class Outcome(str, Enum):
    OK = "ok"
    RATE_LIMIT = "429"
    TRANSPORT = "transport"
    GATEWAY = "gateway"
    OTHER_HTTP = "other_http"
    PROXY_REFUSED = "proxy_refused"
class Grade(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    F = "F"
@dataclass
class AttemptResult:
    outcome: Outcome
    status_code: int | None = None
    latency_s: float | None = None
    detail: str = ""
@dataclass
class PortReport:
    port: int
    grade: Grade
    attempts: int
    ok: int
    rate_limit: int
    transport: int
    gateway: int
    other_http: int
    proxy_refused: int
    ok_rate: float
    transport_rate: float
    rate_limit_rate: float
    max_consecutive_transport: int
    latency_p50_s: float | None
    latency_p95_s: float | None
    sample_details: list[str] = field(default_factory=list)
def load_probe_url(path: Path, *, index: int) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"URL list not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path.name} must be a non-empty JSON array of URL strings")
    if index < 0 or index >= len(raw):
        raise ValueError(f"url-index {index} out of range (0..{len(raw) - 1})")
    url = raw[index]
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"{path.name}[{index}] must be a non-empty string URL")
    return url.strip()
def _proxy_url(port: int) -> str | None:
    if port == -1:
        return None
    return f"http://127.0.0.1:{port}"
def _ssl_for_port(port: int, *, ignore_tls_for_26561: bool) -> bool:
    if ignore_tls_for_26561 and port == STEAMPP_LOCAL_PROXY_PORT:
        return False
    return False
def _classify_exception(exc: BaseException) -> tuple[Outcome, str]:
    text = str(exc).lower()
    if isinstance(exc, aiohttp.ClientConnectorError):
        if "connection refused" in text or "connect call failed" in text:
            return Outcome.PROXY_REFUSED, _short(exc)
        return Outcome.TRANSPORT, _short(exc)
    if isinstance(exc, asyncio.TimeoutError):
        return Outcome.TRANSPORT, "timeout"
    if isinstance(exc, aiohttp.ClientError):
        if "reset" in text or "broken pipe" in text:
            return Outcome.TRANSPORT, _short(exc)
        return Outcome.TRANSPORT, _short(exc)
    return Outcome.TRANSPORT, f"{type(exc).__name__}: {_short(exc)}"
def _short(exc: BaseException, *, max_len: int = 100) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= max_len else text[: max_len - 3] + "..."
async def _probe_once(
    session: aiohttp.ClientSession,
    url: str,
    port: int,
    *,
    ignore_tls_for_26561: bool,
    timeout_s: float,
) -> AttemptResult:
    t0 = time.monotonic()
    try:
        async with session.get(
            url,
            proxy=_proxy_url(port),
            ssl=_ssl_for_port(port, ignore_tls_for_26561=ignore_tls_for_26561),
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as resp:
            code = int(resp.status)
            elapsed = time.monotonic() - t0
            if code == 200:
                await resp.release()
                return AttemptResult(
                    Outcome.OK,
                    status_code=code,
                    latency_s=elapsed,
                    detail=f"HTTP {code} {elapsed:.2f}s",
                )
            if code == 429:
                await resp.release()
                return AttemptResult(
                    Outcome.RATE_LIMIT,
                    status_code=code,
                    detail=f"HTTP {code}",
                )
            if code in _GATEWAY_CODES:
                await resp.release()
                return AttemptResult(
                    Outcome.GATEWAY,
                    status_code=code,
                    detail=f"HTTP {code}",
                )
            await resp.release()
            return AttemptResult(
                Outcome.OTHER_HTTP,
                status_code=code,
                detail=f"HTTP {code}",
            )
    except Exception as e:
        outcome, detail = _classify_exception(e)
        return AttemptResult(outcome, detail=detail)
async def probe_port(
    session: aiohttp.ClientSession,
    port: int,
    url: str,
    *,
    samples: int,
    gap_s: float,
    ignore_tls_for_26561: bool,
    timeout_s: float,
) -> list[AttemptResult]:
    results: list[AttemptResult] = []
    for i in range(samples):
        if i > 0 and gap_s > 0:
            await asyncio.sleep(gap_s)
        results.append(
            await _probe_once(
                session,
                url,
                port,
                ignore_tls_for_26561=ignore_tls_for_26561,
                timeout_s=timeout_s,
            )
        )
    return results
def _max_consecutive_transport(attempts: list[AttemptResult]) -> int:
    transport_outcomes = {
        Outcome.TRANSPORT,
        Outcome.GATEWAY,
        Outcome.PROXY_REFUSED,
    }
    best = 0
    cur = 0
    for a in attempts:
        if a.outcome in transport_outcomes:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best
def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)
def grade_port(attempts: list[AttemptResult]) -> PortReport:
    n = len(attempts)
    ok = sum(1 for a in attempts if a.outcome == Outcome.OK)
    rl = sum(1 for a in attempts if a.outcome == Outcome.RATE_LIMIT)
    transport = sum(1 for a in attempts if a.outcome == Outcome.TRANSPORT)
    gateway = sum(1 for a in attempts if a.outcome == Outcome.GATEWAY)
    other = sum(1 for a in attempts if a.outcome == Outcome.OTHER_HTTP)
    refused = sum(1 for a in attempts if a.outcome == Outcome.PROXY_REFUSED)
    transport_like = transport + gateway + refused
    ok_rate = ok / n if n else 0.0
    transport_rate = transport_like / n if n else 0.0
    rl_rate = rl / n if n else 0.0
    latencies = [a.latency_s for a in attempts if a.latency_s is not None]
    p50 = statistics.median(latencies) if latencies else None
    p95 = _percentile(latencies, 0.95)
    max_consec = _max_consecutive_transport(attempts)
    has_refused = refused > 0
    grade = Grade.F
    if ok == 0 or has_refused and ok == 0:
        grade = Grade.F
    elif ok_rate >= _MIN_OK_A and transport_rate <= _MAX_TRANSPORT_A and rl_rate <= _MAX_429_A:
        if p95 is None or p95 <= _P95_MAX_S:
            grade = Grade.A
        else:
            grade = Grade.B
    elif ok_rate >= _MIN_OK_B and not (has_refused and ok == 0) and max_consec < 5:
        grade = Grade.B
    elif ok > 0:
        grade = Grade.C
    else:
        grade = Grade.F
    # Refine: high transport with little success -> C not B
    if grade == Grade.B and ok_rate < _MIN_OK_A:
        if transport_rate >= 0.6 or max_consec >= _MAX_CONSEC_TRANSPORT_WARN:
            grade = Grade.C
    if grade == Grade.B and ok_rate < _MIN_OK_A and max_consec >= 5:
        grade = Grade.C
    port = attempts  # placeholder fix below
    return PortReport(
        port=-1,  # filled by caller
        grade=grade,
        attempts=n,
        ok=ok,
        rate_limit=rl,
        transport=transport,
        gateway=gateway,
        other_http=other,
        proxy_refused=refused,
        ok_rate=ok_rate,
        transport_rate=transport_rate,
        rate_limit_rate=rl_rate,
        max_consecutive_transport=max_consec,
        latency_p50_s=p50,
        latency_p95_s=p95,
        sample_details=[a.detail for a in attempts],
    )
def build_port_report(port: int, attempts: list[AttemptResult]) -> PortReport:
    rep = grade_port(attempts)
    return PortReport(
        port=port,
        grade=rep.grade,
        attempts=rep.attempts,
        ok=rep.ok,
        rate_limit=rep.rate_limit,
        transport=rep.transport,
        gateway=rep.gateway,
        other_http=rep.other_http,
        proxy_refused=rep.proxy_refused,
        ok_rate=rep.ok_rate,
        transport_rate=rep.transport_rate,
        rate_limit_rate=rep.rate_limit_rate,
        max_consecutive_transport=rep.max_consecutive_transport,
        latency_p50_s=rep.latency_p50_s,
        latency_p95_s=rep.latency_p95_s,
        sample_details=rep.sample_details,
    )
async def run_probe(
    ports: list[int],
    url: str,
    *,
    samples: int,
    gap_s: float,
    port_workers: int,
    ignore_tls_for_26561: bool,
    timeout_s: float,
) -> list[PortReport]:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": _ACCEPT,
    }
    connector = aiohttp.TCPConnector(limit=0, limit_per_host=0)
    sem = asyncio.Semaphore(max(1, port_workers))
    reports: list[PortReport | None] = [None] * len(ports)
    async with aiohttp.ClientSession(
        headers=headers,
        connector=connector,
        trust_env=False,
    ) as session:
        async def one(idx: int, port: int) -> None:
            async with sem:
                attempts = await probe_port(
                    session,
                    port,
                    url,
                    samples=samples,
                    gap_s=gap_s,
                    ignore_tls_for_26561=ignore_tls_for_26561,
                    timeout_s=timeout_s,
                )
                reports[idx] = build_port_report(port, attempts)
        await asyncio.gather(*(one(i, p) for i, p in enumerate(ports)))
    return [r for r in reports if r is not None]
def format_report(
    *,
    reports: list[PortReport],
    probe_url: str,
    urls_path: Path,
    url_index: int,
    samples: int,
    config_path: Path,
    generated_at: str,
) -> str:
    by_grade: dict[Grade, list[PortReport]] = {g: [] for g in Grade}
    for r in reports:
        by_grade[r.grade].append(r)
    lines: list[str] = [
        "Port health report (checkportdetail)",
        f"Generated: {generated_at}",
        f"Config: {config_path}",
        f"Probe URL ({urls_path.name}[{url_index}]): {probe_url}",
        f"Samples per port: {samples}",
        "",
        "Grade definitions:",
        "  A healthy   — ok>=80%, transport<=20%, 429<=20%, p95<=30s",
        "  B degraded  — ok>=40%, proxy up, max_consecutive_transport<5",
        "  C unstable  — some OK but poor reliability",
        "  F dead      — no OK, or proxy refused, or all transport failures",
        "",
        "Summary:",
    ]
    total = len(reports)
    for g in (Grade.A, Grade.B, Grade.C, Grade.F):
        n = len(by_grade[g])
        pct = 100.0 * n / total if total else 0.0
        lines.append(f"  {g.value}: {n:4d}  ({pct:5.1f}%)")
    lines.append(f"  Total: {total}")
    healthy = len(by_grade[Grade.A])
    lines.append(f"  Production-ready (A only): {healthy}")
    lines.append("")
    lines.append("Per port (sorted by grade F→C→B→A, then port number):")
    order = {Grade.F: 0, Grade.C: 1, Grade.B: 2, Grade.A: 3}
    sorted_reports = sorted(reports, key=lambda r: (order[r.grade], r.port))
    for r in sorted_reports:
        lat = ""
        if r.latency_p50_s is not None:
            lat = f" p50={r.latency_p50_s:.2f}s"
            if r.latency_p95_s is not None:
                lat += f" p95={r.latency_p95_s:.2f}s"
        lines.append(
            f"  {r.port:>5}  {r.grade.value}  "
            f"ok={r.ok}/{r.attempts}  transport={r.transport + r.gateway + r.proxy_refused}  "
            f"429={r.rate_limit}  max_consec_transport={r.max_consecutive_transport}"
            f"{lat}"
        )
        if r.grade in (Grade.C, Grade.F):
            for d in r.sample_details:
                lines.append(f"           {d}")
    return "\n".join(lines) + "\n"
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Grade proxy ports for Steam detail fetch.")
    ap.add_argument(
        "--urls",
        type=Path,
        default=URLS_JSON,
        help=f"JSON array of probe URLs (default: {URLS_JSON.name})",
    )
    ap.add_argument(
        "--url-index",
        type=int,
        default=0,
        help="Index into urls JSON (default: 0)",
    )
    ap.add_argument(
        "--samples",
        type=int,
        default=5,
        help="Probes per port on the same URL (default: 5)",
    )
    ap.add_argument(
        "--gap-s",
        type=float,
        default=0.5,
        help="Seconds between samples on one port (default: 0.5)",
    )
    ap.add_argument(
        "--port-workers",
        type=int,
        default=48,
        help="Max ports probed in parallel (default: 48)",
    )
    ap.add_argument(
        "--timeout-s",
        type=float,
        default=25.0,
        help="Per-request timeout (default: 25)",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"Report path (default: test/{DEFAULT_REPORT.name})",
    )
    ap.add_argument(
        "--also-stdout",
        action="store_true",
        help="Print report to stdout as well as writing the file",
    )
    return ap.parse_args()
async def async_main() -> int:
    args = parse_args()
    if args.samples < 1:
        print("ERROR: --samples must be >= 1", file=sys.stderr)
        return 2
    clear_proxy_env()
    try:
        cfg = load_app_config()
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    try:
        probe_url = load_probe_url(args.urls.resolve(), index=args.url_index)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    ports = cfg.crawler.ports
    ignore_tls = cfg.safety.ignore_tls_for_26561
    print(
        f"Probing {len(ports)} ports, {args.samples} samples each, "
        f"workers={args.port_workers}",
        flush=True,
    )
    print(f"URL: {probe_url}", flush=True)
    t0 = time.monotonic()
    reports = await run_probe(
        ports,
        probe_url,
        samples=args.samples,
        gap_s=args.gap_s,
        port_workers=args.port_workers,
        ignore_tls_for_26561=ignore_tls,
        timeout_s=args.timeout_s,
    )
    elapsed = time.monotonic() - t0
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = format_report(
        reports=reports,
        probe_url=probe_url,
        urls_path=args.urls.resolve(),
        url_index=args.url_index,
        samples=args.samples,
        config_path=CONFIG_PATH,
        generated_at=generated_at,
    )
    body += f"Wall time: {elapsed:.1f}s\n"
    out_path = args.output.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    print(f"Report written: {out_path}", flush=True)
    if args.also_stdout:
        print(body, end="")
    n_f = sum(1 for r in reports if r.grade == Grade.F)
    return 1 if n_f == len(reports) else 0
def main() -> None:
    raise SystemExit(asyncio.run(async_main()))
if __name__ == "__main__":
    main()
