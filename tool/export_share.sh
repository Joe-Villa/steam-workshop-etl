#!/usr/bin/env bash
# 导出可分享 zip（仅含 Git 跟踪文件，自动排除 .gitignore 中的敏感/大数据）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

NAME="analysis_paradox-share"
OUT="${1:-$ROOT/../${NAME}.zip}"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "ERROR: 不在 git 仓库内" >&2
  exit 1
fi

# 确保工作区已提交，否则 archive 不含未提交改动
if ! git diff-index --quiet HEAD -- 2>/dev/null; then
  echo "WARN: 工作区有未提交改动，导出内容以最后一次 commit 为准。" >&2
  echo "      可先 git add -A && git commit，再重新运行本脚本。" >&2
fi

git archive --format=zip HEAD -o "$OUT"
echo "已导出: $OUT"
echo "大小: $(du -h "$OUT" | cut -f1)"
