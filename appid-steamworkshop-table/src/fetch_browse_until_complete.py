#!/usr/bin/env python3
"""
Fetch all browse pages, then retry gaps until simple_info/html matches browse_urls.json.

1. Full pass (skip valid existing HTML).
2. Gap retry loop (this run only): each missing/invalid row_id may be re-fetched
   up to MAX_RETRIES_PER_RUN times; counters reset every time you start this script.
3. Exit 1 if gaps remain after per-run retry budget is exhausted.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

from browse_coverage import check_coverage, gap_row_ids
from browse_html import browse_html_path, load_browse_urls
from fetch_browse_pages import (
    _DELAY_MAX_S,
    _DELAY_MIN_S,
    _atomic_write_text,
    _url_label,
    fetch_url,
    run_fetch,
)
from base_config import format_egress, http_settings_from_cfg_and_args, load_base_json
from http_tls import add_no_tls_verify_arg, clear_proxy_env
from paths import get_layout, project_root_for_logs

_layout = get_layout()
_MAX_RETRIES_PER_RUN = 5
_DEFAULT_URLS_PATH = _layout.browse_urls_json
_DEFAULT_HTML_ROOT = _layout.simple_html_root
_DEFAULT_GAPS_PATH = _layout.browse_html_gaps_json


def _print_coverage(report: dict, *, header: str) -> None:
    print(header, flush=True)
    print(f"  URLs: {report['url_count']}, OK: {report['ok']}", flush=True)
    n_missing = len(report["missing"])
    n_empty = len(report["empty"])
    n_invalid = len(report["invalid"])
    n_gaps = len(report["gaps"])
    if n_missing:
        print(f"  missing: {n_missing} {report['missing'][:20]}{'…' if n_missing > 20 else ''}", flush=True)
    if n_empty:
        print(f"  empty:   {n_empty} {report['empty']}", flush=True)
    if n_invalid:
        print(f"  invalid: {n_invalid} {report['invalid']}", flush=True)
    if report["extra_row_ids"]:
        print(f"  extra HTML row_ids: {report['extra_row_ids'][:20]}", flush=True)
    if n_gaps == 0 and not report["extra_row_ids"]:
        print("  coverage: complete", flush=True)


def fetch_gap_rows(
    urls: list[str],
    html_root: Path,
    row_ids: list[int],
    attempts: dict[int, int],
    *,
    max_retries_per_run: int,
    verify_tls: bool = True,
    proxy_port: int | None = None,
) -> dict[str, int]:
    """Fetch each row_id once; increment this run's per-row attempt counter."""
    stats = {"attempted": 0, "ok": 0, "fail": 0, "invalid": 0}
    n = len(row_ids)
    for i, row_id in enumerate(row_ids, start=1):
        if row_id < 1 or row_id > len(urls):
            continue
        attempt_no = attempts[row_id] + 1
        url = urls[row_id - 1]
        out_path = browse_html_path(html_root, row_id)
        label = _url_label(url)
        stats["attempted"] += 1
        print(
            f"  [{i}/{n}] row_id={row_id} "
            f"try {attempt_no}/{max_retries_per_run} GET {label}",
            flush=True,
        )
        outcome = fetch_url(url, verify_tls=verify_tls, proxy_port=proxy_port)
        attempts[row_id] = attempt_no
        if outcome.ok and outcome.text is not None:
            _atomic_write_text(out_path, outcome.text)
            stats["ok"] += 1
            print("    -> OK", flush=True)
        elif outcome.invalid:
            stats["invalid"] += 1
            print("    -> invalid (404)", flush=True)
        else:
            stats["fail"] += 1
            print(f"    -> FAIL: {outcome.error}", flush=True)
        if i < n:
            time.sleep(random.uniform(_DELAY_MIN_S, _DELAY_MAX_S))
    return stats


