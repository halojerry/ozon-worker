"""跟卖导入节点 v5 — import-by-sku 复制竞品卡片 → 统一定价 → AI 生图 → UPDATE 上传

跟卖管线 v5（Ozon 官方规范）:
  ① import-by-sku {sku, name, offer_id, currency_code, price, vat}
     → 复制竞品类目+属性到我们店铺
  ② 失败则 Fallback: 记录 "need_create"，后续 /v3/product/import CREATE
  ③ 成功 → 轮询获取 product_id → 统一定价(pricing_node) → AI 生图 → /v3/product/import UPDATE

✅ v4 (B2): 定价统一走 pricing_node, 此节点不再内联定价公式
✅ v4 (B3): 类目三层解析全部失败时传播错误(不静默降级为空类目)

关键设计:
  - offer_id = ozon_product_id（如 3852000144），保证一竞品一商品
  - import-by-sku 优先，不可复制时自动降级
"""
import logging
import re
import time
from typing import Any

import requests as req

from graphs.state import GlobalState, FollowSellImportOutput

logger = logging.getLogger(__name__)

BRAND_DICT_ID = 126745801
CHINA_DICT_ID = 90296


def follow_sell_import_node(state: GlobalState) -> dict[str, Any]:
    """跟卖导入节点 v5 — v4: 返回 TypedDict 替代直接修改 GlobalState"""
    draft = state.envelope.get("draft", {}) if state.envelope else {}
    extensions = state.envelope.get("extensions", {}) if state.envelope else {}
    # ⚠️ v0.22（参考 maozi follow_type）：hand=防侵权跟卖（模拟人工，跳过
    # import-by-sku 1:1 复制，走 CREATE 重建——我们管线重做类目/属性/生图，
    # 天然防同款/侵权检测）；api=强制跟卖（import-by-sku 1:1 复制竞品卡片，
    # 快但可能报错/被下架）。默认 hand。
    follow_type = str(extensions.get("follow_type") or "hand").lower()

    # ── 局部变量（替代 state.xxx 直接赋值）──
    product_id: str = ""
    comp_price: str = ""
    comp_name: str = ""
    dc_id: str = ""
    tp_id: str = ""
    orig_images: list = []
    variants: list = []
    item_id: str = ""
    final_attrs: list = []
    attrs_schema: list = []
    up_status: str = "pending"
    error_msg: str = ""
    failed_stg: str = ""
    category_missing: bool = False
    # v0.22 P2a: import-by-sku 已提交但未完成标记（防超时 fallback CREATE 双卡）
    import_submitted: bool = False
    import_task_id_val: str = ""

    ozon_product_id = str(draft.get("ozon_product_id", ""))
    if not ozon_product_id:
        logger.error("❌ 跟卖: ozon_product_id 为空")
        # fix/image-ref-pollution R2: 错误路径同样透传 extensions（下游一致语义）
        return {"error_message": "跟卖需要竞品 ozon_product_id", "failed_stage": "follow_sell_import",
                "extensions": extensions}

    # ⚠️ v0.25 FIX: offer_id 统一用竞品 ID（无 follow_ 前缀），与 prepare/upload 一致。
    # 旧 v0.22 曾改 import-by-sku 用 follow_{id}，但 prepare 层 upload 一直用裸 {id}，
    # 导致 api 复制模式 import 卡 offer_id=follow_x，后续 UPDATE 用 x 匹配不到 → 双卡。
    # 现有 hand 模式卡片 offer_id 都是裸 {id}（如 3807171071），统一裸 ID 全部兼容。
    offer_id = str(ozon_product_id)
    ozon_title = draft.get("title", "") or draft.get("ozon_title", "") or ""
    ozon_images = draft.get("images", []) or []
    ozon_cat = draft.get("ozon_category", {}) or {}

    # 占位价（import-by-sku 需要，实际售价由 pricing_node 计算）
    purchase_cost = float(draft.get("purchase_cost", 0) or 0)
    placeholder_price = max(10, int(purchase_cost * 2.0)) if purchase_cost > 0 else 100
    price_val = placeholder_price
    old_price_val = int(placeholder_price * 1.3)

    # 类目解析（v0.69 P-B：确定性直采 → 门控仲裁，模糊直采通道全部移除）
    dc_raw = str(ozon_cat.get("description_category_id") or "")
    type_raw = str(ozon_cat.get("type_id") or "")
    dc_fallback, type_fallback = dc_raw, type_raw
    language = ozon_cat.get("language", "")
    _src = (state.envelope or {}).get("source", {}) if state.envelope else {}
    if not isinstance(_src, dict):
        _src = {}
    # ✅ v0.26 权威类目信任：skill 已从 what_to_sell 拿到 Seller 空间权威组合
    # （category2=dc + category3=type，schema API 200 验证有效，wave2 眉笔实证）。
    # 此时 dc/type 都有效 → 直接信任，不二次解析——
    # 否则 _resolve_category_by_id 数字直查会返回该 dc 下**第一个** type
    # （眉笔 dc=17028990 → PG 返回 93409 BB-средство，覆盖权威 93418 → 类目又错）。
    _dc_valid = bool(dc_raw and dc_raw.isdigit() and int(dc_raw) > 0)
    _type_valid = bool(type_raw and str(type_raw).isdigit() and int(type_raw) > 0)
    _cat_trusted = False
    if _dc_valid and _type_valid:
        # ✅ v0.26 权威类目信任（自校验）：skill 已从 what_to_sell 拿到 Seller 空间
        # 权威组合（category2=dc + category3=type，schema API 200 验证有效，wave2 眉笔实证）。
        # 但 Widget 空间的面包屑 ID 也是数字（如盘子 102080114）→ 不能只看"是数字"就信任。
        # 用 schema API 自校验：200 有效 → 信任（不二次解析，防 _resolve_category_by_id
        # 数字直查返回该 dc 下第一个 type 覆盖权威 type）；400 无效 → 回退二次解析。
        _cat_trusted = _verify_category_schema(
            getattr(state, "ozon_client_id", "") or "",
            getattr(state, "ozon_api_key", "") or "",
            dc_raw, type_raw,
        )
        if _cat_trusted:
            logger.info("✅ 信封类目 schema 验证通过（dc=%s type=%s），直接信任不二次解析", dc_raw, type_raw)
        else:
            logger.warning(
                "⚠️ 信封类目 schema 验证失败（dc=%s type=%s，疑似 Widget 无效 ID），回退二次解析",
                dc_raw, type_raw,
            )
    # 文本类目名（非数字）不作类目采纳，只作门控补充搜索词
    _gate_extra: list = []
    if not _cat_trusted and dc_raw and dc_raw.isdigit():
        category_hint = ozon_cat.get("category_path", "") or ozon_cat.get("category", "")
        # ✅ v0.25 FIX: 传完整 category_path（不是只有末段）——路径精配在函数内确定性尝试。
        resolved_dc, resolved_type = _resolve_category_by_id(int(dc_raw), type_name_hint=category_hint, token=state.token)
        if resolved_dc and resolved_type:
            dc_raw, type_raw = resolved_dc, resolved_type
        else:
            logger.warning("数字 ID 确定性解析失败: dc=%s type=%s，进入门控仲裁", dc_fallback, type_fallback)
            # ✅ v0.20 A: 解析失败绝不保留原始值（品牌页 ID 会被当有效类目上传 → Ozon 拒）
            dc_raw, type_raw = "", ""
    elif not _cat_trusted and dc_raw:
        _gate_extra.append(dc_raw)
        dc_raw, type_raw = "", ""
    # v0.69 P-B: 确定性失败/无 dc → 门控仲裁（搜索词=面包屑末两段+1688 来源类目末两段）
    if not dc_raw or not type_raw:
        _terms = _gate_search_terms(ozon_cat, _src, extra=_gate_extra)
        if _terms:
            _g_dc, _g_tp = _gated_category_arbitration(
                _terms, " ".join(_terms), draft, state)
            if _g_dc and _g_tp:
                dc_raw, type_raw = _g_dc, _g_tp

    # 拉取属性 schema
    client_id = state.ozon_client_id
    api_key = state.ozon_api_key
    if dc_raw and type_raw:
        try:
            import requests as _req
            _resp = _req.post(
                "https://api-seller.ozon.ru/v1/description-category/attribute",
                headers={"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"},
                json={"description_category_id": int(dc_raw), "type_id": int(type_raw), "language": "ZH_HANS"},
                timeout=15,
            )
            if _resp.status_code == 200:
                # ⚠️ v0.22 防御: Ozon 异常/限流时 result 可能非 list，
                # 直接赋值会让 GlobalState.attributes_schema 校验失败卡死管线
                _schema_raw = _resp.json().get("result", [])
                attrs_schema = _schema_raw if isinstance(_schema_raw, list) else []
                logger.info("✅ 跟卖 schema 已拉取: %d 个属性", len(attrs_schema))
        except Exception as e:
            logger.warning("⚠️ 跟卖 schema 拉取失败（降级继续）: %s", e)

    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    # ── ① import-by-sku（仅 api 强制跟卖模式；hand 模式跳过 1:1 复制）──
    # v0.22（参考 maozi follow_type）：hand=防侵权跟卖（模拟人工，走 CREATE 重建，
    # 我们管线重做类目/属性/生图，天然防同款/侵权检测）；api=import-by-sku 1:1 复制。
    # ⚠️ 触发规则：hand 需要 1688 货源数据重建；信封缺货源（无 purchase_url /
    # purchase_cost）→ hand 无法重建，自动降级 api 复制竞品（不丢单）。
    # ✅ v0.26 增强: hand 类目解析失败（dc/tp 为空）也降级 api——官方 import-by-sku
    # 复制竞品卡会带出完整类目（含 Canevia/NEATIFY 等品牌子类目，Seller 树查不到），
    # 比 hand CREATE 硬失败（created=False，wave1 盘子实证）不丢单。
    _has_source = bool(draft.get("purchase_url") or draft.get("purchase_cost"))
    _cat_ok = bool(dc_raw and type_raw)
    if follow_type == "hand" and (not _has_source or not _cat_ok):
        _reason = "缺 1688 货源数据" if not _has_source else f"类目解析失败 dc={dc_fallback}"
        logger.warning(
            "⚠️ hand 模式 %s，自动降级 api import-by-sku 复制竞品卡片（不丢单）",
            _reason,
        )
        follow_type = "api"
    if follow_type == "discover":
        # ✅ v0.69 P-D: discover 变体绝不 import-by-sku/api 复制——竞品 SKU 复制
        # 会把竞品卡 1:1 建进店铺（wave D 测试店 5 卡实证）+ 每单 180s 轮询白等，
        # discover 场景有百害无一利。类目未定稿 → 置空交由 assemble 全闸链。
        logger.info("🧭 discover 变体：跳过 import-by-sku/api 复制，走 CREATE 重建")
    elif follow_type == "api":
        try:
            import_body = {
                "items": [{
                    "sku": int(ozon_product_id),
                    "name": ozon_title or f"Товар {ozon_product_id}",
                    "offer_id": offer_id,
                    "currency_code": state.currency_code or "CNY",
                    "price": str(price_val),
                    "old_price": str(old_price_val),
                    "vat": "0",
                }]
            }
            logger.info("🔄 import-by-sku: sku=%s, offer_id=%s", ozon_product_id, offer_id)
            resp = req.post("https://api-seller.ozon.ru/v1/product/import-by-sku",
                            headers=headers, json=import_body, timeout=30)
            data = resp.json().get("result", {})
            unmatched = data.get("unmatched_sku_list", [])
            ibs_task_id = str(data.get("task_id", ""))
            if resp.status_code == 200 and not unmatched:
                logger.info("✅ import-by-sku 已提交: task_id=%s", ibs_task_id)
                for _ibs_attempt in range(60):  # v0.22 P2a: 60s→180s，降低超时 fallback 双卡概率
                    time.sleep(3)
                    try:
                        info_resp = req.post(
                            "https://api-seller.ozon.ru/v1/product/import/info",
                            headers=headers, json={"task_id": int(ibs_task_id)}, timeout=15,
                        )
                        if info_resp.status_code == 200:
                            info_items = info_resp.json().get("result", {}).get("items", [])
                            for _it in info_items:
                                _pid = _it.get("product_id")
                                _status = _it.get("status", "")
                                if _pid and _status == "imported":
                                    product_id = str(_pid)
                                    logger.info("✅ import-by-sku 完成: product_id=%s，后续走 UPDATE", _pid)
                                    break
                        if product_id:
                            break
                    except Exception:
                        pass
                if not product_id:
                    # v0.22 P2a: import 已提交但超时 → 标记 pending，不 fallback CREATE
                    # （避免与后台 import 竞争同 offer_id 双卡；由后续轮询 import/info 收尾）
                    logger.warning(
                        "⚠️ import-by-sku 已提交(task=%s)但未在180s内确认，标记 pending，不 fallback CREATE",
                        ibs_task_id,
                    )
                    import_submitted = True
                    import_task_id_val = str(ibs_task_id)
                    up_status = "pending"
            else:
                logger.info("⚠️ import-by-sku 不可复制(unmatched=%s)，走 Fallback", unmatched)
        except Exception as e:
            logger.info("⚠️ import-by-sku 异常，走 Fallback: %s", e)
    else:
        logger.info("🛡️ hand 防侵权跟卖：跳过 import-by-sku（1:1 复制），走 CREATE 重建")


    # ── ② 组装返回值 ──
    # ⚠️ v0.14 P0-6: 优先读 draft.competitor_price（Skill 从 Ozon 页面提取的真实竞品售价）
    # 旧逻辑读 draft.get("price")/draft.get("ozon_price") —— draft.price 实为 1688 采购价(CNY)，
    # 被误当竞品价 → 竞品价保护分支(≥成本×1.3 保持竞品价)永不生效
    ozon_price = draft.get("competitor_price", "") or draft.get("ozon_price", "") or ""
    if ozon_price:
        comp_price = str(ozon_price).strip()
        logger.info(f"💰 竞品 Ozon 价格: {comp_price}")

    if dc_raw and dc_raw.isdigit() and int(dc_raw) > 0:
        dc_id = dc_raw
    if type_raw and type_raw.isdigit() and int(type_raw) > 0:
        tp_id = type_raw

    # ✅ v0.19.1 P0: 类目缺失不再一刀切失败
    # 1) import-by-sku 成功（product_id 已返回）→ Ozon 官方复制已带出类目/属性，
    #    不再强制要求 dc/tp（此前官方通道被前置校验掐死——南辕北辙）
    # 2) Fallback CREATE 才需要类目：缺失时用 1688 来源类目/标题 pg_trgm 兜底
    #    （复用 direct 管线现成引擎）
    import_by_sku_ok = bool(product_id)
    if not dc_id or not tp_id:
        if import_by_sku_ok:
            logger.warning("⚠️ 跟卖无类目但 import-by-sku 已成功（%s），"
                           "继续走 UPDATE（类目由官方复制带出）", product_id)
            category_missing = True
        elif follow_type == "discover":
            # ✅ v0.69 P-D: discover 类目未定稿不报错不进 retry——类目置空继续走管，
            # 由 assemble 跟卖分支（文本解析+全闸链）定稿。
            logger.warning("⚠️ discover 变体类目未定稿，置空继续（assemble 全闸链定稿）")
        else:
            src_path = ""
            if isinstance(_src, dict):
                src_path = _src.get("source_category_path", "") or ""
            search_text = src_path.split(" > ")[-1].strip() if src_path else ""
            if not search_text:
                search_text = draft.get("source_category", "") or draft.get("title", "") or ""
            if search_text:
                # ✅ v0.25 T1: 先查 1688→Ozon 类目学习表（数字 ID 优先，curated 确定性数据）
                try:
                    from utils.category_mapping_learn import lookup_mapping
                    _sid = draft.get("source_category_id") or _src.get("category_id")
                    _mapped = lookup_mapping(
                        source_category_id=int(_sid) if _sid else None,
                        leaf_name=search_text,
                    )
                    if _mapped:
                        dc_id, tp_id = _mapped["dc"], _mapped["tp"]
                        logger.info("✅ 类目映射学习命中: '%s' → %s/%s", search_text, dc_id, tp_id)
                except Exception as _me:
                    logger.warning("类目映射查询失败（继续门控仲裁）: %s", _me)
                # v0.69 P-B: 原 pg_trgm 直采兜底移除——改走门控仲裁
                if not dc_id or not tp_id:
                    _f_terms = _gate_search_terms(ozon_cat, _src, extra=[search_text])
                    if _f_terms:
                        _g_dc, _g_tp = _gated_category_arbitration(
                            _f_terms, " ".join(_f_terms), draft, state)
                        if _g_dc and _g_tp:
                            dc_id, tp_id = _g_dc, _g_tp
        if (not dc_id or not tp_id) and not import_by_sku_ok and follow_type != "discover":
            cat_path = ozon_cat.get("category_path", "") or ozon_cat.get("category", "")
            logger.error("❌ 跟卖类目解析全部失败（Fallback CREATE 需要类目）: "
                         "Widget ID=%s, breadcrumb=%s", dc_fallback, cat_path or "(empty)")
            return {
                "error_message": f"类目解析失败: Widget ID={dc_fallback}, "
                                 f"breadcrumb={cat_path or '(empty)'}, 门控仲裁也未通过",
                "failed_stage": "follow_sell_import",
                "extensions": extensions,
            }

    if not ozon_title:
        ozon_title = draft.get("ozon_title", "") or draft.get("title", "") or "Товар"
    comp_name = ozon_title
    orig_images = ozon_images[:10]
    variants = []
    item_id = draft.get("item_id", ozon_product_id)
    # ⚠️ v0.31 T1: 跟卖属性合并链 — draft.ozon_attributes(RU 名→attr_id→dict_id 字典解析)
    # → draft.attributes(1688 中文) → 硬编码兜底。旧代码把硬编码 5 属性当竞品数据
    # （品牌85/5076+产地4389+型号9048+数量8962），真实竞品属性(draft.ozon_attributes)全丢。
    # 合并链在 attr_defaults.build_follow_attr_merge: 字典属性 /values/search 解析 dict_id,
    # 竞品文本值无字典匹配 → 跳过(绝不注入原文)；硬编码 5 属性仅作双无兜底。
    try:
        from utils.attr_defaults import build_follow_attr_merge
        final_attrs = build_follow_attr_merge(
            draft=draft,
            schema=attrs_schema,
            dc_id=dc_id,
            tp_id=tp_id,
            client_id=client_id,
            api_key=api_key,
            product_id=ozon_product_id,
        )
    except Exception as _merge_err:
        logger.warning("跟卖属性合并链异常，回退硬编码 5 属性: %s", _merge_err)
        final_attrs = [
            {"id": 85, "values": [{"dictionary_value_id": BRAND_DICT_ID, "value": "Нет бренда"}]},
            {"id": 5076, "values": [{"dictionary_value_id": BRAND_DICT_ID, "value": "Нет бренда"}]},
            {"id": 4389, "values": [{"dictionary_value_id": CHINA_DICT_ID, "value": "Китай"}]},
            {"id": 9048, "values": [{"dictionary_value_id": 0, "value": str(ozon_product_id)}]},
            {"id": 8962, "values": [{"dictionary_value_id": 0, "value": "1"}]},
        ]
    up_status = "pending"

    logger.info("✅ 跟卖 v5: product_id=%s, cat=%s/%s, imgs=%d",
                product_id, dc_id, tp_id, len(ozon_images))

    return {
        "progress_counter": 3,
        "product_id": product_id or None,
        "competitor_price": comp_price,
        "competitor_name": comp_name,
        "description_category_id": dc_id,
        "type_id": tp_id,
        "original_images": orig_images,
        "variants": variants,
        "item_id": item_id,
        "final_attributes": final_attrs,
        "attributes_schema": attrs_schema,
        # fix/image-ref-pollution R2: 信封 extensions 透传（follow_sell/
        # follow_type/competitor_ref_images），供 prepare 跟卖判定与生图参考分线
        "extensions": extensions,
        "upload_status": up_status,
        "category_missing": category_missing,
        "import_submitted": import_submitted,
        "import_task_id": import_task_id_val,
        "error_message": error_msg,
        "failed_stage": failed_stg,
    }


