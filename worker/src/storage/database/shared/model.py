from sqlalchemy import BigInteger, Boolean, Date, DateTime, Identity, Index, Integer, JSON, PrimaryKeyConstraint, Text, text, String, Float, UniqueConstraint, ARRAY, func
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
    # v0.37 P0-1: SKU 级重复提交防护 — 空值存 NULL 不参与部分唯一索引
    sku_key: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True,
        comment="SKU 级去重键 {user_id}:{product_id}"
    )

    __table_args__ = (
        Index(
            "idx_ozon_product_tasks_status_priority",
            "status", "priority", "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("idx_ozon_product_tasks_tenant_id", "tenant_id"),
        # ⚠️ v0.38.1: 唯一索引加状态过滤——只对活跃任务(pending/running)唯一。
        # 修复 N1×N2 冲突：旧谓词 WHERE sku_key IS NOT NULL 无状态过滤，旧 rejected/failed
        # 行保留 sku_key，resubmit 以相同 sku_key INSERT 新行 → IntegrityError → 500。
        # 现在 rejected/failed/completed 终态行不参与唯一约束，可正常重提交。
        Index(
            "uq_ozon_product_tasks_tenant_sku",
            "tenant_id", "sku_key",
            unique=True,
            postgresql_where=text(
                "sku_key IS NOT NULL AND status IN ('pending', 'running')"
            ),
        ),
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
    # PR-6: provenance — 区分 learned_approved / default_fallback / retry_recovered / fetch_back_corrected
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="learned_approved")
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
    source_category_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # 1688 叶子类目数字 ID（Skill 侧提取）
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


class AttrMatchLog(Base):
    """v0.40: 属性匹配审计日志 — 每次属性解析记录，用于填满率评估 + A/B + 误配复盘。

    对照 category_match_log 先例：task_id 定位 + status/match_layer 分桶 +
    candidates_json 快照 + source_value 截断（500）。
    """
    __tablename__ = "attr_match_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    attr_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    attr_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    candidate_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    match_layer: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dictionary_value_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    should_fill: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    candidates_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_attr_match_log_task", "task_id"),
        Index("idx_attr_match_log_layer", "match_layer", "created_at"),
        Index("idx_attr_match_log_attr", "attr_id", "created_at"),
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


class TaskGeneratedImage(Base):
    """v0.26: 任务生图缓存 — 重跑/重启不重烧生图额度。

    每次图节点生成成功后写入 (task_id, slot) → url；
    同一任务被队列重试/重启重新执行时，图节点先查缓存，命中直接复用，
    不再重新调用生图 API（P0 修复：重跑必重烧 9+N 张图，Sentry 超时×100/failed×120 实证）。
    slot 取值: main/white_bg/multi_angle/detail/social_proof/comparison/scene_1/scene_2/scene_3/variant_{idx}
    """
    __tablename__ = "task_generated_images"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="PG 任务 ID（config.configurable.thread_id）")
    slot: Mapped[str] = mapped_column(String(32), primary_key=True, comment="图片槽位（见模块注释）")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_task_images_task", "task_id"),
    )


# ==================== 选品分析数据表（v0.34: skill what-to-sell 上报） ====================
# 数据来源于用户、服务于用户：skill 采集的蓝海/榜单数据集体沉淀到 worker PG。
# 去重键 = 数据自然键 + contributed_by_token_id（用户隔离，同一用户重复采集走 upsert 更新）。
# source 列区分采集来源（fetched=skill 采集），预留后续扩展。

