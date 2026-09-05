#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# cos-update.sh — Worker 一键升级(服务器端, 服务器无法访问 GitHub)
#
# 配合 cd.yml cos-deploy job: tag push 自动打包源码 → COS /ozon-worker/ +
# manifest.json。本脚本读 manifest → 下载 → sha256 校验 → 备份 → 覆盖 →
# 优雅重建 → 健康检查 → 失败回滚。
#
# 用法:
#   bash deploy/cos-update.sh              # 升级到最新版(manifest 指向)
#   bash deploy/cos-update.sh v0.29.0      # 升级/回滚到指定版本
#
# 安全:
#   - 生产 .env 绝不覆盖
#   - 升级前自动备份 deploy/ worker/ VERSION → backups/
#   - 健康检查失败自动回滚到备份
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── 路径/配置 ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$ROOT_DIR/backups"
VERSION_FILE="$ROOT_DIR/VERSION"
MANIFEST_URL_BASE="https://yss-1256275613.cos.ap-guangzhou.myqcloud.com"
COS_BUCKET="${COS_BUCKET:-yss-1256275613}"
COS_REGION="${COS_REGION:-ap-guangzhou}"

# 从 deploy/.env 读取 COS 配置(若已配置, 覆盖默认)
if [ -f "$SCRIPT_DIR/.env" ]; then
  # shellcheck disable=SC1091
  _env_bucket=$(grep -E '^COS_BUCKET=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"' || true)
  _env_region=$(grep -E '^COS_REGION=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"' || true)
  [ -n "$_env_bucket" ] && COS_BUCKET="$_env_bucket"
  [ -n "$_env_region" ] && COS_REGION="$_env_region"
fi
MANIFEST_URL="https://${COS_BUCKET}.cos.${COS_REGION}.myqcloud.com/ozon-worker/manifest.json"
PACKAGE_BASE_URL="https://${COS_BUCKET}.cos.${COS_REGION}.myqcloud.com/ozon-worker"

log()  { echo -e "\033[1;32m[cos-update]\033[0m $*"; }
warn() { echo -e "\033[1;33m[cos-update]\033[0m ⚠️ $*"; }
fail() { echo -e "\033[1;31m[cos-update]\033[0m ❌ $*" >&2; exit 1; }

# ── 工具检查 ──
command -v curl >/dev/null || fail "需要 curl"
command -v docker >/dev/null || fail "需要 docker"

# ── 1. 读取 manifest(或指定版本) ──
REQUESTED_VERSION="${1:-}"
if [ -n "$REQUESTED_VERSION" ]; then
  log "指定版本: $REQUESTED_VERSION"
  # 指定版本: 直接用该版本的包(manifest 只指向最新, 指定版本需存在同名包)
  VERSION="${REQUESTED_VERSION#v}"
  PKG="ozon-worker-deploy-v${VERSION}.tar.gz"
  PACKAGE_URL="${PACKAGE_BASE_URL}/${PKG}"
  SHA256=""
else
  log "读取 COS manifest: $MANIFEST_URL"
  MANIFEST_JSON=$(curl -fsSL --retry 3 --retry-delay 2 --max-time 30 "$MANIFEST_URL") \
    || fail "无法读取 manifest(检查网络/COs 配置): $MANIFEST_URL"
  VERSION=$(echo "$MANIFEST_JSON" | grep -oE '"version"\s*:\s*"[^"]+"' | head -1 | sed 's/.*"\([^"]*\)"/\1/')
  PKG=$(echo "$MANIFEST_JSON" | grep -oE '"package"\s*:\s*"[^"]+"' | head -1 | sed 's/.*"\([^"]*\)"/\1/')
  SHA256=$(echo "$MANIFEST_JSON" | grep -oE '"sha256"\s*:\s*"[^"]+"' | head -1 | sed 's/.*"\([^"]*\)"/\1/')
  [ -n "$VERSION" ] || fail "manifest 无 version 字段"
  [ -n "$PKG" ] || fail "manifest 无 package 字段"
  PACKAGE_URL="${PACKAGE_BASE_URL}/${PKG}"
  log "最新版本: v${VERSION} ($PKG)"
fi

# ── 2. 对比本地版本 ──
LOCAL_VERSION=""
[ -f "$VERSION_FILE" ] && LOCAL_VERSION=$(cat "$VERSION_FILE" | tr -d ' \n')
if [ "$LOCAL_VERSION" = "$VERSION" ] && [ -z "$REQUESTED_VERSION" ]; then
  log "已是最新版本 v${VERSION}, 无需更新"
  exit 0
