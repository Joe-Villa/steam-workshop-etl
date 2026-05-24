# 冒烟测试

在跑主流水线前执行：

```bash
python test/smoke.py
```

## 检查项

| 名称 | 说明 |
|------|------|
| `config` | `cfg/base.json`、`cfg/crawler.json` 可读 |
| `data_dir` | `data-folder` 目录可创建且可写 |
| `proxy_base` | 阶段 1 用的 `PORT` 已监听且能访问 Steam |
| `app_workshop` | APPID 有效且商店标注含创意工坊 |
| `workshop_hub` | `steamcommunity.com/app/{id}/workshop/` 可打开 |
| `proxy_crawler` | 阶段 2：`crawler.json` 里至少一个端口可用 |

跳过某项：`python test/smoke.py --skip proxy_crawler`

退出码：`0` 通过；`1` 配置/业务（如无工坊）；`2` 网络/代理。
