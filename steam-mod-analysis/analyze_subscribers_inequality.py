from __future__ import annotations

import argparse
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from game_profile import (
    DEFAULT_GAME_KEY,
    PROFILES,
    GameProfile,
    default_db_path,
    default_result_dir,
    figures_subdir,
    format_report_path,
    game_scope_line,
    get_profile,
    output_basename,
)


@dataclass
class DagumGroupPair:
    left_group: str
    right_group: str
    pair_weight: float
    g_between_pair: float
    d_gh: float
    net_between_contribution: float
    transvariation_contribution: float


def gini(values: np.ndarray) -> float:
    x = np.asarray(values, dtype=float)
    if x.size == 0:
        return 0.0
    mean_x = np.mean(x)
    if mean_x <= 0:
        return 0.0
    sorted_x = np.sort(x)
    n = sorted_x.size
    ranks = np.arange(1, n + 1, dtype=float)
    return float((2 * np.sum(ranks * sorted_x) / (n * np.sum(sorted_x))) - ((n + 1) / n))


def percentile(values: np.ndarray, q: float) -> float:
    if values.size == 0:
        return 0.0
    return float(np.percentile(values, q))


def share_top(values: np.ndarray, p: float) -> float:
    n = values.size
    if n == 0:
        return 0.0
    k = max(1, math.ceil(n * p))
    sorted_x = np.sort(values)
    return float(np.sum(sorted_x[-k:]) / np.sum(sorted_x))


def share_bottom(values: np.ndarray, p: float) -> float:
    n = values.size
    if n == 0:
        return 0.0
    k = max(1, math.ceil(n * p))
    sorted_x = np.sort(values)
    return float(np.sum(sorted_x[:k]) / np.sum(sorted_x))


