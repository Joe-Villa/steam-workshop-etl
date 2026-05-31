"""从已建 SQLite 库计算 statistic 表（key-value 汇总统计）。"""

from __future__ import annotations

import math
import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PROJECT_ROOT.parent
_LIB = _REPO_ROOT / "lib"
for _p in (_PROJECT_ROOT, _LIB):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from data_paths import MODS_TABLE  # noqa: E402
from domestic_stats import calc_stats, percentile  # noqa: E402

STATISTIC_TABLE = "statistic"

_RATIO_TIER_LABELS = (
    ("订阅量 100 以下", lambda s: s < 100),
    ("订阅量 100–1000", lambda s: 100 <= s < 1000),
    ("订阅量 1000–10000", lambda s: 1000 <= s < 10000),
    ("订阅量 10000 以上", lambda s: s >= 10000),
)


def _fmt_value(value: float | int | str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return f"{value:,}"
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Inf" if value > 0 else "-Inf"
    abs_v = abs(value)
    if abs_v >= 1000:
        return f"{value:,.2f}"
    if abs_v >= 1:
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _row(name: str, value: float | int | str) -> tuple[str, str]:
    return (name, _fmt_value(value))


def _pct(share: float) -> str:
    return f"{share * 100:.2f}%"


def _gini(values: list[int]) -> float:
    sorted_asc = sorted(values)
    n = len(sorted_asc)
    total = sum(sorted_asc)
    if n == 0 or total == 0:
        return 0.0
    weighted_sum = sum((idx + 1) * x for idx, x in enumerate(sorted_asc))
    return (2 * weighted_sum) / (n * total) - (n + 1) / n


def _share_top(sorted_desc: list[int], p: float) -> float:
    if not sorted_desc:
        return 0.0
    total = sum(sorted_desc)
    if total == 0:
        return 0.0
    k = max(1, math.ceil(len(sorted_desc) * p))
    return sum(sorted_desc[:k]) / total


def _share_bottom(sorted_asc: list[int], p: float) -> float:
    if not sorted_asc:
        return 0.0
    total = sum(sorted_asc)
    if total == 0:
        return 0.0
    k = max(1, math.ceil(len(sorted_asc) * p))
    return sum(sorted_asc[:k]) / total


def _sample_skewness(values: list[float]) -> float:
    n = len(values)
    if n < 3:
        return float("nan")
    mean_v = sum(values) / n
    var = sum((x - mean_v) ** 2 for x in values) / (n - 1)
    if var == 0:
        return 0.0
    std_v = math.sqrt(var)
    m3 = sum(((x - mean_v) / std_v) ** 3 for x in values) / n
    return m3


def _sample_kurtosis_excess(values: list[float]) -> float:
    n = len(values)
    if n < 4:
        return float("nan")
    mean_v = sum(values) / n
    var = sum((x - mean_v) ** 2 for x in values) / (n - 1)
    if var == 0:
        return float("nan")
    std_v = math.sqrt(var)
    m4 = sum(((x - mean_v) / std_v) ** 4 for x in values) / n
    return m4 - 3.0


def _fetch_all_subscribers(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        f'SELECT subscribers FROM "{MODS_TABLE}" ORDER BY subscribers DESC, mod_id ASC;'
    ).fetchall()
    return [int(row[0]) for row in rows]


def _fetch_filtered_subscribers(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        f"""
        SELECT m.subscribers
        FROM "{MODS_TABLE}" m
        LEFT JOIN mod_private_like_flags f ON f.mod_id = m.mod_id
        WHERE COALESCE(f.is_private_like, 0) = 0
        ORDER BY m.subscribers DESC, m.mod_id ASC;
        """
    ).fetchall()
    return [int(row[0]) for row in rows]


def _fetch_ratio_rows(conn: sqlite3.Connection) -> list[tuple[float, int]]:
    row = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'mod_subscriber_exposure_ratio_with_mod_name'
        LIMIT 1;
        """
    ).fetchone()
    if row:
        rows = conn.execute(
            """
            SELECT subscriber_exposure_ratio, subscribers
            FROM mod_subscriber_exposure_ratio_with_mod_name
            ORDER BY mod_id;
            """
        ).fetchall()
        return [(float(ratio), int(subs)) for ratio, subs in rows]

    rows = conn.execute(
        f"""
        SELECT
            ROUND(CAST(m.subscribers AS REAL) / m.exposure, 3),
            m.subscribers
        FROM "{MODS_TABLE}" m
        LEFT JOIN mod_private_like_flags f ON f.mod_id = m.mod_id
        WHERE m.exposure > 0
          AND COALESCE(f.is_private_like, 0) = 0
        ORDER BY m.mod_id;
        """
    ).fetchall()
    return [(float(ratio), int(subs)) for ratio, subs in rows]


def _count_private_like(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM mod_private_like_flags
        WHERE is_private_like = 1;
        """
    ).fetchone()
    return int(row[0]) if row else 0


