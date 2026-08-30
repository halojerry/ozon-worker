#!/bin/bash
# PRD M5: 本地一键 worker 测试环境(与 CI 对齐)
# 用法: bash scripts/test-docker.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "═══════════════════════════════════════"
echo "  Worker Docker 测试(PRD M5)"
echo "═══════════════════════════════════════"

docker compose -f deploy/docker-compose.test.yml build worker-test
docker compose -f deploy/docker-compose.test.yml up -d postgres

echo "→ 等待 PG 就绪..."
for i in $(seq 1 30); do
  if docker compose -f deploy/docker-compose.test.yml exec -T postgres pg_isready -U postgres -d ozon >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

docker compose -f deploy/docker-compose.test.yml run --rm worker-test
echo "✅ 测试完成"
