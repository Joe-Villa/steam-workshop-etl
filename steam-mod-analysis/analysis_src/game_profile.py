"""Steam 创意工坊分析：游戏标识与通用输出命名。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from data_paths import DB_PATH, RESULT_DIR  # noqa: E402


@dataclass(frozen=True)
class GameProfile:
    key: str
    appid: int
    display_name: str
    slug: str  # 空字符串表示通用输出，文件名不带游戏前缀


PROFILES: dict[str, GameProfile] = {
    "workshop": GameProfile("workshop", 0, "Steam 创意工坊", ""),
    "civ6": GameProfile("civ6", 289070, "文明6", "civ6"),
    "vic3": GameProfile("vic3", 529340, "维多利亚3", "vic3"),
}

DEFAULT_GAME_KEY = "workshop"


def get_profile(game_key: str | None = None) -> GameProfile:
    key = (game_key or DEFAULT_GAME_KEY).strip().lower()
    if key not in PROFILES:
        choices = ", ".join(sorted(PROFILES))
        raise ValueError(f"未知游戏 {game_key!r}，可选: {choices}")
    return PROFILES[key]


def output_basename(slug: str, name: str) -> str:
    """slug 为空时返回 name，否则返回 {slug}_{name}。"""
    return name if not slug else f"{slug}_{name}"


def figures_subdir(slug: str) -> str:
    return output_basename(slug, "figures")


def figure_stem(slug: str, stem: str) -> str:
    return output_basename(slug, stem)


def game_scope_line(game: GameProfile) -> str:
    if game.appid > 0:
        return f"- 游戏：{game.display_name}（Steam AppID `{game.appid}`）。"
    return f"- 范围：{game.display_name}。"


def default_db_path(project_root: Path) -> Path:
    _ = project_root
    return DB_PATH


def default_result_dir(project_root: Path) -> Path:
    _ = project_root
    return RESULT_DIR


def format_report_path(path: Path, *, anchor: Path | None = None) -> str:
    """对外报告用的路径：优先相对 anchor，避免泄露本机绝对路径。"""
    resolved = path.resolve()
    if anchor is not None:
        try:
            return resolved.relative_to(anchor.resolve()).as_posix()
        except ValueError:
            pass
    return path.name
