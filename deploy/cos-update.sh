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
# v0.73 W4 终审修正: 自举 exec 的目标是包内临时脚本——不回正路径的话新进程会把
# $TMP_DIR 当 ROOT_DIR(备份/VERSION/整包解压全落 tmp、.env 读不到致 compose 保护
# 静默跳过)。exec 时透传真实安装目录, 此处在 BACKUP_DIR 等派生之前重算。
if [ "${COS_UPDATE_EXECED:-0}" = "1" ] && [ -n "${COS_UPDATE_REAL_SCRIPT_DIR:-}" ]; then SCRIPT_DIR="$COS_UPDATE_REAL_SCRIPT_DIR"; ROOT_DIR="$(dirname "$SCRIPT_DIR")"; fi
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

# ── 0. v0.75 部署加固：预检 fail-fast（磁盘/主密钥前移，docs/audit/2026-09-11-io-avalanche.md）──
# 此前 CREDENTIAL_MASTER_KEY 缺失只在升级完成后 warn（事后诸葛——凭证功能已坏才提示）；
# 磁盘空间从不检查（v0.72 40G 盘教训 + --no-cache 全量重建需要 GB 级空间）。
# 逃生门：确认不用凭证功能可设 COS_UPDATE_ALLOW_NO_MASTER_KEY=1。
# ⚠️ 首装引导路径（deploy.sh 本地无源码先跑本脚本）时 .env 可能尚不存在——
# 此时跳过主密钥检查（空库无加密凭证），deploy.sh 在 .env 就位后有硬校验。
FREE_KB=$(df -P "$SCRIPT_DIR" 2>/dev/null | awk 'NR==2 {print $4}')
MIN_FREE_KB=$(( ${DISK_MIN_FREE_GB:-6} * 1024 * 1024 ))
if [ -n "$FREE_KB" ] && [ "$FREE_KB" -lt "$MIN_FREE_KB" ]; then
  fail "磁盘剩余不足: ${FREE_KB}KB < ${DISK_MIN_FREE_GB:-6}GB（--no-cache 全量重建需要）——先清理: backups/ 轮转、docker builder prune -a"
fi
if [ -f "$SCRIPT_DIR/.env" ]; then
  if ! grep -qE '^CREDENTIAL_MASTER_KEY=.+' "$SCRIPT_DIR/.env" 2>/dev/null; then
    if [ "${COS_UPDATE_ALLOW_NO_MASTER_KEY:-0}" = "1" ]; then
      warn "CREDENTIAL_MASTER_KEY 缺失但已显式放行（COS_UPDATE_ALLOW_NO_MASTER_KEY=1）"
    else
      fail ".env 未配置 CREDENTIAL_MASTER_KEY —— 凭证加密必需(AES-256-GCM)。生成: openssl rand -base64 32；确认不用凭证功能可设 COS_UPDATE_ALLOW_NO_MASTER_KEY=1 放行"
    fi
  fi
fi

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
  # v0.73 W5: manifest version 是 tag 名(带 v 前缀), 剥 v 统一口径——否则日志/比较出现 vv0.72.0
  VERSION="${VERSION#v}"
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
# v0.73 W5: 服务器现存 VERSION 文件可能带 v 前缀(旧版 cd.yml 写入的是 tag 名), 比较前剥 v
LOCAL_VERSION="${LOCAL_VERSION#v}"
# v0.73 终审顺手修: VERSION 文件非空即导出(空文件=首装态不导出)——compose 的
# ${VERSION:-latest} 与之同源, 后续任何 compose 调用不再解析不存在的 latest tag
[ -n "$LOCAL_VERSION" ] && export VERSION
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

# ── 3.5 v0.73 W4: 自举——包内脚本比当前新则 exec 新版重跑 ──
# 此前: 解压覆盖运行中的脚本 → bash 后续读到新旧混合字节（v0.64 升级
# 白费 1h 事故根因）。自举后所有变更性操作都在新版逻辑下执行。
if [ "${COS_UPDATE_EXECED:-0}" != "1" ]; then
  tar -xzf "$TMP_DIR/$PKG" -C "$TMP_DIR" deploy/cos-update.sh 2>/dev/null || true
  if [ -f "$TMP_DIR/deploy/cos-update.sh" ] && ! cmp -s "$TMP_DIR/deploy/cos-update.sh" "${BASH_SOURCE[0]}"; then
    log "检测到包内新版 cos-update.sh，自举重启以新版逻辑继续…"
    exec env COS_UPDATE_EXECED=1 COS_UPDATE_REAL_SCRIPT_DIR="$SCRIPT_DIR" bash "$TMP_DIR/deploy/cos-update.sh" "$@"
  fi
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
  # v0.73 终审顺手修: 回滚重建按备份版本号打 tag——镜像元数据不再谎报新版本
  # （LOCAL_VERSION 为空时 VERSION 置空, compose ${VERSION:-latest} 的 :- 对空串同样兜底 latest）
  export VERSION="$LOCAL_VERSION"
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
  # v0.72 防复发：备份后剔除 assets 缓存 JSON（数百 MB 级，轮转下会成磁盘复发放大点）
  cp -a "$ROOT_DIR/worker" "$BACKUP_PATH/worker"
  rm -f "$BACKUP_PATH/worker/assets/attribute_schemas_zh.json" \
        "$BACKUP_PATH/worker/assets/dictionary_values_zh.json" 2>/dev/null || true
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

# ✅ v0.72 防复发：备份轮转（保留最近 3 份，此前零轮转——40G 盘复发放大点之一）
BACKUP_KEEP=3
ls -1dt "$BACKUP_DIR"/v*_* 2>/dev/null | tail -n +"$((BACKUP_KEEP + 1))" | while read -r _old; do
  log "轮转旧备份: rm -rf $_old"
  rm -rf "$_old"