def _append_all_mods_subscriber_rows(rows: list[tuple[str, str]], stats: dict[str, object]) -> None:
    overview = stats["overview"]
    percentiles = stats["percentiles"]
    concentration = stats["concentration"]
    pareto = stats["pareto"]
    thresholds = stats["thresholds"]
    buckets = stats["buckets"]

    prefix = "[全体模组·订阅]"
    rows.extend(
        [
            _row(f"{prefix} 模组总数", overview["total_mods"]),
            _row(f"{prefix} 订阅总量", overview["total_subscribers"]),
            _row(f"{prefix} 平均订阅", overview["mean_subscribers"]),
            _row(f"{prefix} 中位数订阅", overview["median_subscribers"]),
            _row(f"{prefix} 标准差", overview["std_subscribers"]),
            _row(f"{prefix} 最低订阅", overview["min_subscribers"]),
            _row(f"{prefix} 最高订阅", overview["max_subscribers"]),
            _row(f"{prefix} P10", percentiles["p10"]),
            _row(f"{prefix} P25", percentiles["p25"]),
            _row(f"{prefix} P50", percentiles["p50"]),
            _row(f"{prefix} P75", percentiles["p75"]),
            _row(f"{prefix} P90", percentiles["p90"]),
            _row(f"{prefix} P95", percentiles["p95"]),
            _row(f"{prefix} P99", percentiles["p99"]),
        ]
    )

    label_map = {
        "top_10": "前10名",
        "top_50": "前50名",
        "top_100": "前100名",
        "top_1pct": "前1%",
        "top_5pct": "前5%",
        "top_10pct": "前10%",
    }
    for key, label in label_map.items():
        item = concentration[key]
        rows.append(_row(f"{prefix} {label}·模组数", item["count"]))
        rows.append(_row(f"{prefix} {label}·模组占比", _pct(item["share_mods"])))
        rows.append(_row(f"{prefix} {label}·订阅总量", item["sum_subscribers"]))
        rows.append(_row(f"{prefix} {label}·订阅占比", _pct(item["share_subscribers"])))

    rows.append(_row(f"{prefix} HHI 指数", concentration["hhi"]))
    rows.append(_row(f"{prefix} 基尼系数", concentration["gini"]))

    for key, target in [
        ("share_50pct_subscribers", "50%"),
        ("share_80pct_subscribers", "80%"),
        ("share_90pct_subscribers", "90%"),
    ]:
        item = pareto[key]
        rows.append(_row(f"{prefix} 帕累托·达成{target}订阅占比所需模组数", item["mods_needed"]))
        rows.append(_row(f"{prefix} 帕累托·达成{target}订阅占比所需模组占比", _pct(item["share_mods"])))

    for key in ["at_least_100", "at_least_500", "at_least_1000", "at_least_5000", "at_least_10000"]:
        item = thresholds[key]
        threshold = item["threshold"]
        rows.append(_row(f"{prefix} 订阅≥{threshold}·模组数", item["mods_at_or_above"]))
        rows.append(_row(f"{prefix} 订阅≥{threshold}·模组占比", _pct(item["share_mods"])))

    bucket_label = {
        "0_99": "0-99",
        "100_999": "100-999",
        "1000_4999": "1000-4999",
        "5000_9999": "5000-9999",
        "10000_49999": "10000-49999",
        "50000_plus": "50000+",
    }
    for key, label in bucket_label.items():
        item = buckets[key]
        rows.append(_row(f"{prefix} 区间{label}·模组数", item["mods"]))
        rows.append(_row(f"{prefix} 区间{label}·模组占比", _pct(item["share_mods"])))


