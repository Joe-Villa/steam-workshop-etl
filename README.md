# Steam 创意工坊多阶段数据采集与分析流水线

[English](#english) · 中文文档见下文

> **Purpose:** End-to-end **ETL pipeline** for Steam Workshop — discover mods, crawl detail pages, load SQLite, export reports. Built for long-running, resumable collection at scale.

```
  APPID ──► simple_info ──► detail_fetch ──► grant_table ──► mod_analysis
           (browse HTML)    (async crawl)    (parse→SQLite)   (stats/report)
                │                  │                │                 │
                └──────── pipeline_state.json (resume / stage gate) ──┘
```

## English

**steam-workshop-etl** is a Python pipeline that takes a single Steam **APPID** and runs:

1. **simple_info** — browse-page discovery (tag-crossing to work around Steam’s 1,667 pages/tag cap)
2. **detail_fetch** — async HTML crawler (`aiohttp`, multi-port pool, 429 backoff)
3. **grant_table** — parse HTML into an **11-table SQLite** warehouse + Excel export
4. **mod_analysis** — inequality / Pareto metrics, **125** auto-generated statistics, CSV & charts

**Verified locally (not shipped in repo):** two full runs (~36k / ~63k mods); largest in-progress job ~316k URLs (~67k detail pages fetched). Crawler bench target **≥4 pages/s**.

```bash
python3 -m venv resumable-batch-fetch/.venv
resumable-batch-fetch/.venv/bin/pip install -r resumable-batch-fetch/requirements.txt
pip install -r requirements.txt
python test/smoke.py && python main.py --status && python main.py
```

Clone → configure `cfg/base.json` → run. Scraped data (GB-scale) stays local.

---

## 项目简介

面向 **数据采集 · ETL · 统计分析** 的 Steam 创意工坊流水线：输入 APPID，自动完成 **列表发现 → 详情抓取 → 解析建库 → 统计报告**，支持断点续跑与误删保护。

## 项目亮点

- **四阶段 ETL 总控**：`simple_info → detail_fetch → grant_table → mod_analysis`，CLI 编排，全局 + 子包双层断点恢复
- **覆盖率方案**：针对 Steam **1667 页/tag** 硬上限，tag 交叉 + 无标签全站列表，最大化 mod 发现率
- **异步爬虫**（`resumable-batch-fetch`）：多端口池、429 退避、熔断降级；压测稳定吞吐 **≥4 页/秒**
- **SQLite 数仓 + 报告**：11 张表、Excel/CSV 导出、基尼 / 帕累托 / 可视化（**125** 项统计指标）
- **可运维**：`test/smoke.py` 预检、结构化错误码、非空数据目录拒绝覆盖

## 已验证规模（本地运行，仓库不含成品数据）

| 指标 | 数值 |
|------|------|
| 端到端跑通 | 2 款游戏：**3.6 万 / 6.3 万** mod，**1.3 万 / 2.8 万** 作者 |
| 最大在研数据集 | 发现 **31.6 万** mod URL，已抓取 **6.7 万** 详情页 |
| 简略信息阶段 | **14,524** 浏览页，约 **88 分钟**（≈166 页/分钟，该阶段无限流） |
| 分析结论（非自用 mod） | 订阅基尼 **0.90 / 0.92**；顶层 **1%** mod 占 **48% / 55%** 订阅；**0.96% / 0.58%** mod 贡献 **50%** 订阅 |

## 仓库结构

| 目录 | 阶段 |
|------|------|
| `main.py` | 总控 CLI |
| `appid-steamworkshop-table/` | 1 · 工坊列表采集 |
| `resumable-batch-fetch/` | 2 · 详情页异步爬虫 |
| `steam-grant-table/` | 3a · HTML → SQLite |
| `steam-mod-analysis/` | 3b · 统计分析 → `report/` |
| `lib/` · `cfg/` · `test/` | 编排、配置、冒烟测试 |

## 快速开始

```bash
python3 -m venv resumable-batch-fetch/.venv
resumable-batch-fetch/.venv/bin/pip install -r resumable-batch-fetch/requirements.txt
pip install -r requirements.txt

# 编辑 cfg/base.json 中的 target-game-id / data-folder

python test/smoke.py
python main.py --status
python main.py
```

```bash
python main.py 529340              # 切换游戏
python main.py --only detail_fetch # 只跑某一阶段
python main.py --fresh             # 空目录时从头开始
```

## Roadmap

- [x] 四阶段 ETL + 断点续跑
- [x] 2 款游戏端到端分析与报告导出
- [ ] 最大规模数据集详情页采集（31.6 万 URL，进行中）
- [ ] 作者属地等下游字段（LLM 批量分类，设计中）
- [ ] 全 Steam 有工坊游戏 mod 规模统计（本地实验，未纳入本仓库）
- [ ] 工坊 mod 实体抽取（本地实验，未纳入本仓库）

## 技术栈

Python 3.10+ · asyncio / aiohttp · SQLite · Pandas / NumPy · HTML 解析 · Matplotlib · argparse

## 详细文档

阶段交付物与平台细节见 [`README`](README)（设计长文档，无 `.md` 后缀）。

## License

MIT — see [LICENSE](LICENSE).

## 相关仓库

阶段 1 早期原型 [`appid-steamworkshop-table`](https://github.com/Joe-Villa/appid-steamworkshop-table) 已合并入本仓库，**请以本仓库为准**。
