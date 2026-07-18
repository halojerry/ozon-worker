#!/bin/bash
# 一键更新脚本（拉取代码 → 重建镜像 → 滚动更新）
# 用法: bash deploy/update.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "═══════════════════════════════════════"
echo "  Ozon Worker 更新"
echo "═══════════════════════════════════════"

# 拉取最新代码
echo ""
echo "📥 拉取最新代码..."
cd "$PROJECT_DIR"
git pull

# 重建镜像
echo ""
echo "📦 重建 Docker 镜像..."
cd "$SCRIPT_DIR"
docker compose build --no-cache

# 滚动更新
echo ""
echo "🔄 滚动更新..."
docker compose up -d --force-recreate

# 等待健康检查
echo ""
echo "⏳ 等待服务就绪..."
MAX_WAIT=60
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    if curl -sf http://localhost:8080/health > /dev/null 2>&1; then
        echo "✅ 更新完成，服务正常"
        exit 0
    fi
    sleep 2
    WAITED=$((WAITED + 2))
    echo "  等待中... (${WAITED}s/${MAX_WAIT}s)"
done

echo "⚠️  更新后服务异常，请检查日志:"
echo "   docker compose logs worker --tail=50"
exit 1
