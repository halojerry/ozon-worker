from sqlalchemy import BigInteger, DateTime, Identity, Index, Integer, JSON, PrimaryKeyConstraint, Text, text, String, Float, UniqueConstraint
from typing import Optional
import datetime
import uuid

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB


class Base(DeclarativeBase):
    pass


# ==================== 任务队列表 ====================

class OzonProductTask(Base):
    """任务队列表 — worker 核心调度"""
    __tablename__ = "ozon_product_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending",
        comment="pending/running/completed/failed/cancelled"
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)

    __table_args__ = (
        Index(
            "idx_ozon_product_tasks_status_priority",
            "status", "priority", "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("idx_ozon_product_tasks_tenant_id", "tenant_id"),
    )


# ==================== 缓存表（从 SQLite 迁入 PG） ====================

class AttributeCache(Base):
    """Ozon 类目属性缓存"""
    __tablename__ = "attribute_cache"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    description_category_id: Mapped[int] = mapped_column(Integer, nullable=False)
    type_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    language: Mapped[str] = mapped_column(String(20), default="ZH_HANS")
    attributes_schema: Mapped[dict] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=True, comment="Unix 时间戳（秒）")
    created_at: Mapped[int] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("description_category_id", "type_id", "language", name="uq_attribute_cache"),
        Index("idx_attr_cache_composite", "description_category_id", "type_id", "language"),
    )


class DictionaryValueCache(Base):
    """Ozon 字典值缓存"""
    __tablename__ = "dictionary_value_cache"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    attribute_id: Mapped[int] = mapped_column(Integer, nullable=False)
    description_category_id: Mapped[int] = mapped_column(Integer, nullable=False)
    type_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    language: Mapped[str] = mapped_column(String(20), default="ZH_HANS")
    values_data: Mapped[dict] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("attribute_id", "description_category_id", "type_id", "language", name="uq_dict_value_cache"),
        Index("idx_dict_val_cache_composite", "attribute_id", "description_category_id", "type_id", "language"),
    )


class CategoryCache(Base):
    """Ozon 类目树缓存"""
    __tablename__ = "category_cache"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    ozon_client_id: Mapped[str] = mapped_column(String(50), nullable=False)
    language: Mapped[str] = mapped_column(String(20), default="ZH_HANS")
    tree_data: Mapped[dict] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("ozon_client_id", "language", name="uq_category_cache"),
        Index("idx_category_cache_client", "ozon_client_id", "language"),
    )


class LogisticsRate(Base):
    """物流费率"""
    __tablename__ = "logistics_rates"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    scoring_group: Mapped[str] = mapped_column(String(50), nullable=False)
    service_level: Mapped[str] = mapped_column(String(50), nullable=False)
    tpl_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    delivery_method: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    base_cost: Mapped[float] = mapped_column(Float, nullable=False)
    per_gram_rate: Mapped[float] = mapped_column(Float, nullable=False)
    weight_min: Mapped[int] = mapped_column(Integer, nullable=False)
    weight_max: Mapped[int] = mapped_column(Integer, nullable=False)
    sum_limit_cm: Mapped[int] = mapped_column(Integer, nullable=False)
    longest_limit_cm: Mapped[int] = mapped_column(Integer, nullable=False)
    charge_type: Mapped[str] = mapped_column(String(50), nullable=False)
    vol_weight_divisor: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        Index("idx_logistics_rates_weight", "weight_min", "weight_max"),
    )


class ExchangeRate(Base):
    """汇率缓存"""
    __tablename__ = "exchange_rates"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    from_currency: Mapped[str] = mapped_column(String(10), nullable=False)
    to_currency: Mapped[str] = mapped_column(String(10), nullable=False)
    rate: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("from_currency", "to_currency", name="uq_exchange_rates"),
        Index("idx_exchange_rates_currency", "from_currency", "to_currency"),
    )


class OzonAttributeMapping(Base):
    """属性映射学习记录"""
    __tablename__ = "ozon_attribute_mappings"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    category_id: Mapped[int] = mapped_column(Integer, nullable=False)
    attribute_id: Mapped[int] = mapped_column(Integer, nullable=False)
    attribute_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    source_value: Mapped[str] = mapped_column(String(500), nullable=True)
    target_value: Mapped[str] = mapped_column(String(500), nullable=True)
    dictionary_value_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("category_id", "attribute_id", "source_value", name="uq_ozon_attr_mappings"),
        Index("idx_ozon_attr_mappings_category", "category_id", "attribute_id"),
    )


class GatewayTask(Base):
    """任务状态追踪"""
    __tablename__ = "gateway_tasks"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    result_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    stages: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[int] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("idx_gateway_tasks_task_id", "task_id"),
    )
