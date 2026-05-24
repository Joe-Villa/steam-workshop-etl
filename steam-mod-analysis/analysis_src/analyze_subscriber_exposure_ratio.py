from __future__ import annotations

import argparse
import math
import sqlite3
from pathlib import Path
from typing import NamedTuple

import matplotlib.pyplot as plt
import numpy as np

from game_profile import (
    DEFAULT_GAME_KEY,
    PROFILES,
    GameProfile,
    default_db_path,
    default_result_dir,
    figures_subdir,
    figure_stem,
    format_report_path,
    game_scope_line,
    get_profile,
    output_basename,
)

try:
    from openpyxl import Workbook
except ImportError as e:  # pragma: no cover
    Workbook = None  # type: ignore[misc, assignment]
    _OPENPYXL_ERR = e
else:
    _OPENPYXL_ERR = None


DEFAULT_TABLE = "mod_subscriber_exposure_ratio"

# (报告/图表标题, Excel 工作表名, 文件名片段, 订阅量判定)
TIER_SPECS: list[tuple[str, str, str, str]] = [
    ("订阅量 100 以下", "订阅＜100", "tier_lt100", "subscribers < 100"),
    ("订阅量 100–1000", "订阅100-1000", "tier_100_999", "100 ≤ subscribers < 1,000"),
    ("订阅量 1000–10000", "订阅1k-9999", "tier_1k_9999", "1,000 ≤ subscribers < 10,000"),
    ("订阅量 10000 以上", "订阅≥1万", "tier_10k_plus", "subscribers ≥ 10,000"),
]


def subscriber_tier_index(subscribers: int) -> int:
    if subscribers < 100:
        return 0
    if subscribers < 1000:
        return 1
    if subscribers < 10000:
        return 2
    return 3


class ModRatioRow(NamedTuple):
    mod_id: str
    mod_name: str
    subscribers: int
    ratio: float


def kde_gaussian(grid: np.ndarray, sample: np.ndarray) -> np.ndarray:
    n = sample.size
    if n < 2:
        return np.zeros_like(grid)
    std = float(np.std(sample, ddof=1))
    if std == 0 or math.isnan(std):
        return np.zeros_like(grid)
    bandwidth = 1.06 * std * (n ** (-1 / 5))
    if bandwidth <= 0:
        return np.zeros_like(grid)
    z = (grid[:, None] - sample[None, :]) / bandwidth
    kernel = np.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    return np.mean(kernel, axis=1) / bandwidth