def _detect_language(text: str) -> str:
    """检测文本语言 → 类目搜索语言"""
    if any('\u4e00' <= c <= '\u9fff' for c in text):
        return "ZH_HANS"
    return "RU"  # 默认俄语（Cyrillic）


# ══ v0.69 P-B: 跟卖类目门控仲裁 ══
# wave D 实证（docs/TEST-v067-wave-plan.md）：本节点内部 pg_trgm sim≥0.5 直采
# （俄语源词 → 医用 Рециркулятор sim=0.500 恰好过线）+ 1688 类目 jieba 直采
# （单字「取暖」sim=0.70 → 配件类）完全绕过 assemble 的 R2b 四段闸/R1——
# 三单跨域错放全经此通道。模糊解析改为「候选池 → R1 剔除 → R2b 仲裁池 →
# LLM vision 仲裁 → 四段判定 → R1 定稿 veto」，任一环不过 → 类目置空交由
# assemble 全闸链，绝不保底直采。

def _gate_search_terms(ozon_cat: dict, source: dict, extra=None) -> list[str]:
    """门控搜索词：面包屑/1688 来源类目只取末段+倒数第二段。

    整段路径已实证喂噪音（wave D：完整 RU 路径 pg_trgm 命中 0 或纯噪音），
    末段词才是有效名称先验；品牌末段保留（树查自然落空，不致错配）。
    """
    terms: list[str] = []
    for path in (str((ozon_cat or {}).get("category_path") or ""),
                 str((source or {}).get("source_category") or "")):
        segs = [s.strip() for s in path.split(">") if s.strip()]
        for seg in segs[-2:]:
            if seg and seg not in terms:
                terms.append(seg)
    for e in (extra or []):
        e = str(e or "").strip()
        if e and e not in terms:
            terms.append(e)
    return terms


