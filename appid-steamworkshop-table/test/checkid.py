#!/usr/bin/env python3
"""
Read APPID from cfg/base.json (or optional CLI override), query Steam Store
appdetails, and print whether the app exists and lists Steam Workshop support.

Egress: cfg ``PORT`` → 127.0.0.1 HTTP proxy; cfg ``no_tls_verify`` or ``--no-tls-verify``.
System proxy environment variables are ignored.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from base_config import (
    cfg_path,
    format_egress,
    http_settings_from_cfg_and_args,
    load_appid_from_cfg,
    load_base_json,
)
from http_tls import add_no_tls_verify_arg, clear_proxy_env, open_url

# Steam Store API: https://wiki.teamfortress.com/wiki/User:RJackson/StorefrontAPI#appdetails
_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={appid}&l=english"


def _fetch_appdetails(
    appid: int, *, verify_tls: bool, proxy_port: int | None
) -> dict:
    url = _APPDETAILS_URL.format(appid=appid)
    req = urllib.request.Request(url, headers={"User-Agent": "SteamWorkshopCrawler/1.0 (checkid)"})
    try:
        with open_url(
            req,
            verify_tls=verify_tls,
            proxy_port=proxy_port,
            timeout=30,
        ) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        print(f"ERROR: HTTP {e.code} while fetching app details.", file=sys.stderr)
        sys.exit(2)
    except urllib.error.URLError as e:
        print(f"ERROR: Network failure: {e.reason}", file=sys.stderr)
        sys.exit(2)
    except TimeoutError:
        print("ERROR: Request timed out.", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON from Steam API: {e}", file=sys.stderr)
        sys.exit(2)


def _has_steam_workshop(categories: list) -> bool:
    for c in categories:
        if not isinstance(c, dict):
            continue
        desc = (c.get("description") or "").lower()
        cid = c.get("id")
        if cid == 30 or "workshop" in desc:
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify Steam APPID and Workshop support.")
    ap.add_argument(
        "appid",
        type=int,
        nargs="?",
        default=None,
        help="Steam APPID (default: cfg/base.json).",
    )
    add_no_tls_verify_arg(ap)
    args = ap.parse_args()
    clear_proxy_env()
    cfg = load_base_json()
    proxy_port, verify_tls = http_settings_from_cfg_and_args(args, cfg)
    cfg_file = cfg_path()

    if args.appid is not None:
        if args.appid <= 0:
            print("ERROR: AppID must be positive.", file=sys.stderr)
            sys.exit(2)
        appid = int(args.appid)
        print(f"Using AppID from command line: {appid}")
    else:
        appid = load_appid_from_cfg(cfg)
        print(f"Using AppID from {cfg_file}: {appid}")

    print(f"egress={format_egress(proxy_port)}, tls_verify={verify_tls}", flush=True)

    payload = _fetch_appdetails(appid, verify_tls=verify_tls, proxy_port=proxy_port)
    key = str(appid)
    if key not in payload:
        print("ERROR: Unexpected API response (missing app key).", file=sys.stderr)
        sys.exit(2)

    entry = payload[key]
    if not entry.get("success"):
        print(f"Invalid or unknown Steam AppID: {appid}")
        print("Steam returned success=false for this id (unlisted, wrong id, or not a store app).")
        sys.exit(1)

    data = entry.get("data")
    if not isinstance(data, dict):
        print(f"AppID {appid} is listed but has no store data payload.")
        sys.exit(1)

    name = data.get("name") or "(no name)"
    steam_appid = data.get("steam_appid", appid)
    app_type = data.get("type") or "unknown"

    print("OK: valid store app")
    print(f"  Name: {name}")
    print(f"  steam_appid: {steam_appid}")
    print(f"  type: {app_type}")

    workshop = _has_steam_workshop(data.get("categories") or [])
    if workshop:
        print("  Steam Workshop: yes (store lists Steam Workshop)")
    else:
        print("  Steam Workshop: no (no Steam Workshop category on store page)")

    sys.exit(0)


if __name__ == "__main__":
    main()
