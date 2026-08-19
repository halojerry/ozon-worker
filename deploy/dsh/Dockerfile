# ── Pounding dsh 电商客户端 — Docker 测试沙箱镜像 ──
#
# 用途：在 Docker 隔离环境验证 dsh web + 电商插件集成链路
#   （pounding-guard 门控 / mcp-pounding MCP 管道 / better-sidebar+pounding-sidebar 8 板块）
# 避免污染本机环境。构建上下文 = ozon-worker 仓库根（见 docker-compose.dsh.yml）。
#
# 说明：skill 的 CDP 浏览器能力依赖本机 Chrome profile（产品架构上 skill 就跑在
# 用户机器上），容器内仅验证 harness + 插件 + MCP 管道层；CDP 采集工具的实际
# 运行仍需真机。
# ⚠️ 基础镜像选 ubuntu:24.04：自带 python3.12（node:24-slim 是 3.11，
# pounding-mcp 要求 >=3.12，曾在构建时 pip 直接报错）。node 经 NodeSource 装 24。
FROM ubuntu:24.04

# node 24（NodeSource）+ python3.12 + 构建工具（node-pty 编译兜底）+ git/curl
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends \
      nodejs python3 python3-venv python3-pip \
      build-essential git \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g pnpm \
    && npm install -g @deepseek-ai/dsh@0.1.0-rc.7 \
    && python3 --version

# dsh profile 落在 $HOME/.dsh
ENV HOME=/app/dsh-home
WORKDIR /app

# ── 电商插件 + skill + profile 生成器 ──
COPY dsh-pounding-guard /app/dsh-pounding-guard
COPY pounding-sidebar /app/pounding-sidebar
COPY pounding-mcp /app/pounding-mcp
COPY skill /app/skill
COPY deploy/dsh/setup-profile.py /app/setup-profile.py

# pounding-mcp python venv（fastmcp + skill 运行时依赖）
RUN python3 -m venv /app/pounding-mcp/.venv \
    && /app/pounding-mcp/.venv/bin/pip install --no-cache-dir -q fastmcp requests websocket-client Pillow \
    && /app/pounding-mcp/.venv/bin/pip install --no-cache-dir -q -e /app/pounding-mcp

# guard patch 里的开发机绝对路径 → 容器路径
RUN sed -i 's|/Volumes/os/dev/ozon-worker/|/app/|g' /app/dsh-pounding-guard/cordis.patch.yml \
    && grep -q 'command: /app/pounding-mcp/.venv/bin/python3' /app/dsh-pounding-guard/cordis.patch.yml

# ── 预建 web profile + 装插件依赖 ──
RUN python3 /app/setup-profile.py \
    && cd "$HOME/.dsh/profiles/web" && pnpm install --silent

EXPOSE 3080
CMD ["dsh", "web", "--host", "0.0.0.0", "--port", "3080"]
