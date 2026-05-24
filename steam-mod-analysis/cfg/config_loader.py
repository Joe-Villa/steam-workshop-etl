"""加载 cfg/*.yml，并与 base_info.yml 合并。"""

from __future__ import annotations

import os
from typing import Any

import yaml

CFG_DIR = os.path.dirname(os.path.abspath(__file__))


def load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML 顶层必须是对象: {path}")
    return data


def load_config(config_name: str) -> dict[str, Any]:
    config_path = os.path.join(CFG_DIR, f"{config_name}.yml")
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    base_path = os.path.join(CFG_DIR, "base_info.yml")
    base_data = load_yaml(base_path) if os.path.isfile(base_path) else {}
    cfg_data = load_yaml(config_path)
    merged = dict(base_data)
    merged.update(cfg_data)
    return merged