def lorenz_points(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    sorted_x = np.sort(values)
    n = sorted_x.size
    if n == 0:
        return np.array([0.0, 1.0]), np.array([0.0, 1.0])
    cum_x = np.cumsum(sorted_x)
    total = cum_x[-1]
    cum_share_resource = np.concatenate(([0.0], cum_x / total))
    cum_share_population = np.concatenate(([0.0], np.arange(1, n + 1) / n))
    return cum_share_population, cum_share_resource


def kde_gaussian(grid: np.ndarray, sample: np.ndarray) -> np.ndarray:
    n = sample.size
    if n < 2:
        return np.zeros_like(grid)
    std = np.std(sample, ddof=1)
    if std == 0:
        return np.zeros_like(grid)
    bandwidth = 1.06 * std * (n ** (-1 / 5))
    if bandwidth <= 0:
        return np.zeros_like(grid)
    z = (grid[:, None] - sample[None, :]) / bandwidth
    kernel = np.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    return np.mean(kernel, axis=1) / bandwidth


def dagum_decomposition(
    values: np.ndarray, group_labels: np.ndarray
) -> tuple[float, float, float, list[DagumGroupPair]]:
    overall_gini = gini(values)
    n = values.size
    mu = float(np.mean(values))
    unique_groups = sorted(set(group_labels.tolist()))
    by_group: dict[str, np.ndarray] = {g: values[group_labels == g] for g in unique_groups}

    within = 0.0
    group_stats: dict[str, tuple[int, float, float, float, float]] = {}
    for group, arr in by_group.items():
        n_g = arr.size
        mu_g = float(np.mean(arr)) if n_g > 0 else 0.0
        p_g = n_g / n
        s_g = (n_g * mu_g) / (n * mu) if mu > 0 else 0.0
        g_g = gini(arr)
        within += g_g * p_g * s_g
        group_stats[group] = (n_g, mu_g, p_g, s_g, g_g)

    net_between = 0.0
    transvariation = 0.0
    pair_details: list[DagumGroupPair] = []

    ordered_groups = sorted(unique_groups, key=lambda g: group_stats[g][1])
    for i in range(len(ordered_groups)):
        for j in range(i + 1, len(ordered_groups)):
            g_h = ordered_groups[i]
            g_k = ordered_groups[j]
            arr_h = by_group[g_h]
            arr_k = by_group[g_k]
            n_h, _, p_h, s_h, _ = group_stats[g_h]
            n_k, _, p_k, s_k, _ = group_stats[g_k]
            if n_h == 0 or n_k == 0:
                continue

            diff = arr_k[:, None] - arr_h[None, :]
            mean_abs_diff = float(np.mean(np.abs(diff)))
            g_between_pair = mean_abs_diff / (2.0 * mu) if mu > 0 else 0.0

            d_hk = float(np.mean(np.maximum(diff, 0.0)))
            p_hk = float(np.mean(np.maximum(-diff, 0.0)))
            denom = d_hk + p_hk
            if denom == 0:
                overlap_intensity = 0.0
            else:
                overlap_intensity = (d_hk - p_hk) / denom

            pair_weight = p_k * s_h + p_h * s_k
            net_pair = g_between_pair * pair_weight * overlap_intensity
            trans_pair = g_between_pair * pair_weight * (1.0 - overlap_intensity)
            net_between += net_pair
            transvariation += trans_pair

            pair_details.append(
                DagumGroupPair(
                    left_group=g_h,
                    right_group=g_k,
                    pair_weight=pair_weight,
                    g_between_pair=g_between_pair,
                    d_gh=overlap_intensity,
                    net_between_contribution=net_pair,
                    transvariation_contribution=trans_pair,
                )
            )

    residual = overall_gini - (within + net_between + transvariation)
    if abs(residual) > 1e-6:
        transvariation += residual
    return within, net_between, transvariation, pair_details


def load_data(db_path: Path) -> tuple[np.ndarray, np.ndarray]:
    from data_paths import MODS_TABLE
    from sample_scope import join_private_like_flags, non_private_like_predicate

    flags_join = join_private_like_flags("m")
    non_private = non_private_like_predicate()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%';
                """
            ).fetchall()
        }
        has_domestic = "mod_browse_info" in tables and "authors" in tables

        if has_domestic:
            query = f"""
            SELECT
                m.subscribers AS subscribers,
                COALESCE(a.is_chinese, 0) AS is_chinese
            FROM "{MODS_TABLE}" m
            LEFT JOIN mod_browse_info b ON b.mod_id = m.mod_id
            LEFT JOIN authors a ON a.username = b.author
            {flags_join}
            WHERE m.subscribers IS NOT NULL
              AND m.subscribers >= 0
              AND {non_private}
            """
        else:
            query = f"""
            SELECT
                m.subscribers AS subscribers,
                0 AS is_chinese
            FROM "{MODS_TABLE}" m
            {flags_join}
            WHERE m.subscribers IS NOT NULL
              AND m.subscribers >= 0
              AND {non_private}
            """
        rows = conn.execute(query).fetchall()
    subscribers = np.array([r[0] for r in rows], dtype=float)
    groups = np.array(["国模" if int(r[1]) == 1 else "非国模" for r in rows], dtype=object)
    return subscribers, groups


def write_markdown_report(
    output_path: Path,
    figure_dir: Path,
    subscribers: np.ndarray,
    groups: np.ndarray,
    game: GameProfile,
    db_path_label: str,
) -> None:
    from sample_scope import SAMPLE_SCOPE_NOTE
    def fmt_ratio(value: float) -> str:
        if math.isinf(value):
            return "不可计算（分母分位数为 0）"
        return f"{value:.2f}"

    n = subscribers.size
    total = float(np.sum(subscribers))
    mean = float(np.mean(subscribers))
    med = float(np.median(subscribers))
    min_v = float(np.min(subscribers))
    max_v = float(np.max(subscribers))
    g = gini(subscribers)

    p1 = percentile(subscribers, 1)
    p10 = percentile(subscribers, 10)
    p90 = percentile(subscribers, 90)
    p99 = percentile(subscribers, 99)

    top1_share = share_top(subscribers, 0.01)
    top5_share = share_top(subscribers, 0.05)
    top10_share = share_top(subscribers, 0.10)
    bottom10_share = share_bottom(subscribers, 0.10)
    bottom20_share = share_bottom(subscribers, 0.20)
    bottom40_share = share_bottom(subscribers, 0.40)

    p90_p10 = p90 / p10 if p10 > 0 else float("inf")
    p99_p1 = p99 / p1 if p1 > 0 else float("inf")
    palma = top10_share / bottom40_share if bottom40_share > 0 else float("inf")

    within, between, trans, pair_details = dagum_decomposition(subscribers, groups)
    figure_rel = Path(figure_dir.name).as_posix()

    lines: list[str] = []
    lines.append(f"# {game.display_name} 模组订阅不平等分析报告")
    lines.append("")
    lines.append("## 1. 数据说明")
    lines.append("")
    lines.append(game_scope_line(game))
    lines.append(f"- 数据源：`{db_path_label}`，字段 `aaa_mods.subscribers`。")
    lines.append(f"- {SAMPLE_SCOPE_NOTE}")
    lines.append(f"- 样本量：`{n:,}`。")
    lines.append(f"- 总订阅量：`{total:,.0f}`。")
    lines.append("")
    lines.append("## 2. 收入/财富份额（订阅份额）")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---:|")
    lines.append(f"| 顶层 1% 份额 | {top1_share * 100:.2f}% |")
    lines.append(f"| 顶层 5% 份额 | {top5_share * 100:.2f}% |")
    lines.append(f"| 顶层 10% 份额 | {top10_share * 100:.2f}% |")
    lines.append(f"| 底层 10% 份额 | {bottom10_share * 100:.4f}% |")
    lines.append(f"| 底层 20% 份额 | {bottom20_share * 100:.4f}% |")
    lines.append("")
    lines.append("## 3. 分位数比率")
    lines.append("")
    lines.append("| 指标 | 数值 | 解释 |")
    lines.append("|---|---:|---|")
    lines.append(f"| P90/P10 | {fmt_ratio(p90_p10)} | 90 分位订阅数是 10 分位的倍数。 |")
    lines.append(f"| P99/P1 | {fmt_ratio(p99_p1)} | 极端头尾差距。 |")
    lines.append(f"| Palma | {fmt_ratio(palma)} | 顶层 10% 份额 / 底层 40% 份额。 |")
    lines.append("")
    lines.append("## 4. 分布概览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---:|")
    lines.append(f"| 最小值 | {min_v:,.0f} |")
    lines.append(f"| P1 | {p1:,.2f} |")
    lines.append(f"| P10 | {p10:,.2f} |")
    lines.append(f"| 中位数 | {med:,.2f} |")
    lines.append(f"| 均值 | {mean:,.2f} |")
    lines.append(f"| P90 | {p90:,.2f} |")
    lines.append(f"| P99 | {p99:,.2f} |")
    lines.append(f"| 最大值 | {max_v:,.0f} |")
    lines.append(f"| 基尼系数 | {g:.6f} |")
    lines.append("")
    lines.append("## 5. 洛伦茨曲线")
    lines.append("")
    lines.append(f"![]({figure_rel}/lorenz_curve.png)")
    lines.append("")
    lines.append("## 6. 直方图与 KDE")
    lines.append("")
    lines.append(f"![]({figure_rel}/subscribers_hist_kde.png)")
    lines.append("")
    lines.append("## 7. Dagum 基尼分解（按作者是否国模）")
    lines.append("")
    lines.append("| 组成部分 | 数值 | 占总体基尼比重 |")
    lines.append("|---|---:|---:|")
    lines.append(f"| 组内差异 (Within) | {within:.6f} | {within / g * 100:.2f}% |")
    lines.append(f"| 组间净差异 (Between) | {between:.6f} | {between / g * 100:.2f}% |")
    lines.append(f"| 超变密度 (Transvariation) | {trans:.6f} | {trans / g * 100:.2f}% |")
    lines.append(f"| 总体基尼 | {g:.6f} | 100.00% |")
    lines.append("")
    lines.append("### 7.1 Dagum 组对明细")
    lines.append("")
    lines.append("| 组对 | pair_weight | G_jh | D_jh | 组间净差异贡献 | 超变密度贡献 |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for item in pair_details:
        lines.append(
            f"| {item.left_group} vs {item.right_group} | "
            f"{item.pair_weight:.6f} | {item.g_between_pair:.6f} | {item.d_gh:.6f} | "
            f"{item.net_between_contribution:.6f} | {item.transvariation_contribution:.6f} |"
        )
    lines.append("")
    lines.append("## 8. 解释与结论")
    lines.append("")
    lines.append("- 头部份额和 Palma 比率用于衡量“资源向顶层集中”的程度。")
    lines.append("- P90/P10、P99/P1 对应常见分布差距与极端尾部差距。")
    lines.append("- 洛伦茨曲线越偏离 45 度线，表示不平等越明显。")
    lines.append("- Dagum 分解把总体不平等拆成：组内、组间净差异、跨组重叠（超变密度）。")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def build_figures(figure_dir: Path, subscribers: np.ndarray, game: GameProfile) -> None:
    figure_dir.mkdir(parents=True, exist_ok=True)

    lx, ly = lorenz_points(subscribers)
    plt.figure(figsize=(7.5, 6))
    plt.plot(lx, ly, linewidth=2, label="Lorenz Curve")
    plt.plot([0, 1], [0, 1], linestyle="--", linewidth=1.5, label="Equality Line")
    plt.title(f"{game.display_name} — Lorenz Curve of Mod Subscribers")
    plt.xlabel("Cumulative Population Share")
    plt.ylabel("Cumulative Subscriber Share")
    plt.legend()
    plt.grid(alpha=0.2)
    plt.tight_layout()
    plt.savefig(figure_dir / "lorenz_curve.png", dpi=180)
    plt.close()

    log_sub = np.log10(subscribers + 1.0)
    grid = np.linspace(log_sub.min(), log_sub.max(), 200)
    density = kde_gaussian(grid, log_sub)

    fig, ax1 = plt.subplots(figsize=(9, 6))
    ax1.hist(log_sub, bins=50, density=True, alpha=0.5, color="#4C72B0", label="Histogram")
    ax1.plot(grid, density, color="#C44E52", linewidth=2, label="KDE")
    ax1.set_title(f"{game.display_name} — Subscribers Distribution (log10(subscribers + 1))")
    ax1.set_xlabel("log10(subscribers + 1)")
    ax1.set_ylabel("Density")
    ax1.grid(alpha=0.2)
    ax1.legend()
    fig.tight_layout()
    fig.savefig(figure_dir / "subscribers_hist_kde.png", dpi=180)
    plt.close(fig)


def main() -> None:
    from data_paths import ensure_data_dirs

    ensure_data_dirs()
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    result_dir = default_result_dir(project_root)

    parser = argparse.ArgumentParser(description="Analyze subscribers inequality from sqlite.")
    parser.add_argument(
        "--game",
        choices=sorted(PROFILES),
        default=DEFAULT_GAME_KEY,
        help="workshop（默认）/ civ6 / vic3：控制报告标题与输出文件名前缀。",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite 路径（默认 data/table/mods.sqlite3）。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Markdown 报告路径（默认 subscribers_inequality_report.md 或带游戏前缀）。",
    )
    args = parser.parse_args()

    profile = get_profile(args.game)
    db_path = (args.db or default_db_path(project_root)).resolve()
    output_path = (
        args.output
        or result_dir / output_basename(profile.slug, "subscribers_inequality_report.md")
    ).resolve()
    figure_dir = output_path.parent / figures_subdir(profile.slug)

    subscribers, groups = load_data(db_path)
    if subscribers.size == 0:
        raise RuntimeError("No subscribers data loaded from sqlite.")

    build_figures(figure_dir, subscribers, profile)
    write_markdown_report(
        output_path,
        figure_dir,
        subscribers,
        groups,
        game=profile,
        db_path_label=format_report_path(db_path, anchor=project_root),
    )
    print(f"Report written to: {output_path}")
    print(f"Figures written to: {figure_dir}")


if __name__ == "__main__":
    main()
