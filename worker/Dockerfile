FROM python:3.12-slim AS base

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# ── 依赖层（利用 Docker 缓存）──
COPY pyproject.toml ./
RUN pip install --no-cache-dir . pytest pytest-asyncio

# ── 应用代码层 ──
COPY src/ ./src/
COPY config/ ./config/
COPY assets/ ./assets/
COPY scripts/ ./scripts/

# ── 版本信息 ──
ARG VERSION=dev
ARG BUILD_TIME=unknown
ENV APP_VERSION=${VERSION}
ENV APP_BUILD_TIME=${BUILD_TIME}
ENV APP_WORKSPACE_PATH=/app
ENV PYTHONPATH=/app/src

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:5000/api/v1/health || exit 1

CMD ["python", "-m", "src.main", "-m", "http", "-p", "5000"]
