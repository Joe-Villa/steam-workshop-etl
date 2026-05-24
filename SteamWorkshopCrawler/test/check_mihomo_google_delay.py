#!/usr/bin/env python3
"""
Reproduce Mihomo's built-in per-node delay test (``/proxies/<name>/delay``) against Google.

This matches the workflow used to observe ~30 leaf outbounds with ~15–20 successes:
a **single** Mihomo exposes ``external-controller``; each leaf proxy is probed in parallel
via the REST API (not ``urllib`` through ``mixed-port``).

Prerequisites
-------------
* One running Mihomo with **REST** on ``external-controller`` (see ``config.yml``:
  ``external-controller: '127.0.0.1:9097'``). This is **not** the same as ``mixed-port``
  (7897): pointing ``MIHOMO_URL`` at mixed-port usually yields **HTTP 404** on ``/proxies``.

Examples
--------

    export MIHOMO_URL=http://127.0.0.1:9097   # same host:port as external-controller, NOT mixed-port
    python3 test/check_mihomo_google_delay.py

    python3 test/check_mihomo_google_delay.py \\
        --api-base http://127.0.0.1:9097 --timeout-ms 15000 --workers 12 --min-ok 5

Exit codes: 0 if at least ``--min-ok`` nodes report a delay; 1 if below threshold; 2 on usage/API errors.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

_SKIP_TYPES = frozenset(
    {
        "Selector",
        "URLTest",
        "Fallback",
        "LoadBalance",
        "Relay",
        "Compatible",
        "Pass",
        "Reject",
        "Direct",
        "RejectDrop",
    },
)


def _api_request(
    method: str,
    url: str,
    *,
    secret: str | None,
    timeout_s: float,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method)
    if secret:
        req.add_header("Authorization", f"Bearer {secret}")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _leaf_names(proxies: dict) -> list[str]:
    return [
        n
        for n, p in proxies.items()
        if isinstance(p, dict)
        and p.get("type") not in _SKIP_TYPES
        and n not in ("GLOBAL", "REJECT-DROP")
    ]


def _hint_api_base_mismatch(base: str, _code: int, body: bytes) -> None:
    """Explain common misconfiguration when REST returns 404 etc."""
    tail = body[:400].decode("utf-8", errors="replace") if body else ""
    print("", file=sys.stderr)
    print(
        "HINT: MIHOMO_URL / --api-base must be the Mihomo REST root (same as "
        "`external-controller` in config.yml), e.g. http://127.0.0.1:9097",
        file=sys.stderr,
    )
    print(
        "      Do NOT use mixed-port (e.g. 7897) — that is the HTTP/SOCKS proxy, "
        "not the controller; /proxies and /version return 404 there.",
        file=sys.stderr,
    )
    print(
        "      Check:  curl -sS \"{}/proxies\" | head -c 200".format(base.rstrip("/")),
        file=sys.stderr,
    )
    print(
        "      If you use a secret: export MIHOMO_SECRET=... "
        "(401 Unauthorized without it).",
        file=sys.stderr,
    )
    if tail.strip():
        print(f"      Response body (truncated): {tail!r}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Probe each Mihomo leaf proxy via /delay?url=Google generate_204 (parallel).",
    )
    ap.add_argument(
        "--api-base",
        default=os.environ.get("MIHOMO_URL", "http://127.0.0.1:9097").rstrip("/"),
        help="Mihomo REST base URL = external-controller (default: env MIHOMO_URL or http://127.0.0.1:9097). NOT mixed-port.",
    )
    ap.add_argument(
        "--secret",
        default=os.environ.get("MIHOMO_SECRET", ""),
        help="REST secret as Bearer token (default: env MIHOMO_SECRET)",
    )
    ap.add_argument(
        "--url",
        default="https://www.google.com/generate_204",
        help="URL passed to the delay endpoint (default: Google generate_204)",
    )
    ap.add_argument("--timeout-ms", type=int, default=12000, help="delay probe timeout in ms")
    ap.add_argument("--workers", type=int, default=12, help="max parallel delay requests")
    ap.add_argument(
        "--min-ok",
        type=int,
        default=1,
        metavar="N",
        help="exit 0 if at least N nodes return a delay (default: 1)",
    )
    args = ap.parse_args()

    base = args.api_base.rstrip("/")
    secret = args.secret.strip() or None
    url_q = urllib.parse.quote(args.url, safe="")
    timeout_http = max(30.0, args.timeout_ms / 1000.0 + 5.0)

    try:
        code, body = _api_request("GET", f"{base}/proxies", secret=secret, timeout_s=timeout_http)
    except (urllib.error.URLError, OSError) as e:
        print(f"ERROR: GET /proxies failed: {e}", file=sys.stderr)
        raise SystemExit(2) from e
    if code != 200:
        print(f"ERROR: GET /proxies -> HTTP {code}", file=sys.stderr)
        if code in (401, 403):
            print("HINT: set MIHOMO_SECRET / --secret if external-controller has a non-empty secret.", file=sys.stderr)
        else:
            _hint_api_base_mismatch(base, code, body)
        if body:
            print(body[:800].decode("utf-8", errors="replace"), file=sys.stderr)
        raise SystemExit(2)

    try:
        payload = json.loads(body.decode("utf-8"))
        proxies = payload["proxies"]
    except (json.JSONDecodeError, KeyError) as e:
        print(f"ERROR: invalid /proxies JSON: {e}", file=sys.stderr)
        raise SystemExit(2) from e

    leaves = _leaf_names(proxies)
    if not leaves:
        print("ERROR: no leaf proxies found under /proxies", file=sys.stderr)
        raise SystemExit(2)

    vcode, vbody = 0, b""
    try:
        vcode, vbody = _api_request("GET", f"{base}/version", secret=secret, timeout_s=15.0)
    except (urllib.error.URLError, OSError):
        pass
    version_line = ""
    if vcode == 200:
        try:
            vj = json.loads(vbody.decode("utf-8"))
            version_line = f" (core {vj.get('version', '?')})"
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    print(f"API: {base}{version_line}")
    print(f"Leaf proxy count: {len(leaves)}")
    print(f"Delay URL: {args.url}")
    print(f"Parallel workers: {args.workers}")
    print("")

    def delay_one(name: str) -> tuple[str, int | None, str | None]:
        enc = urllib.parse.quote(name, safe="")
        u = f"{base}/proxies/{enc}/delay?timeout={args.timeout_ms}&url={url_q}"
        try:
            c, b = _api_request("GET", u, secret=secret, timeout_s=timeout_http)
            if c != 200:
                try:
                    j = json.loads(b.decode("utf-8"))
                    msg = j.get("message") or f"HTTP {c}"
                except (json.JSONDecodeError, UnicodeDecodeError):
                    msg = f"HTTP {c}"
                return name, None, msg
            j = json.loads(b.decode("utf-8"))
            d = j.get("delay")
            if d is not None:
                return name, int(d), None
            return name, None, j.get("message") or "no delay field"
        except urllib.error.URLError as e:
            return name, None, str(e.reason)
        except TimeoutError:
            return name, None, "timeout"
        except (json.JSONDecodeError, OSError, ValueError) as e:
            return name, None, str(e)[:120]

    max_workers = max(1, min(args.workers, len(leaves)))
    results: list[tuple[str, int | None, str | None]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(delay_one, n) for n in leaves]
        for fut in as_completed(futs):
            results.append(fut.result())

    ok = [(n, d) for n, d, err in results if d is not None]
    bad = [(n, err) for n, d, err in results if d is None]
    ok.sort(key=lambda x: x[1])

    for name, ms in ok:
        print(f"  OK   {ms:>5} ms  {name}")
    for name, err in sorted(bad, key=lambda x: x[0]):
        detail = err or "fail"
        print(f"  FAIL          {name}  — {detail}")

    print("")
    ok_n, bad_n = len(ok), len(bad)
    print(f"Summary: {ok_n} OK / {bad_n} FAIL (of {len(leaves)} leaves).")

    if ok_n >= args.min_ok:
        print(f"Exit: OK (>= {args.min_ok} successes).")
        raise SystemExit(0)
    print(f"Exit: FAIL (need >= {args.min_ok}, got {ok_n}).", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
