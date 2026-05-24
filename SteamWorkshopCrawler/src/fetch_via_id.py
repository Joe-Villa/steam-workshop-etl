from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from crawler import Crawler, CrawlResult

_SWC_ROOT = Path(__file__).resolve().parent.parent
_CFG_DIR = _SWC_ROOT / "cfg"
_STATUS_DIR = _SWC_ROOT / "status"
_FETCH_CFG_PATH = _CFG_DIR / "fetch_via_modid.json"
_BASE_CFG_PATH = _CFG_DIR / "base.json"

DEFAULT_ID_COLLECTION = _STATUS_DIR / "id_collection_state.json"
DEFAULT_SQLITE = _STATUS_DIR / "mod_fetch.sqlite"
DEFAULT_MODS_ROOT = _SWC_ROOT / "data" / "mods"


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _load_fetch_cfg() -> dict:
    if not _FETCH_CFG_PATH.is_file():
        return {"max_fail": 5}
    cfg = _load_json(_FETCH_CFG_PATH)
    if not isinstance(cfg, dict):
        raise ValueError("fetch_via_modid.json must be a JSON object")
    return cfg


def _load_appid_and_crawler() -> tuple[int, Crawler]:
    base = _load_json(_BASE_CFG_PATH)
    appid = base.get("APPID")
    if not isinstance(appid, int) or appid <= 0:
        raise ValueError("base.json must contain a positive integer APPID")
    crawler = Crawler.from_base_json(_BASE_CFG_PATH)
    return appid, crawler


def _resolve_under_root(p: str | None, default: Path) -> Path:
    if not p:
        return default
    path = Path(p)
    if not path.is_absolute():
        path = _SWC_ROOT / path
    return path


