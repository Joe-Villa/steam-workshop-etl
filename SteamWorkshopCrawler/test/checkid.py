#!/usr/bin/env python3
"""
Read APPID from cfg/base.json (or optional CLI override), query Steam Store
appdetails, and print whether the app exists and lists Steam Workshop support.

HTTPS certificate verification is disabled for all requests (compat with local
MITM proxies such as Steam++/Watt if you later route traffic through them).
"""

from __future__ import annotations

import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Steam Store API: https://wiki.teamfortress.com/wiki/User:RJackson/StorefrontAPI#appdetails
_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={appid}&l=english"
_CFG_REL = Path(__file__).resolve().parent.parent / "cfg" / "base.json"
# No TLS verify: store API may be reached via MITM local proxy; avoids CERTIFICATE_VERIFY_FAILED.
_SSL_NO_VERIFY = ssl._create_unverified_context()


def _load_appid_from_cfg() -> int:
    if not _CFG_REL.is_file():
        print(f"ERROR: Config file not found: {_CFG_REL}", file=sys.stderr)
        sys.exit(2)
    with _CFG_REL.open(encoding="utf-8") as f:
        cfg = json.load(f)
    if "APPID" not in cfg:
        print("ERROR: cfg/base.json must contain an integer field 'APPID'.", file=sys.stderr)
        sys.exit(2)
    raw = cfg["APPID"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)
        print("ERROR: 'APPID' must be a positive integer.", file=sys.stderr)
        sys.exit(2)
    if raw <= 0:
        print("ERROR: 'APPID' must be a positive integer.", file=sys.stderr)
        sys.exit(2)
    return int(raw)


def _fetch_appdetails(appid: int) -> dict:
    url = _APPDETAILS_URL.format(appid=appid)
    req = urllib.request.Request(url, headers={"User-Agent": "SteamWorkshopCrawler/1.0 (checkid)"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_NO_VERIFY) as resp:
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
    if len(sys.argv) > 1:
        try:
            appid = int(sys.argv[1])
        except ValueError:
            print("ERROR: Optional argument must be a numeric AppID.", file=sys.stderr)
            sys.exit(2)
        if appid <= 0:
            print("ERROR: AppID must be positive.", file=sys.stderr)
            sys.exit(2)
        print(f"Using AppID from command line: {appid}")
    else:
        appid = _load_appid_from_cfg()
        print(f"Using AppID from {_CFG_REL}: {appid}")

    payload = _fetch_appdetails(appid)
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

    print(f"OK: valid store app")
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
