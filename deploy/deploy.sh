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

# v0.29.0: 首次部署引导 — 服务器无法访问 GitHub 时, 本地无 worker 源码,
# 自动从 COS 拉取最新部署包(cos-update.sh: manifest → 下载 → 校验 → 解压覆盖)
if [ ! -d "$PROJECT_DIR/worker/src" ]; then
    echo "⚠️  本地无 worker 源码, 从 COS 拉取最新部署包..."
    bash "$SCRIPT_DIR/cos-update.sh" || {
        echo "❌ COS 拉取失败。首次部署请手动执行:"
        echo "   curl -O <COS 部署包 URL> && tar -xzf ozon-worker-deploy-v*.tar.gz"
        exit 1
    }
    # cos-update.sh 已更新 VERSION
    VERSION=$(cat "$PROJECT_DIR/VERSION" 2>/dev/null || echo "dev")
fi

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

# v0.62.1 P1-3: 凭证加密主密钥必配校验（缺失 → 凭证 CRUD 500 + store_sync 解密失败刷屏）
if [ -z "$CREDENTIAL_MASTER_KEY" ]; then
    echo "❌ CREDENTIAL_MASTER_KEY 未设置 — 凭证加密/解密必需（AES-256-GCM 列级加密）"
    echo "   生成: openssl rand -base64 32"
    echo "   ⚠️ 启用后不可随意更换，否则存量凭证全部无法解密（轮换走 rotate_master_key.py）"
    exit 1
fi
echo "✅ CREDENTIAL_MASTER_KEY 已配置"

echo "✅ 环境检查通过"

# v0.62.2: webui 已随镜像多阶段内建(worker/Dockerfile webui-builder 阶段),
# 不再要求宿主机预构建/挂载 webui/dist。仅保留提示, 不再阻断部署。
if [ -f "$PROJECT_DIR/webui/dist/index.html" ]; then
    echo "ℹ️  检测到宿主机 webui/dist(已随镜像内建, 不再使用 bind-mount, 可删除)"
fi
echo "✅ WebUI 将随镜像构建, 无需宿主机 dist"

# 构建镜像(同时 tag latest, 避免 up 时无 VERSION 落到旧 latest)
echo ""
echo "📦 构建 Docker 镜像 (v${VERSION})..."
cd "$SCRIPT_DIR"
VERSION="$VERSION" BUILD_TIME="$BUILD_TIME" docker compose build
# v0.29.1: 修复① — build 只出 v{VERSION} 标签, up 不带 VERSION 时
# compose 落到 image 默认 latest(旧镜像)。显式 tag latest 保持一致。
docker tag "ozon-worker:${VERSION}" ozon-worker:latest 2>/dev/null || true

# 启动服务(带 VERSION, 保证 image 标签一致)
echo ""
echo "🚀 启动服务..."
VERSION="$VERSION" docker compose up -d

	# 等待 PG 就绪后初始化数据（建表 + 导入类目树 + 物流费率 + 属性缓存）
	echo ""
	echo "📦 初始化数据库..."
	docker compose exec -T worker python scripts/init_data.py

	# ── v0.70: 属性缓存全量 JSON——COS 下载 → 拷入容器 → 后台 --import-only ──
	# 「部署即全量」：一次性分片预热(~16h) → --export-only → 上传 COS 后，此后每次
	# 部署自动灌入全量缓存（30 天 TTL）。COS 缺失时跳过（懒加载兜底，不阻断部署）。
	# 运维手册: docs/CACHE-WARM-RUNBOOK.md
	COS_BUCKET="${COS_BUCKET:-yss-1256275613}"
	COS_REGION="${COS_REGION:-ap-guangzhou}"
	if [ -f "$SCRIPT_DIR/.env" ]; then
		_b=$(grep -E '^COS_BUCKET=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"')
		_r=$(grep -E '^COS_REGION=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"')
		[ -n "$_b" ] && COS_BUCKET="$_b"
		[ -n "$_r" ] && COS_REGION="$_r"
	fi
	CACHE_BASE_URL="https://${COS_BUCKET}.cos.${COS_REGION}.myqcloud.com/ozon-worker/cache"
	CACHE_OK=0
	for _f in attribute_schemas_zh.json dictionary_values_zh.json; do
		_tmp=$(mktemp)
		if curl -fsSL --retry 2 --retry-delay 2 --max-time 600 -o "$_tmp" "$CACHE_BASE_URL/$_f"; then
			if docker compose cp "$_tmp" "worker:/app/assets/$_f" 2>/dev/null; then
				echo "   ✓ 属性缓存 JSON 就位: $_f"
				CACHE_OK=1
			fi
		else
			echo "   ⚠️ COS 无属性缓存 $_f（跳过，运行时懒加载兜底）"
		fi
		rm -f "$_tmp"
	done
	if [ "$CACHE_OK" = "1" ]; then
		echo "🔥 后台灌入全量属性缓存（--import-only，日志 /app/logs/warm_import.log）..."
		docker compose exec -T worker sh -c "python scripts/warm_category_cache.py --import-only >> /app/logs/warm_import.log 2>&1" &
	fi

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
    if curl -sf http://localhost:8080/api/v1/health > /dev/null 2>&1; then
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
