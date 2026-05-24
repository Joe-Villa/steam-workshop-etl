"""Load repo-root cfg/base.json (APPID, proxy PORT, no_tls_verify)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from http_tls import verify_tls_enabled

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _REPO_ROOT / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from paradox_paths import (  # noqa: E402
    base_json_path,
    find_repo_root,
    load_base_json as _load_base_json,
    load_layout,
    merge_write_target_game,
    parse_appid,
)


def cfg_path() -> Path:
    return base_json_path(_REPO_ROOT)


def load_base_json() -> dict:
    return _load_base_json(_REPO_ROOT)


def load_appid_from_cfg(cfg: dict | None = None) -> int:
    if cfg is None:
        cfg = load_base_json()
    return parse_appid(cfg)


def merge_write_appid(appid: int) -> None:
    merge_write_target_game(appid, repo_root=_REPO_ROOT)


def data_layout():
    return load_layout(_REPO_ROOT)


def parse_proxy_port(cfg: dict) -> int | None:
    """``PORT`` omitted or ``-1`` → direct; else HTTP proxy on 127.0.0.1:<port>."""
    if "PORT" not in cfg:
        return None
    raw = cfg["PORT"]
    if raw is None or raw == -1:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        print('ERROR: cfg/base.json "PORT" must be -1 or 1..65535.', file=sys.stderr)
        raise SystemExit(2)
    if not 1 <= raw <= 65535:
        print('ERROR: cfg/base.json "PORT" must be -1 or 1..65535.', file=sys.stderr)
        raise SystemExit(2)
    return int(raw)


def http_settings_from_cfg_and_args(args, cfg: dict | None = None) -> tuple[int | None, bool]:
    if cfg is None:
        cfg = load_base_json()
    return parse_proxy_port(cfg), verify_tls_enabled(args, cfg)


def format_egress(proxy_port: int | None) -> str:
    if proxy_port is None:
        return "direct"
    return f"HTTP proxy 127.0.0.1:{proxy_port}"
