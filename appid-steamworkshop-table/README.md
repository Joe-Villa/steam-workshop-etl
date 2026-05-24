**Languages:** English (this file, default) · [简体中文](README.zh-CN.md)

> **Chinese documentation:** A full [Simplified Chinese README](README.zh-CN.md) is available. Unless you explicitly request Chinese (`zh-CN`), treat **this file** as the canonical project documentation.

# What is this?

An out-of-the-box crawler pipeline.  
Given a Steam **APPID**, it fetches every mod’s browse-page snapshot from that game’s Steam Workshop and builds a data table.

**Input:** the game’s Steam **APPID**.  
**Output:** a summary table of **all** Workshop mods for that game (`output/workshop_mods.sqlite` / `workshop_mods.xlsx`).

The pipeline: resolve the Workshop hub → build a URL list → fetch browse-page HTML → parse into the database. The browse-page fetcher is a **minimal in-repo implementation**; you can swap it for a faster, more robust external downloader (see **Browse-page fetcher interface** below).

## Prerequisites

- **Python 3.10+** (3.9 may work; not tested)
- **pip** (optional dependency for Excel export):

```bash
python3 -m pip install -r requirements.txt
```

Crawling uses only the Python standard library. **openpyxl** is required only for the Excel export step.

## One-shot run

Writes `cfg/base.json`, then runs the full pipeline (crawl + SQLite + Excel). Common `main.py` usage:

1. Specify APPID and run the full pipeline

```bash
python3 main.py 529340
```

2. Use the APPID already set in `cfg/base.json`

```bash
python3 main.py
```

3. Skip network fetch; rebuild sqlite and Excel from existing `output/html/` only

```bash
python3 main.py 529340 --skip-fetch
```

4. When reaching Steam through a local proxy (e.g. Steam++/Watt), set `PORT` and `no_tls_verify` in `cfg/base.json`, or pass `--no-tls-verify` on the CLI

```bash
python3 main.py
```

If `output/html/` already contains some HTML, the crawler resumes; otherwise it clears `html/`, `sqlite`, and `xlsx` and starts from scratch.

Steam Workshop rate-limits **browse pages** lightly (detail pages are stricter). This pipeline uses single-threaded sequential fetching; most time is spent downloading HTML.

**Crawl duration** depends on how many **browse pages** you need (see the table below), not the total mod count on the Workshop hub. On a typical connection with sequential single-machine fetching, budget roughly **1 second per page**; parsing and Excel export take only a few minutes. Re-running `main.py` after an interrupt skips pages that already have HTML.

### Scale reference: Cities: Skylines vs Stellaris

Figures below come from hub HTML / `workshop_main.json` parsed with this repo’s `build_browse_urls.py` rules (`ceil(count/30)` per tag, capped at **1667** pages per tag). Times are **rule-of-thumb** for browse-page crawling; actual runs vary with network and HTTP 429.

| | Cities: Skylines (255710) | Stellaris (281990) |
|---|---------------------------|---------------------|
| Total Workshop mods (hub “See all”) | ~**369k** | ~**36k** |
| Official filter tags | **83** | **25** |
| Tags hitting the 1667-page cap | **4** | **0** |
| **Browse pages to fetch** | ~**12.9k** | ~**2.0k (2028)** |
| **Browse crawl time (rough)** | ~**3–4 hours** | ~**30–60 minutes** |

Notes: Skylines has many mods, but page count is bounded by “many tags summed + a few tags at the 1667 cap”—you do **not** get one page per mod. Stellaris has fewer tags and no capped tags; total mods are ~10% of Skylines but browse pages are ~**one-sixth** of Skylines, so the crawl is shorter. Smaller titles (e.g. EU4, ~1k pages) are usually under an hour.

## Directory layout

A fresh clone has **no** `cfg/`, `input/`, `output/`, or `status/` directories (they are gitignored or simply absent). Running `main.py` creates them as needed:

