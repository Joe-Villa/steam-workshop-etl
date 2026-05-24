#!/usr/bin/env python3
"""
Load ``APPID`` and ``Ports`` from cfg/base.json.

For each port entry, probe the game's Steam Community workshop hub in parallel:

    https://steamcommunity.com/app/<APPID>/workshop/

``-1`` means a direct connection (no HTTP proxy); any other integer is treated as
an HTTP proxy on 127.0.0.1:<port> (typical Clash/mixed ports). Port ``26561``
(Steam++/Watt local proxy) skips HTTPS certificate verification because the proxy
presents its own MITM certificate.

Print one line per configured port (same order as in JSON) with valid / invalid
and a short reason. Exit 0 if at least ``--min-ok`` probes succeed (default: all).
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_WORKSHOP_URL_TMPL = "https://steamcommunity.com/app/{appid}/workshop/"
_CFG_REL = Path(__file__).resolve().parent.parent / "cfg" / "base.json"
_USER_AGENT = "SteamWorkshopCrawler/1.0 (checkport)"
# urllib may not accept a (connect, read) tuple for all proxy code paths; use one bound.
_REQUEST_TIMEOUT_S = 30
# Steam++/Watt 本机 HTTP 代理常见端口；HTTPS 经其 MITM 时系统 CA 不信任，仅该端口跳过 TLS 校验。
_STEAMPP_LOCAL_PROXY_PORT_SKIP_TLS_VERIFY = 26561


def _parse_appid(cfg: dict) -> int:
    if "APPID" not in cfg:
        print("ERROR: cfg/base.json must contain an integer field 'APPID'.", file=sys.stderr)
        sys.exit(2)
    raw = cfg["APPID"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        if isinstance(raw, str) and raw.isdigit():
            appid = int(raw)
        else:
            print("ERROR: 'APPID' must be a positive integer.", file=sys.stderr)
            sys.exit(2)
    else:
        appid = raw
    if appid <= 0:
        print("ERROR: 'APPID' must be a positive integer.", file=sys.stderr)
        sys.exit(2)
    return int(appid)


def _load_appid_and_ports_from_cfg() -> tuple[int, list[int]]:
    if not _CFG_REL.is_file():
        print(f"ERROR: Config file not found: {_CFG_REL}", file=sys.stderr)
        sys.exit(2)
    with _CFG_REL.open(encoding="utf-8") as f:
        cfg = json.load(f)
    appid = _parse_appid(cfg)
    if "Ports" not in cfg:
        print("ERROR: cfg/base.json must contain a 'Ports' array.", file=sys.stderr)
        sys.exit(2)
    raw = cfg["Ports"]
    if not isinstance(raw, list) or not raw:
        print("ERROR: 'Ports' must be a non-empty list of integers.", file=sys.stderr)
        sys.exit(2)
    out: list[int] = []
    for i, item in enumerate(raw):
        if isinstance(item, bool) or not isinstance(item, int):
            if isinstance(item, str) and item.lstrip("-").isdigit():
                out.append(int(item))
                continue
            print(f"ERROR: Ports[{i}] must be an integer (got {type(item).__name__}).", file=sys.stderr)
            sys.exit(2)
        if item == -1:
            out.append(-1)
        elif 1 <= item <= 65535:
            out.append(item)
        else:
            print(
                f"ERROR: Ports[{i}] must be -1 (direct) or 1..65535 (proxy port), got {item}.",
                file=sys.stderr,
            )
            sys.exit(2)
    return appid, out


def _build_opener(proxy_port: int | None) -> urllib.request.OpenerDirector:
    if proxy_port is None:
        return urllib.request.build_opener()
    proxy_url = f"http://127.0.0.1:{proxy_port}"
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}),
    ]
    if proxy_port == _STEAMPP_LOCAL_PROXY_PORT_SKIP_TLS_VERIFY:
        handlers.append(
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
        )
    return urllib.request.build_opener(*handlers)


def _probe_url(target_url: str, port_entry: int) -> tuple[bool, str]:
    """
    Return (ok, detail). ``port_entry`` is -1 for direct, else local HTTP proxy port.
    """
    proxy_port: int | None = None if port_entry == -1 else port_entry
    opener = _build_opener(proxy_port)
    req = urllib.request.Request(target_url, headers={"User-Agent": _USER_AGENT})
    try:
        with opener.open(req, timeout=_REQUEST_TIMEOUT_S) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                return False, f"HTTP {status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, str(e.reason)
    except TimeoutError:
        return False, "timeout"
    except OSError as e:
        return False, str(e)
    return True, "HTTP 200"


def _label(port_entry: int) -> str:
    if port_entry == -1:
        return "direct (no proxy)"
    return f"HTTP proxy 127.0.0.1:{port_entry}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Probe Steam workshop hub per proxy port in cfg/base.json")
    ap.add_argument(
        "--min-ok",
        type=int,
        default=None,
        metavar="N",
        help="exit 0 if at least N probes succeed (default: all ports must succeed)",
    )
    args = ap.parse_args()

    appid, ports = _load_appid_and_ports_from_cfg()
    min_ok = args.min_ok if args.min_ok is not None else len(ports)
    if min_ok < 1:
        print("ERROR: --min-ok must be >= 1", file=sys.stderr)
        sys.exit(2)
    if min_ok > len(ports):
        print("ERROR: --min-ok cannot exceed number of port entries", file=sys.stderr)
        sys.exit(2)
    target_url = _WORKSHOP_URL_TMPL.format(appid=appid)
    print(f"Using APPID and Ports from {_CFG_REL} ({len(ports)} port entries)")
    print(f"APPID: {appid}")
    print(f"Target: {target_url}")
    print("")

    # Preserve cfg order while still hitting Steam in parallel.
    def job(idx: int, p: int) -> tuple[int, int, bool, str]:
        try:
            ok, detail = _probe_url(target_url, p)
            return idx, p, ok, detail
        except Exception as e:
            return idx, p, False, f"{type(e).__name__}: {e}"

    by_idx: dict[int, tuple[int, bool, str]] = {}
    max_workers = min(32, max(1, len(ports)))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        pending = [ex.submit(job, i, p) for i, p in enumerate(ports)]
        for fut in as_completed(pending):
            idx, p, ok, detail = fut.result()
            by_idx[idx] = (p, ok, detail)
    results = [by_idx[i] for i in range(len(ports))]

    ok_count = sum(1 for p, ok, detail in results if ok)
    for p, ok, detail in results:
        status = "VALID" if ok else "INVALID"
        line = f"  port {p:>5}  ({_label(p)}): {status}"
        if not ok:
            line += f"  — {detail}"
        print(line)

    print("")
    if ok_count >= min_ok:
        print(f"Summary: {ok_count}/{len(ports)} OK (required >= {min_ok}).")
        sys.exit(0)
    print(f"Summary: {ok_count}/{len(ports)} OK but required >= {min_ok}.")
    sys.exit(1)


if __name__ == "__main__":
    main()