def _append_filtered_inequality_rows(rows: list[tuple[str, str]], values: list[int]) -> None:
    prefix = "[非自用模组·订阅不平等]"
    n = len(values)
    if n == 0:
        rows.append(_row(f"{prefix} 样本量", 0))
        return

    sorted_asc = sorted(values)
    sorted_desc = list(reversed(sorted_asc))
    total = sum(values)
    mean_v = total / n
    med = percentile(sorted_asc, 0.50)
    p1 = percentile(sorted_asc, 0.01)
    p10 = percentile(sorted_asc, 0.10)
    p90 = percentile(sorted_asc, 0.90)
    p99 = percentile(sorted_asc, 0.99)
    g = _gini(values)

    top1 = _share_top(sorted_desc, 0.01)
    top5 = _share_top(sorted_desc, 0.05)
    top10 = _share_top(sorted_desc, 0.10)
    bottom10 = _share_bottom(sorted_asc, 0.10)
    bottom20 = _share_bottom(sorted_asc, 0.20)
    bottom40 = _share_bottom(sorted_asc, 0.40)

    p90_p10 = p90 / p10 if p10 > 0 else float("inf")
    p99_p1 = p99 / p1 if p1 > 0 else float("inf")
    palma = top10 / bottom40 if bottom40 > 0 else float("inf")

    rows.extend(
        [
            _row(f"{prefix} 样本量", n),
            _row(f"{prefix} 总订阅量", total),
            _row(f"{prefix} 顶层1%订阅份额", _pct(top1)),
            _row(f"{prefix} 顶层5%订阅份额", _pct(top5)),
            _row(f"{prefix} 顶层10%订阅份额", _pct(top10)),
            _row(f"{prefix} 底层10%订阅份额", _pct(bottom10)),
            _row(f"{prefix} 底层20%订阅份额", _pct(bottom20)),
            _row(f"{prefix} P90/P10", p90_p10),
            _row(f"{prefix} P99/P1", p99_p1),
            _row(f"{prefix} Palma 比率", palma),
            _row(f"{prefix} 最小值", sorted_asc[0]),
            _row(f"{prefix} P1", p1),
            _row(f"{prefix} P10", p10),
            _row(f"{prefix} 中位数", med),
            _row(f"{prefix} 均值", mean_v),
            _row(f"{prefix} P90", p90),
            _row(f"{prefix} P99", p99),
            _row(f"{prefix} 最大值", sorted_asc[-1]),
            _row(f"{prefix} 基尼系数", g),
        ]
    )