| Directory | Created by `main.py`? | Purpose |
|-----------|----------------------|---------|
| `cfg/` | Yes (`cfg/base.json` on run) | APPID configuration |
| `status/` | Yes (step 2–3 artifacts) | `browse_urls.json`, `browse_html_gaps.json`, etc. |
| `output/` | Yes (`output/html/`, sqlite, xlsx, hub JSON) | Crawled HTML and deliverables |
| `input/` | **No** | Reserved for optional external inputs; create manually if you use any |

Individual `src/*.py` scripts also call `mkdir` on their output parents when run standalone.

- `cfg/` — configuration (`cfg/base.json`; see below)
- `input/` — external inputs for the pipeline (if any); not produced by this repo
- `output/` — deliverables (crawled HTML, `workshop_main.json`, `workshop_mods.sqlite`, exported Excel)
  - `workshop_main.json` — parsed Workshop hub (current snapshot)
- `status/` — intermediate artifacts (users usually ignore)
  - `browse_urls.json` — browse-page URL list (`build_browse_urls.py`)
  - `browse_html_gaps.json` — produced by tests when HTML and the URL list disagree

## Workflow

You can run steps individually (see `src/*.py` below) or use `main.py`.

1. Set APPID in `cfg/` (or pass `main.py <APPID>` to write it automatically), then run `checkid` to confirm the game is correct.
2. Fetch `https://steamcommunity.com/app/{APPID}/workshop/`, parse the HTML, and write `output/workshop_main.json` with:
   - (1) APPID, e.g. `394360`
   - (2) All tags: tag name and mod count per tag
   - (3) Total tag count
   - (4) Total Workshop mod count
   - (5) Source URL (`https://steamcommunity.com/app/{APPID}/workshop/`)
3. From `workshop_main.json`, build the crawl URL list → `status/browse_urls.json` (JSON string array). Run: `python3 src/build_browse_urls.py`

   Pages per official tag: `ceil(mods_in_tag / 30)`, capped at **1667**. Also appends a synthetic tag **`No_selected_tag`**: hub-wide browse without `requiredtags`, sorted by subscribers, `ceil(min(hub_total, 50000) / 30)` pages (max 1667); in `mod_tag_ranks` that column is second, right after `mod_id`. URLs include `section=readytouseitems`, e.g.:

   `https://steamcommunity.com/workshop/browse/?appid=3450310&browsesort=totaluniquesubscribers&section=readytouseitems&actualsort=totaluniquesubscribers&p=3`

   **Truncation (platform limit, not intentional under-crawl):** Steam Workshop browse URLs sorted by subscribers can only paginate to page **1667** (~**50k** entries, 1667×30). Pages `p=1668` and beyond return no valid list—this is not a missed crawl. If a tag shows more than 50k mods on the hub, entries beyond that cannot be retrieved via browse pages and are dropped; we sort by subscribers, so truncated entries tend to be lower-subscriber mods.

   Example URL (`appid`, `requiredtags[0]`, `p` vary):

   `https://steamcommunity.com/workshop/browse/?appid=529340&requiredtags%5B0%5D=Alternative+History&actualsort=totaluniquesubscribers&browsesort=totaluniquesubscribers&p=1`

4. Fetch browse pages (read `status/browse_urls.json`, write `output/html/`):

   `python3 src/fetch_browse_until_complete.py`

   Full pass first (skip valid existing pages), then retry gaps: each missing `row_id` gets at most **5** extra requests **within that run** (counter resets each run). Persistent failures exit with an error and write `status/browse_html_gaps.json`.

   Single pass only: `python3 src/fetch_browse_pages.py` (`--force` to re-fetch). Check only: `python3 test/check_browse_html_coverage.py`

### Browse-page fetcher interface (pluggable)

Step 4 uses `src/fetch_browse_pages.py` and `src/fetch_browse_until_complete.py`—a **single-threaded, stdlib-only** minimal fetcher for out-of-the-box use. **You can and should** replace them when you need higher speed, proxy rotation, or concurrency (e.g. a nearby `HtmlBatchRunner` or another downloader). Keep the contract below and `build_mods_sqlite.py` needs no changes.

