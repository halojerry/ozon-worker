#!/bin/bash
# WebUI 全页审计（v0.62.2）—— 本地 Docker 栈 + Playwright
# 用法: E2E_TOKEN=<真实API Key> [E2E_SUPABASE_URL=... E2E_SUPABASE_KEY=...] bash scripts/test-docker-e2e.sh
#   或: E2E_USERNAME=<账号> E2E_PASSWORD=<密码> ...
# 说明: worker 镜像内建 webui; 需提供真实会话(e2e 默认用 E2E_TOKEN 注入, 否则走 MXOU 登录)。
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

CFG="deploy/docker-compose.e2e.yml"

echo "═══════════════════════════════════════"
echo "  WebUI 全页审计 (Docker + Playwright)"
echo "═══════════════════════════════════════"

docker compose -f "$CFG" build worker
docker compose -f "$CFG" up -d postgres

echo "→ 等待 PG 就绪..."
for i in $(seq 1 30); do
  if docker compose -f "$CFG" exec -T postgres pg_isready -U postgres -d ozon >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

docker compose -f "$CFG" up -d worker
echo "→ 等待 worker 就绪 + /app 可访问..."
for i in $(seq 1 60); do
  if curl -sf http://localhost:8080/api/v1/health >/dev/null 2>&1 \
     && curl -s http://localhost:8080/app/ | grep -q "<div id=\"root\">"; then
    break
  fi
  sleep 2
done

echo "→ 初始化数据库..."
docker compose -f "$CFG" exec -T worker python scripts/init_data.py >/dev/null 2>&1 || \
  echo "⚠️  init_data 失败(审计可继续, 请检查日志)"

echo "→ 启动 Playwright 审计..."
cd webui
bunx playwright install chromium --with-deps >/dev/null 2>&1 || true
E2E_BASE_URL="http://localhost:8080/app" bunx playwright test

if [ "${E2E_KEEP:-0}" != "1" ]; then
  cd "$PROJECT_DIR"
  docker compose -f "$CFG" down -v >/dev/null 2>&1 || true
fi
echo "✅ 审计完成"
