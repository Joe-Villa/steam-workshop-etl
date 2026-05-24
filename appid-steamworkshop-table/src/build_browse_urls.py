#!/usr/bin/env python3
"""
Read simple_info/current_situation.json and emit browse URLs to simple_info/browse_urls.json
(one per official tag × page, plus unfiltered ``No_selected_tag`` pages).

Per-tag page count: ceil(tag_count / 30), capped at 1667 (Steam browse URLs only
serve pages 1..1667 per tag/sort — not an arbitrary limit; pages beyond are empty).

The synthetic tag ``No_selected_tag`` adds browse URLs without ``requiredtags`` (Steam hub
“Items” list by subscribers), up to 50,000 mods (1667 pages).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from urllib.parse import urlencode

from paths import get_layout

_layout = get_layout()
_DEFAULT_IN_PATH = _layout.current_situation_json
_DEFAULT_OUT_PATH = _layout.browse_urls_json

_BROWSE_BASE = "https://steamcommunity.com/workshop/browse/"
_ITEMS_PER_PAGE = 30
_MAX_PAGE = 1667
_MAX_UNTAGGED_ITEMS = 50_000
_BROWSE_SORT = "totaluniquesubscribers"
_BROWSE_SECTION = "readytouseitems"

# Fixed pseudo-tag for unfiltered browse (mod_tag_ranks column No_selected_tag, 2nd after mod_id).
NO_SELECTED_TAG = "No_selected_tag"


def _load_workshop_main(path: Path) -> dict:
    if not path.is_file():
        print(f"ERROR: Input not found: {path}", file=sys.stderr)
        sys.exit(2)
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        print(f"ERROR: {path} must be a JSON object.", file=sys.stderr)
        sys.exit(2)
    return data


def pages_for_tag_count(count: int, *, max_page: int = _MAX_PAGE) -> int:
    if count <= 0:
        return 0
    return min(max_page, math.ceil(count / _ITEMS_PER_PAGE))


def pages_for_untagged_browse(workshop_item_count: int) -> int:
    """Pages for hub-wide browse (no ``requiredtags``), capped at 50k mods."""
    if workshop_item_count <= 0:
        return 0
    capped_items = min(workshop_item_count, _MAX_UNTAGGED_ITEMS)
    return pages_for_tag_count(capped_items)


def _browse_query_base(appid: int, page: int) -> dict[str, str | int]:
    if page < 1:
        raise ValueError("page must be >= 1")
    return {
        "appid": appid,
        "browsesort": _BROWSE_SORT,
        "section": _BROWSE_SECTION,
        "actualsort": _BROWSE_SORT,
        "p": page,
    }


def build_browse_url(appid: int, tag_name: str, page: int) -> str:
    query = dict(_browse_query_base(appid, page))
    query["requiredtags[0]"] = tag_name
    return f"{_BROWSE_BASE}?{urlencode(query)}"


def build_untagged_browse_url(appid: int, page: int) -> str:
    return f"{_BROWSE_BASE}?{urlencode(_browse_query_base(appid, page))}"


def build_browse_urls_from_workshop_main(data: dict) -> list[str]:
    raw_appid = data.get("APPID")
    if isinstance(raw_appid, bool) or not isinstance(raw_appid, int) or raw_appid <= 0:
        if isinstance(raw_appid, str) and raw_appid.isdigit():
            appid = int(raw_appid)
        else:
            raise ValueError("'APPID' must be a positive integer.")
    else:
        appid = int(raw_appid)

    tags = data.get("tags")
    if not isinstance(tags, list) or not tags:
        raise ValueError("'tags' must be a non-empty array.")

    urls: list[str] = []
    for i, tag in enumerate(tags):
        if not isinstance(tag, dict):
            raise ValueError(f"tags[{i}] must be an object.")
        name = tag.get("name")
        count = tag.get("count")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"tags[{i}].name must be a non-empty string.")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"tags[{i}].count must be a non-negative integer.")

        n_pages = pages_for_tag_count(count)
        for p in range(1, n_pages + 1):
            urls.append(build_browse_url(appid, name.strip(), p))

    raw_total = data.get("workshop_item_count")
    if isinstance(raw_total, bool) or not isinstance(raw_total, int) or raw_total < 0:
        if isinstance(raw_total, str) and raw_total.isdigit():
            workshop_total = int(raw_total)
        else:
            workshop_total = 0
    else:
        workshop_total = int(raw_total)

    n_untagged = pages_for_untagged_browse(workshop_total)
    for p in range(1, n_untagged + 1):
        urls.append(build_untagged_browse_url(appid, p))

    return urls


def main() -> None:
    ap = argparse.ArgumentParser(
        description="workshop_main.json → browse URL list (JSON array)."
    )
    ap.add_argument(
        "--in",
        dest="in_path",
        type=Path,
        default=_DEFAULT_IN_PATH,
        help=f"current_situation.json path (default: {_DEFAULT_IN_PATH}).",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=_DEFAULT_OUT_PATH,
        help=f"Output URL list path (default: {_DEFAULT_OUT_PATH}).",
    )
    args = ap.parse_args()

    data = _load_workshop_main(args.in_path)
    try:
        urls = build_browse_urls_from_workshop_main(data)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        json.dump(urls, f, ensure_ascii=False, indent=2)
        f.write("\n")

    tags = data["tags"]
    capped = sum(
        1
        for t in tags
        if isinstance(t, dict)
        and isinstance(t.get("count"), int)
        and pages_for_tag_count(t["count"]) >= _MAX_PAGE
    )
    workshop_total = data.get("workshop_item_count")
    if not isinstance(workshop_total, int):
        workshop_total = 0
    n_untagged = pages_for_untagged_browse(workshop_total)
    print(
        f"Wrote {args.out} ({len(urls)} URLs: {len(tags)} official tag(s), "
        f"+ {n_untagged} page(s) for '{NO_SELECTED_TAG}' "
        f"(hub total {workshop_total}, cap {_MAX_UNTAGGED_ITEMS} mods), "
        f"{capped} official tag(s) hit page cap {_MAX_PAGE})."
    )
    if urls:
        print(f"Sample (first tag): {urls[0]}")
        if n_untagged:
            print(f"Sample ({NO_SELECTED_TAG}): {urls[-n_untagged]}")


if __name__ == "__main__":
    main()
