#!/usr/bin/env python3
"""
Fetch https://steamcommunity.com/app/{APPID}/workshop/ (or parse saved HTML),
extract workshop home stats, write simple_info/current_situation.json.

HTTP egress: cfg/base.json ``PORT`` (127.0.0.1) and ``no_tls_verify``; CLI ``--no-tls-verify`` overrides TLS only.
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from base_config import http_settings_from_cfg_and_args, load_appid_from_cfg, load_base_json
from http_tls import add_no_tls_verify_arg, clear_proxy_env, open_url

from paths import get_layout

_DEFAULT_OUTPUT_PATH = get_layout().current_situation_json
_WORKSHOP_URL_TMPL = "https://steamcommunity.com/app/{appid}/workshop/"
_UA = "Mozilla/5.0 (compatible; appid-steamworkshop-table-workshop-main/1.0)"

# English Steam home: "See all 16,244 Items"
_RE_SEE_ALL_ITEMS = re.compile(
    r"See\s+all\s+([\d\s.,']+)\s+Items",
    re.IGNORECASE,
)
_RE_FILTER_BLOCK = re.compile(
    r'<div[^>]*\bclass="[^"]*\bfilterOption\b[^"]*"[^>]*>'
    r'.*?<input[^>]*\bname="requiredtags\[\]"[^>]*\bvalue="([^"]*)"[^>]*>'
    r'.*?<label[^>]*\bclass="tag_label"[^>]*>(.*?)</label>',
    re.IGNORECASE | re.DOTALL,
)
_RE_APPID_INPUT = re.compile(
    r'<input[^>]*\bname="appid"[^>]*\bvalue="(\d+)"',
    re.IGNORECASE,
)
_RE_DATA_COMMUNITY_APPID = re.compile(r'"APPID"\s*:\s*(\d+)', re.IGNORECASE)
_RE_URL_APP = re.compile(
    r"https://steamcommunity\.com/app/(\d+)/workshop/?",
    re.IGNORECASE,
)


def _parse_int_loose(s: str) -> int:
    """Parse counts like '16,244', '16 244', '16.244' (thousands), \"1'234'567\"."""
    t = s.strip()
    for ch in (" ", ",", "'", "\u00a0", "\u202f"):
        t = t.replace(ch, "")
    # If multiple dots, treat last as decimal — Steam uses comma thousands in EN page.
    if t.count(".") > 1 or (t.count(".") == 1 and len(t.split(".")[-1]) == 3):
        t = t.replace(".", "")
    elif t.count(".") == 1:
        left, right = t.split(".", 1)
        if len(right) == 3 and right.isdigit() and left.isdigit():
            t = left + right
    if not t.isdigit():
        raise ValueError(f"not a plain integer: {s!r}")
    return int(t)


def _tag_display_name(label_inner: str) -> str:
    """Strip trailing &nbsp; and tag_count span; keep visible title."""
    s = label_inner
    s = re.split(r"(?i)&nbsp;|\s*<span\s+class=\"tag_count\"", s, maxsplit=1)[0]
    s = html_module.unescape(s)
    s = re.sub(r"<[^>]+>", "", s)
    return " ".join(s.split())


