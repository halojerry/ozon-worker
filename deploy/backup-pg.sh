#!/bin/bash
# PRD M5: PG 自动备份(pg_dump 加密 + 保留 14 天)
# 用法: bash deploy/backup-pg.sh [--restore backup_YYYYMMDD.sql]
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKUP_DIR="${PG_BACKUP_DIR:-$SCRIPT_DIR/backups}"
RETENTION_DAYS="${PG_BACKUP_RETENTION_DAYS:-14}"
COMPOSE="docker compose -f $SCRIPT_DIR/docker-compose.yml"

if [ "$1" = "--restore" ]; then
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

$COMPOSE exec -T postgres pg_dump -U postgres -d ozon > "$OUT"
if [ -n "$PG_BACKUP_PASSPHRASE" ]; then
  gpg --symmetric --batch --yes --passphrase "$PG_BACKUP_PASSPHRASE" -o "$OUT.gpg" "$OUT"
  rm -f "$OUT"
  echo "✅ 备份(加密): $OUT.gpg"
else
  echo "✅ 备份: $OUT (未加密;建议设置 PG_BACKUP_PASSPHRASE)"
fi

# 保留策略:删除 N 天前的备份
find "$BACKUP_DIR" -name "backup_*.sql*" -mtime +"$RETENTION_DAYS" -delete
echo "ℹ️  保留 $RETENTION_DAYS 天;目录: $BACKUP_DIR"
