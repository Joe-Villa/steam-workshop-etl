**语言：** [English](README.md)（默认文档）· 简体中文（本文件）

> **默认语言说明：** 本仓库以 **[README.md](README.md)（英文）** 为默认说明文档。仅在明确声明使用中文（如 `zh-CN`、简体中文 README）时，才以本文件为准。

这是什么？

这是一个开箱即用的爬虫程序。
只需要给定一个APPID，就能将其在steam创意工坊的所有模组简略页面爬取下来，并建立一个数据表。

**输入**：游戏的 Steam **APPID**。  
**输出**：该游戏创意工坊**全部模组**的简略信息表（`output/workshop_mods.sqlite` / `workshop_mods.xlsx`）。

流水线包含：解析工坊主页 → 生成待爬 URL 列表 → 爬浏览页 HTML → 解析入库。其中浏览页爬虫是**自研的简略实现**，可替换为更快、更稳的外部方案（见下文「浏览页爬虫接口」）。

## 环境要求

- **Python 3.10+**（3.9 可能可用，未专门测试）
- Excel 导出需安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

爬取阶段仅使用 Python 标准库；**openpyxl** 仅用于最后导出 Excel。

一键运行（写入 `cfg/base.json`，走完爬取 + SQLite + Excel）。`main.py` 常用用法：

1. 指定 APPID，跑完整流水线

```bash
python3 main.py 529340
```

2. 使用 `cfg/base.json` 里已配置的 APPID

```bash
python3 main.py
```

3. 不爬网，仅用已有 `output/html/` 重建 sqlite 与 Excel

```bash
python3 main.py 529340 --skip-fetch
```

4. 经本地代理（如 Steam++/Watt）访问 Steam：在 `cfg/base.json` 配置 `PORT` 与 `no_tls_verify`，或命令行加 `--no-tls-verify`

```bash
python3 main.py
# 或临时覆盖 TLS：python3 main.py --no-tls-verify
```

若 `output/html/` 里已有部分 HTML，则从爬虫续跑；否则清空 html/sqlite/xlsx 后从头爬取。

创意工坊对**浏览页**限速很松（详情页才严），本流水线用单线程顺序抓取，主要时间花在下载 HTML 上。

**爬取耗时**取决于待爬**浏览页数**（见下表），不是工坊主页上的模组总数。单机顺序抓取、常见网络下，可按约 **1 秒/页** 粗算；解析入库与导出 Excel 只需几分钟。中断后重跑 `main.py` 会跳过已有 HTML。

### 规模参考：城市天际线 vs 群星

以下数字来自工坊主页 HTML / `workshop_main.json` 解析后，用本仓库 `build_browse_urls.py` 规则统计（每 tag `ceil(数量/30)`，单 tag 最多 **1667** 页）。时间为浏览页爬取的**经验量级**，实际随网络、429 略有浮动。

| | 城市：天际线（255710） | 群星（281990） |
|---|------------------------|----------------|
| 工坊模组总量（主页「See all」） | 约 **36.9 万** | 约 **3.6 万** |
| 官方筛选 tag 数 | **83** | **25** |
| 触及 1667 页上限的 tag 数 | **4** | **0** |
| **待爬浏览页数** | 约 **1.29 万** | 约 **0.20 万（2028）** |
| **浏览页爬取时间（粗估）** | 约 **3～4 小时** | 约 **30～60 分钟** |

说明：天际线模组很多，但页数被「多 tag 累加 + 少数 tag 顶格 1667 页」压住，不会出现「三十万模组就要三十万页」；群星 tag 少、无顶格 tag，总量虽为天际线的约十分之一，待爬页约为天际线的 **六分之一**，耗时更短。EU4 等更小户（约千余页）通常在一小时量级以内。

## 目录约定

全新 clone 时**没有** `cfg/`、`input/`、`output/`、`status/`（部分目录在 `.gitignore` 中，或尚未生成）。运行 `main.py` 时会按需创建：

| 目录 | `main.py` 是否创建 | 用途 |
|------|-------------------|------|
| `cfg/` | 是（写入 `cfg/base.json`） | APPID 配置 |
| `status/` | 是（步骤 2–3 中间产物） | `browse_urls.json`、`browse_html_gaps.json` 等 |
| `output/` | 是（`output/html/`、sqlite、xlsx、主页 JSON） | 爬取 HTML 与交付物 |
| `input/` | **否** | 预留外部输入；若使用请自行创建 |