fi
log "本地 v${LOCAL_VERSION:-无} → 目标 v${VERSION}"

# ── 3. 下载 + sha256 校验 ──
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
log "下载 $PACKAGE_URL ..."
curl -fsSL --retry 3 --retry-delay 2 --max-time 300 -o "$TMP_DIR/$PKG" "$PACKAGE_URL" \
  || fail "下载失败: $PACKAGE_URL"
if [ -n "$SHA256" ]; then
  DOWNLOAD_SHA=$(sha256sum "$TMP_DIR/$PKG" | awk '{print $1}')
  if [ "$DOWNLOAD_SHA" != "$SHA256" ]; then
    fail "sha256 校验失败: 期望 $SHA256, 实际 $DOWNLOAD_SHA"
  fi
  log "✅ sha256 校验通过"
else
  warn "指定版本无 manifest sha256, 跳过校验"
fi

# ── 回滚函数(须在使用前定义) ──
rollback() {
  local _bk="$1"
  warn "回滚到备份: $_bk"
  # 停止当前容器
  cd "$SCRIPT_DIR"
  docker compose down 2>/dev/null || true
  # v0.64.x P1-1: 恢复定制 compose(宝塔 PG/host 网络定制版; 若无独立备份则跳过)
  [ -f "$_bk/docker-compose.yml" ] && cp "$_bk/docker-compose.yml" "$SCRIPT_DIR/docker-compose.yml"
  # 恢复备份（v0.63.1 D2: 含 webui/ —— v0.62.2 起镜像内建前端, 只回 worker
  # 会旧 worker + 新前端版本错配; 前端源码一并回滚）
  [ -d "$_bk/worker" ] && rm -rf "$ROOT_DIR/worker" && cp -a "$_bk/worker" "$ROOT_DIR/worker"
  [ -d "$_bk/webui" ] && rm -rf "$ROOT_DIR/webui" && cp -a "$_bk/webui" "$ROOT_DIR/webui"
  [ -f "$_bk/deploy/deploy.tar.gz" ] && tar -xzf "$_bk/deploy/deploy.tar.gz" -C "$ROOT_DIR"
  [ -f "$_bk/VERSION" ] && cp -a "$_bk/VERSION" "$VERSION_FILE" || echo "" > "$VERSION_FILE"
  # 重建启动（v0.63.1 D2: build 失败不再 || true 吞掉——保留现场供诊断,
  # 避免回滚后半死状态）
  if ! docker compose build --no-cache >/dev/null 2>&1; then
    warn "回滚 build 失败, 请手动介入: cd $SCRIPT_DIR && docker compose build --no-cache"
    return 1
  fi
  if ! docker compose up -d >/dev/null 2>&1; then
    warn "回滚启动失败, 请手动 docker compose up -d"
    return 1
  fi
  # v0.63.1 D2: 回滚后健康检查（复用升级流程的 curl 循环）
  local _h_ok=0
  for _i in $(seq 1 30); do
    if curl -fsS --max-time 3 "http://localhost:8080/api/v1/health" >/dev/null 2>&1; then
      _h_ok=1
      break
    fi
    sleep 2
  done
  if [ "$_h_ok" -ne 1 ]; then
    warn "回滚后健康检查失败(60s), 请手动检查容器状态"
  else
    warn "✅ 回滚后健康检查通过"
  fi
  warn "已回滚到 v$(cat "$VERSION_FILE" 2>/dev/null || echo unknown)"
}

# ── 4. 备份当前版本 ──
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_PATH="$BACKUP_DIR/v${LOCAL_VERSION:-unknown}_${TIMESTAMP}"
mkdir -p "$BACKUP_PATH"
log "备份当前版本 → $BACKUP_PATH"
if [ -d "$ROOT_DIR/worker" ]; then
  cp -a "$ROOT_DIR/worker" "$BACKUP_PATH/worker"
fi
if [ -d "$ROOT_DIR/webui" ]; then
  # v0.63.1 D2: 备份 webui 源码（v0.62.2 起镜像内建前端, 回滚需同版本源码）
  cp -a "$ROOT_DIR/webui" "$BACKUP_PATH/webui"
