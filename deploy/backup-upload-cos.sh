#!/bin/bash
# backup-upload-cos.sh — 备份异地上传（v0.75 部署加固）
#
# 背景：backup-pg.sh 的备份默认落本机 deploy/backups/——与生产库同一块盘同生共死
# （docs/DEPLOY.md 旧说法「与生产库同命运」），2026-09-11 I/O 雪崩事故后补异地化。
# ofelia 的 dump 流程不动，本脚本只读 backups/ 目录做增量上传（.uploaded sidecar
# 幂等标记），二者可并存、零重复备份风险。
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
if [ -f "$SCRIPT_DIR/.env" ]; then
  # shellcheck disable=SC1091
  _b=$(grep -E '^COS_BUCKET=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"' || true)
  _r=$(grep -E '^COS_REGION=' "$SCRIPT_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"' || true)
  [ -n "$_b" ] && COS_BUCKET="$_b"
  [ -n "$_r" ] && COS_REGION="$_r"
fi

command -v coscli >/dev/null 2>&1 || {
  echo "❌ coscli 不在 PATH——安装见 https://cloud.tencent.com/document/product/436/63144"
  exit 1
}

# 显式凭证存在则覆盖 coscli 配置（默认走 ~/.coscli.yaml）
COSCLI="coscli --disable-log=true"
if [ -n "${COS_SECRET_ID:-}" ] && [ -n "${COS_SECRET_KEY:-}" ]; then
  COSCLI="coscli -e cos.accelerate.myqcloud.com -i $COS_SECRET_ID -k $COS_SECRET_KEY --init-skip=true --disable-log=true"
fi

if [ ! -d "$BACKUP_DIR" ]; then
  echo "ℹ️  备份目录不存在（尚无备份），跳过: $BACKUP_DIR"
  exit 0
fi

# ---- 增量上传（.uploaded sidecar 幂等）----
UPLOADED=0
FAILED=0
for f in "$BACKUP_DIR"/backup_*.sql*; do
  [ -f "$f" ] || continue
  [ -f "$f.uploaded" ] && continue
  if $COSCLI cp "$f" "cos://${COS_BUCKET}/${REMOTE_PREFIX}/$(basename "$f")" >/dev/null 2>&1; then
    date -u +%Y-%m-%dT%H:%M:%SZ > "$f.uploaded"
    echo "  ✓ 已上传: $(basename "$f")"
    UPLOADED=$((UPLOADED + 1))
  else
    echo "  ❌ 上传失败: $(basename "$f")" >&2
    FAILED=$((FAILED + 1))
  fi
done
echo "ℹ️  上传完成: 新传 $UPLOADED / 失败 $FAILED → cos://${COS_BUCKET}/${REMOTE_PREFIX}/"

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
