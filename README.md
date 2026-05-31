# Steam 创意工坊多阶段数据采集与分析流水线

> 个人项目 · Python · 2025.05 — 至今  
> 输入单个 Steam APPID，自动完成工坊模组 **列表发现 → 详情抓取 → 解析建库 → 统计分析** 全链路；支持断点续跑与数据防误删。

## 项目亮点（简历向）

- **三阶段 ETL 总控**：`simple_info → detail_fetch → analysis`，CLI 一键编排，全局 + 子包双层断点恢复
- **平台约束下的覆盖率方案**：针对 Steam 浏览页 1667 页硬上限，tag 交叉 + 无标签全站列表最大化 mod 发现率
- **生产向异步爬虫**（`resumable-batch-fetch`）：aiohttp 并发、多端口池、熔断降级、429 退避；内容与业务解耦，可复用于任意 URL 簇
- **HTML → SQLite 数仓 + 自动报告**：多表建模、Excel 导出、Gini/Dagum 不平等分析、matplotlib 可视化
- **可运维性**：`test/smoke.py` 跑前预检、结构化错误码、非空数据目录拒绝误覆盖

更完整的简历表述见 [`RESUME_SNIPPET.md`](RESUME_SNIPPET.md)。

## 仓库结构

| 目录 | 说明 |
|------|------|
| `main.py` | 流水线总控入口 |
| `lib/` | 路径解析、状态机、子进程编排 |
| `cfg/` | 全局配置（`base.json`、`crawler.json`） |
| `appid-steamworkshop-table/` | 阶段 1：工坊列表页采集 |
| `resumable-batch-fetch/` | 阶段 2：详情页异步爬虫 |
| `steam-grant-table/` | 阶段 3a：详情 HTML → SQLite / Excel |
| `steam-mod-analysis/` | 阶段 3b：统计分析 → `report/` |
| `data/APPID/` | 数据目录结构模板（空占位） |
| `test/` | 冒烟测试 |

`SteamWorkshopCrawler/`、`workshopmap/` 为早期实验代码，主流程以 `main.py` 为准。

## 快速开始

```bash
# 1. 依赖（详情爬虫建议独立 venv）
python3 -m venv resumable-batch-fetch/.venv
resumable-batch-fetch/.venv/bin/pip install -r resumable-batch-fetch/requirements.txt
pip install -r requirements.txt   # 或根目录 venv 装全量

# 2. 配置游戏 APPID（编辑 cfg/base.json）
#    "target-game-id": 529340
#    "data-folder": "data/529340"

# 3. 冒烟 → 运行
python test/smoke.py
python main.py --status
python main.py
```

常用参数：

```bash
python main.py 529340              # 切换游戏
python main.py --only detail_fetch # 只跑某一阶段
python main.py --fresh             # 空目录时从头开始
```

## 关于数据

本仓库**不包含**爬取的 HTML / SQLite 成品数据（单游戏可达数 GB）。  
`data/APPID/` 仅保留目录结构模板；克隆后需自行运行流水线生成数据。

## 技术栈

Python 3.10+ · asyncio / aiohttp · SQLite · HTML 解析 · NumPy / Matplotlib · argparse CLI

## 详细文档

完整设计说明、阶段交付物、平台技术细节见 [`README`](README)（无 `.md` 后缀的原始长文档）。