done

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
# v0.73 W5: compose 的 ${VERSION:-dev} build arg 与 ${VERSION:-latest} image tag 同源——
# 不 export 则 build arg 落 dev、镜像 tag 只有 latest, 任务 APP_VERSION 无法追踪版本。
export VERSION
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

# ── 7.4 v0.75 部署加固：生产库 marker 幂等写入（worker 测试闸门，prod_db_guard 消费）──
# 原 CREDENTIAL_MASTER_KEY 事后 warn 已前移到步骤 0 预检 fail-fast。
# marker 随 pg_dump 备份走；恢复到全新集群后需重跑本节 SQL（RESTORE-RUNBOOK 有提醒）。
if docker compose exec -T postgres sh -c 'psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-ozon}" -c "CREATE TABLE IF NOT EXISTS prod_marker (id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1), marked_at timestamptz NOT NULL DEFAULT now()); INSERT INTO prod_marker (id) VALUES (1) ON CONFLICT (id) DO NOTHING;"' >/dev/null 2>&1; then
  log "✅ 生产库 marker 就位（worker 测试闸门激活）"
else
  warn "prod_marker 写入失败——测试闸门未激活，请手动检查 postgres 容器"
fi

# ── 7.5 数据库迁移(v0.56.7: 升级后必跑 init_data, 幂等) ──
# init_data.py 内含全部幂等 ALTER(ADD COLUMN IF NOT EXISTS / SET DEFAULT)。
# v0.56.3 教训: 列默认值只在 model.py 对新建表生效, 存量旧表缺默认值 →
# 升级后任务表 INSERT 违反 NOT NULL。升级后自动跑, 无需手动补 ALTER。
# v0.75 加固第二批（H9）：失败从 warn 升级为 fail——「升级成功但 schema 半就绪」
# 是静默 500 源头。不自动回滚：代码回滚治不了半迁移的库（且 health 已绿）；
# 手动修复后本脚本重跑会因版本一致早退，缓存导入靠运行时懒加载兜底（7.6 注释）。
log "🛠️ 执行数据库迁移(init_data.py, 幂等)..."
# 输出落日志文件不吞 /dev/null（终审 review Important#2：fail 时排障需根因，
# 手动重跑才能看输出是坑）；成功时也留档对账回填行数。
if docker compose exec -T worker python scripts/init_data.py >"$SCRIPT_DIR/init_data_upgrade.log" 2>&1; then
  log "✅ 数据库迁移完成（输出: $SCRIPT_DIR/init_data_upgrade.log）"
else
  fail "init_data.py 执行失败——升级文件已就位但 schema 未完成（新列缺失=运行时 500）。根因看 $SCRIPT_DIR/init_data_upgrade.log；修复后无需回滚/重跑升级（缓存走懒加载兜底）"
fi

# ── 7.6 v0.70: 属性缓存全量 JSON——COS 下载 → 拷入容器 → 后台 --import-only ──
# 「部署即全量」：一次性分片预热(~16h) → --export-only → 上传 COS 后，此后每次
# 升级自动灌入全量缓存（30 天 TTL）。COS 缺失时跳过（懒加载兜底，不阻断升级）。
# 运维手册: docs/CACHE-WARM-RUNBOOK.md
CACHE_BASE_URL="https://${COS_BUCKET}.cos.${COS_REGION}.myqcloud.com/ozon-worker/cache"
CACHE_OK=0
for _f in attribute_schemas_zh.json dictionary_values_zh.json; do
  _tmp=$(mktemp)
  if curl -fsSL --retry 2 --retry-delay 2 --max-time 600 -o "$_tmp" "$CACHE_BASE_URL/$_f"; then
    if docker compose cp "$_tmp" "worker:/app/assets/$_f" 2>/dev/null; then
      log "  ✓ 属性缓存 JSON 就位: $_f"
      CACHE_OK=1
    fi
  else
    warn "  COS 无属性缓存 $_f（跳过，运行时懒加载兜底）"
  fi
  rm -f "$_tmp"
done
if [ "$CACHE_OK" = "1" ]; then
  log "🔥 后台灌入全量属性缓存(--import-only, 日志 /app/logs/warm_import.log)..."
  docker compose exec -T worker sh -c "python scripts/warm_category_cache.py --import-only >> /app/logs/warm_import.log 2>&1" &
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
  # 3) 旧的 ozon-worker 历史版本镜像(保留 latest + 当前运行版本, 只删更旧的 untagged/历史 tag)。
  #    v0.73 收口: export VERSION 后镜像不再有 latest tag, 旧 grep -v latest 会把在用镜像
  #    也列进来(rmi 被拒仅告警噪音)——改为排除 latest + 当前 VERSION(空则退回只排 latest)。
  _exclude="latest"; [ -n "${VERSION:-}" ] && _exclude="latest|${VERSION}"
  docker images ozon-worker --format '{{.Repository}}:{{.Tag}} {{.ID}}' 2>/dev/null | grep -Ev ":(${_exclude}) " | while read -r _img _id; do
    if [ -n "$_id" ]; then
      docker rmi "$_id" >/dev/null 2>&1 && log "  ✅ 移除旧镜像层 $_id" || warn "  ⚠️ 移除 $_id 失败(可能被引用, 忽略)"
    fi
  done
  log "🧹 Docker 清理完成"
else
  warn "未找到 docker, 跳过清理"
fi
log "📦 最终磁盘占用: $(docker system df 2>/dev/null | grep -E 'Images|Build Cache' | tr '\n' ' ' || echo 'N/A')"