单独运行各 `src/*.py` 时，也会在写入目标文件前 `mkdir` 对应父目录。

- `cfg/`：配置（见下表 `cfg/base.json`）
- `input/`：本流水线外部读入（若有）；不由本仓库步骤生成
- `output/`：交付物（爬取的 HTML、`workshop_main.json`、`workshop_mods.sqlite`、导出的 Excel）
  - `workshop_main.json`：工坊主页解析结果（当前快照）
- `status/`：中间态（用户一般无需关心）
  - `browse_urls.json`：待爬取的浏览页 URL 列表（`build_browse_urls.py` 生成）
  - `browse_html_gaps.json`：HTML 与 URL 列表不一致时由测试脚本生成

工作流程（也可分步执行；见下方各 `src/*.py`）：
1.在cfg当中输入APPID（或 `main.py <APPID>` 自动写入），然后用checkid验证游戏是否是我们想要的。
2.访问https://steamcommunity.com/app/{APPID}/workshop/，解析生成的html，解析如下信息并写入 `output/workshop_main.json`：
    （1）APPID，例如394360
    （2）所有tag的信息。有两个参数：tag的名称以及当前tag所拥有的模组数量
    （3）tag的总数量
    （4）创意工坊目前有多少个模组
    （5）URL来源（就是https://steamcommunity.com/app/{APPID}/workshop/）
3.根据 `workshop_main.json` 生成待爬 URL 列表，写入 `status/browse_urls.json`（JSON 字符串数组）。运行：`python3 src/build_browse_urls.py`

   每个官方 tag 的页数：`ceil(该 tag 模组数 / 30)`，再与 **1667** 取较小值。另追加固定伪 tag **`No_selected_tag`**：不带 `requiredtags` 的全站订阅排序列表，页数为 `ceil(min(工坊总数, 50000) / 30)`（最多 1667 页）；`mod_tag_ranks` 中对应列紧挨 `mod_id` 为第二列。URL 含 `section=readytouseitems`，示例：

   `https://steamcommunity.com/workshop/browse/?appid=3450310&browsesort=totaluniquesubscribers&section=readytouseitems&actualsort=totaluniquesubscribers&p=3`

   **截断说明（平台限制，不是人为少爬）：** Steam 创意工坊按订阅量排序的浏览 URL，每个 tag 最多只能翻到第 **1667** 页，约 **5 万**条（1667×30）。`p=1668` 及以后没有有效列表，不是漏爬。若某 tag 在工坊里显示的模组数超过 5 万，多出来的条目无法通过浏览页拿到，只能舍弃；我们按订阅量排序，被截掉的多半是订阅更低的模组。

   URL 示例（`appid`、`requiredtags[0]`、`p` 会变）：

   `https://steamcommunity.com/workshop/browse/?appid=529340&requiredtags%5B0%5D=Alternative+History&actualsort=totaluniquesubscribers&browsesort=totaluniquesubscribers&p=1`

4.爬取浏览页（读取 `status/browse_urls.json`，写入 `output/html/`）：

   `python3 src/fetch_browse_until_complete.py`

   先全量爬取（跳过已有有效页），再对缺口重试：每个漏掉的 `row_id` 在**本次运行**内最多再请求 5 次（每次重新执行脚本都会重新计数，不跨运行累计）。仍失败则退出并报错，写入 `status/browse_html_gaps.json`。

   仅单次爬取：`python3 src/fetch_browse_pages.py`（`--force` 强制重爬）。仅检查：`python3 test/check_browse_html_coverage.py`

### 浏览页爬虫接口（可替换）

第 4 步用的 `src/fetch_browse_pages.py`、`src/fetch_browse_until_complete.py` 仅为**单线程、标准库**的简略爬虫，方便开箱即用。**可以且建议**在需要更快速度、代理轮换、更高并发时换成你们自己的实现（例如本仓库旁的 `HtmlBatchRunner`，或其它下载器），只要满足下列约定，后续 `build_mods_sqlite.py` 无需修改。

**上游（本仓库提供，一般不换）**

| 产物 | 路径 | 说明 |
|------|------|------|
| URL 列表 | `status/browse_urls.json` | JSON **字符串数组**，每项为一条 Steam 工坊**浏览页** URL；下标 `i`（从 0 起）对应 `row_id = i + 1` |
| 生成方式 | `python3 src/build_browse_urls.py` | 由 `output/workshop_main.json` 按 tag 展开 |

