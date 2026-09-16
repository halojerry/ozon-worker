#!/bin/bash
# backup-upload-cos.sh — 备份异地上传（v0.75 部署加固；v0.76 修复波补上传侧加密+心跳）
#
# 背景：backup-pg.sh 的备份默认落本机 deploy/backups/——与生产库同一块盘同生共死
# （docs/DEPLOY.md 旧说法「与生产库同命运」），2026-09-11 I/O 雪崩事故后补异地化。
# ofelia 的 dump 流程不动，本脚本只读 backups/ 目录做增量上传（.uploaded sidecar
# 幂等标记），二者可并存、零重复备份风险。
#
# v0.76 修复（PR#29 评审 75 分项「备份链静默失效」）：
# - 上传侧加密：设 PG_BACKUP_PASSPHRASE（env 或 deploy/.env）且宿主机有 gpg 时，
#   明文 dump 现场加密成 .gpg 再上传——container 模式生产者（postgres:16-alpine）
#   无 gpg 的结构缺口在消费端闭环，bucket 里永远只有密文（明文仍留本地盘，14 天
#   本地保留不变；加密幂等：$f.gpg.uploaded 在场即跳过，.gpg 在场无 sidecar 续传）。
# - 心跳可观测（BL-08「成功/失败都写一行」延伸到异地链）：明文跳过/加密失败/
#   上传失败写 backup_heartbeat ok=false；有新上传写 ok=true；空跑（无新文件）
#   不写——生产者 dump 心跳照常保鲜。修复前跳过路径 exit 0 且零心跳，
#   /health backup_stale 恒绿、异地链静默断供。
#
# 用法（建议 crontab，每日 04:10 跑在 ofelia dump 之后）：
#   10 4 * * * cd /<安装目录>/deploy && bash backup-upload-cos.sh >> backups/upload.log 2>&1
#
# coscli 认证：优先用 coscli 自身配置（~/.coscli.yaml，与 CACHE-WARM-RUNBOOK 上传
# 缓存同配置）；也可在环境/export 提供 COS_SECRET_ID/COS_SECRET_KEY 显式覆盖。
# coscli 缺失 → exit 1 显性告警（让 cron 邮件/日志可见，不静默）。
#
# 上传目标：cos://<COS_BUCKET>/ozon-worker/backups/；远端按文件名内嵌日期
# （backup_YYYYMMDD_HHMMSS）保留 N 天（默认 14，与本地保留一致）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="${PG_BACKUP_DIR:-$SCRIPT_DIR/backups}"
RETENTION_DAYS="${PG_BACKUP_REMOTE_RETENTION_DAYS:-14}"
REMOTE_PREFIX="ozon-worker/backups"

# COS 配置读取（与 cos-update.sh 同源：环境变量 > deploy/.env > 默认）
COS_BUCKET="${COS_BUCKET:-yss-1256275613}"
COS_REGION="${COS_REGION:-ap-guangzhou}"
# 上传侧加密与心跳所需（环境变量 > deploy/.env）。⚠️ PG_BACKUP_PASSPHRASE 仅供
# 本脚本宿主侧 gpg 加密用——勿注入 postgres 容器：container 模式生产者无 gpg，
# 注入会让 backup-pg.sh 直接拒跑（宁可不备份也不落明文），本机备份一并消失。
# gitleaks 纪律：勿写 `PG_BACKUP_PASSPHRASE=<...>` 字面赋值（ozon-custom-password-assign
# 会把变量间接赋值当密码命中）——用 POSIX `: "${VAR:=默认}"` 惯用法，语义等价
: "${PG_BACKUP_PASSPHRASE:=}"
PG_USER="${POSTGRES_USER:-postgres}"
PG_DB="${POSTGRES_DB:-ozon}"
if [ -f "$SCRIPT_DIR/.env" ]; then
  # shellcheck disable=SC1091
  _b=$(grep -E '^COS_BUCKET=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"' || true)
  _r=$(grep -E '^COS_REGION=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"' || true)
  _p=$(grep -E '^PG_BACKUP_PASSPHRASE=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"' || true)
  _u=$(grep -E '^POSTGRES_USER=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"' || true)
  _d=$(grep -E '^POSTGRES_DB=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"' || true)
  [ -n "$_b" ] && COS_BUCKET="$_b"
  [ -n "$_r" ] && COS_REGION="$_r"
  if [ -n "${_p:-}" ] && [ -z "$PG_BACKUP_PASSPHRASE" ]; then
    : "${PG_BACKUP_PASSPHRASE:=$_p}"
  fi
  [ -n "$_u" ] && PG_USER="$_u"
  [ -n "$_d" ] && PG_DB="$_d"