def _append_ratio_rows(rows: list[tuple[str, str]], ratio_rows: list[tuple[float, int]]) -> None:
    prefix = "[非自用模组·订阅/曝光比]"
    ratios = [r for r, _ in ratio_rows]
    n = len(ratios)
    if n == 0:
        rows.append(_row(f"{prefix} 样本量", 0))
        return

    mean_v = sum(ratios) / n
    var = sum((x - mean_v) ** 2 for x in ratios) / (n - 1) if n > 1 else 0.0
    std_v = math.sqrt(var)
    sorted_ratios = sorted(ratios)
    med = percentile(sorted_ratios, 0.50)
    q1 = percentile(sorted_ratios, 0.25)
    q3 = percentile(sorted_ratios, 0.75)
    mn, mx = min(ratios), max(ratios)
    cv = std_v / mean_v if mean_v else float("nan")
    skew = _sample_skewness(ratios)
    kurt = _sample_kurtosis_excess(ratios)
    n_gt1 = sum(1 for x in ratios if x > 1.0)
    n_eq1 = sum(1 for x in ratios if x == 1.0)

    rows.extend(
        [
            _row(f"{prefix} 样本量", n),
            _row(f"{prefix} 均值", mean_v),
            _row(f"{prefix} 标准差", std_v),
            _row(f"{prefix} 变异系数", cv),
            _row(f"{prefix} 最小值", mn),
            _row(f"{prefix} 最大值", mx),
            _row(f"{prefix} 极差", mx - mn),
            _row(f"{prefix} 偏度", skew),
            _row(f"{prefix} 超额峰度", kurt),
            _row(f"{prefix} ratio>1 模组数", n_gt1),
            _row(f"{prefix} ratio=1 模组数", n_eq1),
            _row(f"{prefix} P1", percentile(sorted_ratios, 0.01)),
            _row(f"{prefix} P5", percentile(sorted_ratios, 0.05)),
            _row(f"{prefix} P10", percentile(sorted_ratios, 0.10)),
            _row(f"{prefix} Q1 (P25)", q1),
            _row(f"{prefix} 中位数 (P50)", med),
            _row(f"{prefix} Q3 (P75)", q3),
            _row(f"{prefix} P90", percentile(sorted_ratios, 0.90)),
            _row(f"{prefix} P95", percentile(sorted_ratios, 0.95)),
            _row(f"{prefix} P99", percentile(sorted_ratios, 0.99)),
            _row(f"{prefix} IQR", q3 - q1),
        ]
    )

    tier_buckets: list[list[float]] = [[] for _ in _RATIO_TIER_LABELS]
    for ratio, subs in ratio_rows:
        for idx, (_, pred) in enumerate(_RATIO_TIER_LABELS):
            if pred(subs):
                tier_buckets[idx].append(ratio)
                break

    for (label, _), bucket in zip(_RATIO_TIER_LABELS, tier_buckets, strict=True):
        tier_prefix = f"{prefix} {label}"
        bn = len(bucket)
        rows.append(_row(f"{tier_prefix}·样本量", bn))
        if bn == 0:
            continue
        rows.append(_row(f"{tier_prefix}·比率均值", sum(bucket) / bn))
        rows.append(_row(f"{tier_prefix}·比率中位数", percentile(sorted(bucket), 0.50)))


def compute_statistic_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []

    all_subscribers = _fetch_all_subscribers(conn)
    filtered_subscribers = _fetch_filtered_subscribers(conn)
    ratio_rows = _fetch_ratio_rows(conn)
    private_like_count = _count_private_like(conn)
    total_mods = len(all_subscribers)

    rows.extend(
        [
            _row("[数据口径] 全体模组数", total_mods),
            _row("[数据口径] 疑似自用模组数", private_like_count),
            _row(
                "[数据口径] 疑似自用模组占比",
                _pct(private_like_count / total_mods) if total_mods else "0.00%",
            ),
            _row("[数据口径] 非自用模组数", len(filtered_subscribers)),
            _row("[数据口径] 订阅/曝光比样本量", len(ratio_rows)),
        ]
    )

    if all_subscribers:
        _append_all_mods_subscriber_rows(rows, calc_stats(all_subscribers))
    if filtered_subscribers:
        _append_filtered_inequality_rows(rows, filtered_subscribers)
    if ratio_rows:
        _append_ratio_rows(rows, ratio_rows)

    return rows


def create_statistic_table(conn: sqlite3.Connection) -> int:
    rows = compute_statistic_rows(conn)
    conn.execute(f'DROP TABLE IF EXISTS "{STATISTIC_TABLE}"')
    conn.execute(
        f"""
        CREATE TABLE "{STATISTIC_TABLE}" (
            统计数据名称 TEXT NOT NULL,
            统计数据数值 TEXT NOT NULL
        );
        """
    )
    conn.executemany(
        f'INSERT INTO "{STATISTIC_TABLE}" (统计数据名称, 统计数据数值) VALUES (?, ?);',
        rows,
    )
    conn.commit()
    return len(rows)
