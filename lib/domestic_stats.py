"""订阅分布汇总统计（建库 statistic 表与分析报告共用）。"""

from __future__ import annotations

import math


def percentile(sorted_values: list[int], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * p
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return float(sorted_values[low])
    weight = rank - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def median(sorted_values: list[int]) -> float:
    n = len(sorted_values)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_values[mid])
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2


def std_dev(values: list[int], mean_value: float) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    variance = sum((x - mean_value) ** 2 for x in values) / n
    return math.sqrt(variance)


def gini(sorted_desc_values: list[int]) -> float:
    n = len(sorted_desc_values)
    total = sum(sorted_desc_values)
    if n == 0 or total == 0:
        return 0.0
    sorted_asc = list(reversed(sorted_desc_values))
    weighted_sum = 0.0
    for idx, x in enumerate(sorted_asc, start=1):
        weighted_sum += idx * x
    return (2 * weighted_sum) / (n * total) - (n + 1) / n


def hhi(values: list[int]) -> float:
    total = sum(values)
    if total == 0:
        return 0.0
    return sum((v / total) ** 2 for v in values)


def concentration_block(
    values_desc: list[int], total_mods: int, total_subscribers: int
) -> dict[str, dict[str, float | int]]:
    def top_slice(count: int) -> dict[str, float | int]:
        slice_values = values_desc[:count]
        slice_sum = sum(slice_values)
        return {
            "count": count,
            "share_mods": count / total_mods if total_mods else 0.0,
            "sum_subscribers": slice_sum,
            "share_subscribers": slice_sum / total_subscribers if total_subscribers else 0.0,
        }

    top_1pct_count = max(1, round(total_mods * 0.01)) if total_mods else 0
    top_5pct_count = max(1, round(total_mods * 0.05)) if total_mods else 0
    top_10pct_count = max(1, round(total_mods * 0.10)) if total_mods else 0

    return {
        "top_10": top_slice(min(10, total_mods)),
        "top_50": top_slice(min(50, total_mods)),
        "top_100": top_slice(min(100, total_mods)),
        "top_1pct": top_slice(min(top_1pct_count, total_mods)),
        "top_5pct": top_slice(min(top_5pct_count, total_mods)),
        "top_10pct": top_slice(min(top_10pct_count, total_mods)),
        "hhi": hhi(values_desc),
        "gini": gini(values_desc),
    }


def pareto_block(
    values_desc: list[int], total_mods: int, total_subscribers: int
) -> dict[str, dict[str, float | int]]:
    targets = [0.5, 0.8, 0.9]
    cumulative = 0
    pointer = 0
    result: dict[str, dict[str, float | int]] = {}
    for target in targets:
        while pointer < len(values_desc):
            if total_subscribers > 0 and cumulative / total_subscribers >= target:
                break
            cumulative += values_desc[pointer]
            pointer += 1
        if total_subscribers > 0 and cumulative / total_subscribers >= target:
            mods_needed = pointer
        else:
            mods_needed = total_mods
        key = f"share_{int(target * 100)}pct_subscribers"
        result[key] = {
            "target_share": target,
            "mods_needed": mods_needed,
            "share_mods": mods_needed / total_mods if total_mods else 0.0,
        }
    return result


def thresholds_block(values: list[int], total_mods: int) -> dict[str, dict[str, float | int]]:
    thresholds = [100, 500, 1000, 5000, 10000]
    result: dict[str, dict[str, float | int]] = {}
    for threshold in thresholds:
        mods_at_or_above = sum(1 for v in values if v >= threshold)
        result[f"at_least_{threshold}"] = {
            "threshold": threshold,
            "mods_at_or_above": mods_at_or_above,
            "share_mods": mods_at_or_above / total_mods if total_mods else 0.0,
        }
    return result


def buckets_block(values: list[int], total_mods: int) -> dict[str, dict[str, float | int]]:
    conditions = {
        "0_99": lambda v: 0 <= v <= 99,
        "100_999": lambda v: 100 <= v <= 999,
        "1000_4999": lambda v: 1000 <= v <= 4999,
        "5000_9999": lambda v: 5000 <= v <= 9999,
        "10000_49999": lambda v: 10000 <= v <= 49999,
        "50000_plus": lambda v: v >= 50000,
    }
    result: dict[str, dict[str, float | int]] = {}
    for key, fn in conditions.items():
        mods = sum(1 for v in values if fn(v))
        result[key] = {
            "mods": mods,
            "share_mods": mods / total_mods if total_mods else 0.0,
        }
    return result


def calc_stats(values: list[int]) -> dict[str, object]:
    sorted_asc = sorted(values)
    sorted_desc = list(reversed(sorted_asc))
    total_mods = len(values)
    total_subscribers = sum(values)
    mean_subscribers = total_subscribers / total_mods if total_mods else 0.0

    return {
        "overview": {
            "total_mods": total_mods,
            "total_subscribers": total_subscribers,
            "mean_subscribers": mean_subscribers,
            "median_subscribers": median(sorted_asc),
            "std_subscribers": std_dev(values, mean_subscribers),
            "min_subscribers": min(values) if values else 0,
            "max_subscribers": max(values) if values else 0,
        },
        "percentiles": {
            "p10": percentile(sorted_asc, 0.10),
            "p25": percentile(sorted_asc, 0.25),
            "p50": percentile(sorted_asc, 0.50),
            "p75": percentile(sorted_asc, 0.75),
            "p90": percentile(sorted_asc, 0.90),
            "p95": percentile(sorted_asc, 0.95),
            "p99": percentile(sorted_asc, 0.99),
        },
        "concentration": concentration_block(sorted_desc, total_mods, total_subscribers),
        "pareto": pareto_block(sorted_desc, total_mods, total_subscribers),
        "thresholds": thresholds_block(values, total_mods),
        "buckets": buckets_block(values, total_mods),
    }