def parse_workshop_main_html(page_html: str, source_url: str | None = None) -> dict:
    appid: int | None = None
    m = _RE_APPID_INPUT.search(page_html)
    if m:
        appid = int(m.group(1))
    if appid is None:
        m2 = _RE_DATA_COMMUNITY_APPID.search(page_html)
        if m2:
            appid = int(m2.group(1))
    if appid is None and source_url:
        m3 = _RE_URL_APP.search(source_url)
        if m3:
            appid = int(m3.group(1))
    if appid is None:
        m4 = _RE_URL_APP.search(page_html)
        if m4:
            appid = int(m4.group(1))
    if appid is None:
        raise ValueError("Could not determine APPID from HTML (no appid input, data-community, or URL).")

    tags: list[dict[str, object]] = []
    for value_q, label_html in _RE_FILTER_BLOCK.findall(page_html):
        raw_name = urllib.parse.unquote_plus(value_q)
        display = _tag_display_name(label_html) or raw_name.replace("+", " ")
        count_m = re.search(
            r'<span[^>]*\bclass="tag_count"[^>]*>\(([^)]+)\)</span>',
            label_html,
            re.IGNORECASE | re.DOTALL,
        )
        if not count_m:
            raise ValueError(f"Tag block missing tag_count: {display!r}")
        count = _parse_int_loose(html_module.unescape(count_m.group(1)))
        tags.append({"name": display, "count": count})

    counts: list[int] = []
    for m in _RE_SEE_ALL_ITEMS.finditer(page_html):
        try:
            counts.append(_parse_int_loose(m.group(1)))
        except ValueError:
            continue
    if not counts:
        raise ValueError(
            'Could not find total item count (expected English text like "See all 16,244 Items").'
        )
    workshop_item_count = max(counts)
    if len(set(counts)) > 1:
        # Should be identical; keep max but caller can inspect raw HTML if needed.
        pass

    url = source_url or _WORKSHOP_URL_TMPL.format(appid=appid)
    return {
        "APPID": appid,
        "tags": tags,
        "tag_count": len(tags),
        "workshop_item_count": workshop_item_count,
        "source_url": url,
    }


def fetch_workshop_main(
    appid: int,
    *,
    verify_tls: bool = True,
    proxy_port: int | None = None,
) -> tuple[str, str]:
    url = _WORKSHOP_URL_TMPL.format(appid=appid)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with open_url(
            req,
            verify_tls=verify_tls,
            proxy_port=proxy_port,
            timeout=60,
        ) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            final = resp.geturl() or url
    except urllib.error.HTTPError as e:
        print(f"ERROR: HTTP {e.code} fetching {url}", file=sys.stderr)
        sys.exit(2)
    except urllib.error.URLError as e:
        print(f"ERROR: Network failure: {e.reason!r}", file=sys.stderr)
        sys.exit(2)
    except TimeoutError:
        print("ERROR: Request timed out.", file=sys.stderr)
        sys.exit(2)
    return body, final


def main() -> None:
    ap = argparse.ArgumentParser(description="Steam app workshop home → output JSON.")
    ap.add_argument("--appid", type=int, default=None, help="Override cfg/base.json APPID.")
    ap.add_argument(
        "--html-file",
        type=Path,
        default=None,
        help="Parse this saved HTML instead of downloading (for tests / offline).",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUTPUT_PATH,
        help=f"Output JSON path (default: {_DEFAULT_OUTPUT_PATH}).",
    )
    add_no_tls_verify_arg(ap)
    args = ap.parse_args()
    clear_proxy_env()
    cfg = load_base_json()
    proxy_port, verify_tls = http_settings_from_cfg_and_args(args, cfg)

    if args.html_file is not None:
        if not args.html_file.is_file():
            print(f"ERROR: File not found: {args.html_file}", file=sys.stderr)
            sys.exit(2)
        page = args.html_file.read_text(encoding="utf-8", errors="replace")
        # Prefer URL from HTML comment when present.
        src = None
        m = re.search(
            r"https://steamcommunity\.com/app/\d+/workshop/?",
            page[:8000],
            re.IGNORECASE,
        )
        if m:
            src = m.group(0).rstrip("/") + "/"
        data = parse_workshop_main_html(page, source_url=src)
    else:
        appid = args.appid if args.appid is not None else load_appid_from_cfg(cfg)
        page, final_url = fetch_workshop_main(
            appid, verify_tls=verify_tls, proxy_port=proxy_port
        )
        data = parse_workshop_main_html(page, source_url=final_url)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Wrote {args.out}")
    print(json.dumps(data, ensure_ascii=False)[:500] + ("…" if len(json.dumps(data)) > 500 else ""))


if __name__ == "__main__":
    main()
