"""Compare status/browse_urls.json with output/html/."""

from __future__ import annotations

from pathlib import Path

from browse_html import browse_html_path, browse_page_status


def iter_saved_row_ids(html_root: Path) -> list[int]:
    if not html_root.is_dir():
        return []
    ids: list[int] = []
    for path in html_root.rglob("*.html"):
        if not path.is_file():
            continue
        if path.stem.isdigit():
            ids.append(int(path.stem))
    return sorted(set(ids))


def check_coverage(
    urls: list[str],
    html_root: Path,
    *,
    project_root: Path | None = None,
) -> dict:
    root = project_root or html_root
    n = len(urls)
    by_status: dict[str, list[int]] = {
        "ok": [],
        "missing": [],
        "empty": [],
        "invalid": [],
    }
    gaps: list[dict[str, object]] = []

    for row_id in range(1, n + 1):
        path = browse_html_path(html_root, row_id)
        status = browse_page_status(path)
        by_status[status].append(row_id)
        if status != "ok":
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                rel = str(path)
            gaps.append(
                {
                    "row_id": row_id,
                    "status": status,
                    "path": rel,
                    "url": urls[row_id - 1],
                }
            )

    expected = set(range(1, n + 1))
    on_disk = set(iter_saved_row_ids(html_root))
    extra_ids = sorted(on_disk - expected)

    return {
        "url_count": n,
        "ok": len(by_status["ok"]),
        "missing": by_status["missing"],
        "empty": by_status["empty"],
        "invalid": by_status["invalid"],
        "gaps": gaps,
        "extra_row_ids": extra_ids,
    }


def gap_row_ids(report: dict) -> list[int]:
    return sorted(int(g["row_id"]) for g in report["gaps"])