fi
if [ -d "$SCRIPT_DIR" ]; then
  # 备份 deploy(排除 .env 凭证与 backups 自身)
  mkdir -p "$BACKUP_PATH/deploy"
  (cd "$ROOT_DIR" && tar -czf "$BACKUP_PATH/deploy/deploy.tar.gz" \
      --exclude='.env' --exclude='backups' --exclude='*.tar.gz' \
      deploy 2>/dev/null || true)
  # v0.64.x P1-1: 单独留一份 compose —— 升级整包会覆盖 docker-compose.yml,
  # 回滚需恢复此定制版(host 网络/外部 PG); .env 已被上面排除。
  [ -f "$SCRIPT_DIR/docker-compose.yml" ] && cp "$SCRIPT_DIR/docker-compose.yml" "$BACKUP_PATH/docker-compose.yml"
fi
[ -f "$VERSION_FILE" ] && cp -a "$VERSION_FILE" "$BACKUP_PATH/VERSION" || true
echo "$LOCAL_VERSION" > "$BACKUP_PATH/local_version.txt"

# ── 5. 解压覆盖(保留 .env) ──
log "解压覆盖(生产 .env 保留)..."
# v0.64.x P1-1: 定制 compose 保护 —— 服务器可能用宝塔 PG + host 网络定制 compose
# (network_mode: host / mem_limit / config+assets bind mount), 官方 compose(容器 PG +
# bridge + ports 8080:5000)整包覆盖会让升级必失败。先留一份当前 compose 供解包后恢复。
[ -f "$SCRIPT_DIR/docker-compose.yml" ] && cp "$SCRIPT_DIR/docker-compose.yml" "$TMP_DIR/compose.custom.yml"
tar -xzf "$TMP_DIR/$PKG" -C "$ROOT_DIR"
echo "$VERSION" > "$VERSION_FILE"
# 恢复定制 compose: 显式开关 COS_UPDATE_PRESERVE_COMPOSE=1, 或 .env 的 PGDATABASE_URL
# 主机不是 postgres(= 非 compose 内建容器 PG, 而是外部/宝塔 PG 定制环境)。
_preserve_compose=0
if [ "${COS_UPDATE_PRESERVE_COMPOSE:-0}" = "1" ]; then
  _preserve_compose=1
elif [ -f "$SCRIPT_DIR/.env" ]; then
  # 提取 PGDATABASE_URL 里 @host(:port/path) 的主机部分; 无 .env/无该行 → 空 → 非定制
  _pg_host=$(sed -nE 's#^PGDATABASE_URL=.*@([^:/]+).*#\1#p' "$SCRIPT_DIR/.env" | head -1 || true)
  [ -n "$_pg_host" ] && [ "$_pg_host" != "postgres" ] && _preserve_compose=1
fi
if [ "$_preserve_compose" = "1" ] && [ -f "$TMP_DIR/compose.custom.yml" ]; then
  cp "$TMP_DIR/compose.custom.yml" "$SCRIPT_DIR/docker-compose.yml"
  log "已恢复定制 compose(host 网络/外部 PG)"
fi
log "✅ 新版本文件就位 v${VERSION}"

# ── 5.5 WebUI 前端校验(v0.41: 部署包含 webui/dist, compose 挂载到容器 /app/webui/dist) ──
# v0.62.2: webui 已随 worker 镜像内建(worker/Dockerfile webui-builder 阶段),
# 部署包内 webui/dist 仅作旁路校验, 缺失不影响(镜像会从 webui 源码重建)。
if [ -f "$ROOT_DIR/webui/dist/index.html" ]; then
  log "✅ WebUI 部署包产物存在(镜像内建, 宿主 dist 不再挂载)"
else
  log "ℹ️  部署包无 webui/dist —— 镜像将从 webui 源码内建, 无需宿主 dist"
fi

# ── 6. 优雅重建(compose 已配 stop_grace_period: 5m) ──
log "docker compose build + up(优雅关闭, 排空运行中任务)..."
cd "$SCRIPT_DIR"
if ! docker compose build --no-cache 2>&1 | tail -3; then
  warn "build 失败, 尝试回滚"
  rollback "$BACKUP_PATH"
  exit 1
fi
if ! docker compose up -d 2>&1 | tail -3; then
  warn "up 失败, 尝试回滚"
  rollback "$BACKUP_PATH"
  exit 1
fi