def sample_skewness(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    n = x.size
    if n < 3:
        return float("nan")
    m = float(np.mean(x))
    s = float(np.std(x, ddof=1))
    if s == 0:
        return 0.0
    return float(np.mean(((x - m) / s) ** 3))


def sample_kurtosis_excess(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    n = x.size
    if n < 4:
        return float("nan")
    m = float(np.mean(x))
    s = float(np.std(x, ddof=1))
    if s == 0:
        return float("nan")
    z = (x - m) / s
    return float(np.mean(z**4) - 3.0)


def load_mod_ratio_rows(db_path: Path, table_name: str) -> list[ModRatioRow]:
    sql = f"""
        SELECT
            r.mod_id,
            m.mod_name,
            m.subscribers,
            r.subscriber_exposure_ratio
        FROM "{table_name}" AS r
        INNER JOIN mods AS m ON m.mod_id = r.mod_id
        ORDER BY r.mod_id;
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(sql).fetchall()
    out: list[ModRatioRow] = []
    for mod_id, mod_name, subs, ratio in rows:
        out.append(
            ModRatioRow(
                str(mod_id),
                str(mod_name) if mod_name is not None else "",
                int(subs),
                float(ratio),
            )
        )
    return out


def sort_by_ratio_desc(rows: list[ModRatioRow]) -> list[ModRatioRow]:
    return sorted(rows, key=lambda r: (-r.ratio, r.mod_id))


def build_figures(figure_dir: Path, ratios: np.ndarray, name_prefix: str, game: GameProfile) -> None:
    if ratios.size == 0:
        return

    figure_dir.mkdir(parents=True, exist_ok=True)

    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "SimHei", "Noto Sans CJK SC"]
    plt.rcParams["axes.unicode_minus"] = False

    # Box plot
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.boxplot(
        ratios,
        vert=True,
        showmeans=True,
        meanline=True,
        widths=0.35,
    )
    ax.set_ylabel("subscribers / exposure")
    ax.set_title(f"{game.display_name} — Subscriber / exposure ratio (box plot)")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(figure_dir / f"{name_prefix}_boxplot.png", dpi=180)
    plt.close(fig)

    # Histogram (density)
    n = ratios.size
    n_bins = int(max(1, min(60, max(5, round(math.sqrt(n))))))
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.hist(
        ratios,
        bins=n_bins,
        density=True,
        color="#4C72B0",
        alpha=0.75,
        edgecolor="white",
        linewidth=0.5,
    )
    ax.set_xlabel("subscribers / exposure")
    ax.set_ylabel("Density")
    ax.set_title("Histogram (density)")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(figure_dir / f"{name_prefix}_histogram.png", dpi=180)
    plt.close(fig)

    # KDE
    lo, hi = float(np.min(ratios)), float(np.max(ratios))
    pad = (hi - lo) * 0.02 + 1e-6
    grid = np.linspace(lo - pad, hi + pad, 400)
    density = kde_gaussian(grid, ratios)

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(grid, density, color="#C44E52", linewidth=2)
    ax.fill_between(grid, density, alpha=0.15, color="#C44E52")
    ax.set_xlabel("subscribers / exposure")
    ax.set_ylabel("Density")
    ax.set_title("Kernel density estimate (Gaussian kernel, Silverman-like bandwidth)")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(figure_dir / f"{name_prefix}_kde.png", dpi=180)
    plt.close(fig)


def build_tier_violin_comparison(
    figure_dir: Path, tier_rows: list[list[ModRatioRow]], game: GameProfile
) -> None:
    """各订阅量分组的比率分布，单图小提琴对比（仅包含有样本的档）。"""
    figure_dir.mkdir(parents=True, exist_ok=True)

    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "SimHei", "Noto Sans CJK SC"]
    plt.rcParams["axes.unicode_minus"] = False

    labels_short = ["<100", "100–1k", "1k–10k", "≥10k"]
    plot_data: list[np.ndarray] = []
    plot_pos: list[int] = []
    labels_used: list[str] = []

    for i, bucket in enumerate(tier_rows):
        arr = np.array([r.ratio for r in bucket], dtype=float)
        if arr.size == 0:
            continue
        plot_data.append(arr)
        plot_pos.append(len(plot_pos) + 1)
        labels_used.append(labels_short[i])

    fig, ax = plt.subplots(figsize=(10, 5.5))
    if plot_data:
        parts = ax.violinplot(
            plot_data,
            positions=plot_pos,
            vert=True,
            showmeans=True,
            showmedians=True,
            showextrema=True,
            widths=0.72,
        )
        for pc in parts["bodies"]:
            pc.set_facecolor("#4C72B0")
            pc.set_alpha(0.55)
            pc.set_edgecolor("#333333")
            pc.set_linewidth(0.6)
        for key in ("cbars", "cmins", "cmaxes", "cmeans", "cmedians"):
            if key in parts:
                parts[key].set_color("#333333")
                parts[key].set_linewidth(0.8)
        ax.set_xticks(plot_pos)
        ax.set_xticklabels(labels_used)
    ax.set_ylabel("subscribers / exposure")
    ax.set_xlabel("Subscriber count tier")
    ax.set_title(f"{game.display_name} — Ratio by subscriber tier (violin)")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    violin_name = f"{figure_stem(game.slug, 'subscriber_exposure_ratio_tiers_violin')}.png"
    fig.savefig(figure_dir / violin_name, dpi=180)
    plt.close(fig)


def _stats_tables_markdown(ratios: np.ndarray) -> tuple[list[str], list[str]]:
    """描述统计 + 分位数两段 markdown 行（无标题）。"""
    n = ratios.size
    if n == 0:
        empty = ["", "_本组样本量为 0，无统计量。_", ""]
        return empty, empty

    mean_v = float(np.mean(ratios))
    std_v = float(np.std(ratios, ddof=1))
    med = float(np.median(ratios))
    q1, q3 = float(np.percentile(ratios, 25)), float(np.percentile(ratios, 75))
    iqr = q3 - q1
    cv = std_v / mean_v if mean_v else float("nan")

    def pct(q: float) -> float:
        return float(np.percentile(ratios, q))

    p = {1: pct(1), 5: pct(5), 10: pct(10), 25: q1, 75: q3, 90: pct(90), 95: pct(95), 99: pct(99)}
    mn, mx = float(np.min(ratios)), float(np.max(ratios))
    skew = sample_skewness(ratios)
    kurt_ex = sample_kurtosis_excess(ratios)

    n_gt1 = int(np.sum(ratios > 1.0))
    n_eq1 = int(np.sum(ratios == 1.0))

    desc: list[str] = [
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 样本量 n | {n:,} |",
        f"| 均值 | {mean_v:.6f} |",
        f"| 标准差 | {std_v:.6f} |",
        f"| 变异系数 (std/mean) | {cv:.6f} |",
        f"| 最小值 | {mn:.6f} |",
        f"| 最大值 | {mx:.6f} |",
        f"| 极差 | {mx - mn:.6f} |",
        f"| 偏度 (样本) | {skew:.6f} |",
        f"| 超额峰度 (样本) | {kurt_ex:.6f} |",
        f"| ratio > 1 的模组数 | {n_gt1} |",
        f"| ratio = 1 的模组数 | {n_eq1} |",
        "",
    ]
    quant: list[str] = [
        "| 分位 | 数值 |",
        "|---|---:|",
        f"| P1 | {p[1]:.6f} |",
        f"| P5 | {p[5]:.6f} |",
        f"| P10 | {p[10]:.6f} |",
        f"| Q1 (P25) | {p[25]:.6f} |",
        f"| 中位数 (P50) | {med:.6f} |",
        f"| Q3 (P75) | {p[75]:.6f} |",
        f"| P90 | {p[90]:.6f} |",
        f"| P95 | {p[95]:.6f} |",
        f"| P99 | {p[99]:.6f} |",
        f"| IQR (Q3 − Q1) | {iqr:.6f} |",
        "",
    ]
    return desc, quant


def write_report(
    output_path: Path,
    figure_rel_dir: str,
    figure_prefix: str,
    all_rows: list[ModRatioRow],
    tier_rows: list[list[ModRatioRow]],
    game: GameProfile,
    db_path_label: str,
) -> None:
    ratios = np.array([r.ratio for r in all_rows], dtype=float)
    n = ratios.size

    mean_v = float(np.mean(ratios))
    std_v = float(np.std(ratios, ddof=1))
    med = float(np.median(ratios))
    q1, q3 = float(np.percentile(ratios, 25)), float(np.percentile(ratios, 75))
    iqr = q3 - q1
    cv = std_v / mean_v if mean_v else float("nan")

    def pct(q: float) -> float:
        return float(np.percentile(ratios, q))

    p = {1: pct(1), 5: pct(5), 10: pct(10), 25: q1, 75: q3, 90: pct(90), 95: pct(95), 99: pct(99)}
    mn, mx = float(np.min(ratios)), float(np.max(ratios))
    skew = sample_skewness(ratios)
    kurt_ex = sample_kurtosis_excess(ratios)

    n_gt1 = int(np.sum(ratios > 1.0))
    n_eq1 = int(np.sum(ratios == 1.0))

    lines: list[str] = []
    lines.append(f"# {game.display_name} 订阅 / 曝光 比例分布实验报告")
    lines.append("")
    lines.append("## 1. 数据说明")
    lines.append("")
    lines.append(game_scope_line(game))
    lines.append(f"- 数据源：`{db_path_label}`，表 `mod_subscriber_exposure_ratio` JOIN `mods`。")
    lines.append("- 数据为模组维度汇总：每个模组有当前订阅量、Unique Visitors，以及二者之比（订阅 / 曝光）。")
    lines.append("- 样本口径：与建表脚本一致——排除 `exposure = 0`，并排除 `mod_private_like_flags.is_private_like = 1` 的疑似自用模组。")
    lines.append(f"- 样本量：**{n:,}**。")
    lines.append("")
    lines.append("## 2. 描述统计")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---:|")
    lines.append(f"| 样本量 n | {n:,} |")
    lines.append(f"| 均值 | {mean_v:.6f} |")
    lines.append(f"| 标准差 | {std_v:.6f} |")
    lines.append(f"| 变异系数 (std/mean) | {cv:.6f} |")
    lines.append(f"| 最小值 | {mn:.6f} |")
    lines.append(f"| 最大值 | {mx:.6f} |")
    lines.append(f"| 极差 | {mx - mn:.6f} |")
    lines.append(f"| 偏度 (样本) | {skew:.6f} |")
    lines.append(f"| 超额峰度 (样本) | {kurt_ex:.6f} |")
    lines.append(f"| ratio > 1 的模组数 | {n_gt1} |")
    lines.append(f"| ratio = 1 的模组数 | {n_eq1} |")
    lines.append("")
    lines.append("## 3. 分位数与四分位")
    lines.append("")
    lines.append("| 分位 | 数值 |")
    lines.append("|---|---:|")
    lines.append(f"| P1 | {p[1]:.6f} |")
    lines.append(f"| P5 | {p[5]:.6f} |")
    lines.append(f"| P10 | {p[10]:.6f} |")
    lines.append(f"| Q1 (P25) | {p[25]:.6f} |")
    lines.append(f"| 中位数 (P50) | {med:.6f} |")
    lines.append(f"| Q3 (P75) | {p[75]:.6f} |")
    lines.append(f"| P90 | {p[90]:.6f} |")
    lines.append(f"| P95 | {p[95]:.6f} |")
    lines.append(f"| P99 | {p[99]:.6f} |")
    lines.append(f"| IQR (Q3 − Q1) | {iqr:.6f} |")
    lines.append("")
    lines.append("## 4. 箱线图")
    lines.append("")
    lines.append(f"![]({figure_rel_dir}/{figure_prefix}_boxplot.png)")
    lines.append("")
    lines.append("## 5. 直方图")
    lines.append("")
    lines.append(f"![]({figure_rel_dir}/{figure_prefix}_histogram.png)")
    lines.append("")
    lines.append("## 6. 核密度估计")
    lines.append("")
    lines.append(f"![]({figure_rel_dir}/{figure_prefix}_kde.png)")
    lines.append("")
    lines.append("## 7. 简注")
    lines.append("")
    lines.append("- 比例为「当前订阅 / Unique Visitors」；多数模组小于 1（访客多于订阅转化）。")
    lines.append("- KDE 使用高斯核，带宽按正态参考规则 `1.06 * σ * n^(-1/5)`，与直方图密度对照阅读即可。")
    lines.append("")

    # —— 按订阅量分组 ——
    lines.append("## 8. 按订阅量分组说明")
    lines.append("")
    lines.append("分组以 `mods.subscribers`（当前订阅量）为准，边界为左闭右开区间，末组为闭区间：")
    lines.append("")
    lines.append("| 组别 | 订阅量范围（subscribers） |")
    lines.append("|---|---|")
    for (title_md, sheet, _slug, human), bucket in zip(TIER_SPECS, tier_rows, strict=True):
        lines.append(f"| {title_md} | {human} |")
    lines.append("")
    lines.append("各组样本量与比率（subscriber_exposure_ratio）的均值、中位数：")
    lines.append("")
    lines.append("| 组别（工作表名） | n | 比率均值 | 比率中位数 |")
    lines.append("|---|---:|---:|---:|")
    for (title_md, sheet, _slug, human), bucket in zip(TIER_SPECS, tier_rows, strict=True):
        br = np.array([r.ratio for r in bucket], dtype=float)
        if br.size == 0:
            m1, m2 = float("nan"), float("nan")
        else:
            m1, m2 = float(np.mean(br)), float(np.median(br))
        lines.append(f"| {title_md} | {br.size:,} | {m1:.6f} | {m2:.6f} |")
    lines.append("")
    lines.append("## 9. 各订阅量分组：比率分布对比（小提琴图）")
    lines.append("")
    lines.append(
        "- 下图将四个订阅量档位的「订阅 / 曝光」比率放在同一坐标系中对比；"
        "小提琴宽度表示该档内比率的核密度（与常见箱线+KDE 解读方式一致）。"
    )
    lines.append("")
    lines.append(
        f"![]({figure_rel_dir}/{figure_stem(game.slug, 'subscriber_exposure_ratio_tiers_violin')}.png)"
    )
    lines.append("")

    for idx, ((title_md, sheet, _slug, human), bucket) in enumerate(
        zip(TIER_SPECS, tier_rows, strict=True), start=1
    ):
        section = 9 + idx
        lines.append(f"## {section}. 分组：{title_md}")
        lines.append("")
        lines.append("### 描述统计")
        lines.append("")
        br = np.array([r.ratio for r in bucket], dtype=float)
        desc, quant = _stats_tables_markdown(br)
        lines.extend(desc)
        lines.append("### 分位数与四分位")
        lines.append("")
        lines.extend(quant)

    lines.append("## 14. 导出数据")
    lines.append("")
    lines.append("- 另附 Excel：五个工作表（总表 + 上述四组），列为模组 id、模组名称、订阅量、比率；各表按比率降序。")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_excel(path: Path, all_sorted: list[ModRatioRow], tier_sorted: list[list[ModRatioRow]]) -> None:
    if Workbook is None:
        raise RuntimeError(
            "需要 openpyxl 才能写出 .xlsx。请运行：python3 -m pip install openpyxl"
        ) from _OPENPYXL_ERR

    wb = Workbook()
    default_ws = wb.active
    wb.remove(default_ws)

    def fill_sheet(title: str, rows: list[ModRatioRow]) -> None:
        safe_title = title[:31]
        ws = wb.create_sheet(title=safe_title)
        ws.append(["模组id", "模组名称", "订阅量", "比率"])
        for r in rows:
            ws.append([r.mod_id, r.mod_name, r.subscribers, r.ratio])

    fill_sheet("总表", all_sorted)
    for (_, sheet, _, _), rows in zip(TIER_SPECS, tier_sorted, strict=True):
        fill_sheet(sheet, rows)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    parser = argparse.ArgumentParser(
        description=(
            "Stats + box/hist/KDE for full sample; subscriber tiers get tables + one violin comparison chart."
        )
    )
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
        "--table",
        type=str,
        default=DEFAULT_TABLE,
        help="Table with subscriber_exposure_ratio column.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Markdown 报告路径（默认 subscriber_exposure_ratio_report.md 或带游戏前缀）。",
    )
    parser.add_argument(
        "--excel",
        type=Path,
        default=None,
        help="Excel 路径（默认 subscriber_exposure_ratio_by_subscribers_tier.xlsx 或带游戏前缀）。",
    )
    parser.add_argument(
        "--figures-dir",
        type=Path,
        default=None,
        help="PNG 图目录（默认 data/result/figures 或带游戏前缀）。",
    )
    args = parser.parse_args()

    from data_paths import ensure_data_dirs

    ensure_data_dirs()
    result_dir = default_result_dir(project_root)

    profile = get_profile(args.game)
    db_path = (args.db or default_db_path(project_root)).resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    all_rows = load_mod_ratio_rows(db_path, args.table)
    if not all_rows:
        raise RuntimeError(f"No rows in table {args.table!r} (after join with mods).")

    tier_rows: list[list[ModRatioRow]] = [[] for _ in TIER_SPECS]
    for r in all_rows:
        tier_rows[subscriber_tier_index(r.subscribers)].append(r)

    ratios_all = np.array([r.ratio for r in all_rows], dtype=float)
    figures_dir = (args.figures_dir or result_dir / figures_subdir(profile.slug)).resolve()
    fig_prefix = figure_stem(profile.slug, "subscriber_exposure_ratio")
    build_figures(figures_dir, ratios_all, fig_prefix, profile)
    build_tier_violin_comparison(figures_dir, tier_rows, profile)

    output_path = (
        args.output
        or result_dir / output_basename(profile.slug, "subscriber_exposure_ratio_report.md")
    ).resolve()
    figure_rel = Path(figures_dir.name).as_posix()
    write_report(
        output_path,
        figure_rel,
        fig_prefix,
        all_rows,
        tier_rows,
        profile,
        format_report_path(db_path, anchor=project_root),
    )

    all_sorted = sort_by_ratio_desc(all_rows)
    tier_sorted = [sort_by_ratio_desc(b) for b in tier_rows]
    excel_path = (
        args.excel
        or result_dir
        / output_basename(profile.slug, "subscriber_exposure_ratio_by_subscribers_tier.xlsx")
    ).resolve()
    write_excel(excel_path, all_sorted, tier_sorted)

    print(f"Report: {output_path}")
    print(f"Excel: {excel_path}")
    print(f"Figures: {figures_dir}")


if __name__ == "__main__":
    main()
