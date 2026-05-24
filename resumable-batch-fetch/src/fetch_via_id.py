from __future__ import annotations

import asyncio
import os
import random
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from app_config import PROJECT_ROOT, load_app_config
from crawler import AsyncCrawler, CrawlResult
from mod_fetch_db import TABLE_NAME, connect_db, rowcount, table_exists

INIT_SCRIPT = "src/init_mod_fetch_sqlite.py"


@dataclass(frozen=True)
class FetchSettings:
    max_fail: int
    sqlite_path: Path
    html_root: Path
    log_path: Path | None
    use_mod_html_buckets: bool
    wait_min: float
    wait_max: float
    success_gap_s: float
    concurrency: int


def load_fetch_settings() -> FetchSettings:
    cfg = load_app_config()
    return FetchSettings(
        max_fail=cfg.crawler.max_fail,
        sqlite_path=cfg.io.sqlite_path,
        html_root=cfg.io.html_root,
        log_path=cfg.io.log_path,
        use_mod_html_buckets=cfg.io.use_mod_html_buckets,
        wait_min=cfg.crawler.wait_min,
        wait_max=cfg.crawler.wait_max,
        success_gap_s=cfg.crawler.success_gap_s,
        concurrency=cfg.crawler.concurrency,
    )


def _load_crawler(success_gap_s: float) -> AsyncCrawler:
    cfg = load_app_config()
    from crawler import EgressHealthSettings

    health = EgressHealthSettings(
        transport_fail_to_degraded=cfg.egress_health.transport_fail_to_degraded,
        transport_fail_to_quarantine=cfg.egress_health.transport_fail_to_quarantine,
        quarantine_s=cfg.egress_health.quarantine_s,
    )
    return AsyncCrawler.from_port_numbers(
        cfg.crawler.ports,
        success_gap_s=success_gap_s,
        ignore_tls_for_26561=cfg.safety.ignore_tls_for_26561,
        egress_health=health,
    )


