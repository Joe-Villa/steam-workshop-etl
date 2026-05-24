#!/usr/bin/env python3
"""
Parse Steam workshop browse HTML under simple_info/html into simple_info/name.sqlite.

Three tables (README): mod_detail_url, mod_tag_ranks, mod_browse_info — all keyed by mod_id.
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import re
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from build_browse_urls import NO_SELECTED_TAG

from paths import get_layout

_layout = get_layout()
_DEFAULT_HTML_ROOT = _layout.simple_html_root
_DEFAULT_DB_PATH = _layout.simple_sqlite
_DEFAULT_TAGS_JSON = _layout.current_situation_json

_TABLE_DETAIL = "mod_detail_url"
_TABLE_RANKS = "mod_tag_ranks"
_TABLE_BROWSE = "mod_browse_info"

_RE_PARAMS_GET = re.compile(r"var\s+paramsGET\s*=\s*(\{.*?\})\s*;", re.DOTALL)
_RE_SHOWING = re.compile(
    r'workshopBrowsePagingInfo">Showing\s+([\d,\s\u00a0]+)-([\d,\s\u00a0]+)\s+of',
    re.IGNORECASE,
)
_RE_TAG_LABEL = re.compile(
    r"searchedForTerm[^>]*>[\s\n]*Tag:\s*([^<]+?)\s*<",
    re.IGNORECASE,
)
_RE_FILE_RATING = re.compile(
    r'<img\s+class="fileRating"\s+src="([^"]+)"',
    re.IGNORECASE,
)
_RE_AUTHOR_LINK = re.compile(
    r'<a\s+class="workshop_author_link"[^>]*>([^<]*)</a>',
    re.IGNORECASE,
)
_HOVER_MARKER = "SharedFileBindMouseHover"


@dataclass(frozen=True)
class ModRow:
    mod_id: str
    title: str
    description: str
    rank_in_tag: int
    star_rating: str
    author: str
    detail_url: str


@dataclass
class ModAccum:
    detail_url: str = ""
    title: str = ""
    description: str = ""
    star_rating: str = ""
    author: str = ""
    ranks: dict[str, int] = field(default_factory=dict)


def _parse_int_loose(s: str) -> int:
    t = re.sub(r"[\s,\u00a0']", "", s.strip())
    return int(t) if t else 0


def _items_section(page_html: str) -> str:
    start = page_html.find('class="workshopBrowseItems"')
    if start < 0:
        return ""
    end = page_html.find('class="workshopBrowsePaging"', start)
    if end < 0:
        end = len(page_html)
    return page_html[start:end]


def _read_braced_json(text: str, open_index: int) -> tuple[str | None, int]:
    depth = 0
    in_string = False
    escape = False
    for i in range(open_index, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_index : i + 1], i + 1
    return None, open_index + 1


def _extract_hover_payloads(page_html: str) -> list[dict]:
    section = _items_section(page_html)
    if not section:
        return []
    out: list[dict] = []
    pos = 0
    while True:
        j = section.find(_HOVER_MARKER, pos)
        if j < 0:
            break
        brace = section.find("{", j)
        if brace < 0:
            break
        raw, end = _read_braced_json(section, brace)
        pos = end
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("id"):
            out.append(payload)
    return out


def _params_get(page_html: str) -> dict | None:
    m = _RE_PARAMS_GET.search(page_html)
    if not m:
        return None
    try:
        params = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return params if isinstance(params, dict) else None


def _is_unfiltered_browse(page_html: str) -> bool:
    params = _params_get(page_html)
    if params is None:
        return False
    req = params.get("requiredtags")
    return req is None or (isinstance(req, list) and len(req) == 0)


def _parse_tag_name(page_html: str) -> str | None:
    params = _params_get(page_html)
    if params is not None:
        tags = params.get("requiredtags")
        if isinstance(tags, list) and tags:
            first = tags[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
        if tags is None or (isinstance(tags, list) and len(tags) == 0):
            return NO_SELECTED_TAG
    m2 = _RE_TAG_LABEL.search(page_html)
    if m2:
        return html_module.unescape(m2.group(1)).strip()
    return None


def _parse_showing_start(page_html: str) -> int:
    m = _RE_SHOWING.search(page_html)
    if not m:
        return 1
    return _parse_int_loose(m.group(1)) or 1


def _star_rating_from_src(src: str) -> str:
    name = Path(src.split("?", 1)[0]).name
    if name.endswith(".png"):
        return name[: -len(".png")]
    return name


def _normalize_author(raw: str) -> str:
    """Strip a single leading ``by`` prefix (label), keep names like ``by天草``."""
    s = html_module.unescape(raw).strip()
    if re.match(r"^by\s", s, re.IGNORECASE):
        s = re.sub(r"^by\s*", "", s, count=1, flags=re.IGNORECASE)
    return s.strip()


def _detail_url(mod_id: str) -> str:
    return f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}"


def parse_browse_html(page_html: str) -> tuple[str | None, list[ModRow]]:
    tag = _parse_tag_name(page_html)
    rank_start = _parse_showing_start(page_html)
    section = _items_section(page_html)
    payloads = _extract_hover_payloads(page_html)
    ratings = [_star_rating_from_src(m.group(1)) for m in _RE_FILE_RATING.finditer(section)]
    authors = [_normalize_author(m.group(1)) for m in _RE_AUTHOR_LINK.finditer(section)]

    if len(payloads) != len(ratings) or len(payloads) != len(authors):
        n = min(len(payloads), len(ratings), len(authors))
        payloads = payloads[:n]
        ratings = ratings[:n]
        authors = authors[:n]

    rows: list[ModRow] = []
    for i, payload in enumerate(payloads):
        mod_id = str(payload.get("id", "")).strip()
        if not mod_id.isdigit():
            continue
        title = str(payload.get("title", "")).strip()
        description = str(payload.get("description", "")).strip()
        rows.append(
            ModRow(
                mod_id=mod_id,
                title=title,
                description=description,
                rank_in_tag=rank_start + i,
                star_rating=ratings[i] if i < len(ratings) else "",
                author=authors[i] if i < len(authors) else "",
                detail_url=_detail_url(mod_id),
            )
        )
    return tag, rows


def tag_to_column_name(tag: str) -> str:
    """Column name for a Steam tag label (e.g. ``Alternative History`` → ``Alternative_History``)."""
    slug = re.sub(r"[^\w]+", "_", tag.strip(), flags=re.UNICODE).strip("_")
    if not slug:
        slug = "unknown"
    if slug[0].isdigit():
        slug = f"t_{slug}"
    if len(slug) > 48:
        digest = format(abs(hash(tag)) % (36**6), "x")[:8]
        slug = f"{slug[:39]}_{digest}"
    return slug


def no_selected_tag_column() -> str:
    return tag_to_column_name(NO_SELECTED_TAG)


def _ensure_rank_column(
    tag_columns: list[str], tag_column_set: set[str], col: str
) -> None:
    if col in tag_column_set:
        return
    primary = no_selected_tag_column()
    if col == primary:
        tag_columns.insert(0, col)
    else:
        tag_columns.append(col)
    tag_column_set.add(col)


def load_tag_columns_from_json(tags_json: Path | None) -> list[str]:
    """``No_selected_tag`` is always the first rank column (immediately after ``mod_id``)."""
    primary = no_selected_tag_column()
    out: list[str] = [primary]
    seen: set[str] = {primary}

    if tags_json is None or not tags_json.is_file():
        return out
    try:
        data = json.loads(tags_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"WARN: cannot read tags json {tags_json}: {e}", file=sys.stderr)
        return out
    tags = data.get("tags")
    if not isinstance(tags, list):
        return out
    for item in tags:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        col = tag_to_column_name(name)
        if col not in seen:
            seen.add(col)
            out.append(col)
    return out


def iter_html_files(html_root: Path) -> Iterator[Path]:
    if not html_root.is_dir():
        return
    files = [p for p in html_root.rglob("*.html") if p.is_file()]
    yield from sorted(files, key=lambda p: int(p.stem) if p.stem.isdigit() else p.stem)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _write_tables(
    conn: sqlite3.Connection,
    mods: dict[str, ModAccum],
    tag_columns: list[str],
) -> None:
    conn.execute(
        f"""
        CREATE TABLE "{_TABLE_DETAIL}" (
            mod_id TEXT PRIMARY KEY,
            detail_url TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE "{_TABLE_BROWSE}" (
            mod_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            star_rating TEXT NOT NULL,
            author TEXT NOT NULL
        )
        """
    )

    rank_cols_sql = ",\n            ".join(
        f"{_quote_ident(c)} INTEGER" for c in tag_columns
    )
    conn.execute(
        f"""
        CREATE TABLE "{_TABLE_RANKS}" (
            mod_id TEXT PRIMARY KEY,
            {rank_cols_sql}
        )
        """
    )

    detail_rows = [(mid, m.detail_url) for mid, m in mods.items()]
    browse_rows = [
        (mid, m.title, m.description, m.star_rating, m.author) for mid, m in mods.items()
    ]
    rank_placeholders = ", ".join("?" * (1 + len(tag_columns)))
    rank_rows = [
        (mid,) + tuple(m.ranks.get(c) for c in tag_columns) for mid, m in mods.items()
    ]

    conn.executemany(
        f'INSERT INTO "{_TABLE_DETAIL}" (mod_id, detail_url) VALUES (?, ?)',
        detail_rows,
    )
    conn.executemany(
        f"""
        INSERT INTO "{_TABLE_BROWSE}"
            (mod_id, title, description, star_rating, author)
        VALUES (?, ?, ?, ?, ?)
        """,
        browse_rows,
    )
    rank_cols_quoted = ", ".join(_quote_ident(c) for c in tag_columns)
    conn.executemany(
        f"""
        INSERT INTO "{_TABLE_RANKS}" (mod_id, {rank_cols_quoted})
        VALUES ({rank_placeholders})
        """,
        rank_rows,
    )


def build_database(
    html_root: Path,
    db_path: Path,
    *,
    tags_json: Path | None = _DEFAULT_TAGS_JSON,
    verbose: bool = False,
) -> dict[str, int]:
    html_root = html_root.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.is_file():
        db_path.unlink()

    tag_columns: list[str] = load_tag_columns_from_json(tags_json)
    tag_column_set = set(tag_columns)

    stats = {
        "html_files": 0,
        "html_skipped": 0,
        "mods_parsed": 0,
        "unique_mods": 0,
        "tag_columns": 0,
        "rank_overwrites": 0,
    }
    mods: dict[str, ModAccum] = {}

    for path in iter_html_files(html_root):
        stats["html_files"] += 1
        try:
            page = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"WARN: cannot read {path}: {e}", file=sys.stderr)
            stats["html_skipped"] += 1
            continue

        tag, rows = parse_browse_html(page)
        if not tag or not rows:
            stats["html_skipped"] += 1
            if verbose:
                print(f"skip (no items): {path}", file=sys.stderr)
            continue

        col = tag_to_column_name(tag)
        _ensure_rank_column(tag_columns, tag_column_set, col)

        for row in rows:
            acc = mods.get(row.mod_id)
            if acc is None:
                mods[row.mod_id] = ModAccum(
                    detail_url=row.detail_url,
                    title=row.title,
                    description=row.description,
                    star_rating=row.star_rating,
                    author=row.author,
                    ranks={col: row.rank_in_tag},
                )
            else:
                if col in acc.ranks and acc.ranks[col] != row.rank_in_tag:
                    stats["rank_overwrites"] += 1
                acc.ranks[col] = row.rank_in_tag
            stats["mods_parsed"] += 1

        if verbose and stats["html_files"] % 100 == 0:
            print(f"... {stats['html_files']} html files", file=sys.stderr)

    stats["unique_mods"] = len(mods)
    stats["tag_columns"] = len(tag_columns)

    conn = sqlite3.connect(str(db_path))
    try:
        _write_tables(conn, mods, tag_columns)
        conn.commit()
    finally:
        conn.close()

    return stats


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build simple_info/name.sqlite from simple_info/html browse pages."
    )
    ap.add_argument(
        "--html-root",
        type=Path,
        default=_DEFAULT_HTML_ROOT,
        help=f"Directory of crawled HTML (default: {_DEFAULT_HTML_ROOT}).",
    )
    ap.add_argument(
        "--db",
        type=Path,
        default=_DEFAULT_DB_PATH,
        help=f"Output SQLite path (default: {_DEFAULT_DB_PATH}).",
    )
    ap.add_argument(
        "--tags-json",
        type=Path,
        default=_DEFAULT_TAGS_JSON,
        help=f"Tag column order from workshop main JSON (default: {_DEFAULT_TAGS_JSON}).",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not args.html_root.is_dir():
        print(f"ERROR: html root not found: {args.html_root}", file=sys.stderr)
        raise SystemExit(2)

    stats = build_database(
        args.html_root,
        args.db,
        tags_json=args.tags_json,
        verbose=args.verbose,
    )
    print(
        f"Wrote {args.db}\n"
        f"  tables: {_TABLE_DETAIL}, {_TABLE_RANKS}, {_TABLE_BROWSE}\n"
        f"  html files: {stats['html_files']} (skipped {stats['html_skipped']})\n"
        f"  mod rows applied: {stats['mods_parsed']}\n"
        f"  unique mods: {stats['unique_mods']}\n"
        f"  tag columns: {stats['tag_columns']}\n"
        f"  duplicate mod_id rank updates: {stats['rank_overwrites']}"
    )


if __name__ == "__main__":
    main()
