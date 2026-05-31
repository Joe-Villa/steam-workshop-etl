#!/usr/bin/env python3
"""
简略信息表：仅接受一个 paths JSON 参数。

用法:
  python3 main.py pipeline/collect_simple_info.json
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

_MOD_ROOT = Path(__file__).resolve().parent
_SRC = _MOD_ROOT / "src"
_TOOL = _MOD_ROOT / "tool"
for _p in (_SRC, _TOOL):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from browse_coverage import iter_saved_row_ids
from build_browse_urls import build_browse_urls_from_workshop_main
from build_mods_sqlite import build_database
from export_detail_urls_json import export_detail_urls
from export_sqlite_to_csv_excel import export_database
from fetch_browse_until_complete import run_until_complete
from base_config import (
    format_egress,
    http_settings_from_cfg_and_args,
    load_appid_from_cfg,
    resolve_run,
)
from http_tls import clear_proxy_env
from paths import project_root_for_logs, set_active_layout
from pipeline_manifest import StageManifest
from stage_entry import run_stage_main
from workshop_main_status import fetch_workshop_main, parse_workshop_main_html

_MAX_RETRIES_PER_RUN = 5


def html_root_has_any_pages(html_root: Path) -> bool:
    return bool(iter_saved_row_ids(html_root))


def status_appid_matches(status_path: Path, appid: int) -> bool:
    if not status_path.is_file():
        return False
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    raw = data.get("APPID")
    if isinstance(raw, bool) or not isinstance(raw, int):
        if isinstance(raw, str) and raw.isdigit():
            return int(raw) == appid
        return False
    return int(raw) == appid


def should_resume_crawl(html_root: Path, current_situation: Path, appid: int) -> bool:
    if not html_root_has_any_pages(html_root):
        return False
    if not status_appid_matches(current_situation, appid):
        print(
            "simple_info/html/ has files but current_situation.json APPID differs; "
            "treating as full restart.",
            flush=True,
        )
        return False
    return True


def clear_crawl_and_build_outputs(layout) -> None:
    html_root = layout.simple_html_root
    if html_root.exists():
        shutil.rmtree(html_root)
    html_root.mkdir(parents=True, exist_ok=True)
    for path in (layout.simple_sqlite, layout.simple_xlsx, layout.detail_urls_json):
        if path.is_file():
            path.unlink()


def run_workshop_main(
    appid: int, *, verify_tls: bool, proxy_port: int | None, output_path: Path
) -> None:
    print(f"=== Workshop home (APPID {appid}) ===", flush=True)
    page, final_url = fetch_workshop_main(
        appid, verify_tls=verify_tls, proxy_port=proxy_port
    )
    data = parse_workshop_main_html(page, source_url=final_url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    root = project_root_for_logs()
    print(f"  wrote {output_path.relative_to(root)}", flush=True)


def run_build_browse_urls(current_situation: Path, browse_urls_path: Path) -> list[str]:
    print("=== Browse URL list ===", flush=True)
    data = json.loads(current_situation.read_text(encoding="utf-8"))
    urls = build_browse_urls_from_workshop_main(data)
    browse_urls_path.parent.mkdir(parents=True, exist_ok=True)
    browse_urls_path.write_text(
        json.dumps(urls, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    root = project_root_for_logs()
    print(
        f"  wrote {browse_urls_path.relative_to(root)} ({len(urls)} URLs)",
        flush=True,
    )
    return urls


def run_fetch(
    urls: list[str],
    *,
    verify_tls: bool,
    proxy_port: int | None,
    layout,
) -> None:
    print("=== Fetch browse HTML ===", flush=True)
    run_until_complete(
        urls,
        layout.simple_html_root,
        skip_initial_fetch=False,
        max_retries_per_run=_MAX_RETRIES_PER_RUN,
        gaps_path=layout.browse_html_gaps_json,
        verify_tls=verify_tls,
        proxy_port=proxy_port,
    )


def run_build_sqlite(layout) -> None:
    print("=== Build SQLite ===", flush=True)
    stats = build_database(
        layout.simple_html_root,
        layout.simple_sqlite,
        tags_json=layout.current_situation_json,
    )
    root = project_root_for_logs()
    print(
        f"  wrote {layout.simple_sqlite.relative_to(root)} "
        f"({stats['unique_mods']} mods, {stats['html_files']} html files)",
        flush=True,
    )


def run_export_excel(layout) -> None:
    print("=== Export Excel ===", flush=True)
    export_database(layout.simple_sqlite, layout.simple_xlsx)
    root = project_root_for_logs()
    print(f"  wrote {layout.simple_xlsx.relative_to(root)}", flush=True)


def run_export_detail_urls(layout) -> None:
    print("=== Export detail URLs ===", flush=True)
    n = export_detail_urls(layout.simple_sqlite, layout.detail_urls_json)
    root = project_root_for_logs()
    print(f"  wrote {layout.detail_urls_json.relative_to(root)} ({n} URLs)", flush=True)


def _run_stage(spec: StageManifest, cfg: dict) -> None:
    clear_proxy_env()
    _, layout, cfg = resolve_run(spec.manifest_path)
    set_active_layout(layout)
    tls_args = SimpleNamespace(no_tls_verify=bool(cfg.get("no_tls_verify")))
    proxy_port, verify_tls = http_settings_from_cfg_and_args(tls_args, cfg)
    appid = load_appid_from_cfg(cfg)
    skip_fetch = bool(spec.options.get("skip_fetch"))

    print(
        f"appid-steamworkshop-table — output={spec.output_root}, "
        f"APPID {appid}, egress={format_egress(proxy_port)}, tls_verify={verify_tls}",
        flush=True,
    )

    if skip_fetch:
        if not html_root_has_any_pages(layout.simple_html_root):
            print("ERROR: skip_fetch but html/ is empty.", file=sys.stderr)
            raise SystemExit(2)
        n = len(iter_saved_row_ids(layout.simple_html_root))
        print(f"skip_fetch: using existing HTML ({n} file(s)).", flush=True)
    elif should_resume_crawl(
        layout.simple_html_root, layout.current_situation_json, appid
    ):
        n = len(iter_saved_row_ids(layout.simple_html_root))
        print(f"Found existing HTML ({n} file(s)); resuming crawl.", flush=True)
    else:
        print("No usable HTML cache; full restart.", flush=True)
        clear_crawl_and_build_outputs(layout)

    run_workshop_main(
        appid,
        verify_tls=verify_tls,
        proxy_port=proxy_port,
        output_path=layout.current_situation_json,
    )
    urls = run_build_browse_urls(
        layout.current_situation_json, layout.browse_urls_json
    )
    if not skip_fetch:
        run_fetch(urls, verify_tls=verify_tls, proxy_port=proxy_port, layout=layout)
    run_build_sqlite(layout)
    run_export_excel(layout)
    run_export_detail_urls(layout)

    root = project_root_for_logs()
    print("\nDone.", flush=True)
    print(f"  {layout.simple_sqlite.relative_to(root)}", flush=True)


def main() -> None:
    run_stage_main(_run_stage)


if __name__ == "__main__":
    main()
