"""Pydantic 请求/响应 schemas — API 契约的单一事实来源。

FastAPI 自动从这些 model 生成 OpenAPI 文档（/docs 和 /openapi.json）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# 通用
# ──────────────────────────────────────────────


class ApiVersion(str, Enum):
    V1 = "v1"


class ErrorBody(BaseModel):
    """统一错误响应体。"""
    ok: bool = False
    error_code: str = Field(..., description="错误码，如 TOKEN_INVALID、RATE_LIMITED")
    message: str = Field(..., description="人类可读的错误描述")
    detail: Optional[Any] = Field(None, description="附加详情（调试用）")


# ──────────────────────────────────────────────
# /api/v1/submit_task
# ──────────────────────────────────────────────


class SubmitTaskRequest(BaseModel):
    """提交任务请求。

    字段直接放在 body 顶层。Worker 同时兼容 body.payload 包装格式（向后兼容），
    但 schema 只描述标准格式。
    """
    token: str = Field(..., description="MXOU API Key（带或不带 sk- 前缀）")
    ozon_client_id: str = Field(..., description="Ozon 卖家 Client-Id")
    ozon_api_key: str = Field(..., description="Ozon 卖家 Api-Key")
    envelope: dict[str, Any] = Field(..., description="产品数据信封 {draft, source, extensions}")
    timeout_seconds: int = Field(1800, description="任务超时时间（秒），默认 30 分钟")
    max_retries: int = Field(3, description="最大重试次数，默认 3")


class SubmitTaskResponse(BaseModel):
    """提交任务成功响应。"""
    ok: bool = True
    task_id: str = Field(..., description="任务 UUID，用于轮询状态")
    message: str = Field(..., description="提交成功消息")


# ──────────────────────────────────────────────
# /api/v1/task_status/{task_id}
# ──────────────────────────────────────────────


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    PENDING_MODERATION = "pending_moderation"  # T14: 在线商品改图重传后重新审核中


class TaskStatusResponse(BaseModel):
    """任务状态响应。"""
    id: str = Field(..., description="任务 UUID")
    status: TaskStatus = Field(..., description="任务状态")
    tenant_id: str = Field(..., description="用户 ID")
    priority: int = Field(0, description="任务优先级")
    result: Optional[dict[str, Any]] = Field(None, description="任务执行结果（completed 时有值）")
    error_message: Optional[str] = Field(None, description="错误信息（failed 时有值）")
    retry_count: int = Field(0, description="已重试次数")
    max_retries: int = Field(3, description="最大重试次数")
    created_at: Optional[datetime] = Field(None, description="创建时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")
    started_at: Optional[datetime] = Field(None, description="开始执行时间")
    completed_at: Optional[datetime] = Field(None, description="完成时间")
    timeout_seconds: int = Field(1800, description="超时时间（秒）")
    progress: Optional[dict[str, Any]] = Field(None, description="实时进度 {stage, percent, stages_completed[], stages_remaining[], message}")


# ──────────────────────────────────────────────
# /api/v1/cancel_task/{task_id}
# ──────────────────────────────────────────────


class CancelTaskResponse(BaseModel):
    """取消任务响应。"""
    ok: bool = True
    task_id: str = Field(..., description="任务 UUID")
    message: str = Field(..., description="取消结果消息")


# ──────────────────────────────────────────────
# /api/v1/health
# ──────────────────────────────────────────────


class HealthResponse(BaseModel):
    """健康检查响应。"""
    status: str = Field(..., description="服务状态: ok / degraded")
    message: str = Field(..., description="状态描述")
    db: str = Field(..., description="数据库连接状态: connected / disconnected")


# ──────────────────────────────────────────────
# /api/v1/task_statistics
# ──────────────────────────────────────────────


class TaskStatisticsResponse(BaseModel):
    """任务统计响应。"""
    total: int = Field(0, description="总任务数")
    pending: int = Field(0, description="待处理")
    running: int = Field(0, description="执行中")
    completed: int = Field(0, description="已完成")
    failed: int = Field(0, description="已失败")
    cancelled: int = Field(0, description="已取消")
    avg_duration_seconds: Optional[float] = Field(None, description="平均执行时长（秒）")


class AuthVerifyRequest(BaseModel):
    """Skill 鉴权请求。"""
    token: str = Field(..., description="MXOU_TOKEN")
    client_id: str = Field("", description="Ozon Client ID（可选）")
    api_key: str = Field("", description="Ozon API Key（可选）")


class AuthVerifyResponse(BaseModel):
    """Skill 鉴权响应。"""
    valid: bool = Field(..., description="是否有效")
    reason: str = Field("ok", description="原因: ok / token_invalid / balance_insufficient / account_inactive")
    expires_in: int = Field(86400, description="缓存有效期（秒）")
    ozon_valid: Optional[bool] = Field(None, description="Ozon API 是否有效（仅当传了 client_id/api_key 时返回）")


# ──────────────────────────────────────────────
# /api/v1/analytics/* — skill what-to-sell 采集数据上报
# ──────────────────────────────────────────────


class BlueOceanQueryItem(BaseModel):
    """all-queries 关键词蓝海数据单条。"""
    query: str = Field(..., description="蓝海关键词")
    count: int = Field(0, description="商品数")
    ca: Optional[float] = Field(None, description="CA 系数")
    avg_ca_rub: Optional[float] = Field(None, description="平均 CA（卢布）")
    avg_count_items: Optional[float] = Field(None, description="平均商品数")
    items_views: Optional[float] = Field(None, description="商品浏览量")
    uniq_queries_wca: Optional[int] = Field(None, description="含 CA 的独立查询数")
    uniq_sellers: Optional[float] = Field(None, description="独立卖家数")


class AnalyticsQueriesRequest(BaseModel):
    """蓝海关键词上报请求。"""
    token: str = Field(..., description="MXOU API Key（带或不带 sk- 前缀）")
    queries: list[BlueOceanQueryItem] = Field(default_factory=list, description="蓝海关键词列表")


class OzonBestsellerItem(BaseModel):
    """ozon-bestsellers 榜单数据单条。"""
    sku_or_id: str = Field(..., description="Ozon SKU 或商品 ID")
    brand: Optional[str] = Field(None, description="品牌")
    category_id: Optional[int] = Field(None, description="Ozon 类目 ID")
    category_path: Optional[str] = Field(None, description="类目路径")
    ordering_amount: Optional[float] = Field(None, description="订购金额")
    ordering_count: Optional[int] = Field(None, description="订购数量")
    avg_price_rub: Optional[float] = Field(None, description="平均售价（卢布）")


class AnalyticsOzonBestsellersRequest(BaseModel):
    """Ozon 榜单上报请求。"""
    token: str = Field(..., description="MXOU API Key（带或不带 sk- 前缀）")
    items: list[OzonBestsellerItem] = Field(default_factory=list, description="榜单条目列表")


class MarketBestsellerItem(BaseModel):
    """market-bestsellers 全平台榜单数据单条。"""
    product_name: str = Field(..., description="商品名称")
    brand: Optional[str] = Field(None, description="品牌")
    category_id: Optional[int] = Field(None, description="类目 ID")
    category_path: Optional[str] = Field(None, description="类目路径")
    ordering_amount: Optional[float] = Field(None, description="订购金额")
    daily_avg: Optional[float] = Field(None, description="日均销量")
    other_platform_price: Optional[float] = Field(None, description="其他平台价格")


class AnalyticsMarketBestsellersRequest(BaseModel):
    """全平台榜单上报请求。"""
    token: str = Field(..., description="MXOU API Key（带或不带 sk- 前缀）")
    items: list[MarketBestsellerItem] = Field(default_factory=list, description="榜单条目列表")


class AnalyticsReportResponse(BaseModel):
    """上报成功响应。"""
    status: str = Field("ok", description="状态: ok / error")
    inserted: int = Field(0, description="本次新增行数")
    upserted: int = Field(0, description="本次覆盖更新行数")


# ──────────────────────────────────────────────
# /api/v1/drafts — 采集箱草稿（WebUI T6，契约 C1/C4/C5）
# ──────────────────────────────────────────────


class DraftCreate(BaseModel):
    """POST /drafts：收 GraphInput 信封 + 凭证。

    Worker 剥离凭证（AES-256-GCM 加密存 credentials 表），payload 只存 envelope。
    """
    token: str = Field(..., description="MXOU API Key（带或不带 sk- 前缀）")
    ozon_client_id: str = Field("", description="Ozon 卖家 Client-Id（剥离存储）")
    ozon_api_key: str = Field("", description="Ozon 卖家 Api-Key（剥离加密存储）")
    envelope: dict[str, Any] = Field(..., description="产品数据信封 {draft, source, extensions}；NO raw credentials")
    source: str = Field("skill", description="'skill' | 'webui'")


class DraftOut(BaseModel):
    """草稿详情（payload = envelope，不含任何凭证）。"""
    id: UUID = Field(..., description="草稿 ID")
    tenant_id: str = Field(..., description="归属用户（_authenticate_token 的 user_id）")
    payload: dict[str, Any] = Field(..., description="envelope {draft, source, extensions}；无 api_key 明文")
    source: str = Field("skill", description="'skill' | 'webui'")
    version: int = Field(1, description="乐观并发版本（PATCH 带旧 version，不匹配 → 409）")
    created_at: Optional[datetime] = Field(None, description="创建时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")
    submission_status: Optional[str] = Field(
        None,
        description="最新一次提交状态（draft_submissions.status）：pending/uploading/published/failed；NULL = 未上架（C1 状态机，T10 采集箱列）",
    )


class DraftPatch(BaseModel):
    """PATCH /drafts/{id}：乐观锁更新。"""
    version: int = Field(..., description="必须等于当前 version，否则 409（stale）")
    payload: dict[str, Any] = Field(..., description="新的 envelope；成功后 version++")
    source: Optional[str] = Field(None, description="可选更新 source 字段")


class DraftSubmitRequest(BaseModel):
    """POST /drafts/{id}/submit：凭证注入 → 入队。"""
    token: str = Field(..., description="MXOU API Key（重建 GraphInput 用）")
    credential_id: Optional[UUID] = Field(
        None, description="目标店铺凭证 ID；NULL → 用 is_default=true 店铺"
    )
    update_product_id: Optional[str] = Field(
        None, description="更新模式：已存在商品的 Ozon product_id；设置后跳过 per-store 重复校验，注入 extensions.update_product_id"
    )


class SubmitResponse(BaseModel):
    """提交成功响应（含 C5 跨店确认标记）。"""
    ok: bool = True
    draft_id: UUID = Field(..., description="草稿 ID（多次提交永不变）")
    submission_id: Optional[UUID] = Field(None, description="本次提交记录 ID（draft_submissions.id）")
    task_id: str = Field("", description="ozon_product_tasks.id")
    status: str = Field("pending", description="提交记录状态：pending/uploading/published/failed")
    confirm_required: bool = Field(False, description="跨店提醒：该草稿已提交到其他店铺（不硬拦）")
    existing_stores: list[str] = Field(default_factory=list, description="已有提交的店铺 client_id 列表")


# ──────────────────────────────────────────────
# /api/v1/credentials (T5) — 凭证三层防御（掩码 + AES-GCM 加密 + 轮换）
# ──────────────────────────────────────────────


class CredentialCreate(BaseModel):
    """创建凭证请求。

    明文 api_key 仅存在于请求体；响应只回 api_key_masked，永不回显明文。
    """
    ozon_client_id: str = Field(..., description="Ozon 卖家 Client-Id（半公开）")
    api_key: str = Field(..., description="Ozon 卖家 Api-Key（仅请求，永不回显）")
    shop_name: Optional[str] = Field(None, description="店铺名称（绑定弹窗）")
    currency: str = Field("CNY", description="CNY/RUB")
    is_default: bool = Field(False, description="「默认上传产品的店铺」radio；置 true 时同租户旧默认自动清")
    credential_type: str = Field("api_key", description="api_key | oauth（预留）")


class CredentialUpdate(BaseModel):
    """轮换凭证请求（旧行 revoked + 新行 active，默认标记继承）。"""
    api_key: str = Field(..., description="新 Api-Key")
    shop_name: Optional[str] = Field(None, description="可选更新店铺名称")
    currency: Optional[str] = Field(None, description="可选更新货币")


class CredentialOut(BaseModel):
    """凭证响应 — 仅掩码，永不包含明文 api_key / ozon_api_key_enc。"""
    id: str = Field(..., description="凭证 UUID")
    ozon_client_id: str = Field(..., description="Ozon 卖家 Client-Id")
    api_key_masked: str = Field(..., description="掩码 ****abcd（仅后 4 位）")
    shop_name: Optional[str] = Field(None, description="店铺名称")
    currency: str = Field("CNY", description="CNY/RUB")
    is_default: bool = Field(False, description="默认店铺标记")
    credential_type: str = Field("api_key", description="api_key | oauth（预留）")
    status: str = Field("active", description="active/revoked")
    last_validated_at: Optional[datetime] = Field(None, description="最近一次校验时间")
    last_rotated_at: Optional[datetime] = Field(None, description="最近一次轮换时间")
    created_at: Optional[datetime] = Field(None, description="创建时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")


class ValidateResponse(BaseModel):
    """凭证校验响应。"""
    valid: bool = Field(..., description="key 是否有效")
    reason: str = Field(..., description="ok / invalid_key / ozon_api_error / decrypt_failed")
    last_validated_at: Optional[datetime] = Field(None, description="本次校验时间")


# ──────────────────────────────────────────────
# /api/v1/templates (P0-1) — 上架配置模板
# ──────────────────────────────────────────────


class ListingTemplateConfig(BaseModel):
    """模板扩展参数（白名单；全部可选，None 表示不注入）。"""
    margin_rate: Optional[float] = Field(None, ge=0.0, le=1.0, description="利润率（0-1），不设则 worker 默认 0.25")
    commission_rate: Optional[float] = Field(None, ge=0.0, le=0.5, description="佣金率；0=让 worker 自动查店铺真实佣金")
    fx_buffer: Optional[float] = Field(None, ge=0.0, le=0.5, description="汇率缓冲（0-0.5），不设则 worker 默认 0.05")
    offer_id_prefix: Optional[str] = Field(None, description="货号前缀（仅新建上架生效；更新模式忽略）")
    follow_type: Optional[str] = Field(None, pattern="^(hand|api)$", description="跟卖方式：hand 防侵权 / api 强制")
    stock: Optional[int] = Field(None, ge=0, description="上架后库存（extensions.stock）")
    warehouse_id: Optional[str] = Field(None, description="仓库（extensions.warehouse_id）")


class ListingTemplateCreate(BaseModel):
    """创建上架配置模板请求。"""
    name: str = Field(..., min_length=1, max_length=50, description="配置名称")
    description: str = Field("", description="备注")
    platform: str = Field("OZON", description="平台（当前仅 OZON）")
    is_default: bool = Field(False, description="默认模板（置 true 时同租户旧默认自动清）")
    config: ListingTemplateConfig = Field(default_factory=ListingTemplateConfig, description="扩展参数")


class ListingTemplateUpdate(BaseModel):
    """部分更新上架配置模板请求（仅提供需更新的字段）。"""
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = None
    platform: Optional[str] = None
    is_default: Optional[bool] = None
    config: Optional[ListingTemplateConfig] = None


class ListingTemplateOut(BaseModel):
    """上架配置模板响应。"""
    id: str = Field(..., description="模板 UUID")
    tenant_id: str = Field(..., description="所属租户")
    name: str = Field(..., description="配置名称")
    description: str = Field("", description="备注")
    platform: str = Field("OZON", description="平台")
    is_default: bool = Field(False, description="默认模板标记")
    config: ListingTemplateConfig = Field(default_factory=ListingTemplateConfig, description="扩展参数")
    store_overrides: Optional[Dict[str, ListingTemplateConfig]] = Field(None, description="按店铺（credential_id）差异化覆盖配置")
    created_at: Optional[datetime] = Field(None, description="创建时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")


# ──────────────────────────────────────────────
# /api/v1/orders (P0-4) — Ozon FBS 订单（实时拉取，不建表）
# ──────────────────────────────────────────────


class OrderProductOut(BaseModel):
    """订单内商品行。"""
    name: str = Field("", description="商品名称")
    sku: Optional[int] = Field(None, description="Ozon SKU")
    quantity: int = Field(0, description="数量")
    price: Optional[float] = Field(None, description="单价")
    offer_id: str = Field("", description="货号")


class OrderOut(BaseModel):
    """订单行（Ozon FBS posting 标准化）。"""
    posting_number: str = Field(..., description="货件编号")
    status: str = Field(..., description="统一态：pending/awaiting/waiting/delivering/delivered/cancelled/other")
    raw_status: str = Field("", description="Ozon 原始状态")
    created_at: Optional[str] = Field(None, description="下单时间（ISO）")
    products: list[OrderProductOut] = Field(default_factory=list, description="商品行")
    product_count: int = Field(0, description="商品总件数")
    total_amount: float = Field(0.0, description="订单金额")
    commission_amount: float = Field(0.0, description="平台费用")
    profit: Optional[float] = Field(None, description="估算利润（金额-费用）")
    warehouse: str = Field("", description="仓库")
    delivery_method: str = Field("", description="配送方式")
    cancel_reason: str = Field("", description="取消原因")
    cancellation: str = Field("", description="取消方/类型")


class OrderListResponse(BaseModel):
    """订单列表响应。"""
    items: list[OrderOut] = Field(default_factory=list)
    total: int = Field(0, description="订单总数")
    limit: int = Field(50, description="本次页大小")
    offset: int = Field(0, description="偏移")
    store: dict = Field(default_factory=dict, description="查询店铺 {id, ozon_client_id}")
    last_synced_at: Optional[str] = Field(None, description="最近同步时间（v0.56 缓存）")
    sync_error: Optional[str] = Field(None, description="最近同步错误（v0.56）")


class OrderNoteOut(BaseModel):
    """订单货源/采购信息标注（P1-1 本地元数据）。"""
    posting_number: str = Field(..., description="Ozon FBS 货件编号")
    tenant_id: str = Field(..., description="所属租户")
    source_url: str = Field("", description="货源地址")
    source_cost: Optional[float] = Field(None, description="货源价格（CNY）")
    source_remark: str = Field("", description="货源备注")
    purchase_no: str = Field("", description="采购单号")
    purchase_carrier: str = Field("", description="采购快递")
    purchase_tracking: str = Field("", description="采购快递单号")
    created_at: Optional[str] = Field(None, description="创建时间")
    updated_at: Optional[str] = Field(None, description="更新时间")


class OrderNoteUpsert(BaseModel):
    """订单标注写入（部分字段可选，缺失字段清空）。"""
    source_url: Optional[str] = None
    source_cost: Optional[float] = Field(None, ge=0, description="货源价格（CNY）")
    source_remark: Optional[str] = None
    purchase_no: Optional[str] = None
    purchase_carrier: Optional[str] = None
    purchase_tracking: Optional[str] = None


class OrderLabelResponse(BaseModel):
    """面单 PDF 响应（base64，路由层编码）。"""
    posting_number: str = Field(..., description="货件编号")
    content_type: str = Field("application/pdf", description="MIME")
    label_base64: str = Field(..., description="PDF base64")


class CancelReasonOut(BaseModel):
    """订单取消原因（/v1/posting/fbs/cancel-reason）。"""
    id: int = Field(..., description="原因 ID")
    title: str = Field("", description="原因标题")


class CancelRequest(BaseModel):
    """取消订单请求。"""
    cancel_reason_id: int = Field(..., description="取消原因 ID（先 GET cancel-reasons）")
    credential_id: Optional[str] = Field(None, description="店铺凭证（默认店铺兜底）")


class OrderActionResponse(BaseModel):
    """订单写入操作响应（备货/取消）。"""
    ok: bool = Field(True, description="操作是否提交成功")
    posting_number: str = Field(..., description="货件编号")
    result: dict = Field(default_factory=dict, description="Ozon 返回 result")


class OzonProductOut(BaseModel):
    """Ozon 店铺在线商品（v0.50 实时拉取，覆盖非本系统上架商品）。"""
    product_id: str = Field(..., description="Ozon product_id")
    offer_id: str = Field("", description="货号")
    name: str = Field("", description="商品名称")
    image: Optional[str] = Field(None, description="主图 URL")
    price: Optional[float] = Field(None, description="售价")
    stock: Optional[int] = Field(None, description="可用库存")
    currency: str = Field("", description="货币代码")


class OzonProductListResponse(BaseModel):
    """Ozon 在线商品列表响应。"""
    items: list[OzonProductOut] = Field(default_factory=list)
    total: int = Field(0, description="商品总数")
    limit: int = Field(50, description="本次页大小")
    offset: int = Field(0, description="偏移")
    store: dict = Field(default_factory=dict, description="查询店铺 {id, ozon_client_id}")
    last_synced_at: Optional[str] = Field(None, description="最近同步时间（v0.56 缓存）")
    sync_error: Optional[str] = Field(None, description="最近同步错误（v0.56）")


# ──────────────────────────────────────────────
# /api/v1/admin (v0.51) — 管理员面板（平台运营视图）
# ──────────────────────────────────────────────


class AdminOverviewOut(BaseModel):
    """平台概览。"""
    user_count: int = Field(0, description="用户数")
    store_count: int = Field(0, description="活跃店铺数")
    task_total: int = Field(0, description="任务总数")
    task_today: int = Field(0, description="今日任务数")
    success_rate: float = Field(0.0, description="成功率（%）")
    statistics: dict = Field(default_factory=dict, description="任务统计明细")


class AdminUserOut(BaseModel):
    """用户行（平台视角）。"""
    id: str = Field(..., description="用户 ID")
    username: str = Field("", description="用户名/显示名")
    quota: Optional[float] = Field(None, description="余额")
    role: str = Field("user", description="user/admin")
    created_at: Optional[str] = Field(None, description="注册时间")
    store_count: int = Field(0, description="活跃店铺数")
    task_count: int = Field(0, description="任务总数")


class AdminStoreOut(BaseModel):
    """店铺行（跨用户平台视角）。"""
    id: str = Field(..., description="凭证 UUID")
    tenant_id: str = Field(..., description="归属用户 ID")
    ozon_client_id: str = Field(..., description="Ozon Client-Id")
    shop_name: str = Field("", description="店铺名")
    currency: str = Field("CNY", description="货币")
    is_default: bool = Field(False, description="默认店铺")
    status: str = Field("active", description="active/revoked")
    last_validated_at: Optional[str] = Field(None, description="最近校验")


class AdminUserDetailOut(BaseModel):
    """用户详情（店铺 + 任务统计）。"""
    id: str = Field(..., description="用户 ID")
    stores: list[AdminStoreOut] = Field(default_factory=list, description="店铺列表")
    task_total: int = Field(0, description="任务总数")
    task_completed: int = Field(0, description="已完成")
    task_failed: int = Field(0, description="失败")


# ──────────────────────────────────────────────
# T14b 草稿 AI 单字段重新生成
# ──────────────────────────────────────────────


class DraftAiRequest(BaseModel):
    """T14b: 单字段 AI 重新生成请求。

    请求体仅携带 token（与全站一致：token 在 body 而非 header）；
    草稿读取由 {draft_id} + token 鉴权得到的 tenant_id 共同限定。
    """
    token: str = Field(..., description="mxou API Key（用于 LLM 调用与鉴权）")


class DraftAiResponse(BaseModel):
    """T14b: 单字段 AI 重新生成响应（只读结果，前端决定 PATCH 保存）。"""
    field: str = Field(..., description="title/description/attributes/tags")
    value: str = Field(..., description="俄语 RU 值（非空，无中文/拉丁残留）")


class SubmissionTimelineItem(BaseModel):
    """M2.2: 草稿提交时间线条目（draft_submissions 行，created_at 倒序）。

    供 WebUI 展示「这个草稿被提交过几次、到过哪些店、结果如何」。
    """
    id: UUID = Field(..., description="submission 记录 ID（draft_submissions.id）")
    store_client_id: Optional[str] = Field(None, description="目标店铺 Ozon Client-Id")
    status: str = Field(..., description="提交状态：pending/uploading/published/failed/rejected（M0.3 写回）")
    error_message: Optional[str] = Field(None, description="失败/被拒原因")
    extensions: dict[str, Any] = Field(default_factory=dict, description="提交时 extensions 快照（定价/仓库/库存）")
    submitted_task_id: Optional[str] = Field(None, description="关联任务 ID（ozon_product_tasks.id）")
    created_at: Optional[datetime] = Field(None, description="提交时间")


# ──────────────────────────────────────────────
# /api/v1/tasks (T8) — 任务列表（租户隔离 + 分页）
# ──────────────────────────────────────────────


class TaskListItem(BaseModel):
    """任务列表项 — 只读摘要，不含 payload（体积大且含敏感 token）。"""
    id: str = Field(..., description="任务 UUID")
    status: TaskStatus = Field(..., description="任务状态")
    progress: Optional[dict[str, Any]] = Field(None, description="实时进度 {stage, percent, stages_completed[], stages_remaining[], message}")
    product_summary: list[dict[str, Any]] = Field(default_factory=list, description="产品摘要（result.product_summary，completed 时有值）")
    created_at: Optional[datetime] = Field(None, description="创建时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")
    # ── T12 前端表格列（task_service 从 payload 安全提取，不含 token/密钥） ──
    title: Optional[str] = Field(None, description="产品标题（payload envelope.draft.title）")
    image: Optional[str] = Field(None, description="产品主图 URL（draft.images[0]）")
    item_id: Optional[str] = Field(None, description="货号（draft.item_id，筛选用）")
    ozon_client_id: Optional[str] = Field(None, description="账号（payload.ozon_client_id）")
    shop_name: Optional[str] = Field(None, description="店铺名（payload.shop_name，可为空）")
    follow_sell: bool = Field(False, description="跟卖标记（envelope.extensions.follow_sell）")
    update_mode: bool = Field(False, description="编辑更新标记（extensions.update_product_id，在线商品改后重传）")
    parent_task_id: Optional[str] = Field(None, description="重上来源任务 ID（resubmit 注入，有值=重上任务）")


class TaskListResponse(BaseModel):
    """任务列表响应（T8）。"""
    items: list[TaskListItem] = Field(default_factory=list, description="任务列表（created_at DESC）")
    total: int = Field(0, description="该租户任务总数（分页前）")
    limit: int = Field(20, description="本次分页大小（1-100）")
    offset: int = Field(0, description="本次偏移")


class TaskDraftResponse(BaseModel):
    """GET /tasks/{task_id}/draft 响应（M1.1 失败/被拒任务 → 找回采集箱草稿）。

    解析顺序：draft_submissions.submitted_task_id → product_task_index.task_id → None
    （直连任务无 submission 行时回落到 product_task_index；都无 → draft_id=None）。
    """
    draft_id: Optional[str] = Field(None, description="采集箱草稿 UUID；无关联草稿（直连任务）→ None")


# ──────────────────────────────────────────────
# /api/v1/tasks/{id}/images — 生图缓存版本化（WebUI T7a，契约 C3）
# ──────────────────────────────────────────────


class TaskImageItem(BaseModel):
    """单张生图缓存行（URL 元数据，不存二进制）。"""
    slot: str = Field(..., description="槽位: main/white_bg/multi_angle/detail/social_proof/comparison/scene_1..3/variant_{idx}")
    version: int = Field(..., description="生成版本（1 起；regen 递增）")
    url: str = Field(..., description="图片 URL（COS/1688 alicdn/Ozon，前端自行处理失效）")
    params: Optional[dict[str, Any]] = Field(None, description="节点 Input schema 原样快照")
    image_parent_task_id: Optional[str] = Field(None, description="resubmit 图片血缘（原 task_id；区别于任务级 payload.parent_task_id）")
    created_at: Optional[datetime] = Field(None, description="生成时间")


class TaskImagesResponse(BaseModel):
    """GET /tasks/{id}/images 响应。"""
    ok: bool = True
    task_id: str = Field(..., description="任务 UUID")
    images: list[TaskImageItem] = Field(default_factory=list, description="全部槽位 × 版本")


class ImageRegenResponse(BaseModel):
    """POST /tasks/{id}/images/{slot}/regen 响应（新版本行）。"""
    ok: bool = True
    task_id: str = Field(..., description="任务 UUID")
    slot: str = Field(..., description="槽位")
    version: int = Field(..., description="新版本号（prev+1）")
    url: str = Field(..., description="新图片 URL")
    params: Optional[dict[str, Any]] = Field(None, description="节点 Input schema 快照")
    image_parent_task_id: Optional[str] = Field(None, description="resubmit 图片血缘")


# ──────────────────────────────────────────────
# /api/v1/products — 在售商品列表（WebUI M2.1，product_task_index 数据源）
# ──────────────────────────────────────────────


class ProductListItem(BaseModel):
    """在售商品列表项 — 只读，product_task_index 行 + 任务 result 审核状态。"""
    product_id: str = Field(..., description="Ozon product_id（上传成功后回填）")
    offer_id: str = Field(..., description="信封 offer_id（sku_id / follow_{id}）")
    task_id: str = Field(..., description="上架任务 UUID")
    draft_id: Optional[str] = Field(None, description="采集箱草稿 id；直连任务为 null")
    credential_id: Optional[str] = Field(None, description="店铺凭证 id")
    created_at: Optional[datetime] = Field(None, description="索引创建时间")
    moderation_status: Optional[str] = Field(
        None,
        description="审核状态（从任务 result JSONB 尽力提取，无 → null；不实时调 Ozon，任务终态即最新）",
    )


class ProductListResponse(BaseModel):
    """在售商品列表响应（M2.1）。"""
    items: list[ProductListItem] = Field(default_factory=list, description="在售商品列表（created_at DESC）")
    total: int = Field(0, description="该租户商品总数（分页前）")
    limit: int = Field(20, description="本次分页大小（1-100）")
    offset: int = Field(0, description="本次偏移")


# ──────────────────────────────────────────────
# /api/v1/products/{product_id}/update_images — 在线商品改图全量重传（WebUI T14，契约 C1b/C6）
# ──────────────────────────────────────────────


class UpdateProductImagesRequest(BaseModel):
    """POST /products/{product_id}/update_images 请求。

    全量重传：新 images 整体替换在线商品图片（死 URL 自动过滤后再提交）。
    token 也可走 Authorization: Bearer 头（C6「token body 或 Bearer」）。
    """
    token: str = Field("", description="MXOU API Key（body token 兜底；优先 Bearer）")
    images: list[str] = Field(..., description="新的图片 URL 列表（全量重传；死 URL 自动过滤）")


class UpdateProductImagesResponse(BaseModel):
    """T14 在线商品改图重传响应。"""
    ok: bool = True
    product_id: str = Field(..., description="Ozon product_id")
    offer_id: str = Field(..., description="信封 offer_id（sku_id / follow_{id}）")
    import_task_id: str = Field("", description="Ozon /v3/product/import 返回的 task_id")
    status: str = Field(
        ...,
        description="'pending_moderation' 商品重新审核中 | 'approved' 已通过",
    )
    re_under_review: bool = Field(..., description="「重新审核中」标记（改图触发重新审核）")
    message: str = Field(..., description="人类可读消息")
    images: list[str] = Field(default_factory=list, description="实际提交的存活图片 URL")
    images_filtered: list[str] = Field(default_factory=list, description="被过滤的死 URL")


class ProductEditResponse(BaseModel):
    """T6: GET /products/{product_id}/edit — 在线商品编辑初值。

    数据来源：product_task_index 关联草稿（直连任务无草稿 → 409，仅改图走 update_images）。
    """
    product_id: str = Field(..., description="Ozon product_id")
    offer_id: str = Field(..., description="信封 offer_id（sku_id / follow_{id}）")
    credential_id: str | None = Field(None, description="店铺凭证 id")
    draft_id: str = Field(..., description="关联草稿 id（product_task_index.draft_id）")
    payload: dict = Field(..., description="关联草稿 envelope（编辑表单初值）")
    moderation_status: str | None = Field(
        None,
        description="审核状态（从任务 result JSONB 尽力提取；无 → null，不实时调 Ozon）",
    )


# ──────────────────────────────────────────────
# /api/v1/mxou/login — MXOU 账号密码登录（WebUI T2）
# ──────────────────────────────────────────────


class MxouLoginRequest(BaseModel):
    """MXOU 账号密码登录请求。

    登录入口无 token 鉴权（登录本身是入口）；防爆破在端点层按 username 限流。
    """
    username: str = Field(..., description="MXOU 平台账号（api.mxou.cn）")
    password: str = Field(..., description="MXOU 平台密码（不落库不日志）")


class MxouKeyItem(BaseModel):
    """MXOU API Key 条目（脱敏展示，绝不含 full_key）。"""
    id: str = Field(..., description="token id")
    name: str = Field("", description="token 名称")
    masked: bool = Field(True, description="key 是否为脱敏形态（masked=true 时不含明文）")
    status: int = Field(1, description="token 状态（1=enabled）")


class MxouLoginResponse(BaseModel):
    """MXOU 登录成功响应（keys 已脱敏；选中 key 完整值仅此一次返回用于建立登录态）。"""
    username: str = Field(..., description="MXOU 用户名")
    balance: float | None = Field(None, description="平台真实余额（美元，/v1/dashboard/billing/subscription 同源；查询失败 None）")
    keys: list[MxouKeyItem] = Field(default_factory=list, description="API Key 列表（已脱敏，无 full_key）")
    selected_key_id: str | None = Field(None, description="选中的 enabled key id（未选到 None）")
    key: str | None = Field(None, description="选中 key 完整值（sk- 前缀；仅登录成功返回一次，WebUI 用它建立登录态）")
    session_expires_at: str | None = Field(None, description="MXOU 登录 session 过期时间")
    role: str = Field("user", description="用户角色（admin/user，WebUI 管理员路由守卫用）")


class MxouKeyCreateRequest(BaseModel):
    """新建 API Key 请求。"""
    name: str = Field("default", description="密钥名称")


class MxouKeyCreateResponse(BaseModel):
    """新建 API Key 响应（key 仅此一次返回——用户复制后不再可查）。"""
    id: str = Field(..., description="token id")
    name: str = Field(..., description="token 名称")
    key: str = Field(..., description="新建密钥完整值（仅此一次返回）")


class MxouKeySelectResponse(BaseModel):
    """切换密钥响应（key 仅此一次返回——用户复制后不再可查）。"""
    key: str = Field(..., description="所选密钥完整值（仅此一次返回）")
