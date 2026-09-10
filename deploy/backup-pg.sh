#!/bin/bash
# PRD M5: PG 自动备份(pg_dump 加密 + 保留 14 天)
# 用法: bash deploy/backup-pg.sh [--restore backup_YYYYMMDD.sql]
#
# BL-08 (repo-gov): 双模式 + 备份心跳（backup_heartbeat）
#
# 【双模式】本脚本在两种环境下都能跑（调度由 deploy/docker-compose.yml 的
# ofelia sidecar 通过 job-exec 在 postgres 容器内执行，宿主机 crontab 仍可用）：
# - host 模式（默认）：经 `docker compose exec -T postgres` 转发 pg_dump/psql；
# - container 模式（自动检测：有 pg_dump 无 docker；或显式 BACKUP_PG_MODE=container）：
#   在 postgres 容器内直连本容器 unix socket（postgres:16-alpine 自带
#   pg_dump/psql；busybox sh 即可执行本脚本——全文 POSIX 语法，勿用 bashism）。
#
# 【加密前提差异】postgres:16-alpine 无 gpg：container 模式下若设置了
# PG_BACKUP_PASSPHRASE 会显式失败退出（宁可不备份也不落明文），如需加密备份
# 请走宿主机 cron。host 模式行为不变（gpg 可用则加密）。
#
# 【心跳】成功/失败各写一行 backup_heartbeat(finished_at, ok, detail)：
# - 供 worker /health 的 last_backup_at / backup_stale 判定（>26h 无成功心跳
#   视为备份陈旧）与运维巡检；
# - 表由 worker 进程 create_all 建（services/backup_heartbeat_service 对应模型）
#   —— 前提：升级后的 worker 至少成功启动过一次；表不存在时心跳写不进去只
#   打警告，绝不拖死备份主流程（PG 不可达时同理）。
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="${PG_BACKUP_DIR:-$SCRIPT_DIR/backups}"
RETENTION_DAYS="${PG_BACKUP_RETENTION_DAYS:-14}"
COMPOSE="docker compose -f $SCRIPT_DIR/docker-compose.yml"

# ---- 双模式检测 ------------------------------------------------------------
MODE="${BACKUP_PG_MODE:-}"
if [ -z "$MODE" ]; then
  if command -v pg_dump >/dev/null 2>&1 && ! command -v docker >/dev/null 2>&1; then
    MODE=container
  else
    MODE=host
  fi
fi

PG_USER="${POSTGRES_USER:-postgres}"
PG_DB="${POSTGRES_DB:-ozon}"

if [ "$MODE" = "container" ]; then
  pg_dump_cmd() { pg_dump -U "$PG_USER" -d "$PG_DB"; }
  psql_cmd()    { psql -U "$PG_USER" -d "$PG_DB" "$@"; }
else
  pg_dump_cmd() { $COMPOSE exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB"; }
  psql_cmd()    { $COMPOSE exec -T postgres psql -U "$PG_USER" -d "$PG_DB" "$@"; }
fi

# ---- 备份心跳（BL-08）------------------------------------------------------
# 用法: hb_write <true|false> <detail>。任何失败只告警不中断主流程。
hb_write() {
  if ! psql_cmd -v ON_ERROR_STOP=1 -c \
    "INSERT INTO backup_heartbeat (finished_at, ok, detail) VALUES (extract(epoch from now())::float, $1, '$2')" \
    >/dev/null 2>&1; then
    echo "⚠️  backup_heartbeat 写入失败（表未建或 PG 不可达）——不影响备份主流程" >&2
  fi
}

if [ "$1" = "--restore" ]; then
  if [ "$MODE" = "container" ]; then
    echo "❌ --restore 仅支持宿主机模式（需 docker compose + 宿主机文件访问）"; exit 1
  fi
  FILE="$2"
  [ -n "$FILE" ] && [ -f "$FILE" ] || { echo "❌ 用法: $0 --restore backup.sql"; exit 1; }
  if [ -n "$PG_BACKUP_PASSPHRASE" ]; then
    gpg --decrypt --batch --yes --passphrase "$PG_BACKUP_PASSPHRASE" "$FILE" > /tmp/restore.sql
    $COMPOSE exec -T postgres psql -U postgres -d ozon < /tmp/restore.sql
    rm -f /tmp/restore.sql
  else
    $COMPOSE exec -T postgres psql -U postgres -d ozon < "$FILE"
  fi
  echo "✅ 恢复完成: $FILE"
  exit 0
fi

mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$BACKUP_DIR/backup_$STAMP.sql"

if ! pg_dump_cmd > "$OUT"; then
  rm -f "$OUT"
  echo "❌ 备份失败: pg_dump 非零退出 (mode=$MODE)" >&2
  hb_write false "pg_dump failed (mode=$MODE)"
  exit 1
fi

FINAL="$OUT"
if [ -n "$PG_BACKUP_PASSPHRASE" ]; then
  if ! command -v gpg >/dev/null 2>&1; then
    rm -f "$OUT"
    echo "❌ PG_BACKUP_PASSPHRASE 已设置但环境无 gpg (mode=$MODE)——拒绝落明文备份，请改用宿主机 cron" >&2
    hb_write false "gpg missing, plaintext refused (mode=$MODE)"
    exit 1
  fi
  gpg --symmetric --batch --yes --passphrase "$PG_BACKUP_PASSPHRASE" -o "$OUT.gpg" "$OUT"
  rm -f "$OUT"
  FINAL="$OUT.gpg"
  echo "✅ 备份(加密): $FINAL"
else
  echo "✅ 备份: $OUT (未加密;建议设置 PG_BACKUP_PASSPHRASE)"
fi

# BL-08: 成功心跳（detail=备份文件名）
hb_write true "$(basename "$FINAL")"

# 保留策略:删除 N 天前的备份
find "$BACKUP_DIR" -name "backup_*.sql*" -mtime +"$RETENTION_DAYS" -delete
echo "ℹ️  保留 $RETENTION_DAYS 天;目录: $BACKUP_DIR"