def _term_searchable(term: str) -> bool:
    """单字核心 token 不搜（wave D「单字兜底直采」通道关闭）；修饰词全剥离（成人帽）也不搜。"""
    from utils.ozon_category_query import _strip_modifier_substrings
    t = str(term or "").strip()
    if not t:
        return False
    for tok in t.split():
        core = _strip_modifier_substrings(tok)
        if core and len(core) >= 2:
            return True
    return False


def _gated_category_arbitration(terms, source_words: str, draft: dict, state,
                                query=None) -> tuple[str, str]:
    """模糊类目候选的门控仲裁：返回采纳 (dc, tp)，不过则 ("", "") 置空（绝不保底直采）。"""
    # 调用时点导入（非模块级）——测试 patch asm 属性后此处才能取到 mock
    from graphs.nodes.assemble_ozon_product_node import (
        _build_r2b_confirm_pool,
        _llm_rank_categories,
        _r1_veto,
        _r2b_confirm_adoption,
    )
    from utils.ozon_category_query import sensitive_candidate_filter

    if query is None:
        try:
            from utils.ozon_category_query import get_category_query
            query = get_category_query()
        except Exception as e:
            logger.warning("类目门控: 查询器不可用: %s", e)
            return "", ""
    if query is None:
        return "", ""

    signal = str(source_words or "").strip()
    pool: list = []
    seen: set = set()
    for term in (terms or []):
        if not _term_searchable(term):
            continue
        lang = _detect_language(term)
        try:
            results = query.search_nodes(term, top_k=5, node_type="type", language=lang)
        except Exception as e:
            logger.warning("类目门控搜索失败(term=%s): %s", term, e)
            results = []
        for c in results or []:
            key = (str(c.get("description_category_id")), str(c.get("type_id")))
            if key in seen:
                continue
            seen.add(key)
            pool.append(c)
    if not pool:
        logger.info("类目门控: 搜索词 %s 无候选，类目置空", list(terms or []))
        return "", ""
    pool = sensitive_candidate_filter(pool, signal)
    r2b_pool = _build_r2b_confirm_pool(pool, pool[0], signal)
    confirm = _llm_rank_categories(r2b_pool, signal, draft or {}, state,
                                   context="follow_sell_import 类目门控仲裁")
    # ✅ v0.69 T0.3: _r2b_confirm_adoption 返回 4 元组（末位 adopt_meta：跨大类
    # 高置信旗标等）——follow 门控只关心放行与否，meta 留 assemble 层定稿透传；
    # 门控不过置空汇入 assemble 全闸（入箱只在 assemble 层做一次，此处不建 draft）。
    overlap, vision_ok, reason, _adopt_meta = _r2b_confirm_adoption(
        confirm, r2b_pool, signal, draft or {}, query)
    if not overlap and not vision_ok:
        logger.warning("🛑 类目门控: 仲裁未通过(%s)，类目置空交由 assemble 全闸链", reason)
        return "", ""
    dc = str((confirm or {}).get("description_category_id") or "")
    tp = str((confirm or {}).get("type_id") or "")
    if not dc.isdigit() or not tp.isdigit():
        logger.warning("🛑 类目门控: 仲裁结果非数字 ID（dc=%s tp=%s），置空", dc, tp)
        return "", ""
    if _r1_veto({"category_path": (confirm or {}).get("full_path")
                 or (confirm or {}).get("category_path") or "",
                 "description_category_id": dc}, signal):
        logger.warning("🛑 类目门控: R1 敏感否决（%s/%s %s）",
                       dc, tp, (confirm or {}).get("node_name", ""))
        return "", ""
    logger.info("✅ 类目门控仲裁通过: %s/%s %s (%s)",
                dc, tp, (confirm or {}).get("node_name", ""), reason)
    return dc, tp