# ── 7. 健康检查 ──
log "健康检查(最多 60s)..."
HEALTH_OK=0
for i in $(seq 1 30); do
  if curl -fsS --max-time 3 "http://localhost:8080/api/v1/health" >/dev/null 2>&1; then
    HEALTH_OK=1
    break
  fi
  sleep 2
done
if [ "$HEALTH_OK" -ne 1 ]; then
  warn "健康检查失败, 自动回滚"
  rollback "$BACKUP_PATH"
  exit 1
fi

log "🎉 升级完成: v${LOCAL_VERSION:-无} → v${VERSION}, 健康检查通过"
log "备份保留在: $BACKUP_PATH(如需回滚: bash deploy/cos-update.sh v${LOCAL_VERSION:-0.0.0})"

# ── 7.4 v0.62.1 P1-3: CREDENTIAL_MASTER_KEY 必配校验 ──
# 升级后 .env 由 cos-update 保留（绝不覆盖），此处显式提示缺失，防止
# 「库中有加密凭证但容器无 key」→ store_sync 解密失败刷屏事故重演。
if ! grep -qE '^CREDENTIAL_MASTER_KEY=.+' "$SCRIPT_DIR/.env" 2>/dev/null; then
  warn "⚠️ .env 未配置 CREDENTIAL_MASTER_KEY — 凭证加密/解密必需(AES-256-GCM)。"
  warn "   生成: openssl rand -base64 32；启用后不可随意更换(存量凭证不可逆)。"
  warn "   当前仅提示不阻断；若库中存在加密凭证，凭证 CRUD 将 500、同步将解密失败。"
fi

# ── 7.5 数据库迁移(v0.56.7: 升级后必跑 init_data, 幂等) ──
# init_data.py 内含全部幂等 ALTER(ADD COLUMN IF NOT EXISTS / SET DEFAULT)。
# v0.56.3 教训: 列默认值只在 model.py 对新建表生效, 存量旧表缺默认值 →
# 升级后任务表 INSERT 违反 NOT NULL。升级后自动跑, 无需手动补 ALTER。
log "🛠️ 执行数据库迁移(init_data.py, 幂等)..."
if docker compose exec -T worker python scripts/init_data.py >/dev/null 2>&1; then
  log "✅ 数据库迁移完成"
else
  warn "⚠️ init_data.py 执行失败——检查日志; 建议手动: docker compose exec worker python scripts/init_data.py"
fi

# ── 8. Docker 清理(--no-cache 构建累积历史镜像层/缓存, 防磁盘膨胀) ──
# v0.34.0: 只清理本项目的未使用镜像层 + 全部构建缓存。
# ⚠️ 不用 docker image prune -a(会删服务器上所有未引用镜像, 可能误伤其他项目):
#   仅清理 dangling(无 tag 的孤儿层) + 旧的 ozon-worker 历史镜像(保留 latest + 当前运行)。
log "🧹 Docker 清理(dangling 镜像层 + 构建缓存)..."
if command -v docker >/dev/null 2>&1; then
  # 1) 构建缓存(BuildKit 累积, --no-cache 每次全量构建最占空间)
  docker builder prune -a -f >/dev/null 2>&1 && log "  ✅ 构建缓存已清理" || warn "  ⚠️ builder prune 失败(忽略)"
  # 2) dangling 镜像层(历史 --no-cache 构建留下的 <none> 层)
  docker image prune -f >/dev/null 2>&1 && log "  ✅ dangling 镜像已清理" || warn "  ⚠️ image prune 失败(忽略)"
  # 3) 旧的 ozon-worker 历史版本镜像(保留 latest + 当前运行, 只删更旧的 untagged/历史 tag)
  docker images ozon-worker --format '{{.Repository}}:{{.Tag}} {{.ID}}' 2>/dev/null | grep -v 'latest' | while read -r _img _id; do
    if [ -n "$_id" ]; then
      docker rmi "$_id" >/dev/null 2>&1 && log "  ✅ 移除旧镜像层 $_id" || warn "  ⚠️ 移除 $_id 失败(可能被引用, 忽略)"
    fi
  done
  log "🧹 Docker 清理完成"
else
  warn "未找到 docker, 跳过清理"
fi
log "📦 最终磁盘占用: $(docker system df 2>/dev/null | grep -E 'Images|Build Cache' | tr '\n' ' ' || echo 'N/A')"
