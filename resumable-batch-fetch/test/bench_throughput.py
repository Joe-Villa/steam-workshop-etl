#!/usr/bin/env python3
"""Measure sustained crawl throughput using cfg/crawler.json settings."""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app_config import load_app_config
from crawler import AsyncCrawler, CrawlResult, EgressHealthSettings

URLS_PATH = Path(__file__).resolve().parent / "urls.json"
DEFAULT_DURATION_S = 60.0
TARGET_PAGES_PER_S = 4.0


def _load_crawler() -> AsyncCrawler:
    cfg = load_app_config()
    health = EgressHealthSettings(
        transport_fail_to_degraded=cfg.egress_health.transport_fail_to_degraded,
        transport_fail_to_quarantine=cfg.egress_health.transport_fail_to_quarantine,
        quarantine_s=cfg.egress_health.quarantine_s,
    )
    return AsyncCrawler.from_port_numbers(
        cfg.crawler.ports,
        success_gap_s=cfg.crawler.success_gap_s,
        ignore_tls_for_26561=cfg.safety.ignore_tls_for_26561,
        egress_health=health,
    )


async def _run_bench(duration_s: float) -> None:
    cfg = load_app_config()
    urls = json.loads(URLS_PATH.read_text(encoding="utf-8"))
    if not urls:
        raise SystemExit(f"no urls in {URLS_PATH}")

    crawler = _load_crawler()
    concurrency = cfg.crawler.concurrency
    wait_min = cfg.crawler.wait_min
    wait_max = cfg.crawler.wait_max
    success_gap_s = cfg.crawler.success_gap_s
    clash_ports = [p for p in cfg.crawler.ports if p not in (-1, 26561)]

    print(
        f"bench duration={duration_s:.0f}s concurrency={concurrency} "
        f"wait=[{wait_min}, {wait_max}] success_gap_s={success_gap_s} "
        f"ports={len(clash_ports)} target>={TARGET_PAGES_PER_S:.1f} pages/s"
    )

    started = time.monotonic()
    deadline = started + duration_s
    url_idx = 0
    ok = 0
    fail = 0
    in_flight: dict[asyncio.Task[CrawlResult], None] = {}

    async def next_url() -> str:
        nonlocal url_idx
        url = urls[url_idx % len(urls)]
        url_idx += 1
        return url

    try:
        while time.monotonic() < deadline or in_flight:
            while len(in_flight) < concurrency and time.monotonic() < deadline:
                task = asyncio.create_task(crawler.crawl(await next_url()))
                in_flight[task] = None

            if not in_flight:
                break

            done, _pending = await asyncio.wait(
                in_flight.keys(), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                in_flight.pop(task)
                result = task.result()
                if result.ok:
                    ok += 1
                    if time.monotonic() < deadline:
                        await asyncio.sleep(random.uniform(wait_min, wait_max))
                else:
                    fail += 1
    finally:
        await crawler.aclose()

    elapsed = time.monotonic() - started
    rate = ok / elapsed if elapsed > 0 else 0.0
    theoretical = len(clash_ports) / success_gap_s if success_gap_s > 0 else 0.0
    passed = rate >= TARGET_PAGES_PER_S

    print("")
    print(f"elapsed: {elapsed:.1f}s")
    print(f"success: {ok}  fail: {fail}  total_attempts: {ok + fail}")
    print(f"throughput: {rate:.2f} pages/s")
    print(f"theoretical cap (ports/success_gap_s): {theoretical:.2f} pages/s")
    print(f"target (>={TARGET_PAGES_PER_S:.1f} pages/s): {'PASS' if passed else 'FAIL'}")
    raise SystemExit(0 if passed else 1)


def main() -> None:
    duration_s = DEFAULT_DURATION_S
    if len(sys.argv) > 1:
        duration_s = float(sys.argv[1])
    asyncio.run(_run_bench(duration_s))


if __name__ == "__main__":
    main()
