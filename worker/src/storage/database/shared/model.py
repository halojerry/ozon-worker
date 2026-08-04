from sqlalchemy import BigInteger, Boolean, DateTime, Identity, Index, Integer, JSON, PrimaryKeyConstraint, Text, text, String, Float, UniqueConstraint, ARRAY, func
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
    progress: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
        comment="实时进度数据 {stage, percent, stages_completed[], stages_remaining[], message}"
    )

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


class SizeMapping(Base):
    """尺码映射表（部署时从 assets/*.csv 导入，运行时查询）"""
    __tablename__ = "size_mappings"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    table_type: Mapped[str] = mapped_column(String(20), nullable=False)   # children/male/female/shoes
    input_value: Mapped[str] = mapped_column(String(50), nullable=False)  # 标准化输入（M/48/38…）
    ru_size: Mapped[str] = mapped_column(String(50), nullable=False)      # 俄罗斯尺码
    source_col: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        Index("uq_size_mappings", "table_type", "input_value", unique=True),
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


# ==================== 类目树扁平表 ====================

class CategoryTreeNode(Base):
    """Ozon 类目树扁平节点（支持 pg_trgm 模糊搜索）"""
    __tablename__ = "category_tree_nodes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    description_category_id: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="Ozon 类目ID")
    type_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="Ozon 商品类型ID（叶子节点非空）")
    node_name: Mapped[str] = mapped_column(String(500), nullable=False, comment="类目名称或类型名称")
    node_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="category 或 type")
    parent_description_category_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="父级类目ID")
    full_path: Mapped[str] = mapped_column(Text, nullable=False, comment="完整路径，如 食品 > 面食 > 大米")
    top_level_category_name: Mapped[str] = mapped_column(String(500), nullable=False, comment="顶层类目名")
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    disabled: Mapped[bool] = mapped_column(default=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False, default="ZH_HANS")
    created_at: Mapped[int] = mapped_column(Integer, nullable=True, comment="Unix 时间戳（秒）")

    __table_args__ = (
        UniqueConstraint(
            "description_category_id", "type_id", "language",
            name="uq_category_tree_nodes"
        ),
        Index("idx_ctn_full_path", "full_path"),
        Index("idx_ctn_top_level", "top_level_category_name", "language"),
        Index("idx_ctn_parent", "parent_description_category_id"),
        Index("idx_ctn_node_type", "node_type"),
        Index(
            "idx_ctn_type_id",
            "type_id",
            postgresql_where=text("type_id IS NOT NULL"),
        ),
        # pg_trgm 模糊搜索索引（需 CREATE EXTENSION pg_trgm）
        Index(
            "idx_ctn_name_trgm",
            text("node_name gist_trgm_ops"),
            postgresql_using="gist",
        ),
        Index(
            "idx_ctn_path_trgm",
            text("full_path gist_trgm_ops"),
            postgresql_using="gist",
        ),
    )


class CategoryMapping(Base):
    """v4: 1688→Ozon 类目映射学习表"""
    __tablename__ = "category_mapping"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_category_leaf: Mapped[str] = mapped_column(String(300), nullable=False)
    source_category_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_keywords: Mapped[Optional[list]] = mapped_column(ARRAY(String), nullable=True)
    description_category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category_path_zh: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category_path_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    success_count: Mapped[int] = mapped_column(Integer, default=1)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(50), default="pg_trgm")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("source_category_leaf", "description_category_id", "type_id"),
        Index("idx_cat_map_leaf", "source_category_leaf"),
        Index("idx_cat_map_confidence", "confidence", "success_count"),
    )


class AttributeSynonym(Base):
    """v4: 1688→Ozon 属性名同义词表"""
    __tablename__ = "attribute_synonym"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    source_attr_name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_ozon_attr_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    target_ozon_attr_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("source_attr_name", "target_ozon_attr_id"),
        Index("idx_attr_syn_name", "source_attr_name"),
    )


class CategoryMatchLog(Base):
    """v4: 类目匹配审计日志 — 每次匹配尝试记录，用于评估准确率和A/B测试"""
    __tablename__ = "category_match_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_keywords: Mapped[Optional[list]] = mapped_column(ARRAY(String), nullable=True)
    matched_description_category_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    matched_type_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    matched_path_zh: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    matched_path_ru: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    match_layer: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    candidates_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    llm_raw_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    upload_success: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    upload_error_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_match_log_task", "task_id"),
        Index("idx_match_log_layer", "match_layer", "created_at"),
    )


class DomainHint(Base):
    """v4: 领域消歧规则 — 特定关键词强制导向某Ozon顶级类目"""
    __tablename__ = "domain_hint"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    trigger_keywords: Mapped[list] = mapped_column(ARRAY(String), nullable=False)
    target_top_category: Mapped[str] = mapped_column(Text, nullable=False)
    exclude_top_category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_domain_hint_active", "priority"),
    )