**下游（外部爬虫必须写入）**

| 项目 | 约定 |
|------|------|
| 根目录 | `output/html/`（或通过 `main.py --skip-fetch` 前先放到该目录） |
| 文件路径 | 第 `row_id` 条 URL → `output/html/{row_id 的前两位}/{row_id}.html`（`row_id` 从 **1** 开始）。例：`row_id=4` → `output/html/04/4.html`；`row_id=1124` → `output/html/11/1124.html` |
| 文件内容 | 该 URL 的 HTML 响应体（UTF-8 文本）；须含 Steam 浏览列表标记 `workshopBrowseItems`（与 `src/browse_html.py` 中校验一致） |
| 完整性 | 对 `browse_urls.json` 中**每一个**下标均应有对应文件；缺页可用 `python3 test/check_browse_html_coverage.py` 检查 |

**替换方式（不绑死某一种爬虫）**

1. 跑完本仓库第 1～3 步，得到 `status/browse_urls.json`。  
2. 用自有爬虫按上表路径写入 `output/html/`。  
3. 验收：`python3 test/check_browse_html_coverage.py`（有缺口则补爬或重试）。  
4. 跳过内置爬虫，直接入库导出：  
   `python3 main.py <APPID> --skip-fetch`  
   或在 `main.py` 里将 `run_fetch()` 换成对你们下载器的调用（入参：URL 列表 + `output/html` 根目录）。

内置爬虫与外部爬虫**不要混用同一 `row_id` 的半拉子文件**；换爬虫前建议清空 `output/html/` 或换 APPID 后由 `main.py` 自动清空。

5.阅读 `output/html` 下所有 html，写入 `output/workshop_mods.sqlite`。
   解析字段来源与原先相同（hover JSON 的 id/title/description、页眉排名、星级图、作者、详情 URL）。

   SQLite / Excel 共三张表（主键均为 mod_id）：
   （1）mod_detail_url：mod_id, detail_url
       即 https://steamcommunity.com/sharedfiles/filedetails/?id={mod_id}，单独存一份。

   （2）mod_tag_ranks：mod_id + 各 tag 列（列名见 output/workshop_main.json，如 Alternative_History）
       值为该 tag 内排名（整数）；未出现则为 NULL。列集合随游戏而异。

   （3）mod_browse_info：mod_id, title, description, star_rating, author
       同一 mod 多处出现时只保留首次见到的简介信息。

   构建：python3 src/build_mods_sqlite.py
   导出 Excel：python3 tool/export_sqlite_to_csv_excel.py → `output/workshop_mods.xlsx`




### `cfg/base.json`

| 字段 | 必填 | 说明 |
|------|------|------|
| `APPID` | 是 | Steam 游戏 ID |
| `PORT` | 否 | 本地 HTTP 代理端口（`127.0.0.1`）；省略或 `-1` 为直连 |
| `no_tls_verify` | 否 | `true` 时关闭 HTTPS 证书校验（配合 Steam++ 等 MITM） |

示例（EU5 + Steam++ 26561）：

```json
{
    "APPID": 3450310,
    "PORT": 26561,
    "no_tls_verify": true
}
```

`main.py` 只更新 `APPID`，会保留 `PORT` 与 `no_tls_verify`。进程会清除 `HTTP_PROXY` 等环境变量，**仅**使用 cfg 中的 `PORT`。命令行 `--no-tls-verify` 可临时覆盖 TLS 设置。

注意：
1.交付物均在 `output/`：`html/`、`workshop_main.json`、`workshop_mods.sqlite`、`workshop_mods.xlsx`
2.`status/` 存放中间态（`browse_urls.json`、缺口报告等）；主页快照与表在 `output/`
3.HTTPS 默认校验；代理场景在 cfg 设 `no_tls_verify: true`，或命令行 `--no-tls-verify`。
4.一个excel最多也就1048576 行。如果一个游戏在创意工坊有多于一百万的模组，那就要小心了！
5.每个 tag 浏览页最多约 5 万条模组的上限，见上文第 3 步「截断说明」。

## 许可证

[MIT License](LICENSE)。可自由使用、修改、商用与再分发；保留版权声明即可。