def require_sqlite(sqlite_path: Path) -> sqlite3.Connection:
    if not sqlite_path.is_file():
        print(
            f"SQLite database not found: {sqlite_path}\n"
            f"Initialize first, e.g.:\n"
            f"  python {PROJECT_ROOT / INIT_SCRIPT}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    conn = connect_db(sqlite_path)
    if not table_exists(conn):
        print(
            f"Table `{TABLE_NAME}` missing in {sqlite_path}\n"
            f"Initialize first, e.g.:\n"
            f"  python {PROJECT_ROOT / INIT_SCRIPT}",
            file=sys.stderr,
        )
        conn.close()
        raise SystemExit(2)
    if rowcount(conn, f"SELECT COUNT(*) FROM {TABLE_NAME}") == 0:
        print(
            f"Table `{TABLE_NAME}` is empty in {sqlite_path}\n"
            f"Initialize first, e.g.:\n"
            f"  python {PROJECT_ROOT / INIT_SCRIPT}",
            file=sys.stderr,
        )
        conn.close()
        raise SystemExit(2)
    return conn


def _bucket_dir(row_id: int) -> str:
    return str(row_id)[:2]


def html_path(html_root: Path, row_id: int, *, use_mod_html_buckets: bool) -> Path:
    """
    ``use_mod_html_buckets=False``: ``{html_root}/{row_id}.html``
    ``use_mod_html_buckets=True``: ``{html_root}/{row_id[:2]}/{row_id}.html``
    """
    if use_mod_html_buckets:
        out_dir = html_root / _bucket_dir(row_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"{row_id}.html"
    html_root.mkdir(parents=True, exist_ok=True)
    return html_root / f"{row_id}.html"


def iter_saved_html_paths(
    html_root: Path, *, use_mod_html_buckets: bool
) -> Iterator[Path]:
    """Non-empty ``<row_id>.html`` (flat or under two-char subdirs)."""
    if not html_root.is_dir():
        return
    if use_mod_html_buckets:
        for p in html_root.rglob("*.html"):
            if p.parent == html_root:
                continue
            if p.stat().st_size <= 0:
                continue
            if p.stem.isdigit():
                yield p
        return
    for p in html_root.glob("*.html"):
        if p.stat().st_size <= 0:
            continue
        if p.stem.isdigit():
            yield p


def count_saved_html_files(html_root: Path, *, use_mod_html_buckets: bool) -> int:
    return sum(1 for _ in iter_saved_html_paths(html_root, use_mod_html_buckets=use_mod_html_buckets))


def sync_success_from_disk(
    conn: sqlite3.Connection,
    html_root: Path,
    *,
    use_mod_html_buckets: bool,
) -> int:
    """Existing HTML on disk marks that row success (resume / reruns)."""
    updated = 0
    for p in iter_saved_html_paths(html_root, use_mod_html_buckets=use_mod_html_buckets):
        row_id = int(p.stem)
        cur = conn.execute(
            f"UPDATE {TABLE_NAME} SET status = 'success' "
            f"WHERE id = ? AND status IN ('pending', 'fail')",
            (row_id,),
        )
        updated += cur.rowcount
    if updated:
        conn.commit()
    return updated


def assert_html_count_matches_success_if_resuming(
    sqlite_preexisted: bool,
    conn: sqlite3.Connection,
    html_root: Path,
    *,
    use_mod_html_buckets: bool,
) -> None:
    if not sqlite_preexisted:
        return
    n_html = count_saved_html_files(html_root, use_mod_html_buckets=use_mod_html_buckets)
    n_ok = rowcount(conn, f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE status = 'success'")
    if n_html == n_ok or n_html == n_ok + 1:
        return
    print(
        "FATAL: inconsistent HTML vs sqlite ``success`` counts.\n"
        f"  html_root: {html_root}\n"
        f"  saved <row_id>.html files (non-empty): {n_html}\n"
        f"  rows with status=success: {n_ok}\n"
        "  Expected: n_html == n_ok OR n_html == n_ok + 1 "
        "(one extra file means HTML written but DB not updated yet).\n"
        "  Refusing to start; repair disk or DB (or remove stray HTML / fix statuses) and retry.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def phase_label(conn: sqlite3.Connection, max_fail: int) -> str:
    pending = rowcount(conn, f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE status = 'pending'")
    if pending > 0:
        return f"full (pending={pending})"
    exhausted = rowcount(
        conn,
        f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE status = 'fail' AND retry_count < ?",
        (max_fail,),
    )
    if exhausted == 0:
        return "terminal"
    m = conn.execute(
        f"SELECT MIN(retry_count) FROM {TABLE_NAME} WHERE status = 'fail' AND retry_count < ?",
        (max_fail,),
    ).fetchone()[0]
    return f"gap-fill (min_fail_retry={m}, todo={exhausted})"


def pick_next_row(
    conn: sqlite3.Connection,
    max_fail: int,
    *,
    exclude_ids: set[int] | None = None,
) -> tuple[int, str] | None:
    exclude = exclude_ids or set()
    if exclude:
        placeholders = ",".join("?" * len(exclude))
        not_in = f"AND id NOT IN ({placeholders})"
        exclude_args: tuple[int, ...] = tuple(sorted(exclude))
    else:
        not_in = ""
        exclude_args = ()

    pending = conn.execute(
        f"SELECT id, url FROM {TABLE_NAME} "
        f"WHERE status = 'pending' {not_in} ORDER BY id LIMIT 1",
        exclude_args,
    ).fetchone()
    if pending:
        return int(pending[0]), str(pending[1])
    row = conn.execute(
        f"""
        SELECT id, url FROM {TABLE_NAME}
        WHERE status = 'fail' AND retry_count < ? {not_in}
        ORDER BY retry_count ASC, id ASC
        LIMIT 1
        """,
        (max_fail, *exclude_args),
    ).fetchone()
    if row:
        return int(row[0]), str(row[1])
    return None


def apply_crawl_result(
    conn: sqlite3.Connection,
    row_id: int,
    result: CrawlResult,
    html_root: Path,
    phase_is_full: bool,
    *,
    use_mod_html_buckets: bool,
) -> None:
    if result.ok and result.text is not None:
        out = html_path(html_root, row_id, use_mod_html_buckets=use_mod_html_buckets)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(result.text, encoding="utf-8")
        os.replace(tmp, out)
        conn.execute(
            f"UPDATE {TABLE_NAME} SET status = 'success' WHERE id = ?",
            (row_id,),
        )
        conn.commit()
        return

    code = result.status_code
    if code == 404:
        conn.execute(
            f"UPDATE {TABLE_NAME} SET status = 'invalid' WHERE id = ?",
            (row_id,),
        )
        conn.commit()
        return

    if phase_is_full:
        conn.execute(
            f"UPDATE {TABLE_NAME} SET status = 'fail', retry_count = 0 WHERE id = ?",
            (row_id,),
        )
    else:
        conn.execute(
            f"UPDATE {TABLE_NAME} SET status = 'fail', retry_count = retry_count + 1 WHERE id = ?",
            (row_id,),
        )
    conn.commit()


def terminal(conn: sqlite3.Connection, max_fail: int) -> bool:
    if rowcount(conn, f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE status = 'pending'") > 0:
        return False
    bad = rowcount(
        conn,
        f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE status = 'fail' AND retry_count < ?",
        (max_fail,),
    )
    return bad == 0


def print_summary(conn: sqlite3.Connection, max_fail: int) -> None:
    invalid_ids = [
        int(r[0])
        for r in conn.execute(
            f"SELECT id FROM {TABLE_NAME} WHERE status = 'invalid' ORDER BY id"
        ).fetchall()
    ]
    exhausted_fail = [
        int(r[0])
        for r in conn.execute(
            f"SELECT id FROM {TABLE_NAME} WHERE status = 'fail' AND retry_count >= ? ORDER BY id",
            (max_fail,),
        ).fetchall()
    ]
    print("Done. Human review recommended for the following row ids.")
    if exhausted_fail:
        print(f"Fail after {max_fail} retries ({len(exhausted_fail)}):")
        print(", ".join(str(i) for i in exhausted_fail))
    else:
        print("Fail after max retries: (none)")
    if invalid_ids:
        print(f"Invalid / not found ({len(invalid_ids)}):")
        print(", ".join(str(i) for i in invalid_ids))
    else:
        print("Invalid: (none)")


@dataclass
class _InflightJob:
    row_id: int
    url: str
    phase_is_full: bool
    prev_status: str | None
    phase: str


async def _crawl_one(crawler: AsyncCrawler, url: str) -> CrawlResult:
    return await crawler.crawl(url)


def _log_row_result(row_id: int, result: CrawlResult) -> None:
    if result.ok:
        print(f"  -> OK row_id={row_id}", flush=True)
    elif result.egress_exhausted:
        print(
            f"  -> no healthy egress row_id={row_id} (row unchanged)",
            flush=True,
        )
    elif result.status_code == 404:
        print(f"  -> 404 invalid row_id={row_id}", flush=True)
    else:
        detail = result.error or f"HTTP {result.status_code}"
        print(f"  -> fail row_id={row_id}: {detail}", flush=True)


_PROGRESS_LOG_EVERY = 1000


class _ProgressHeartbeat:
    """Append current local time to ``log_path`` every ~N completed page fetches."""

    def __init__(self, log_path: Path | None, *, every: int = _PROGRESS_LOG_EVERY) -> None:
        self._log_path = log_path
        self._every = max(1, every)
        self._done = 0
        self._next_at = self._every

    def on_page_done(self) -> None:
        self._done += 1
        if self._done < self._next_at:
            return
        self._write_now()
        while self._done >= self._next_at:
            self._next_at += self._every

    def _write_now(self) -> None:
        if self._log_path is None:
            return
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(f"{stamp}\n")


def _counts_transport_like_failure(result: CrawlResult, prev_status: str | None) -> bool:
    if result.ok or result.egress_exhausted or result.status_code == 404:
        return False
    return prev_status in ("pending", "fail")


async def run_fetch_loop(
    conn: sqlite3.Connection,
    settings: FetchSettings,
    crawler: AsyncCrawler,
) -> bool:
    """
    Concurrent async fetch. Returns False if stopped early due to consecutive failures.
    """
    consecutive_transport_like_failures = 0
    stop_after = int(os.environ.get("CONSECUTIVE_FETCH_STOP_THRESHOLD", "50"))
    in_flight: dict[asyncio.Task[CrawlResult], _InflightJob] = {}
    in_flight_ids: set[int] = set()
    stop_early = False
    progress = _ProgressHeartbeat(settings.log_path)

    while not terminal(conn, settings.max_fail) and not stop_early:
        while len(in_flight) < settings.concurrency:
            picked = pick_next_row(
                conn, settings.max_fail, exclude_ids=in_flight_ids
            )
            if picked is None:
                break
            row_id, url = picked
            in_flight_ids.add(row_id)

            phase = phase_label(conn, settings.max_fail)
            phase_is_full = (
                rowcount(conn, f"SELECT COUNT(*) FROM {TABLE_NAME} WHERE status = 'pending'")
                > 0
            )
            before = conn.execute(
                f"SELECT status, retry_count FROM {TABLE_NAME} WHERE id = ?",
                (row_id,),
            ).fetchone()
            prev_status = before["status"] if before else None

            print(f"[{phase}] GET row_id={row_id}", flush=True)
            task = asyncio.create_task(_crawl_one(crawler, url))
            in_flight[task] = _InflightJob(
                row_id=row_id,
                url=url,
                phase_is_full=phase_is_full,
                prev_status=prev_status,
                phase=phase,
            )

        if not in_flight:
            break

        done, _pending = await asyncio.wait(
            in_flight.keys(), return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            job = in_flight.pop(task)
            in_flight_ids.discard(job.row_id)
            try:
                result = task.result()
            except Exception as e:
                result = CrawlResult(
                    ok=False,
                    text=None,
                    error=f"{type(e).__name__}: {e}",
                    status_code=None,
                )

            _log_row_result(job.row_id, result)
            progress.on_page_done()
            apply_crawl_result(
                conn,
                job.row_id,
                result,
                settings.html_root,
                job.phase_is_full,
                use_mod_html_buckets=settings.use_mod_html_buckets,
            )

            if _counts_transport_like_failure(result, job.prev_status):
                consecutive_transport_like_failures += 1
                if consecutive_transport_like_failures >= stop_after:
                    print(
                        f"Stopping: {consecutive_transport_like_failures} consecutive "
                        f"non-success crawls (threshold {stop_after}). Check proxy/network."
                    )
                    stop_early = True
            else:
                consecutive_transport_like_failures = 0

            if result.ok:
                await asyncio.sleep(random.uniform(settings.wait_min, settings.wait_max))

    if in_flight and stop_early:
        for task in in_flight:
            task.cancel()
        await asyncio.gather(*in_flight.keys(), return_exceptions=True)

    return not stop_early


async def main_async() -> None:
    settings = load_fetch_settings()

    sqlite_preexisted = settings.sqlite_path.is_file()
    crawler = _load_crawler(settings.success_gap_s)
    conn = require_sqlite(settings.sqlite_path)
    try:
        n_rows = rowcount(conn, f"SELECT COUNT(*) FROM {TABLE_NAME}")
        sync_success_from_disk(
            conn,
            settings.html_root,
            use_mod_html_buckets=settings.use_mod_html_buckets,
        )
        assert_html_count_matches_success_if_resuming(
            sqlite_preexisted,
            conn,
            settings.html_root,
            use_mod_html_buckets=settings.use_mod_html_buckets,
        )

        buckets_mode = "subdirs" if settings.use_mod_html_buckets else "flat"
        print(
            f"rows={n_rows}, sqlite={settings.sqlite_path}, html={settings.html_root}, "
            f"layout={buckets_mode}, max_fail={settings.max_fail}, "
            f"concurrency={settings.concurrency}, "
            f"wait=[{settings.wait_min}, {settings.wait_max}], "
            f"success_gap_s={settings.success_gap_s}"
        )

        await run_fetch_loop(conn, settings, crawler)
        print_summary(conn, settings.max_fail)
    finally:
        await crawler.aclose()
        conn.close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
