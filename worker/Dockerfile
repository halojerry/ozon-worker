FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from pyproject.toml
COPY pyproject.toml ./
RUN pip install --no-cache-dir . || pip install --no-cache-dir \
    langchain langchain-openai langgraph langgraph-checkpoint langgraph-prebuilt \
    langgraph-sdk langsmith \
    fastapi uvicorn pydantic Jinja2 \
    opencv-python pandas Pillow alembic SQLAlchemy python-dotenv \
    psycopg2-binary boto3 langgraph-checkpoint-postgres \
    pypdf docx2python openpyxl python-pptx rich chardet jieba requests \
    supabase \
    && pip install --no-cache-dir "psycopg[binary]>=3.3,<4" "psycopg-pool>=3.3,<4"

# Copy application code
COPY src/ ./src/
COPY config/ ./config/
COPY assets/ ./assets/
COPY scripts/ ./scripts/

# Environment
ENV APP_WORKSPACE_PATH=/app
ENV PYTHONPATH=/app/src

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:5000/health || exit 1

CMD ["python", "-m", "src.main", "-m", "http", "-p", "5000"]
