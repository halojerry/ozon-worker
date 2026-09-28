"""M1.2: 草稿预估售价/利润服务 — 纯读派生数据（不落库、不调 Ozon 上架）。

输入 envelope（三层结构），输出与 pricing_node 同源的预估价格。

⚠️ v0.83 批①：单 SKU 主链已收敛到 ``utils/pricing_core.compute_pricing_core``
（pricing_node / 本服务 / ``POST /api/v1/estimate/batch`` 三处同源）。本服务只做
「取数 + 解析货币/汇率/3PL + 调共享核 + 投影响应」，不写定价公式。

与 pricing_node 的差异（有意，均为「补全」方向）：
- 重量链做 ``reconcile_weight_with_attrs`` + 体积重兜底 ``ensure_volume_weight_floor``
  （v0.83.2 起与 pricing_node / prepare 三处同一条链）——卡面由 prepare 按兜底后重量
  声明并据此计费，定价必须同口径，否则低密度件少收运费差；
- 无传入汇率且币种为 RUB 时走 fx 三级链 ``resolve_cny_rub_rate``（pg_cache→live→兜底）；
- 请求带 credential_id / ``extensions.credential_id`` / ``extensions.ozon_client_id`` 时
  经 credential_service 解密取店铺 3PL（否则 default_rets）。

⚠️ 铁律：前端/skill 一律不写定价公式；本服务也只做「取数 + 调共享核」。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

# ⚠️ 保留这些模块级导入：既有测试族按 ``estimate_service.<name>`` 模块属性 patch
# （core 经注入点消费本模块当前命名空间的值，patch 语义不变）。
from utils.commission_resolver import get_category_commission, resolve_commission_rate
from utils.logistics_quote import get_store_logistics_config, query_logistics_cost
from utils.pricing_estimate import compute_price, is_dual_margin
from utils.weight_dimension_normalizer import normalize_weight_dimensions

logger = logging.getLogger(__name__)

# 与 pricing_node 逐字一致（Step 3 包装成本固定值 + Step 1 成本兜底）
# ⚠️ v0.83：常量事实源在 utils/pricing_core（本处保留为历史引用，勿再新增消费方）。
PACKAGING_COST_CNY = 2.0
DEFAULT_COST_CNY = 10.0


def _extract_dc_id(draft: dict) -> Optional[int]:
    """从 draft.ozon_category.description_category_id 取类目 ID（digit 才认，否则 None）。"""
    oz = draft.get("ozon_category") or {}
    raw = oz.get("description_category_id") if isinstance(oz, dict) else None
    if raw is None:
        return None
    s = str(raw)
    return int(s) if s.isdigit() else None


def _resolve_credential_id_from_extensions(
    extensions: dict, tenant_id: Optional[str]
) -> Optional[str]:
    """envelope.extensions 的凭证线索 → worker credential_id（v0.83 gate 批① 价格同源）。

    gate 三轮实锤：skill 提交前预估走本服务（envelope 形态），此前**不支持凭证** →
    物流费恒走默认 RETS（钥匙盒例 ¥7.52），而 pricing_node 用店铺真实 3PL（¥6.76）
    → 预估↔卡价 +5.9%（验收线 ±3%）。本函数补上 envelope 形态的凭证入口：

    - 优先 ``extensions.credential_id``（worker 内部 UUID，与 batch 请求体同名字段对齐）；
    - 否则按 ``extensions.ozon_client_id`` + 租户反查 credential 行——skill 侧只持
      店铺 ``client_id``（stores.json），拿不到 worker 内部 UUID，走反查免去其多打
      一次 API 换取 credential_id（改动面最小的方案）。
    - 任何异常 → None（回落 default_rets，绝不 raise：预估是只读派生数据）。
    """
    if not isinstance(extensions, dict):
        return None
    cid = extensions.get("credential_id")
    if cid:
        return str(cid)
    client_id = extensions.get("ozon_client_id")
    if client_id and tenant_id:
        try:
            from services import credential_service

            return credential_service.find_credential_id_by_client(
                str(tenant_id), str(client_id)
            )
        except Exception as exc:
            logger.warning("estimate 按 ozon_client_id 反查凭证失败（回落 default_rets）: %s", str(exc)[:160])
    return None


def _resolve_logistics_config(
    credential_id: Optional[str], tenant_id: Optional[str]
) -> tuple[str, str, str]:
    """店铺 3PL/服务等级探测（v0.83）：无凭证 → 默认 RETS/Standard。

    返回 (tpl_provider, service_level, logistics_source)；
    logistics_source ∈ {"store"（经凭证解密探测到）, "default_rets"}。
    任何异常（凭证 404/解密失败/上游 5xx）→ 回落 default_rets，绝不 raise
    （预估是只读派生数据，不能因探测失败而整体失败）。
    """
    if credential_id and tenant_id:
        try:
            from services import credential_service

            cid, key = credential_service.get_decrypted(str(tenant_id), str(credential_id))
            if cid and key:
                tpl, svc = get_store_logistics_config(cid, key)
                return tpl, svc, "store"
        except Exception as exc:
            logger.warning("estimate 3PL 探测失败（回落 default_rets）: %s", str(exc)[:160])
    return "RETS", "Standard", "default_rets"


def estimate_from_envelope(
    envelope: dict,
    *,
    exchange_rate: Optional[float] = None,
    currency_code: Optional[str] = None,
    margin_rate: Optional[float] = None,
    commission_rate: Optional[float] = None,
    fx_buffer: Optional[float] = None,
    margin_anchor: Optional[float] = None,
    margin_floor: Optional[float] = None,
    variable_cost_rate: Optional[float] = None,
    promo_variable_cost_rate: Optional[float] = None,
    credential_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> dict:
    """根据信封草稿计算预估售价/利润（与 pricing_node 同源公式，走共享 pricing_core）。

    - 纯读：不落库、不调 Ozon 上架 API（credential_id 在场时仅做 3PL 只读探测）。
    - exchange_rate 传 None：
        * 币种解析为 CNY → 按 CNY 处理（与 skill 估算一致）；
        * 币种解析为 RUB → 走 fx 三级链 resolve_cny_rub_rate（v0.83）。
    - currency_code 解析顺序：请求覆盖 → extensions.currency_code → exchange_rate
      有值则 RUB → 否则 CNY。
    - 物流费：credential_id 入参 / ``extensions.credential_id`` / ``extensions.ozon_client_id``
      任一在场 → 解密探测店铺 3PL（logistics_source=store）；否则默认 RETS/Standard
      （logistics_source=default_rets）。
    - v0.60 三档：margin_anchor/margin_floor/variable_cost_rate/promo_variable_cost_rate
      可选（请求覆盖优先，其次 extensions，最后默认 2.0/0.6/0.155/0.245）。
      三档判定唯一口径 utils.pricing_estimate.is_dual_margin（与 pricing_node 同源）。
    """
    draft = (envelope.get("draft") or {}) if isinstance(envelope, dict) else {}
    extensions = (envelope.get("extensions") or {}) if isinstance(envelope, dict) else {}

    # 货币判定（请求覆盖 → extensions → exchange_rate 有值则 RUB → CNY）
    eff_currency = (currency_code or str(extensions.get("currency_code", "") or "")).upper()
    if eff_currency not in ("CNY", "RUB"):
        eff_currency = "RUB" if exchange_rate is not None else "CNY"

    # v0.83: 无传入汇率且币种 RUB → fx 三级链（pg_cache→live→兜底 12）
    _fx_rate = exchange_rate
    _fx_source = ""
    if eff_currency == "RUB" and _fx_rate is None:
        from utils.fx_rate_service import resolve_cny_rub_rate

        _fx_rate, _fx_source = resolve_cny_rub_rate()

    # 店铺 3PL 探测（credential_id 在场才探测；否则 default_rets）
    # v0.83 gate 批①：显式入参缺省时，从 extensions 的凭证线索补齐（credential_id /
    # ozon_client_id），令 envelope 形态的物流费与 pricing_node 同源。
    if not credential_id:
        credential_id = _resolve_credential_id_from_extensions(extensions, tenant_id)
    tpl, svc, logistics_source = _resolve_logistics_config(credential_id, tenant_id)

    # ── 共享定价核（与 pricing_node / batch 同源）──
    from utils.pricing_core import compute_pricing_core

    audit: dict[str, Any] = {}
    info = compute_pricing_core(
        draft,
        extensions,
        currency_code=eff_currency,
        exchange_rate=_fx_rate if _fx_rate is not None else 1.0,
        fx_source=_fx_source,
        description_category_id=_extract_dc_id(draft),
        tpl_provider=tpl,
        service_level=svc,
        logistics_source=logistics_source,
        # v0.83.2：**与 pricing_node / prepare 三处同一条重量链**（含体积重兜底）。
        # 终态收敛：prepare 上传链对低密度件按兜底后重量声明卡面/计费（100g→121g），
        # pricing_node 与预估必须同口径，否则低密度单系统性少收运费差（gate key-box：
        # 100g/96×70×45mm 兜底 121g → 物流 7.52 → 价 18，三者一致）。
        apply_volume_floor=True,
        margin_rate=margin_rate,
        commission_rate=commission_rate,
        fx_buffer=fx_buffer,
        margin_anchor=margin_anchor,
        margin_floor=margin_floor,
        variable_cost_rate=variable_cost_rate,
        promo_variable_cost_rate=promo_variable_cost_rate,
        query_logistics_cost_fn=query_logistics_cost,
        get_category_commission_fn=get_category_commission,
        audit_out=audit,
    )

    profit_est = info.get("profit_estimation") or {}
    response: dict[str, Any] = {
        "price": info["price"],
        "old_price": info["old_price"],
        "profit_cny": profit_est.get("profit_cny"),
        "profit_rate": profit_est.get("profit_rate"),
        "logistics_cost_cny": round(float(info.get("logistics_cost_cny") or 0.0), 2),
        # 币种：按最终生效币种如实回报（fx 链已解析时 RUB 路径为真 RUB）
        "currency": "CNY" if eff_currency == "CNY" else "RUB",
        "commission_rate": round(float(info.get("commission_rate") or 0.0), 4),
        "commission_source": audit.get("commission_source", "fallback"),
        # v0.83 审计 marks（回吐）
        "weight_suspect": audit.get("weight_suspect", ""),
        "exchange_rate_source": audit.get("exchange_rate_source", ""),
        "logistics_source": logistics_source,
    }
    if audit.get("dual_margin"):
        response.update(
            {
                "promo_price": info.get("promo_price"),
                "margin_anchor": audit.get("margin_anchor"),
                "margin_floor": audit.get("margin_floor"),
                "variable_cost_rate": audit.get("variable_cost_rate"),
            }
        )
    return response
