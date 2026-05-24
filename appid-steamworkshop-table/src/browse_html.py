"""Shared paths and validation for browse-page HTML (row_id ↔ browse_urls.json index)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PAGE_OK_MARKER = "workshopBrowseItems"


def browse_html_path(html_root: Path, row_id: int) -> Path:
    """``output/html/{first_two_digits}/{row_id}.html`` (row_id is 1-based)."""
    key = str(row_id)
    bucket = key[:2] if len(key) >= 2 else key.zfill(2)
    return html_root / bucket / f"{row_id}.html"


def load_browse_urls(path: Path) -> list[str]:
    if not path.is_file():
        print(f"ERROR: URL list not found: {path}", file=sys.stderr)
        raise SystemExit(2)
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        print(f"ERROR: {path} must be a JSON array of URLs.", file=sys.stderr)
        raise SystemExit(2)
    urls: list[str] = []
    for i, item in enumerate(data):
        if not isinstance(item, str) or not item.strip():
            print(f"ERROR: urls[{i}] must be a non-empty string.", file=sys.stderr)
            raise SystemExit(2)
        urls.append(item.strip())
    if not urls:
        print(f"ERROR: {path} is empty.", file=sys.stderr)
        raise SystemExit(2)
    return urls


def browse_page_status(path: Path) -> str:
    """
    ``missing`` — file absent
    ``empty`` — zero bytes
    ``invalid`` — present but not a workshop browse listing page
    ``ok`` — usable browse HTML
    """
    if not path.is_file():
        return "missing"
    try:
        if path.stat().st_size <= 0:
            return "empty"
        head = path.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        return "missing"
    if PAGE_OK_MARKER not in head:
        return "invalid"
    return "ok"


def browse_page_ok(path: Path) -> bool:
    return browse_page_status(path) == "ok"
