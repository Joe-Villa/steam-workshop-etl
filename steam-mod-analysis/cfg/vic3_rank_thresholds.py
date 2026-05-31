from __future__ import annotations

import csv
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class RankRule:
    average_multiplier: float
    top_multiplier: float


DEFAULT_RANK_RULES: dict[str, RankRule] = {
    "super_power": RankRule(average_multiplier=30.0, top_multiplier=0.98),
    "great_power": RankRule(average_multiplier=5.0, top_multiplier=0.75),
    "major_power": RankRule(average_multiplier=2.5, top_multiplier=0.5),
    "minor_power": RankRule(average_multiplier=0.6, top_multiplier=0.15),
}


def load_prestige_series(path: str | Path) -> list[float]:
    """
    读取威望序列，支持:
    - .json: [12, 34, ...] 或 {"prestiges": [12, 34, ...]}
    - .csv: 任意列名，自动读取首个可转 float 的列
    - .txt: 每行一个数字
    """
    file_path = Path(path)
    suffix = file_path.suffix.lower()

    if suffix == ".json":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [float(x) for x in data]
        if isinstance(data, dict) and "prestiges" in data:
            return [float(x) for x in data["prestiges"]]
        raise ValueError("JSON 格式不支持，请使用数组或 {'prestiges': [...]}。")

    if suffix == ".txt":
        values: list[float] = []
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            values.append(float(line))
        return values

    if suffix == ".csv":
        values = []
        with file_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                value = _extract_first_float(row.values())
                if value is not None:
                    values.append(value)
        return values

    raise ValueError(f"不支持的文件类型: {suffix}")


def calculate_rank_cutoffs(
    prestiges: Iterable[float | int],
    *,
    min_num_countries: int = 100,
    filler_country_prestige: float = 15.0,
    include_top_constraint: bool = True,
    rank_rules: dict[str, RankRule] | None = None,
) -> dict[str, float]:
    """
    计算国家位阶分数线（下限）。

    规则:
    - 每个位阶需同时满足:
      1) prestige >= average_prestige * average_multiplier
      2) prestige >= top_prestige * top_multiplier
    - 因此位阶分数线 = max(上述两个阈值)
    - 若国家数不足 min_num_countries，会按 Vic3 规则加入 filler 国家参与平均值
    """
    values = [float(x) for x in prestiges]
    if not values:
        raise ValueError("prestiges 不能为空。")

    top_prestige = max(values)
    adjusted_average = _calculate_adjusted_average(
        values,
        min_num_countries=min_num_countries,
        filler_country_prestige=filler_country_prestige,
    )

    rules = rank_rules or DEFAULT_RANK_RULES
    cutoffs: dict[str, float] = {}
    for rank_name, rule in rules.items():
        by_average = adjusted_average * rule.average_multiplier
        if include_top_constraint:
            by_top = top_prestige * rule.top_multiplier
            cutoffs[rank_name] = max(by_average, by_top)
        else:
            cutoffs[rank_name] = by_average

    cutoffs["insignificant_power"] = float("-inf")
    return cutoffs


def calculate_rank_cutoffs_from_mods_sqlite(
    db_path: str | Path,
    *,
    table_name: str = "aaa_mods",
    subscribers_column: str = "subscribers",
    prestige_transform: str = "identity",
    min_num_countries: int = 100,
    filler_country_prestige: float = 15.0,
    include_top_constraint: bool = True,
    rank_rules: dict[str, RankRule] | None = None,
) -> dict[str, float]:
    """
    从 mods.sqlite3 读取“模组订阅量”并计算国家位阶分数线。
    """
    prestiges = _load_subscribers_from_sqlite(
        db_path=db_path,
        table_name=table_name,
        subscribers_column=subscribers_column,
    )
    transformed_prestiges = _apply_prestige_transform(prestiges, prestige_transform)
    return calculate_rank_cutoffs(
        transformed_prestiges,
        min_num_countries=min_num_countries,
        filler_country_prestige=filler_country_prestige,
        include_top_constraint=include_top_constraint,
        rank_rules=rank_rules,
    )


def _calculate_adjusted_average(
    values: list[float],
    *,
    min_num_countries: int,
    filler_country_prestige: float,
) -> float:
    count = len(values)
    total = sum(values)

    if count < min_num_countries:
        filler_count = min_num_countries - count
        total += filler_count * filler_country_prestige
        count = min_num_countries

    return total / count


def _load_subscribers_from_sqlite(
    *,
    db_path: str | Path,
    table_name: str,
    subscribers_column: str,
) -> list[float]:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"SQLite 文件不存在: {path}")

    query = (
        f'SELECT "{subscribers_column}" '
        f'FROM "{table_name}" '
        f'WHERE "{subscribers_column}" IS NOT NULL;'
    )
    with sqlite3.connect(path) as conn:
        rows = conn.execute(query).fetchall()

    values = [float(row[0]) for row in rows if row[0] is not None]
    if not values:
        raise ValueError(
            f"未读取到订阅量数据，请检查表 `{table_name}` 和字段 `{subscribers_column}`。"
        )
    return values


def _extract_first_float(raw_values: Iterable[object]) -> float | None:
    for raw in raw_values:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        try:
            return float(text)
        except ValueError:
            continue
    return None


def _apply_prestige_transform(values: list[float], transform: str) -> list[float]:
    if transform == "identity":
        return values
    if transform == "log1p":
        return [math.log1p(v) for v in values]
    raise ValueError("prestige_transform 仅支持 'identity' 或 'log1p'。")

