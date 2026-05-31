"""Manifest-driven layout + cfg/base.json (APPID, proxy PORT, no_tls_verify)."""

from __future__ import annotations

import sys
from pathlib import Path

from http_tls import verify_tls_enabled

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _REPO_ROOT / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from paradox_paths import (  # noqa: E402
    ParadoxDataLayout,
    load_base_json as _load_base_json,
    parse_appid,
)
from pipeline_manifest import StageManifest, load_cfg_from_manifest, load_stage_manifest  # noqa: E402
from stage_layout import layout_from_manifest  # noqa: E402


def load_base_json() -> dict:
    """Repo ``cfg/base.json`` (for standalone tools invoked without a manifest)."""
    return _load_base_json(_REPO_ROOT)


def resolve_run(manifest_path: Path) -> tuple[StageManifest, ParadoxDataLayout, dict]:
    spec = load_stage_manifest(manifest_path)
    cfg = load_cfg_from_manifest(spec)
    layout = layout_from_manifest(spec, cfg)
    return spec, layout, cfg


def load_appid_from_cfg(cfg: dict) -> int:
    return parse_appid(cfg)


def parse_proxy_port(cfg: dict) -> int | None:
    """``PORT`` omitted or ``-1`` → direct; else HTTP proxy on 127.0.0.1:<port>."""
    if "PORT" not in cfg:
        return None
    raw = cfg["PORT"]
    if raw is None or raw == -1:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        print('ERROR: cfg "PORT" must be -1 or 1..65535.', file=sys.stderr)
        raise SystemExit(2)
    if not 1 <= raw <= 65535:
        print('ERROR: cfg "PORT" must be -1 or 1..65535.', file=sys.stderr)
        raise SystemExit(2)
    return int(raw)


def http_settings_from_cfg_and_args(args, cfg: dict | None = None) -> tuple[int | None, bool]:
    if cfg is None:
        print("ERROR: cfg required for http_settings_from_cfg_and_args", file=sys.stderr)
        raise SystemExit(2)
    return parse_proxy_port(cfg), verify_tls_enabled(args, cfg)


def format_egress(proxy_port: int | None) -> str:
    if proxy_port is None:
        return "direct"
    return f"HTTP proxy 127.0.0.1:{proxy_port}"
