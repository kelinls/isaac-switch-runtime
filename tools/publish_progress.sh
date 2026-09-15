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

# 守卫：**只检查已跟踪文件的改动**。切换分支不能带着未提交改动跑——
# 2026-09-15 就因此把未提交的诊断改动冲掉过（`reset --hard` 之后无影无踪）。
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "工作区有未提交改动，先提交或 git stash 再发布（避免切换分支把改动冲掉）。"
  git status --short --untracked-files=no
  exit 1
fi

# 允许公开的路径（白名单）。**新增目录要显式加进来**，避免把设备备份/文档/第三方内容一起推上去。
PUBLIC_PATHS=(runtime tools tests README.md CONTRIBUTING.md LICENSE .gitignore)
# 白名单里也要**排除**的路径：第三方模组内容（可再分发的授权不明确），保持仓库不夹带他人作品。
PUBLIC_EXCLUDES=(runtime/pc-mods/MuteOnPause runtime/.apiart-build)

# 守卫②：白名单路径下**不许有未跟踪文件/目录**。
# 为什么：第 2 步用的是 `git add -A -- <白名单>`，它会连未跟踪文件一起收进来 ——
# 2026-09-15 就把宿主测试留下的 `runtime/.apiart-build/`（223 个 `.o`/`.d`）推上了公开仓库。
# 真正的修复是给构建产物加 `.gitignore` 或加进 `PUBLIC_EXCLUDES`；这条守卫只是保证
# "下次再有新的未跟踪产物时，发布**失败**而不是悄悄带上去"。
untracked="$(git status --porcelain --untracked-files=all -- "${PUBLIC_PATHS[@]}")"
if [ -n "$untracked" ]; then
  echo "白名单路径下有未跟踪文件，发布会把它们一起推上去。请先加 .gitignore"
  echo "或把路径加进 PUBLIC_EXCLUDES，然后再发布："
  echo "$untracked" | head -20
  exit 1
fi

echo "== 1/4 门禁测试 =="
if [ "${SKIP_TESTS:-0}" = "1" ]; then
  echo "（SKIP_TESTS=1，跳过）"
else
  python3 tools/run_tests.py 2>&1 | tail -12
  # 管道会让 `$?` 变成 `tail` 的退出码，所以显式看第一个命令的：
  test "${PIPESTATUS[0]}" = "0" || { echo "门禁未通过，停止发布"; exit 1; }
fi

echo "== 2/4 把白名单路径同步到公开分支 $BRANCH =="
git checkout "$BRANCH" >/dev/null
# ⚠️ 只同步白名单路径，**绝不用裸 `git add -A`**：工作区里躺着大量未跟踪的杂项
# （设备备份、日志、`runtime/x/**` 之类），一条 `-A` 就会把它们一起推上公开仓库（踩过）。
for path in "${PUBLIC_PATHS[@]}"; do
  git checkout master -- "$path"
done
git add -A -- "${PUBLIC_PATHS[@]}"
for path in "${PUBLIC_EXCLUDES[@]}"; do
  git rm -r --cached -q --ignore-unmatch -- "$path"
done

if git diff --cached --quiet; then
  echo "没有变化，不提交。"
  git checkout -f master >/dev/null
  exit 0
fi

echo "== 3/4 提交 =="
git commit -m "$TITLE" -m "$DETAIL"

echo "== 4/4 推送到 $REMOTE =="
git push "$REMOTE" "$BRANCH:main"
git checkout -f master >/dev/null
echo "完成：$TITLE"