fi

# DRY_RUN=1 只演练上传决策，不执行任何 coscli/gpg 调用——coscli 缺失不阻断（本地验证矩阵用）
if [ "${DRY_RUN:-0}" != "1" ]; then
  command -v coscli >/dev/null 2>&1 || {
    echo "❌ coscli 不在 PATH——安装见 https://cloud.tencent.com/document/product/436/63144"
    exit 1
  }
fi

# 显式凭证存在则覆盖 coscli 配置（默认走 ~/.coscli.yaml）
COSCLI="coscli --disable-log=true"
if [ -n "${COS_SECRET_ID:-}" ] && [ -n "${COS_SECRET_KEY:-}" ]; then
  COSCLI="coscli -e cos.accelerate.myqcloud.com -i $COS_SECRET_ID -k $COS_SECRET_KEY --init-skip=true --disable-log=true"
fi

if [ ! -d "$BACKUP_DIR" ]; then
  echo "ℹ️  备份目录不存在（尚无备份），跳过: $BACKUP_DIR"
  exit 0
fi

# ---- 备份心跳（BL-08 延伸到异地链）------------------------------------------
# 用法: hb_write <true|false> <detail>。宿主机经 docker compose 转发 psql；
# 任何失败只告警不中断主流程（表未建/PG 不可达/compose 未起时同理——
# 与 backup-pg.sh 的 hb_write 同契约）。detail 勿含单引号（裸拼 SQL）。
COMPOSE="docker compose -f $SCRIPT_DIR/docker-compose.yml"
hb_write() {
  if ! $COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 -c \
    "INSERT INTO backup_heartbeat (finished_at, ok, detail) VALUES (extract(epoch from now())::float, $1, '$2')" \
    >/dev/null 2>&1; then
    echo "⚠️  backup_heartbeat 写入失败（表未建或 PG 不可达）——不影响上传主流程" >&2
  fi
}

# ---- 增量上传（.uploaded sidecar 幂等 + 明文拒传/上传侧加密）----
UPLOADED=0
FAILED=0
SKIPPED_PLAINTEXT=0
for f in "$BACKUP_DIR"/backup_*.sql*; do
  [ -f "$f" ] || continue
  # sidecar 本身不进决策：glob *.sql* 会捞到 .uploaded（旧脚本同样误捞，仅以
  # 「跳过明文」噪音形式存在；加密分支则会误给它套一层 .gpg）——先行排除。
  case "$f" in
    *.uploaded) continue ;;
  esac
  [ -f "$f.uploaded" ] && continue
  # 安全闸（crypto-M1/cicd-M1）：bucket 只进密文。明文 dump 三条路：
  # ① 设 PG_BACKUP_PASSPHRASE（env/.env）且宿主机有 gpg → 现场加密成 $f.gpg 再传；
  # ② ALLOW_PLAINTEXT_BACKUP_UPLOAD=1 显式放行（打 warn 留痕，应急用）；
  # ③ 都没有 → 跳过并计数，收尾 hb_write false 让 /health 链路可见——绝不静默。
  # 位置在存在/sidecar 守卫之后：空目录 glob 字面量与已上传文件不产生重复噪音。
  case "$f" in
    *.gpg)
      [ -f "$f.uploaded" ] && continue
      ACTION=upload; UPLOAD_F="$f"; SIDE_F="$f.uploaded"
      ;;
    *)
      # ${f} 必须加花括号：$f 紧跟全角（ 时 macOS 系统 bash 3.2 会把多字节
      # 首字节并进变量名，set -u 下报 unbound variable
      if [ "${ALLOW_PLAINTEXT_BACKUP_UPLOAD:-0}" = "1" ]; then
        echo "  ⚠️ 明文备份放行（ALLOW_PLAINTEXT_BACKUP_UPLOAD=1）: $(basename "$f")" >&2
        [ -f "$f.uploaded" ] && continue
        ACTION=upload; UPLOAD_F="$f"; SIDE_F="$f.uploaded"
      elif [ -n "$PG_BACKUP_PASSPHRASE" ] && command -v gpg >/dev/null 2>&1; then
        if [ -f "${f}.gpg.uploaded" ]; then
          continue
        fi
        ACTION=encrypt; UPLOAD_F="${f}.gpg"; SIDE_F="${f}.gpg.uploaded"
      else
        echo "⏭️ 跳过明文备份 ${f}（设 PG_BACKUP_PASSPHRASE 自动加密上传；应急 ALLOW_PLAINTEXT_BACKUP_UPLOAD=1）" >&2
        SKIPPED_PLAINTEXT=$((SKIPPED_PLAINTEXT + 1))
        continue
      fi
      ;;
  esac
  # DRY_RUN=1 演练：打印上传计划即跳过实际 gpg/coscli 调用（不写 .uploaded sidecar）
  if [ "${DRY_RUN:-0}" = "1" ]; then
    if [ "$ACTION" = "encrypt" ]; then
      echo "[dry-run] 将加密并上传: $(basename "$f") → $(basename "$UPLOAD_F")"
    else
      echo "[dry-run] 将上传: $UPLOAD_F (跳过实际执行)"
    fi
    continue
  fi
  if [ "$ACTION" = "encrypt" ]; then
    if [ ! -f "$UPLOAD_F" ]; then
      _tmp="${UPLOAD_F}.tmp$$"
      if ! gpg --symmetric --batch --yes --passphrase "$PG_BACKUP_PASSPHRASE" -o "$_tmp" "$f" \
        || ! mv -f "$_tmp" "$UPLOAD_F"; then
        rm -f "$_tmp"
        echo "  ❌ 上传侧加密失败: $(basename "$f")" >&2
        FAILED=$((FAILED + 1))
        continue
      fi
    fi
  fi
  if $COSCLI cp "$UPLOAD_F" "cos://${COS_BUCKET}/${REMOTE_PREFIX}/$(basename "$UPLOAD_F")" >/dev/null 2>&1; then
    date -u +%Y-%m-%dT%H:%M:%SZ > "$SIDE_F"
    echo "  ✓ 已上传: $(basename "$UPLOAD_F")"
    UPLOADED=$((UPLOADED + 1))
  else
    echo "  ❌ 上传失败: $(basename "$UPLOAD_F")" >&2
    FAILED=$((FAILED + 1))
  fi