def _verify_category_schema(client_id: str, api_key: str, dc: str, tp: str) -> bool:
    """v0.26 权威类目自校验：dc+type 组合是否有效（schema API 200 且返回属性列表）。

    用于区分「what_to_sell 权威组合」（有效，信任）与「Widget 空间面包屑 ID」
    （数字但无效，如盘子 dc=102080114 报 'category ... is not found'）。
    """
    try:
        _resp = req.post(
            "https://api-seller.ozon.ru/v1/description-category/attribute",
            headers={"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"},
            json={"description_category_id": int(dc), "type_id": int(tp), "language": "RU"},
            timeout=15,
        )
        if _resp.status_code == 200:
            result = _resp.json().get("result", [])
            return isinstance(result, list)  # 空列表也算有效（类目可能无属性）
        return False
    except Exception:
        return False


def _resolve_category_by_id(dc_id: int, type_name_hint: str = "", token: str = "") -> tuple[str, str]:
    """数字 description_category_id → 查 category_tree_nodes 获取 type_id

    v0.69 P-B: **只保留确定性解析**（路径精配 → dc+唯一 type），原模糊 pg_trgm
    多候选/LLM 翻译兜底整体移除——wave D 实证该通道直采噪音（医用 Рециркулятор
    sim=0.500 过线）完全绕过 R2b/R1 闸。模糊场景统一交 `_gated_category_arbitration`。
    - Widget API 和 Seller API 使用不同的 ID 空间，数字直查经常失败；
      此时用面包屑完整路径 `get_node_by_full_path` **确定性精配**。
    - 修 v0.26 眉笔类 bug：`get_node_by_description_category_id` 取 dc 下**第一个** type
      会覆写权威 type（dc=17028990 → 93409 覆写 93418）。改为：该 dc 下 type **唯一**才用，
      多 type 且无路径可消歧 → 不盲取（返回空，交由调用方候选/阻断）。
    """
    try:
        from utils.ozon_category_query import get_category_query
        query = get_category_query()

        # v0.63 ① 路径确定性精配优先（页面面包屑完整路径 → Seller 树）
        if type_name_hint:
            _node = query.get_node_by_full_path(type_name_hint)
            if _node:
                dc_str = str(_node["description_category_id"])
                tp_str = str(_node["type_id"])
                logger.info("✅ 路径精配(确定性): '%s' → dc=%s type=%s (%s)",
                            str(type_name_hint)[:60], dc_str, tp_str, _node.get("full_path", "")[:60])
                return dc_str, tp_str

        # v0.63 ② 数字 dc 直查：仅在 dc 下 type 唯一时采用，**不取第一个**
        node = query.get_node_by_description_category_id(dc_id)
        if node:
            _types = query.get_types_under(int(node["description_category_id"]))
            if len(_types) == 1:
                dc_id_str = str(_types[0]["description_category_id"])
                type_id_str = str(_types[0]["type_id"])
                logger.info("✅ 数字 ID 直查(唯一 type): %d → dc=%s type=%s", dc_id, dc_id_str, type_id_str)
                return dc_id_str, type_id_str
            elif len(_types) > 1:
                logger.warning("⚠️ dc=%d 下存在 %d 个 type，无路径可消歧，不盲取第一个（防 v0.26 覆写）",
                               dc_id, len(_types))
                # 有 hint 已试过路径精配未命中；无 hint → 交由调用方候选/阻断，不返回错误 type
                return "", ""
            # 无 type（category 节点）
            return "", ""
    except Exception as e:
        logger.warning("数字 ID 直查失败: %s", e)
    # v0.69: 确定性失败一律返回空——模糊解析由门控仲裁接手
    return "", ""