def load_id_list(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"ID list not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).isdigit()]
    if isinstance(raw, dict) and isinstance(raw.get("ids"), list):
        return [str(x) for x in raw["ids"] if str(x).isdigit()]
    raise ValueError(
        "id_collection_state.json must be a JSON array of string IDs, "
        "or an object with key \"ids\" (array)."
    )


def mod_html_path(mods_root: Path, mod_id: str) -> Path:
    bucket = mods_root / mod_id[:2]
    bucket.mkdir(parents=True, exist_ok=True)
    return bucket / f"mod_{mod_id}.html"


def connect_db(sqlite_path: Path) -> sqlite3.Connection:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS mod_fetch (
            id TEXT PRIMARY KEY NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('pending', 'success', 'fail', 'invalid')),
            retry_count INTEGER NOT NULL DEFAULT 0
                CHECK (retry_count >= 0)
        )
        """
    )
    conn.commit()


def rowcount(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    cur = conn.execute(sql, params)
    return int(cur.fetchone()[0])


def ensure_populated(
    conn: sqlite3.Connection,
    ids: list[str],
) -> None:
    n = rowcount(conn, "SELECT COUNT(*) FROM mod_fetch")
    if n > 0:
        return
    conn.executemany(
        "INSERT INTO mod_fetch (id, status, retry_count) VALUES (?, 'pending', 0)",
        [(i,) for i in ids],
    )
    conn.commit()


def iter_saved_mod_html_paths(mods_root: Path) -> Iterator[Path]:
    """Non-empty ``mod_<digits>.html`` under ``mods_root`` (same rules as disk sync)."""
    if not mods_root.is_dir():
        return
    for p in mods_root.rglob("mod_*.html"):
        if p.stat().st_size <= 0:
            continue
        name = p.name
        if not name.startswith("mod_") or not name.endswith(".html"):
            continue
        mod_id = name[len("mod_") : -len(".html")]
        if not mod_id.isdigit():
            continue
        yield p


def count_saved_mod_html_files(mods_root: Path) -> int:
    return sum(1 for _ in iter_saved_mod_html_paths(mods_root))


def sync_success_from_disk(conn: sqlite3.Connection, mods_root: Path) -> int:
    """Rows with existing HTML are marked success (recovery / reruns)."""
    updated = 0
    for p in iter_saved_mod_html_paths(mods_root):
        mod_id = p.name[len("mod_") : -len(".html")]
        cur = conn.execute(
            "UPDATE mod_fetch SET status = 'success' "
            "WHERE id = ? AND status IN ('pending', 'fail')",
            (mod_id,),
        )
        updated += cur.rowcount
    if updated:
        conn.commit()
    return updated


def assert_html_count_matches_success_if_resuming(
    sqlite_preexisted: bool,
    conn: sqlite3.Connection,
    mods_root: Path,
) -> None:
    """
    On resume (DB file already existed), disk HTML count must match ``success`` rows,
    or exceed it by exactly one (HTML committed, DB update not yet committed).
    """
    if not sqlite_preexisted:
        return
    n_html = count_saved_mod_html_files(mods_root)
    n_ok = rowcount(conn, "SELECT COUNT(*) FROM mod_fetch WHERE status = 'success'")
    if n_html == n_ok or n_html == n_ok + 1:
        return
    print(
        "FATAL: inconsistent HTML vs sqlite ``success`` counts.\n"
        f"  mods_root: {mods_root}\n"
        f"  saved mod_*.html files (non-empty, digit id): {n_html}\n"
        f"  rows with status=success: {n_ok}\n"
        "  Expected: n_html == n_ok OR n_html == n_ok + 1 "
        "(one extra file means HTML written but DB not updated yet).\n"
        "  Refusing to start; repair disk or DB (or remove stray HTML / fix statuses) and retry.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def phase_label(conn: sqlite3.Connection, max_fail: int) -> str:
    pending = rowcount(conn, "SELECT COUNT(*) FROM mod_fetch WHERE status = 'pending'")
    if pending > 0:
        return f"full (pending={pending})"
    exhausted = rowcount(
        conn,
        "SELECT COUNT(*) FROM mod_fetch WHERE status = 'fail' AND retry_count < ?",
        (max_fail,),
    )
    if exhausted == 0:
        return "terminal"
    m = conn.execute(
        "SELECT MIN(retry_count) FROM mod_fetch WHERE status = 'fail' AND retry_count < ?",
        (max_fail,),
    ).fetchone()[0]
    return f"gap-fill (min_fail_retry={m}, todo={exhausted})"


def pick_next_id(conn: sqlite3.Connection, max_fail: int) -> str | None:
    pending = conn.execute(
        "SELECT id FROM mod_fetch WHERE status = 'pending' ORDER BY id LIMIT 1"
    ).fetchone()
    if pending:
        return str(pending[0])
    row = conn.execute(
        """
        SELECT id FROM mod_fetch
        WHERE status = 'fail' AND retry_count < ?
        ORDER BY retry_count ASC, id ASC
        LIMIT 1
        """,
        (max_fail,),
    ).fetchone()
    if row:
        return str(row[0])
    return None


def apply_crawl_result(
    conn: sqlite3.Connection,
    mod_id: str,
    result: CrawlResult,
    mods_root: Path,
    phase_is_full: bool,
) -> None:
    if result.ok and result.text is not None:
        out = mod_html_path(mods_root, mod_id)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(result.text, encoding="utf-8")
        os.replace(tmp, out)
        conn.execute(
            "UPDATE mod_fetch SET status = 'success' WHERE id = ?",
            (mod_id,),
        )
        conn.commit()
        return

    code = result.status_code
    if code == 404:
        conn.execute(
            "UPDATE mod_fetch SET status = 'invalid' WHERE id = ?",
            (mod_id,),
        )
        conn.commit()
        return

    if phase_is_full:
        conn.execute(
            "UPDATE mod_fetch SET status = 'fail', retry_count = 0 WHERE id = ?",
            (mod_id,),
        )
    else:
        conn.execute(
            "UPDATE mod_fetch SET status = 'fail', retry_count = retry_count + 1 WHERE id = ?",
            (mod_id,),
        )
    conn.commit()


def terminal(conn: sqlite3.Connection, max_fail: int) -> bool:
    if rowcount(conn, "SELECT COUNT(*) FROM mod_fetch WHERE status = 'pending'") > 0:
        return False
    bad = rowcount(
        conn,
        "SELECT COUNT(*) FROM mod_fetch WHERE status = 'fail' AND retry_count < ?",
        (max_fail,),
    )
    return bad == 0


def print_summary(conn: sqlite3.Connection, max_fail: int) -> None:
    invalid_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM mod_fetch WHERE status = 'invalid' ORDER BY id"
        ).fetchall()
    ]
    exhausted_fail = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM mod_fetch WHERE status = 'fail' AND retry_count >= ? ORDER BY id",
            (max_fail,),
        ).fetchall()
    ]
    print("Done. Human review recommended for the following.")
    if exhausted_fail:
        print(f"Fail after {max_fail} retries ({len(exhausted_fail)}):")
        print(", ".join(exhausted_fail))
    else:
        print("Fail after max retries: (none)")
    if invalid_ids:
        print(f"Invalid / not found ({len(invalid_ids)}):")
        print(", ".join(invalid_ids))
    else:
        print("Invalid: (none)")


def main() -> None:
    cfg = _load_fetch_cfg()
    max_fail = int(cfg.get("max_fail", 5))
    if max_fail < 1:
        raise ValueError("max_fail must be >= 1")

    id_path = _resolve_under_root(
        cfg.get("id_collection_path") if isinstance(cfg.get("id_collection_path"), str) else None,
        DEFAULT_ID_COLLECTION,
    )
    sqlite_path = _resolve_under_root(
        cfg.get("sqlite_path") if isinstance(cfg.get("sqlite_path"), str) else None,
        DEFAULT_SQLITE,
    )
    mods_root = _resolve_under_root(
        cfg.get("mods_dir") if isinstance(cfg.get("mods_dir"), str) else None,
        DEFAULT_MODS_ROOT,
    )

    ids = load_id_list(id_path)
    if not ids:
        raise SystemExit("No numeric IDs in id list.")

    sqlite_preexisted = sqlite_path.is_file()

    appid, crawler = _load_appid_and_crawler()
    conn = connect_db(sqlite_path)
    init_schema(conn)
    ensure_populated(conn, ids)
    sync_success_from_disk(conn, mods_root)
    assert_html_count_matches_success_if_resuming(sqlite_preexisted, conn, mods_root)

    print(
        f"APPID={appid}, ids={len(ids)}, sqlite={sqlite_path}, "
        f"mods={mods_root}, max_fail={max_fail}"
    )

    consecutive_transport_like_failures = 0
    stop_after = int(os.environ.get("CONSECUTIVE_FETCH_STOP_THRESHOLD", "50"))

    while not terminal(conn, max_fail):
        mod_id = pick_next_id(conn, max_fail)
        if mod_id is None:
            break
        phase = phase_label(conn, max_fail)
        phase_is_full = rowcount(conn, "SELECT COUNT(*) FROM mod_fetch WHERE status = 'pending'") > 0
        url = (
            f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}&appid={appid}"
        )
        print(f"[{phase}] GET id={mod_id}")
        result = crawler.crawl(url)

        before = conn.execute(
            "SELECT status, retry_count FROM mod_fetch WHERE id = ?",
            (mod_id,),
        ).fetchone()
        prev_status = before["status"] if before else None

        apply_crawl_result(conn, mod_id, result, mods_root, phase_is_full)

        if result.ok:
            consecutive_transport_like_failures = 0
        elif result.status_code == 404:
            consecutive_transport_like_failures = 0
        else:
            if prev_status == "pending" or prev_status == "fail":
                consecutive_transport_like_failures += 1
            if consecutive_transport_like_failures >= stop_after:
                print(
                    f"Stopping: {consecutive_transport_like_failures} consecutive "
                    f"non-success crawls (threshold {stop_after}). Check proxy/network."
                )
                break

    print_summary(conn, max_fail)
    conn.close()


if __name__ == "__main__":
    main()
