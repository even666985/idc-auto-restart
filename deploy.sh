#!/usr/bin/env bash
# 一键部署核云IDC自动开机监控到 GitHub（公开仓库）
# 用法: ./deploy.sh [仓库名]
# 前置: 已安装 gh 并登录 (gh auth login)
set -euo pipefail

REPO_NAME="${1:-idc-auto-restart}"
REMOTE="origin"

echo "==> 检查 GitHub CLI 登录状态"
if ! gh auth status >/dev/null 2>&1; then
  echo "❌ 未登录 GitHub CLI，请先运行: gh auth login"
  exit 1
fi

USER_NAME="$(gh api user --jq .login)"
echo "==> 当前 GitHub 账号: $USER_NAME"

echo "==> 初始化 git 仓库并提交"
git init -q
git add -A
if git diff --cached --quiet; then
  echo "⚠️ 没有可提交的文件，请确认当前目录包含 idc_monitor.py 与 .github/"
  exit 1
fi
git commit -q -m "init: 核云IDC自动开机监控 (GitHub Actions)"

echo "==> 创建公开仓库并推送: $REPO_NAME"
if gh repo create "$REPO_NAME" --public --source=. --remote="$REMOTE" --push 2>/dev/null; then
  echo "✅ 已创建并推送"
else
  echo "⚠️ 仓库可能已存在，尝试直接推送当前分支..."
  DEFAULT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  git push -u "$REMOTE" "$DEFAULT_BRANCH" 2>/dev/null \
    || git push -u "$REMOTE" main 2>/dev/null \
    || git push -u "$REMOTE" master
fi

REPO_URL="$(gh repo view "$REPO_NAME" --json url -q .url 2>/dev/null || echo "https://github.com/$USER_NAME/$REPO_NAME")"
echo ""
echo "🎉 已部署到: $REPO_URL"
echo ""
echo "下一步 — 在仓库 Settings → Secrets and variables → Actions 添加仓库密钥:"
echo "  必需:"
echo "    IDC_ACCOUNT      = 你的手机号或邮箱"
echo "    IDC_API_KEY      = 核云控制台「个人中心」生成的 API Key"
echo "    IDC_WEBHOOK      = 飞书机器人 webhook URL"
echo "  可选:"
echo "    IDC_CONSOLE_URL  = 核云控制台地址（默认: https://www.heyunidc.cn/console/vps）"
echo "    IDC_REPO_URL     = GitHub 仓库地址（卡片按钮跳转用）"
echo ""
echo "添加后去 Actions 页面手动 Run 一次『IDC Auto-Restart Monitor』验证即可。"
