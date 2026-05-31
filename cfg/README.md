# 配置目录（单一事实来源）

本仓库**只应编辑这里的文件**。子包目录里的 `cfg/` 要么已删除，要么仅保留该包独有的脚本配置。

| 文件 | 作用 | 谁读取 |
|------|------|--------|
| `base.json` | 当前游戏 APPID、简略爬取代理/TLS | 总控 `main.py`、`appid-steamworkshop-table`（经 manifest 引用） |
| `crawler.json` | 详情爬虫：端口池、并发、熔断 | `resumable-batch-fetch` |

`steam-mod-analysis/cfg/*.yml` 仍放在子包内：仅供个别修复/分类脚本的参数，与流水线主路径无关。

## `base.json` 字段

- `target-game-id`：Steam APPID
- `PORT`：简略页爬取 HTTP 代理端口（`-1` 或省略为直连）
- `no_tls_verify`：简略页爬取是否跳过 TLS 校验（代理场景）

数据根目录由 `pipeline/collect_simple_info.json` 的 `output` 指定（阶段 1）；阶段 2/3 总控默认使用同一 manifest 的 `output`，否则为 `data/<APPID>/`。

## `crawler.json` 字段

见文件内注释；勿再在此写 `input_path` / `output_path`（由各阶段 pipeline manifest 推导）。

详情爬虫 Python 依赖见 `resumable-batch-fetch/requirements.txt`（`aiohttp`、`requests`）。从仓库根跑 `main.py` 时会优先使用 `resumable-batch-fetch/.venv`。
