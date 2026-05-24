import argparse
import math
import sqlite3
from pathlib import Path


def fmt_num(value: float | int) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    abs_value = abs(value)
    if abs_value == 0:
        return "0"
    if abs_value < 0.01:
        return f"{value:,.6f}"
    if abs_value < 1:
        return f"{value:,.4f}"
    return f"{value:,.2f}"


def fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def bar(value: float, width: int = 20) -> str:
    value = max(0.0, min(1.0, value))
    filled = round(value * width)
    return "█" * filled + "░" * (width - filled)


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


def concentration_block(values_desc: list[int], total_mods: int, total_subscribers: int) -> dict[str, dict[str, float | int]]:
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


def pareto_block(values_desc: list[int], total_mods: int, total_subscribers: int) -> dict[str, dict[str, float | int]]:
    targets = [0.5, 0.8, 0.9]
    cumulative = 0
    pointer = 0
    result: dict[str, dict[str, float | int]] = {}
    for target in targets:
        required = 0
        while pointer < len(values_desc):
            if total_subscribers > 0 and cumulative / total_subscribers >= target:
                break
            cumulative += values_desc[pointer]
            pointer += 1
            required = pointer
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


def fetch_all_subscribers(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute("SELECT subscribers FROM mods ORDER BY subscribers DESC, mod_id ASC").fetchall()
    return [int(row[0]) for row in rows]


def render_dashboard(title: str, note: str, stats: dict[str, object]) -> str:
    overview = stats["overview"]
    percentiles = stats["percentiles"]
    concentration = stats["concentration"]
    pareto = stats["pareto"]
    thresholds = stats["thresholds"]
    buckets = stats["buckets"]

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append("> 数据快照")
    lines.append(f"> {note}")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 核心指标")
    lines.append("")
    lines.append("| 指标卡片 | 数值 |")
    lines.append("|---|---:|")
    lines.append(f"| 模组总数 | **{fmt_num(overview['total_mods'])}** |")
    lines.append(f"| 订阅总量 | **{fmt_num(overview['total_subscribers'])}** |")
    lines.append(f"| 平均订阅 | {fmt_num(overview['mean_subscribers'])} |")
    lines.append(f"| 中位数订阅 | {fmt_num(overview['median_subscribers'])} |")
    lines.append(f"| 波动水平（标准差） | {fmt_num(overview['std_subscribers'])} |")
    lines.append(f"| 最低 / 最高订阅 | {fmt_num(overview['min_subscribers'])} / {fmt_num(overview['max_subscribers'])} |")
    lines.append("")
    lines.append("> 观察：均值显著高于中位数时，说明头部拉升效应明显。")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 分位数")
    lines.append("")
    lines.append("| 分位点 | 订阅数 | 可视化 |")
    lines.append("|---|---:|---|")
    max_p = max(percentiles.values()) if percentiles else 1
    max_p = max(max_p, 1)
    lines.append(f"| P10 | {fmt_num(percentiles['p10'])} | `{bar(percentiles['p10'] / max_p)}` |")
    lines.append(f"| P25 | {fmt_num(percentiles['p25'])} | `{bar(percentiles['p25'] / max_p)}` |")
    lines.append(f"| P50 | {fmt_num(percentiles['p50'])} | `{bar(percentiles['p50'] / max_p)}` |")
    lines.append(f"| P75 | {fmt_num(percentiles['p75'])} | `{bar(percentiles['p75'] / max_p)}` |")
    lines.append(f"| P90 | {fmt_num(percentiles['p90'])} | `{bar(percentiles['p90'] / max_p)}` |")
    lines.append(f"| P95 | {fmt_num(percentiles['p95'])} | `{bar(percentiles['p95'] / max_p)}` |")
    lines.append(f"| P99 | {fmt_num(percentiles['p99'])} | `{bar(percentiles['p99'] / max_p)}` |")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 集中度")
    lines.append("")
    lines.append("| 头部分组 | 模组数 | 模组占比 | 订阅总量 | 订阅占比 | 订阅占比条形图 |")
    lines.append("|---|---:|---:|---:|---:|---|")
    label_map = {
        "top_10": "前10名",
        "top_50": "前50名",
        "top_100": "前100名",
        "top_1pct": "前1%",
        "top_5pct": "前5%",
        "top_10pct": "前10%",
    }
    for key in ["top_10", "top_50", "top_100", "top_1pct", "top_5pct", "top_10pct"]:
        item = concentration[key]
        lines.append(
            f"| {label_map[key]} | {fmt_num(item['count'])} | {fmt_pct(item['share_mods'])} | "
            f"{fmt_num(item['sum_subscribers'])} | {fmt_pct(item['share_subscribers'])} | "
            f"`{bar(item['share_subscribers'])}` |"
        )
    lines.append("")
    lines.append(f"- HHI 指数：**{fmt_num(concentration['hhi'])}**")
    lines.append(f"- 基尼系数：**{fmt_num(concentration['gini'])}**")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 帕累托")
    lines.append("")
    lines.append("| 目标订阅占比 | 需要的模组数 | 模组占比 | 模组占比条形图 |")
    lines.append("|---|---:|---:|---|")
    for key in ["share_50pct_subscribers", "share_80pct_subscribers", "share_90pct_subscribers"]:
        item = pareto[key]
        lines.append(
            f"| {fmt_pct(item['target_share'])} | {fmt_num(item['mods_needed'])} | {fmt_pct(item['share_mods'])} | "
            f"`{bar(item['share_mods'])}` |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 阈值分布")
    lines.append("")
    lines.append("| 阈值 | 达到阈值的模组数 | 模组占比 | 模组占比条形图 |")
    lines.append("|---|---:|---:|---|")
    for key in ["at_least_100", "at_least_500", "at_least_1000", "at_least_5000", "at_least_10000"]:
        item = thresholds[key]
        lines.append(
            f"| >= {fmt_num(item['threshold'])} | {fmt_num(item['mods_at_or_above'])} | {fmt_pct(item['share_mods'])} | "
            f"`{bar(item['share_mods'])}` |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 区间分布")
    lines.append("")
    lines.append("| 订阅区间 | 模组数 | 模组占比 | 分布条形图 |")
    lines.append("|---|---:|---:|---|")
    bucket_label = {
        "0_99": "0-99",
        "100_999": "100-999",
        "1000_4999": "1,000-4,999",
        "5000_9999": "5,000-9,999",
        "10000_49999": "10,000-49,999",
        "50000_plus": "50,000+",
    }
    for key in ["0_99", "100_999", "1000_4999", "5000_9999", "10000_49999", "50000_plus"]:
        item = buckets[key]
        lines.append(
            f"| {bucket_label[key]} | {fmt_num(item['mods'])} | {fmt_pct(item['share_mods'])} | "
            f"`{bar(item['share_mods'])}` |"
        )
    lines.append("")

    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent
    parser = argparse.ArgumentParser(description="生成创意工坊全体模组的订阅统计仪表盘。")
    from game_profile import default_db_path, default_result_dir

    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="SQLite 数据库路径（默认 data/table/mods.sqlite3）",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=default_result_dir(project_root) / "subscribers_stats.md",
        help="输出 Markdown 路径（默认 data/result/subscribers_stats.md）",
    )
    ns = parser.parse_args()
    if ns.db_path is None:
        ns.db_path = default_db_path(project_root)
    return ns


def main() -> None:
    from data_paths import ensure_data_dirs

    ensure_data_dirs()
    args = parse_args()
    if not args.db_path.exists():
        raise FileNotFoundError(f"数据库不存在：{args.db_path}")

    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.db_path) as conn:
        subscriber_values = fetch_all_subscribers(conn)

    stats = calc_stats(subscriber_values)
    report_md = render_dashboard(
        "创意工坊订阅数据仪表盘",
        "数据来自 SQLite mods 表（全体模组）",
        stats,
    )
    args.output_md.write_text(report_md, encoding="utf-8")

    print(f"已生成：{args.output_md}")
    print(f"模组数：{stats['overview']['total_mods']}")


if __name__ == "__main__":
    main()
