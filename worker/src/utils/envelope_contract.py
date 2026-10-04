"""信封契约（Skill↔Worker GraphInput.envelope）——键的唯一权威 + 校验（W2 治理）。

背景：envelope 此前是自由 dict——skill 实发 ~30 个 extensions 键、CONTRACT 文档只记 6 个、
拼错的键静默变 None 流到管线深处才变现（「三处手工同步」纪律就是为它赎罪）。

本文件之后：
- **权威**：extensions 键集合只在本文件维护（`EnvelopeExtensions` extra="forbid" +
  `EXTENSION_KEY_ORIGINS` 来源标注）；draft.ozon_category 接线类型化模型（自 state.py
  迁入，原定义全仓零引用）。
- **生成**：`worker/scripts/gen_contract_docs.py` 从本模型生成 CONTRACT-v4 键表节 +
  `api-integration/envelope-keys.json`；CI `--check` 漂移即红。改键 = 改这里 → 跑生成脚本。
- **校验**：`validate_envelope()` 在两处边界接线（main.py 提交层 + ingest_node 防
  直调绕过）。**只在校验边界生效**——worker 在 ingest 之后会注入 box_reviewed/
  update_* 等键，管线中途不校验。
- **口径**：fail-closed——未知键默认拒绝并点名；`ENVELOPE_STRICT=0` 降级 warn
  （存量旧 skill 包逃生门，与 IMAGE_SALVAGE_FALLBACK 等硬闸同形态）。

⚠️ 位置（2026-10-03 hotfix）：本模块自 `api/` 下沉 `utils/`——ingest_node（graphs 层）
的防直调绕过校验需要 import 它，graphs→api 是 W3a 依赖立法（test_import_direction R2）
明令禁止的 upward 边；api 与 graphs 都向下依赖 utils，双向合法。

范围裁剪（有意）：draft 深层不做全类型化（漂移主战场是 extensions；draft 必填/
合理性已有 ingest/提交层闸）；extensions 逐键类型保持 Any 宽松——本闸管「键存在性」
（拼错/私加键），不管值类型。

legacy 兼容：envelope 无 draft 键的扁平形态（ingest 既有兼容路径）不做模型校验；
`stock`/`warehouse_id`（v0.80 退役）保留在已知集——存量草稿 payload 落库含它们，
resubmit 走 ingest 时不应被新闸砖死。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ────────────────────────── draft.ozon_category 契约（自 graphs/state.py 迁入） ──────────────────────────


class EnvelopeOzonCategory(BaseModel):
    """draft.ozon_category 契约（Ozon 链接类目，来自页面/what_to_sell/search_categories）。"""

    source: str = Field(default="search_kw", description="page|mapping|what_to_sell|manual|search_kw（v0.69 T0.2: manual=人工指定 CLI --category-id 直传，权威级与 page 同）")
    namespace: str = Field(default="seller", description="seller|widget|1688")
    lang: str = Field(default="", description="面包屑语言 ZH_HANS|RU")
    category_path: str = Field(default="", description="完整类目路径（主判据）")
    category1: str = Field(default="")
    category2: str = Field(default="")
    category3: str = Field(default="")
    description_category_id: str = Field(default="", description="顾客/Seller 命名空间 ID，非主判据")
    type_id: str = Field(default="")


class EnvelopeSourceCategory(BaseModel):
    """draft source_category_* 契约（1688 来源类目）。"""

    id: str = Field(default="", description="1688 叶子类目数字 ID（AK cateId）")
    path: str = Field(default="", description="1688 完整类目路径")
    leaf: str = Field(default="", description="1688 末级类目词")


# ────────────────────────── extensions 键权威清单 ──────────────────────────

# 键 → 来源标注（生成文档用；新增键必须同时登记模型字段与本表）
EXTENSION_KEY_ORIGINS: Dict[str, str] = {
    # skill 自动注入（cloud_probe build_envelope resolved_extensions.setdefault）
    "ozon_client_id": "skill-auto",
    "mxou_token": "skill-auto",
    # 模板/店铺可注入（cloud_probe _INJECTABLE_EXT_KEYS）
    "margin_rate": "skill-injectable",
    "commission_rate": "skill-injectable",
    "fx_buffer": "skill-injectable",
    "margin_floor": "skill-injectable",
    "margin_anchor": "skill-injectable",
    "variable_cost_rate": "skill-injectable",
    "promo_variable_cost_rate": "skill-injectable",
    "traffic_keywords": "skill-injectable",
    "offer_id_prefix": "skill-injectable",
    "follow_type": "skill-injectable",
    # 采集腿写入（follow/discover/graph）
    "follow_sell": "skill-collect",
    "follow_clone": "skill-collect",
    "clone_card": "skill-collect",
    "competitor_weight_g": "skill-collect",
    "competitor_dimensions_mm": "skill-collect",
    "competitor_ref_images": "skill-collect",
    "commission_segments": "skill-collect",
    "match_evidence": "skill-collect",
    "discovery_meta": "skill-collect",
    # worker 侧注入/消费
    "box_reviewed": "worker",
    "credential_id": "worker",
    "update_product_id": "worker",
    "update_offer_id": "worker",
    "image_regen": "worker",
    "currency_code": "worker",
    # legacy：v0.80 退役（我方永不设库存），存量草稿 payload 仍含——resubmit 兼容
    "stock": "legacy",
    "warehouse_id": "legacy",
    # legacy：2026-10 死代码清扫退役——唯一写入方在已删的 skill build_envelope 死链
    # （worker 侧消费方为零，活链不写）；采集箱存量草稿 payload 仍含，resubmit 兼容
    "store_id": "legacy",
    "shipping_provider": "legacy",
    "shipping_service": "legacy",
}

_KEY_DESCRIPTIONS: Dict[str, str] = {
    "ozon_client_id": "Ozon Client-Id（skill 从店铺配置注入）",
    "mxou_token": "MXOU token（skill 注入）",
    "store_id": "店铺 ID（2026-10 退役，存量草稿兼容）",
    "shipping_provider": "物流商（2026-10 退役，存量草稿兼容）",
    "shipping_service": "物流服务等级（2026-10 退役，存量草稿兼容）",
    "margin_rate": "日常毛利率（缺省走 worker 三档）",
    "commission_rate": "显式佣金率（优先级最高）",
    "fx_buffer": "汇损缓冲",
    "margin_floor": "促销底线 margin",
    "margin_anchor": "划线原价 margin",
    "variable_cost_rate": "日常变动成本率",
    "promo_variable_cost_rate": "促销变动成本率",
    "traffic_keywords": "SEO 流量词（标题提示词增强）",
    "offer_id_prefix": "offer_id 前缀覆盖",
    "follow_type": "跟卖模式 hand|api|clone",
    "follow_sell": "跟卖标记（路由分流）",
    "follow_clone": "跟卖克隆标记（follow_clone 模式：零 LLM 零生图——跳撰写/生图链，复制卡 images=[] 不动卡图或回退克隆图 CDN 直传）",
    "clone_card": "克隆回退载荷（import-by-sku 不可复制时逐字克隆数据：product_id/name/dc/tp/attributes/images/weight_g/dims_mm，CDP 读卡产物）",
    "competitor_weight_g": "竞品重量 g（draft.weight 缺失兜底）",
    "competitor_dimensions_mm": "竞品尺寸 mm（缺失兜底）",
    "competitor_ref_images": "跟卖竞品主图快照（生图参考，绝不进 draft.images）",
    "commission_segments": "佣金分段 {fbs:{},fbo:{}}（rfbs/fbp 映射）",
    "match_evidence": "图搜匹配证据（method/confidence/divergent/…，学习与语义闸消费）",
    "discovery_meta": "discover 选品元数据快照（worker 零消费整包透传）",
    "box_reviewed": "采集箱草稿提交标记（禁自主改写）",
    "credential_id": "店铺凭证 ID（采集箱提交链）",
    "update_product_id": "UPDATE 目标 product_id（改卡重提）",
    "update_offer_id": "UPDATE 目标 offer_id",
    "image_regen": "改图重传标记（main.py 注入重入队）",
    "currency_code": "店铺币种（currency_resolver 信任序第一级）",
    "stock": "（v0.80 退役）存量兼容",
    "warehouse_id": "（v0.80 退役）存量兼容",
}


class EnvelopeExtensions(BaseModel):
    """extensions 键模型——**本文件是键集合的唯一权威**（extra="forbid"）。

    值类型刻意保持 Any（本闸管键存在性，不管值类型——拼错/私加键才是漂移主类）。
    新增键：模型加字段 + EXTENSION_KEY_ORIGINS/_KEY_DESCRIPTIONS 登记 → 跑
    gen_contract_docs.py。
    """

    model_config = ConfigDict(extra="forbid")

    ozon_client_id: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["ozon_client_id"])
    mxou_token: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["mxou_token"])
    store_id: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["store_id"])
    shipping_provider: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["shipping_provider"])
    shipping_service: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["shipping_service"])
    margin_rate: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["margin_rate"])
    commission_rate: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["commission_rate"])
    fx_buffer: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["fx_buffer"])
    margin_floor: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["margin_floor"])
    margin_anchor: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["margin_anchor"])
    variable_cost_rate: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["variable_cost_rate"])
    promo_variable_cost_rate: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["promo_variable_cost_rate"])
    traffic_keywords: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["traffic_keywords"])
    offer_id_prefix: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["offer_id_prefix"])
    follow_type: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["follow_type"])
    follow_sell: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["follow_sell"])
    follow_clone: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["follow_clone"])
    clone_card: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["clone_card"])
    competitor_weight_g: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["competitor_weight_g"])
    competitor_dimensions_mm: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["competitor_dimensions_mm"])
    competitor_ref_images: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["competitor_ref_images"])
    commission_segments: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["commission_segments"])
    match_evidence: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["match_evidence"])
    discovery_meta: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["discovery_meta"])
    box_reviewed: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["box_reviewed"])
    credential_id: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["credential_id"])
    update_product_id: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["update_product_id"])
    update_offer_id: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["update_offer_id"])
    image_regen: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["image_regen"])
    currency_code: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["currency_code"])
    stock: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["stock"])
    warehouse_id: Optional[Any] = Field(default=None, description=_KEY_DESCRIPTIONS["warehouse_id"])


class EnvelopeRoot(BaseModel):
    """envelope 顶层模型（三层结构 {draft, source, extensions}(+assets)）。"""

    model_config = ConfigDict(extra="forbid")

    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据（必填非空由提交层/ingest 既有闸管）")
    source: Optional[Dict[str, Any]] = Field(default=None, description="采购源信息 {purchase_url, purchase_cost}")
    extensions: Optional[EnvelopeExtensions] = Field(default=None, description="扩展配置（键集见 EnvelopeExtensions）")
    assets: Optional[Dict[str, Any]] = Field(default=None, description="派生资产位（build_envelope 输出 image_urls 派生）")


# ────────────────────────── 校验 ──────────────────────────


def envelope_strict_enabled() -> bool:
    """ENVELOPE_STRICT=0 降级 warn（存量旧 skill 包逃生门）；缺省/其他值 = 严格。"""
    return os.environ.get("ENVELOPE_STRICT", "1").strip() != "0"


def validate_envelope(envelope: Any) -> list[str]:
    """信封契约校验，返回错误清单（每条点名具体键；空 = 通过）。

    - 非法形态（非 dict/空）→ 单条错误
    - legacy 扁平形态（无 draft 键）→ 直接放行（ingest 兼容路径，见模块注释）
    - 标准三层 → EnvelopeRoot 校验（未知顶层/未知 extensions 键逐条点名）+
      draft.ozon_category 存在时按 EnvelopeOzonCategory 窄校验
    """
    if not isinstance(envelope, dict) or not envelope:
        return ["envelope 必须为非空 dict"]
    if "draft" not in envelope:
        return []  # legacy 扁平形态
    errors: list[str] = []
    try:
        EnvelopeRoot.model_validate(envelope)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()))
            errors.append(f"envelope.{loc or '<root>'}: {err.get('msg', '校验失败')}（未知键或类型不符；权威键清单 = utils/envelope_contract.py）")
    draft = envelope.get("draft")
    if isinstance(draft, dict) and isinstance(draft.get("ozon_category"), dict):
        try:
            EnvelopeOzonCategory.model_validate(draft["ozon_category"])
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(x) for x in err.get("loc", ()))
                errors.append(f"envelope.draft.ozon_category.{loc}: {err.get('msg', '校验失败')}")
    return errors
