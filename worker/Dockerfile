FROM python:3.12-slim

WORKDIR /app

# System deps (pycairo/dbus build deps — unused at runtime, but needed for uv sync)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Install Python dependencies (layer cached)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

# Copy application code
COPY src/ ./src/
COPY config/ ./config/
COPY assets/ ./assets/
COPY scripts/ ./scripts/

# Environment
ENV COZE_WORKSPACE_PATH=/app
ENV PYTHONPATH=/app/src

EXPOSE 5000

CMD ["uv", "run", "python", "-m", "src.main", "-m", "http", "-p", "5000"]