**Upstream (provided by this repo; usually unchanged)**

| Artifact | Path | Description |
|----------|------|-------------|
| URL list | `status/browse_urls.json` | JSON **array of strings**; each entry is one Steam Workshop **browse** URL; index `i` (0-based) → `row_id = i + 1` |
| Generator | `python3 src/build_browse_urls.py` | Expands `output/workshop_main.json` by tag |

**Downstream (your fetcher must produce)**

| Item | Contract |
|------|----------|
| Root | `output/html/` (or place files there before `main.py --skip-fetch`) |
| Path | URL at `row_id` → `output/html/{first two digits of row_id}/{row_id}.html` (`row_id` starts at **1**). Example: `row_id=4` → `output/html/04/4.html`; `row_id=1124` → `output/html/11/1124.html` |
| Content | HTML body (UTF-8 text) containing Steam’s browse list marker `workshopBrowseItems` (same check as `src/browse_html.py`) |
| Completeness | One file per index in `browse_urls.json`; gaps: `python3 test/check_browse_html_coverage.py` |

**How to swap fetchers**

1. Complete steps 1–3 here → `status/browse_urls.json`.  
2. Write `output/html/` with your fetcher per the table above.  
3. Verify: `python3 test/check_browse_html_coverage.py` (fill gaps or retry).  
4. Skip the built-in fetcher:  
   `python3 main.py <APPID> --skip-fetch`  
   or replace `run_fetch()` in `main.py` with your downloader (inputs: URL list + `output/html` root).

Do **not** mix built-in and external half-written files for the same `row_id`; clear `output/html/` before switching fetchers, or let `main.py` clear on APPID change.

5. Read all HTML under `output/html/` → `output/workshop_mods.sqlite`.  
   Field sources unchanged (hover JSON id/title/description, header rank, star image, author, detail URL).

   SQLite / Excel share three tables (primary key `mod_id`):

   (1) **mod_detail_url:** `mod_id`, `detail_url`  
       i.e. `https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}`, stored separately.

   (2) **mod_tag_ranks:** `mod_id` + one column per tag (names from `output/workshop_main.json`, e.g. `Alternative_History`)  
       Integer rank within that tag; `NULL` if absent. Columns vary by game.

   (3) **mod_browse_info:** `mod_id`, `title`, `description`, `star_rating`, `author`  
       First seen browse snippet wins when the same mod appears on multiple pages.

   Build DB: `python3 src/build_mods_sqlite.py`  
   Export Excel: `python3 tool/export_sqlite_to_csv_excel.py` → `output/workshop_mods.xlsx`

### `cfg/base.json`

| Field | Required | Meaning |
|-------|----------|---------|
| `APPID` | yes | Steam game ID |
| `PORT` | no | Local HTTP proxy on `127.0.0.1`; omit or `-1` for direct |
| `no_tls_verify` | no | `true` disables HTTPS certificate verification (MITM proxy) |

Example:

```json
{
    "APPID": 3450310,
    "PORT": 26561,
    "no_tls_verify": true
}
```

`main.py` updates only `APPID` and preserves `PORT` / `no_tls_verify`. Proxy env vars are cleared; only cfg `PORT` is used. CLI `--no-tls-verify` overrides TLS for one run.

## Notes

1. Deliverables live under `output/`: `html/`, `workshop_main.json`, `workshop_mods.sqlite`, `workshop_mods.xlsx`
2. `status/` holds intermediates (`browse_urls.json`, gap reports, etc.); hub snapshot and tables are in `output/`
3. HTTPS verifies TLS by default; set `no_tls_verify` in cfg or pass `--no-tls-verify` when using a MITM proxy.
4. Excel row limit is **1,048,576**. Games with more than ~1M Workshop mods need another export strategy.
5. Per-tag browse cap ~50k mods—see step 3 **Truncation** above.

## License

[MIT License](LICENSE). Free to use, modify, commercialize, and redistribute; retain copyright notice.