class BlueOceanQuery(Base):
    """skill what-to-sell all-queries 关键词蓝海数据（v0.34 C5）。

    去重键 (query, contributed_by_token_id)：同一用户重复上报同一关键词 → upsert 覆盖。
    """
    __tablename__ = "blue_ocean_queries"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    query: Mapped[str] = mapped_column(Text, nullable=False, comment="蓝海关键词")
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="商品数")
    ca: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="CA 系数")
    avg_ca_rub: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="平均 CA（卢布）")
    avg_count_items: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="平均商品数")
    items_views: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="商品浏览量")
    uniq_queries_wca: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="含 CA 的独立查询数")
    uniq_sellers: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="独立卖家数")
    contributed_by_token_id: Mapped[str] = mapped_column(Text, nullable=False, comment="上报用户 token（去 sk- 前缀后的 key）")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="fetched", comment="采集来源")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("query", "contributed_by_token_id", name="uq_blue_ocean_query_token"),
        Index("idx_blue_ocean_query_token", "contributed_by_token_id"),
    )


class OzonBestseller(Base):
    """skill ozon-bestsellers 榜单数据（v0.34 C5）。

    去重键 (sku_or_id, contributed_by_token_id)。
    """
    __tablename__ = "ozon_bestsellers"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    sku_or_id: Mapped[str] = mapped_column(Text, nullable=False, comment="Ozon SKU 或商品 ID")
    brand: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="品牌")
    category_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="Ozon 类目 ID")
    category_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="类目路径")
    ordering_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="订购金额")
    ordering_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="订购数量")
    avg_price_rub: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="平均售价（卢布）")
    contributed_by_token_id: Mapped[str] = mapped_column(Text, nullable=False, comment="上报用户 token（去 sk- 前缀后的 key）")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="fetched", comment="采集来源")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("sku_or_id", "contributed_by_token_id", name="uq_ozon_bestseller_token"),
        Index("idx_ozon_bestseller_token", "contributed_by_token_id"),
    )


class MarketBestseller(Base):
    """skill market-bestsellers 全平台榜单数据（v0.34 C5）。

    去重键 (product_name, contributed_by_token_id)。
    """
    __tablename__ = "market_bestsellers"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    product_name: Mapped[str] = mapped_column(Text, nullable=False, comment="商品名称")
    brand: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="品牌")
    category_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="类目 ID")
    category_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="类目路径")
    ordering_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="订购金额")
    daily_avg: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="日均销量")
    other_platform_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="其他平台价格")
    contributed_by_token_id: Mapped[str] = mapped_column(Text, nullable=False, comment="上报用户 token（去 sk- 前缀后的 key）")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="fetched", comment="采集来源")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("product_name", "contributed_by_token_id", name="uq_market_bestseller_token"),
        Index("idx_market_bestseller_token", "contributed_by_token_id"),
    )


# ==================== 店铺使用埋点表（v0.34 C6: shop_usage_stats） ====================
# worker task_processor 在任务终态（failed/completed/重试耗尽）增量写入，按 (ozon_client_id, stat_date) 按天聚合。
# ⚠️ task_count 语义 = 任务执行次数（每次终态 +1，重试/僵尸恢复重新计数是预期行为）。
# ⚠️ common_errors 降级实现：JSONB 数组保留当日最近 5 条失败 error_message（非按日 top-5 聚合），
#    只在失败终态路径累积，成功路径不增（见 task_processor._upsert_shop_usage 注释）。

class ShopUsageStats(Base):
    """店铺使用埋点 — 每次任务终态增量写入，按 (ozon_client_id, stat_date) 按天聚合。"""
    __tablename__ = "shop_usage_stats"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ozon_client_id: Mapped[str] = mapped_column(Text, nullable=False, comment="Ozon 店铺 Client-Id")
    stat_date: Mapped[datetime.date] = mapped_column(
        Date, nullable=False, server_default=func.current_date(), comment="统计日期（按天聚合）"
    )
    task_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"),
        comment="任务执行次数（每次终态 +1，重试重新计数）"
    )
    approved_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="审核通过次数"
    )
    validation_failed_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="审核失败次数"
    )
    common_errors: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, comment="当日最近 5 条失败 error_message（降级实现，成功路径不增）"
    )
    last_error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="当日最近一次失败 error_message"
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("ozon_client_id", "stat_date", name="uq_shop_usage_client_date"),
        Index("idx_shop_usage_client_date", "ozon_client_id", "stat_date"),
    )
