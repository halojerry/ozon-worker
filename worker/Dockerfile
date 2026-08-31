# ── Stage: webui-builder — 前端构建（bun）──
FROM oven/bun:1-slim AS webui-builder

WORKDIR /build/webui
# 国内提速：npm/bun 源指向阿里云镜像（PIP_INDEX_URL 同理）
ENV BUN_CONFIG_REGISTRY=https://registry.npmmirror.com
ENV NPM_CONFIG_REGISTRY=https://registry.npmmirror.com
COPY webui/ ./
RUN bun install --frozen-lockfile && bun run build


# ── Stage 1: builder — 编译工具链只在这里 ──
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY worker/pyproject.toml ./
# pytest 一起装进 /opt/venv(本地 docker run pytest 依赖; 体积 ~50MB, 可接受)
# PIP_INDEX_URL 可覆盖(默认阿里云, 国内网络稳); 官方/国际构建可传 pypi
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" --prefix=/opt/venv . pytest pytest-asyncio


# ── Stage 2: runtime — 只带运行所需 libpq5/curl, 镜像从 ~800MB → ~350MB ──
FROM python:3.12-slim AS runtime

WORKDIR /app

# 运行期依赖(编译工具链全部留在 builder)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 复制已安装的 Python 包(含编译好的 psycopg2 等)
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH=/opt/venv/lib/python3.12/site-packages:/app/src

# ── 应用代码层 ──
COPY worker/src/ ./src/
COPY worker/config/ ./config/
COPY worker/assets/ ./assets/
COPY worker/scripts/ ./scripts/

# ── WebUI 静态产物（自包含, 不再依赖宿主 bind-mount）──
COPY --from=webui-builder /build/webui/dist /app/webui/dist

# ── 版本信息 ──
ARG VERSION=dev
ARG BUILD_TIME=unknown
ENV APP_VERSION=${VERSION}
ENV APP_BUILD_TIME=${BUILD_TIME}
ENV APP_WORKSPACE_PATH=/app

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:5000/api/v1/health || exit 1

CMD ["python", "-m", "src.main", "-m", "http", "-p", "5000"]
