#!/usr/bin/env python3
"""
Load ``Ports`` and probe URLs from cfg/config.json.

For each port entry, probe every URL in ``test_url`` in parallel (one result line
per target; each request times out after ``_REQUEST_TIMEOUT_S`` seconds).

``-1`` means direct (no HTTP proxy); other integers are HTTP proxies on 127.0.0.1:<port>.
Port ``26561`` skips TLS verification only when cfg ``safety.26561_ignore_tsl`` is true.

Proxy-related environment variables are cleared at startup and never used; each probe
uses only the port entry from cfg (explicit ``ProxyHandler``, not ``getproxies()``).
"""

from __future__ import annotations

import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

_here = Path(__file__).resolve().parent.parent / "src"
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from app_config import (
    STEAMPP_LOCAL_PROXY_PORT,
    bootstrap_config_from_argv,
    get_config_path,
    load_app_config,
)
from egress import clear_proxy_env

_USER_AGENT = "HtmlBatchRunner/1.0 (checkport)"
_REQUEST_TIMEOUT_S = 30


@dataclass(frozen=True)
class TargetResult:
    label: str
    url: str
    detail: str


def _probe_targets_from_urls(urls: list[str]) -> list[tuple[str, str]]:
    if not urls:
        print('ERROR: config.json "test_url" must be a non-empty array.', file=sys.stderr)
        sys.exit(2)
    out: list[tuple[str, str]] = []
    for i, url in enumerate(urls):
        host = urlparse(url).netloc
        label = host if host else f"target_{i}"
        out.append((label, url))
    return out


def _build_opener(
    proxy_port: int | None,
    *,
    ignore_tls_for_26561: bool,
) -> urllib.request.OpenerDirector:
    # ProxyHandler() with no args calls getproxies() (reads env). Always pass an explicit dict.
    if proxy_port is None:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    proxy_url = f"http://127.0.0.1:{proxy_port}"
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}),
    ]
    if ignore_tls_for_26561 and proxy_port == STEAMPP_LOCAL_PROXY_PORT:
        handlers.append(
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
        )
    return urllib.request.build_opener(*handlers)


def _probe_url(target_url: str, port_entry: int, *, ignore_tls_for_26561: bool) -> str:
    proxy_port: int | None = None if port_entry == -1 else port_entry
    opener = _build_opener(proxy_port, ignore_tls_for_26561=ignore_tls_for_26561)
    req = urllib.request.Request(target_url, headers={"User-Agent": _USER_AGENT})
    try:
        with opener.open(req, timeout=_REQUEST_TIMEOUT_S) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            return f"HTTP {status}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return str(e.reason)
    except TimeoutError:
        return "timeout"
    except OSError as e:
        return str(e)


def _label(port_entry: int) -> str:
    if port_entry == -1:
        return "direct (no proxy)"
    return f"HTTP proxy 127.0.0.1:{port_entry}"


def _probe_port_all_targets(
    port_idx: int,
    port_entry: int,
    targets: list[tuple[str, str]],
    *,
    ignore_tls_for_26561: bool,
) -> tuple[int, int, list[TargetResult]]:
    """Probe all targets on one port concurrently; wall time ≈ max per-URL latency (cap ``_REQUEST_TIMEOUT_S``)."""
    results: list[TargetResult] = []
    workers = min(32, max(1, len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        pending = [
            (
                label,
                url,
                ex.submit(
                    _probe_url,
                    url,
                    port_entry,
                    ignore_tls_for_26561=ignore_tls_for_26561,
                ),
            )
            for label, url in targets
        ]
        for label, url, fut in pending:
            try:
                detail = fut.result()
            except Exception as e:
                detail = f"{type(e).__name__}: {e}"
            results.append(TargetResult(label=label, url=url, detail=detail))
    return port_idx, port_entry, results


def main() -> None:
    bootstrap_config_from_argv(sys.argv[1:])
    cleared = clear_proxy_env()
    try:
        cfg = load_app_config()
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    config_path = get_config_path()
    ports = cfg.crawler.ports
    ignore_tls = cfg.safety.ignore_tls_for_26561
    targets = _probe_targets_from_urls(cfg.test_urls)

    print(f"Using Ports from {config_path} ({len(ports)} port entries)")
    print(
        f"safety.26561_ignore_tsl={ignore_tls} "
        f"(port {STEAMPP_LOCAL_PROXY_PORT} TLS verify "
        f"{'off' if ignore_tls else 'on'})"
    )
    print(f"Probe URLs from {config_path} test_url ({len(targets)} targets)")
    print("Environment proxy vars: ignored (cleared for this run)")
    if cleared:
        for line in cleared:
            print(f"  was set: {line}")
    for label, url in targets:
        print(f"  {label}: {url}")
    print("")

    by_idx: dict[int, tuple[int, list[TargetResult]]] = {}
    max_workers = min(32, max(1, len(ports)))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        pending = [
            ex.submit(
                _probe_port_all_targets,
                i,
                p,
                targets,
                ignore_tls_for_26561=ignore_tls,
            )
            for i, p in enumerate(ports)
        ]
        for fut in as_completed(pending):
            port_idx, port_entry, target_results = fut.result()
            by_idx[port_idx] = (port_entry, target_results)
    ordered = [by_idx[i] for i in range(len(ports))]

    for port_entry, target_results in ordered:
        print(f"  port {port_entry:>5}  ({_label(port_entry)})")
        for tr in target_results:
            print(f"      {tr.label}: {tr.detail}")


if __name__ == "__main__":
    main()
