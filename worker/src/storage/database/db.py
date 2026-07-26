import os
import time
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
import logging
logger = logging.getLogger(__name__)

_tables_initialized = False

MAX_RETRY_TIME = 20  # 连接最大重试时间（秒）
# Load environment variables from .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

def get_db_url() -> str:
    """Build database URL from environment."""
    url = os.getenv("PGDATABASE_URL") or ""
    if url:
        return url
    raise ValueError("PGDATABASE_URL is not set. Set it via environment variable.")
_engine = None
_SessionLocal = None

def _create_engine_with_retry():
    url = get_db_url()
    if url is None or url == "":
        logger.error("PGDATABASE_URL is not set")
        raise ValueError("PGDATABASE_URL is not set")
    size = 5
    overflow = 10
    recycle = 1800
    timeout = 30
    engine = create_engine(
        url,
        pool_size=size,
        max_overflow=overflow,
        pool_pre_ping=True,
        pool_recycle=recycle,
        pool_timeout=timeout,
    )
    # 验证连接，带重试
    start_time = time.time()
    last_error = None
    while time.time() - start_time < MAX_RETRY_TIME:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return engine
        except OperationalError as e:
            last_error = e
            elapsed = time.time() - start_time
            logger.warning(f"Database connection failed, retrying... (elapsed: {elapsed:.1f}s)")
            time.sleep(min(1, MAX_RETRY_TIME - elapsed))
    logger.error(f"Database connection failed after {MAX_RETRY_TIME}s: {last_error}")
    raise last_error  # pyright: ignore [reportGeneralTypeIssues]

def get_engine():
    global _engine
    if _engine is None:
        _engine = _create_engine_with_retry()
    return _engine

def get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal

def get_session():
    return get_sessionmaker()()

def init_db():
    """创建所有 ORM 表（幂等：CREATE TABLE IF NOT EXISTS）。

    在 lifespan 启动时调用，确保 ozon_product_tasks 等表在 PostgreSQL 中存在。
    同时启用 pg_trgm 扩展用于中文模糊搜索。
    """
    global _tables_initialized
    if _tables_initialized:
        return
    from storage.database.shared.model import Base
    engine = get_engine()
    # 启用 pg_trgm 扩展（幂等）+ 降低相似度阈值（中文多关键词匹配需要）
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.execute(text("SET pg_trgm.similarity_threshold = 0.05"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
    _tables_initialized = True
    logger.info("数据库表初始化完成（create_all + pg_trgm）")


__all__ = [
    "get_db_url",
    "get_engine",
    "get_session",
    "get_sessionmaker",
    "init_db",
]
