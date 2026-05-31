"""Build :class:`ParadoxDataLayout` from a stage ``output`` directory (sibling under data root)."""

from __future__ import annotations

from pathlib import Path

from paradox_paths import ParadoxDataLayout, parse_appid
from pipeline_manifest import StageManifest


def data_root_from_output(output_root: Path) -> Path:
    """Stage output dirs are ``data/<APPID>/{simple_info,concrete_html,...}``."""
    return output_root.resolve().parent


def layout_from_manifest(spec: StageManifest, cfg: dict) -> ParadoxDataLayout:
    appid = parse_appid(cfg)
    root = data_root_from_output(spec.output_root)
    return ParadoxDataLayout(repo_root=spec.repo_root, appid=appid, root=root)
