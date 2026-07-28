#!/bin/bash
# 一键部署脚本（宝塔/自有服务器）
# 用法: bash deploy/deploy.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

VERSION=$(cat "$PROJECT_DIR/VERSION" 2>/dev/null || echo "dev")
BUILD_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "═══════════════════════════════════════"
echo "  Ozon Worker 部署 v${VERSION}"
echo "═══════════════════════════════════════"

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker 未安装，请先安装 Docker"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose 未安装，请先安装 Docker Compose v2"
    exit 1
fi

# 检查 .env
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "⚠️  .env 文件不存在，从模板创建..."
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "📝 请编辑 $SCRIPT_DIR/.env 填入实际配置"
    echo "   然后重新运行此脚本"
    exit 1
fi

# 检查必要变量
source "$SCRIPT_DIR/.env"
if [ -z "$PGDATABASE_URL" ]; then
    echo "❌ PGDATABASE_URL 未设置，请编辑 .env"
    exit 1
fi

echo "✅ 环境检查通过"

# 构建镜像
echo ""
echo "📦 构建 Docker 镜像 (v${VERSION})..."
cd "$SCRIPT_DIR"
VERSION="$VERSION" BUILD_TIME="$BUILD_TIME" docker compose build

# 启动服务
echo ""
echo "🚀 启动服务..."
	docker compose up -d

	# 等待 PG 就绪后初始化数据（建表 + 导入类目树 + 物流费率 + 属性缓存）
	echo ""
	echo "📦 初始化数据库..."
	docker compose exec -T worker python scripts/init_data.py

	# 预热属性缓存（后台运行，top-200 高频类目 ~5分钟）
	# 未缓存的类目运行时将从 Ozon API 懒加载
	echo ""
	echo "🔥 预热属性缓存（后台，top-200 类目）..."
	docker compose exec -T worker python scripts/warm_category_cache.py --limit 200 --pg-only &
	WARM_PID=$!
	echo "   后台 PID: $WARM_PID（不影响服务启动）"

# 等待健康检查
echo ""
echo "⏳ 等待服务就绪..."
MAX_WAIT=60
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    if curl -sf http://localhost:8080/health > /dev/null 2>&1; then
        echo "✅ Worker 服务已就绪"
        echo ""
        echo "═══════════════════════════════════════"
        echo "  部署完成！"
        echo ""
        echo "  Worker API:  http://localhost:8080"
        echo "  Swagger UI:  http://localhost:8080/docs"
        echo "  Health:      http://localhost:8080/health"
        echo "═══════════════════════════════════════"
        exit 0
    fi
    sleep 2
    WAITED=$((WAITED + 2))
    echo "  等待中... (${WAITED}s/${MAX_WAIT}s)"
done

echo "⚠️  服务启动超时，请检查日志:"
echo "   docker compose logs worker"
exit 1