done
echo "ℹ️  上传完成: 新传 $UPLOADED / 失败 $FAILED / 明文跳过 $SKIPPED_PLAINTEXT → cos://${COS_BUCKET}/${REMOTE_PREFIX}/"
if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "[dry-run] 结束：未执行实际上传、心跳与远端保留清理"
  exit 0
fi

# ---- 心跳收尾（判序：失败/跳过 > 新上传 > 静默）--------------------------------
# 明文跳过=异地链断供，即使部分存量 .gpg 传上去了也如实 false（/health 口径：
# >26h 无成功心跳判 backup_stale；false 行同时落 backup_heartbeat 供运维巡检）。
if [ "$FAILED" -gt 0 ]; then
  hb_write false "upload failed: $FAILED"
elif [ "$SKIPPED_PLAINTEXT" -gt 0 ]; then
  hb_write false "plaintext skipped: $SKIPPED_PLAINTEXT (no passphrase/gpg on host) - offsite chain down"
elif [ "$UPLOADED" -gt 0 ]; then
  hb_write true "uploaded: $UPLOADED (encrypted at upload)"
fi

# ---- 远端保留清理（按文件名内嵌日期；失败不阻断）----
# GNU date 优先，macOS BSD date 兜底
# ⚠️ 管道尾 || true：set -o pipefail 下空 bucket/无备份时 grep 无匹配 rc=1
# 会把整条管道判失败（空态天天假报错）——清理段本就声明「失败不阻断」（终审 review Minor#3）。
CUTOFF=$(date -d "-${RETENTION_DAYS} days" +%Y%m%d 2>/dev/null || date -v-"${RETENTION_DAYS}"d +%Y%m%d)
$COSCLI ls "cos://${COS_BUCKET}/${REMOTE_PREFIX}/" 2>/dev/null \
  | grep -oE 'backup_[0-9]{8}_[0-9]{6}\.sql(\.gpg)?' | sort -u \
  | while read -r name; do
    _d=$(echo "$name" | cut -d_ -f2)
    if [ "$_d" -lt "$CUTOFF" ]; then
      if $COSCLI rm "cos://${COS_BUCKET}/${REMOTE_PREFIX}/${name}" >/dev/null 2>&1; then
        echo "  🧹 远端过期清理: $name"
      fi
    fi
  done || true

[ "$FAILED" -eq 0 ] || exit 1
