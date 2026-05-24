#!/usr/bin/env python3
"""
Compare simple_info/browse_urls.json with simple_info/html/.

Each URL at index i (0-based) must have a valid browse page at row_id = i + 1.
Exit 1 if any expected page is not ``ok``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from browse_coverage import check_coverage
from browse_html import load_browse_urls
from paths import get_layout, project_root_for_logs

_layout = get_layout()


def _print_report(report: dict, *, verbose: bool) -> None:
    n = report["url_count"]
    print(f"URLs in browse_urls.json: {n}")
    print(f"OK HTML pages:            {report['ok']}")
    print(f"Missing:                  {len(report['missing'])}")
    print(f"Empty:                    {len(report['empty'])}")
    print(f"Invalid:                  {len(report['invalid'])}")
    print(f"Extra HTML (row_id > {n} or unexpected): {len(report['extra_row_ids'])}")

    def _show_ids(label: str, ids: list[int], limit: int = 40) -> None:
        if not ids:
            return
        print(f"\n{label} ({len(ids)}):")
        if verbose or len(ids) <= limit:
            for i in ids:
                print(f"  {i}")
        else:
            head = ", ".join(str(i) for i in ids[:limit])
            print(f"  {head}, … (+{len(ids) - limit} more, use --verbose)")

    _show_ids("Missing row_id", report["missing"])
    _show_ids("Empty row_id", report["empty"])
    _show_ids("Invalid row_id", report["invalid"])
    _show_ids("Extra row_id", report["extra_row_ids"])

    if verbose and report["gaps"]:
        print("\nGap details (row_id, status, url):")
        for item in report["gaps"]:
            print(f"  [{item['row_id']}] {item['status']}: {item['url']}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Check simple_info/html/ matches simple_info/browse_urls.json."
    )
    ap.add_argument(
        "--urls",
        type=Path,
        default=_layout.browse_urls_json,
    )
    ap.add_argument(
        "--html-root",
        type=Path,
        default=_layout.simple_html_root,
    )
    ap.add_argument(
        "--write-report",
        type=Path,
        default=None,
        help="Write gap list JSON (default: simple_info/browse_html_gaps.json if any gap).",
    )
    ap.add_argument(
        "--no-write-report",
        action="store_true",
        help="Do not write simple_info/browse_html_gaps.json.",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    urls = load_browse_urls(args.urls)
    report = check_coverage(urls, args.html_root, project_root=project_root_for_logs())
    _print_report(report, verbose=args.verbose)

    bad = len(report["gaps"])
    if bad > 0 or report["extra_row_ids"]:
        if not args.no_write_report:
            out = args.write_report or _layout.browse_html_gaps_json
            out.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "url_count": report["url_count"],
                "gap_count": bad,
                "gaps": report["gaps"],
                "extra_row_ids": report["extra_row_ids"],
            }
            out.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"\nWrote {out.relative_to(project_root_for_logs())}")
        raise SystemExit(1)

    print("\nAll expected browse pages are present.")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
