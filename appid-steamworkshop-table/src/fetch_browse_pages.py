#!/usr/bin/env python3
"""
Fetch Steam workshop browse pages from simple_info/browse_urls.json into simple_info/html/.

Stdlib only, direct connection. Browse pages are lightly rate-limited; delay is minimal.
Resume: valid existing HTML is skipped unless --force.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from pathlib import Path

from browse_html import (
    PAGE_OK_MARKER,
    browse_html_path,
    browse_page_ok,
    load_browse_urls,
)
from base_config import format_egress, http_settings_from_cfg_and_args, load_base_json
from http_tls import add_no_tls_verify_arg, build_https_opener, clear_proxy_env

from paths import get_layout, project_root_for_logs

_layout = get_layout()
_DEFAULT_URLS_PATH = _layout.browse_urls_json
_DEFAULT_HTML_ROOT = _layout.simple_html_root

_DELAY_MIN_S = 0.02
_DELAY_MAX_S = 0.08
_TIMEOUT_S = 60.0
_MAX_ROUNDS = 16

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def _url_label(url: str, *, max_len: int = 96) -> str:
    text = re.sub(r"^https?://", "", url, count=1, flags=re.IGNORECASE)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _retry_after_seconds(
    headers: urllib.response.addinfourl | urllib.error.HTTPError, hit_count: int
) -> float:
    retry_after = (headers.headers.get("Retry-After") or "").strip()
    if retry_after.isdigit():
        return float(max(1, int(retry_after)))
    if retry_after:
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return float(max(1.0, (retry_at - datetime.now(timezone.utc)).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            pass
    return float(min(120.0, 30.0 * (2 ** max(0, hit_count - 1))))


@dataclass(frozen=True)
class FetchOutcome:
    ok: bool
    text: str | None = None
    status_code: int | None = None
    error: str | None = None
    invalid: bool = False


def fetch_url(
    url: str, *, verify_tls: bool = True, proxy_port: int | None = None
) -> FetchOutcome:
    headers = {
        "User-Agent": _UA,
        "Accept": _ACCEPT,
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }
    req = urllib.request.Request(url, headers=headers)
    opener = build_https_opener(verify_tls=verify_tls, proxy_port=proxy_port)

    transport_streak = 0
    hits_429 = 0
    for round_idx in range(1, _MAX_ROUNDS + 1):
        try:
            with opener.open(req, timeout=_TIMEOUT_S) as resp:
                code = int(getattr(resp, "status", 200) or 200)
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            code = int(e.code)
            if code == 429:
                hits_429 += 1
                wait = _retry_after_seconds(e, hits_429)
                print(
                    f"    HTTP 429, retry in {wait:.1f}s "
                    f"(round {round_idx}/{_MAX_ROUNDS})",
                    flush=True,
                )
                time.sleep(wait)
                continue
            if code == 404:
                return FetchOutcome(
                    ok=False, status_code=404, error="HTTP 404", invalid=True
                )
            return FetchOutcome(
                ok=False, status_code=code, error=f"HTTP {code} (no retry)"
            )
        except urllib.error.URLError as e:
            transport_streak += 1
            wait = min(60.0, 1.0 * (2 ** max(0, transport_streak - 1)))
            print(
                f"    transport: {e.reason!r}, retry in {wait:.1f}s",
                flush=True,
            )
            time.sleep(wait)
            continue
        except TimeoutError:
            transport_streak += 1
            wait = min(60.0, 1.0 * (2 ** max(0, transport_streak - 1)))
            print(f"    timeout, retry in {wait:.1f}s", flush=True)
            time.sleep(wait)
            continue

        transport_streak = 0
        if code != 200:
            return FetchOutcome(ok=False, status_code=code, error=f"HTTP {code}")
        if PAGE_OK_MARKER not in body:
            return FetchOutcome(
                ok=False,
                status_code=code,
                error="HTTP 200 but page missing workshopBrowseItems",
            )
        return FetchOutcome(ok=True, text=body, status_code=code)

    return FetchOutcome(ok=False, error=f"gave up after {_MAX_ROUNDS} rounds")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def run_fetch(
    urls: list[str],
    html_root: Path,
    *,
    force: bool,
    limit: int | None,
    verify_tls: bool = True,
    proxy_port: int | None = None,
) -> dict[str, int]:
    stats = {"total": 0, "skipped": 0, "ok": 0, "fail": 0, "invalid": 0}
    todo = urls[:limit] if limit is not None else urls
    stats["total"] = len(todo)

    for row_id, url in enumerate(todo, start=1):
        out_path = browse_html_path(html_root, row_id)
        label = _url_label(url)

        if not force and browse_page_ok(out_path):
            stats["skipped"] += 1
            if row_id % 100 == 0 or row_id == len(todo):
                print(f"[{row_id}/{len(todo)}] skip {label}", flush=True)
            continue

        print(f"[{row_id}/{len(todo)}] GET {label}", flush=True)
        outcome = fetch_url(url, verify_tls=verify_tls, proxy_port=proxy_port)

        if outcome.ok and outcome.text is not None:
            _atomic_write_text(out_path, outcome.text)
            stats["ok"] += 1
            print(f"  -> OK {out_path.relative_to(project_root_for_logs())}", flush=True)
        elif outcome.invalid:
            stats["invalid"] += 1
            print("  -> invalid (404)", flush=True)
        else:
            stats["fail"] += 1
            print(f"  -> FAIL: {outcome.error}", flush=True)

        if row_id < len(todo):
            time.sleep(random.uniform(_DELAY_MIN_S, _DELAY_MAX_S))

    return stats


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Fetch browse pages from simple_info/browse_urls.json into simple_info/html/."
    )
    ap.add_argument(
        "--urls",
        type=Path,
        default=_DEFAULT_URLS_PATH,
        help=f"JSON URL list (default: {_DEFAULT_URLS_PATH}).",
    )
    ap.add_argument(
        "--html-root",
        type=Path,
        default=_DEFAULT_HTML_ROOT,
        help=f"HTML output directory (default: {_DEFAULT_HTML_ROOT}).",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-download even when a valid HTML file already exists.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only fetch the first N URLs (for testing).",
    )
    add_no_tls_verify_arg(ap)
    args = ap.parse_args()
    clear_proxy_env()
    cfg = load_base_json()
    proxy_port, verify_tls = http_settings_from_cfg_and_args(args, cfg)

    urls = load_browse_urls(args.urls)
    args.html_root.mkdir(parents=True, exist_ok=True)

    print(
        f"urls={len(urls)}, html_root={args.html_root}, "
        f"delay=[{_DELAY_MIN_S}, {_DELAY_MAX_S}], skip_existing={not args.force}, "
        f"egress={format_egress(proxy_port)}, tls_verify={verify_tls}",
        flush=True,
    )

    stats = run_fetch(
        urls,
        args.html_root,
        force=args.force,
        limit=args.limit,
        verify_tls=verify_tls,
        proxy_port=proxy_port,
    )
    print(
        f"Done. total={stats['total']} ok={stats['ok']} skipped={stats['skipped']} "
        f"fail={stats['fail']} invalid={stats['invalid']}",
        flush=True,
    )
    if stats["fail"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
