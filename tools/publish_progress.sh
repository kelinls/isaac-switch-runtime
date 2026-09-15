#!/usr/bin/env bash
# 每有较大进展（bug 修复 / 新功能 / 重要诊断结论）就往公开仓库更新一次。
#
# 用法：
#   tools/publish_progress.sh "fix: 修好潘多拉魔盒不显示说明" \
#       "做了什么：补上 Level:IsAltStage 等 6 个缺失接口
#        证据：真机报告 01789441863 + 游戏内实测
#        影响：这两个物品恢复显示；启动时间不变
#        回退：git revert 本提交"
#
# 环境变量：
#   PUBLIC_BRANCH  公开分支名（默认 public）
#   PUBLIC_REMOTE  远端名（默认 public）
#   SKIP_TESTS=1   跳过门禁测试（不推荐）
set -euo pipefail

TITLE="${1:?用法: tools/publish_progress.sh \"<一句话标题>\" \"<做了什么/证据/影响/回退>\"}"
DETAIL="${2:-（未填写细节）}"
BRANCH="${PUBLIC_BRANCH:-public}"
REMOTE="${PUBLIC_REMOTE:-public}"

cd "$(git rev-parse --show-toplevel)"

# 允许公开的路径（白名单）。**新增目录要显式加进来**，避免把设备备份/文档/第三方内容一起推上去。
PUBLIC_PATHS=(runtime tools tests README.md CONTRIBUTING.md LICENSE .gitignore)

echo "== 1/4 门禁测试 =="
if [ "${SKIP_TESTS:-0}" = "1" ]; then
  echo "（SKIP_TESTS=1，跳过）"
else
  python3 -m unittest discover -s runtime/tests -t . 2>&1 | tail -3
fi

echo "== 2/4 把白名单路径同步到公开分支 $BRANCH =="
git checkout "$BRANCH" >/dev/null
# ⚠️ 只同步白名单路径，**绝不用裸 `git add -A`**：工作区里躺着大量未跟踪的杂项
# （设备备份、日志、`runtime/x/**` 之类），一条 `-A` 就会把它们一起推上公开仓库（踩过）。
for path in "${PUBLIC_PATHS[@]}"; do
  git checkout master -- "$path"
done
git add -A -- "${PUBLIC_PATHS[@]}"

if git diff --cached --quiet; then
  echo "没有变化，不提交。"
  git checkout master >/dev/null
  exit 0
fi

echo "== 3/4 提交 =="
git commit -m "$TITLE" -m "$DETAIL"

echo "== 4/4 推送到 $REMOTE =="
git push "$REMOTE" "$BRANCH:main"
git checkout master >/dev/null
echo "完成：$TITLE"