def write_gaps_report(path: Path, report: dict, *, attempts: dict[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gaps = []
    for item in report["gaps"]:
        row_id = int(item["row_id"])
        enriched = dict(item)
        enriched["attempts_this_run"] = attempts.get(row_id, 0)
        gaps.append(enriched)
    payload = {
        "url_count": report["url_count"],
        "gap_count": len(gaps),
        "gaps": gaps,
        "extra_row_ids": report["extra_row_ids"],
        "note": "attempts_this_run 仅指触发本报告的那次程序运行，下次启动会重新计数。",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_until_complete(
    urls: list[str],
    html_root: Path,
    *,
    skip_initial_fetch: bool,
    max_retries_per_run: int,
    gaps_path: Path,
    verify_tls: bool = True,
    proxy_port: int | None = None,
) -> None:
    html_root.mkdir(parents=True, exist_ok=True)
    # Per-row fetch attempts; lives only for this process (not saved to disk).
    attempts: dict[int, int] = defaultdict(int)

    if not skip_initial_fetch:
        print("=== Pass 1: fetch all URLs (skip valid existing) ===", flush=True)
        stats = run_fetch(
            urls,
            html_root,
            force=False,
            limit=None,
            verify_tls=verify_tls,
            proxy_port=proxy_port,
        )
        print(
            f"  fetch done: ok={stats['ok']} skipped={stats['skipped']} "
            f"fail={stats['fail']} invalid={stats['invalid']}",
            flush=True,
        )

    report = check_coverage(urls, html_root, project_root=project_root_for_logs())
    _print_coverage(report, header="=== Coverage after initial fetch ===")
    if not report["gaps"] and not report["extra_row_ids"]:
        return

    print(
        f"\nGap retry budget: up to {max_retries_per_run} fetch(es) per row_id "
        f"(this run only; restarting the program resets counters).",
        flush=True,
    )

    sweep = 0
    while report["gaps"]:
        sweep += 1
        ids = gap_row_ids(report)
        pending = [rid for rid in ids if attempts[rid] < max_retries_per_run]
        if not pending:
            break

        exhausted = [rid for rid in ids if attempts[rid] >= max_retries_per_run]
        if exhausted:
            print(
                f"\n  {len(exhausted)} gap(s) already used all {max_retries_per_run} "
                f"retries this run: {exhausted[:15]}"
                f"{'…' if len(exhausted) > 15 else ''}",
                flush=True,
            )

        print(
            f"\n=== Gap retry sweep {sweep}: "
            f"re-fetch {len(pending)} page(s) ===",
            flush=True,
        )
        fetch_gap_rows(
            urls,
            html_root,
            pending,
            attempts,
            max_retries_per_run=max_retries_per_run,
            verify_tls=verify_tls,
            proxy_port=proxy_port,
        )

        report = check_coverage(urls, html_root, project_root=project_root_for_logs())
        _print_coverage(report, header=f"=== Coverage after gap sweep {sweep} ===")
        if not report["gaps"] and not report["extra_row_ids"]:
            return

    if report["gaps"] or report["extra_row_ids"]:
        write_gaps_report(gaps_path, report, attempts=attempts)
        ids = gap_row_ids(report)
        still_retryable = sum(1 for rid in ids if attempts[rid] < max_retries_per_run)
        print(
            f"\nERROR: {len(ids)} browse page(s) still missing or invalid after this run.",
            file=sys.stderr,
            flush=True,
        )
        if still_retryable == 0:
            print(
                f"  Each remaining gap was fetched {max_retries_per_run} time(s) "
                f"in this run. Run the script again for a fresh retry budget.",
                file=sys.stderr,
                flush=True,
            )
        for item in report["gaps"][:30]:
            rid = int(item["row_id"])
            print(
                f"  row_id={rid} status={item['status']} "
                f"attempts_this_run={attempts.get(rid, 0)}: {item['url']}",
                file=sys.stderr,
                flush=True,
            )
        if len(report["gaps"]) > 30:
            print(f"  … and {len(report['gaps']) - 30} more (see {gaps_path})", file=sys.stderr)
        print(f"Gaps written to {gaps_path}", file=sys.stderr)
        raise SystemExit(1)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Fetch browse HTML and retry gaps until complete. "
            "Retry limits apply per program run only."
        )
    )
    ap.add_argument("--urls", type=Path, default=_DEFAULT_URLS_PATH)
    ap.add_argument("--html-root", type=Path, default=_DEFAULT_HTML_ROOT)
    ap.add_argument(
        "--max-retries-per-run",
        type=int,
        default=_MAX_RETRIES_PER_RUN,
        help=(
            f"Max fetch attempts per gap row_id in one invocation "
            f"(default: {_MAX_RETRIES_PER_RUN}; not cumulative across runs)."
        ),
    )
    ap.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        help=argparse.SUPPRESS,
    )
    ap.add_argument(
        "--skip-initial-fetch",
        action="store_true",
        help="Only run coverage check + gap retries (no full first pass).",
    )
    ap.add_argument("--gaps-report", type=Path, default=_DEFAULT_GAPS_PATH)
    add_no_tls_verify_arg(ap)
    args = ap.parse_args()
    clear_proxy_env()
    cfg = load_base_json()
    proxy_port, verify_tls = http_settings_from_cfg_and_args(args, cfg)

    max_retries = args.max_retries_per_run
    if args.max_rounds is not None:
        max_retries = args.max_rounds
        print(
            "WARN: --max-rounds is deprecated; use --max-retries-per-run.",
            file=sys.stderr,
        )

    if max_retries < 1:
        print("ERROR: --max-retries-per-run must be >= 1", file=sys.stderr)
        raise SystemExit(2)

    urls = load_browse_urls(args.urls)
    print(
        f"urls={len(urls)}, html_root={args.html_root}, "
        f"max_retries_per_run={max_retries}, egress={format_egress(proxy_port)}, "
        f"tls_verify={verify_tls}",
        flush=True,
    )

    try:
        run_until_complete(
            urls,
            args.html_root,
            skip_initial_fetch=args.skip_initial_fetch,
            max_retries_per_run=max_retries,
            gaps_path=args.gaps_report,
            verify_tls=verify_tls,
            proxy_port=proxy_port,
        )
    except SystemExit:
        raise
    print("\nAll browse pages fetched and verified.", flush=True)


if __name__ == "__main__":
    main()
