#!/bin/bash
# 一键更新脚本（服务器无法访问 GitHub → 走 COS 分发）
# 用法: bash deploy/update.sh            # 升级到最新
#       bash deploy/update.sh v0.28.0    # 指定版本 / 回滚
#
# v0.29.0: 原实现 git pull(服务器访问不了 GitHub 失效) → 改为调用
# cos-update.sh(读 COS manifest → 下载 → sha256 校验 → 备份 → 覆盖 →
# 优雅重建 → 健康检查 → 失败自动回滚)。

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "═══════════════════════════════════════"
echo "  Ozon Worker 更新 (COS 分发)"
echo "═══════════════════════════════════════"

exec bash "$SCRIPT_DIR/cos-update.sh" "$@"
