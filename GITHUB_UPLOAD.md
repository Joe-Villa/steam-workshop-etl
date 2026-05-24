# 上传到 GitHub 步骤

本地准备工作已完成：`.gitignore`、`README.md`、`requirements.txt`。  
**6.3 GB 爬取数据、`zzz简历复制/`（含手机号）、各 `.venv` 已被排除。**

## 1. 在 GitHub 创建空仓库

1. 打开 https://github.com/new
2. Repository name 建议：`steam-workshop-pipeline` 或 `analysis_paradox`
3. **不要**勾选 "Add a README"（本地已有）
4. Public（给雇主看）或 Private（仅自己/指定人）
5. Create repository

## 2. 本地初始化并首次提交

```bash
cd "/home/liulingda/桌面/vic3modder/analysis_paradox"

git init
git add .
git status    # 确认没有 data/394360、data/281990、.venv、zzz简历复制

git commit -m "$(cat <<'EOF'
Initial commit: Steam workshop data pipeline

Three-stage ETL (list fetch, detail crawl, analysis) with CLI orchestration,
checkpoint resume, and smoke tests. Crawled data excluded from repo.
EOF
)"
```

## 3. 推送

```bash
# 替换为你的 GitHub 用户名和仓库名
git remote add origin git@github.com:YOUR_USERNAME/YOUR_REPO.git
git branch -M main
git push -u origin main
```

若用 HTTPS + PAT：

```bash
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
# 用户名填 GitHub 用户名，密码处粘贴 Fine-grained 或 Classic PAT
```

## 4. 推送前自检清单

- [ ] `git status` 里没有 `.venv/`、`data/281990/`、`data/394360/`
- [ ] 没有 `.env`、API Key 明文
- [ ] `zzz简历复制/` 未被跟踪（含手机号 15584362198）
- [ ] README.md 在 GitHub 上显示正常

## 5. 仓库体积参考

排除数据后，预计跟踪文件 **约 5–15 MB**（源码 + 测试 + 模板），适合 GitHub 免费额度。

## 6. 简历里怎么写链接

```
GitHub: https://github.com/YOUR_USERNAME/YOUR_REPO
项目：Steam 创意工坊多阶段数据采集与分析流水线
```

可把 [`RESUME_SNIPPET.md`](RESUME_SNIPPET.md) 里的片段直接贴进简历。
