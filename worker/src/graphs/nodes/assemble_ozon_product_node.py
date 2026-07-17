"""
统一商品组装节点 — 替代 4 节点管线

将 category_lookup + attributes_fetch + attributes_llm + attributes_learning
合并为单一 Python 函数，消除跨节点状态传递 bug。

流程:
  1. PG 缓存查询 → pg_trgm 搜索 top-15 候选类目
  2. LLM 类目匹配 → 从候选中选出 description_category_id + type_id
  3. PG 缓存查询 → 获取属性 schema + 字典值
  4. LLM 完整组装 → 输出完整 /v3/product/import items JSON
  5. 解析校验 → 写入 state 兼容下游节点

替代节点:
  - category_lookup_node
  - attributes_fetch_node
  - attributes_llm_node
  - attributes_learning_node
"""

import os
import json
import time
import logging
import re
import requests
from typing import Any, Optional
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from graphs.state import GlobalState
from utils.mxou_llm import call_mxou_chat_api
from utils.progress_logger import ProgressLogger
from utils.ozon_category_query import get_category_query, OzonCategoryQuery
from utils.http_session import session

logger = logging.getLogger(__name__)

# ==================== 常量 ====================

# 品牌属性ID列表（按优先级）
BRAND_ATTRIBUTE_IDS = [85, 5076]

# "无品牌" 字典值
NO_BRAND_DICT_ID = 126745801
NO_BRAND_VALUE = "Нет бренда"

# 原产国（中国）
COUNTRY_ATTR_ID = 4389
CHINA_DICT_ID = 90296
CHINA_VALUE = "Китай"

# Ozon 强制属性
FORCE_ATTR_9048 = 9048   # 变体绑定名
FORCE_ATTR_8229 = 8229   # 类型名称
FORCE_ATTR_4191 = 4191   # 完整描述
FORCE_ATTR_4180 = 4180   # 短描述/关键字
FORCE_ATTR_4958 = 4958   # 适用对象（部分类目）
FORCE_ATTR_8962 = 8962   # 件数（部分类目）
FORCE_ATTR_23171 = 23171 # hashtag 标签（部分类目）

# 分类名属性（8229 的替代）
TYPE_NAME_ATTR_IDS = [8229]

# 集合属性（values 数组可包含多个元素）
COLLECTION_ATTR_IDS = {9048, 23171}


def assemble_ozon_product_node(
    state: GlobalState,
    config: RunnableConfig,
    runtime: Runtime,
) -> dict[str, Any]:
    """
    统一商品组装节点。

    输入: GlobalState（含 draft, token, ozon_client_id, ozon_api_key, pricing_info）
    输出: dict（被 LangGraph 合并到 GlobalState）
    """
    progress = ProgressLogger()
    progress.log_node_start("assemble_ozon_product", "统一商品组装")
    
    # ✅ 自修复：如果是重试（类目匹配回退），递增计数器
    retry_count = getattr(state, 'assembly_retry_count', 0)
    if retry_count > 0:
        logger.info(f"   🔄 组装重试 (第{retry_count}次)")
    progress.log_node_action("Step 1: 类目匹配...")

    draft: dict[str, Any] = state.draft or {}
    token: str = state.token or ""
    ozon_client_id: str = str(state.ozon_client_id or "")
    ozon_api_key: str = state.ozon_api_key or ""
    currency_code: str = state.currency_code or "RUB"

    title: str = draft.get("title", "")
    description: str = draft.get("description", "")
    images: list[str] = draft.get("images", []) or []
    weight_grams: int = draft.get("weight", 100)
    dimensions: dict[str, int] = draft.get("dimensions", {}) or {}
    purchase_cost: float = float(draft.get("purchase_cost", 0) or 0)
    sku_id: str = draft.get("sku_id", "")
    attributes_1688: dict[str, Any] = draft.get("attributes", {}) or {}
    variants: list[dict[str, Any]] = draft.get("variants", []) or []

    # 定价信息（来自 pricing_node）
    pricing_info: dict[str, Any] = state.pricing_info if hasattr(state, 'pricing_info') else {}
    price_rub: str = str(pricing_info.get("price", "1000"))
    old_price_rub: str = str(pricing_info.get("old_price", "1500"))

    if not title:
        logger.error("产品标题为空，无法进行类目匹配")
        return {"error_message": "产品标题为空，无法进行类目匹配",
                "assembly_retry_count": (getattr(state, 'assembly_retry_count', 0) or 0) + 1}

    # 初始化查询助手
    query = get_category_query()

    # =====================================================
    # Step 1: 类目匹配
    # =====================================================
    logger.info(f"🔍 Step 1: 类目匹配 — 产品: {title[:60]}")

    # 1a. 提取搜索关键词（jieba 分词 + 1688 类目面包屑）
    keywords = _extract_keywords(title, description, attributes_1688)
    
    # 追加 1688 类目面包屑作为搜索词（与 Ozon ZH_HANS 类目名直接匹配）
    source_category = draft.get("source_category", "")
    source_keywords = ""
    if source_category:
        # 清理：去掉 ">" 分隔符，每级类目名独立搜索
        cat_terms = [t.strip() for t in source_category.replace(">", " ").split() if len(t.strip()) >= 2]
        if cat_terms:
            # 中文同义词映射（1688 用语 → Ozon ZH_HANS 用语）
            # ⚠️ 原则：只用特异性词，不用泛化词（如"宠物"匹配几百个类目，稀释信号）
            _CN_SYNONYMS = {
                "喷壶": "喷雾瓶 喷雾器 浇花壶",
                "洒水壶": "浇花壶 喷雾器",
                "浇花壶": "喷雾瓶 喷雾器",
                "加仑盆": "花盆 塑料花盆",
                # 宠物：1688 用"猫狗/猫猫"，Ozon 用"宠物"
                "猫猫玩具": "宠物玩具",
                "猫狗玩具": "宠物玩具",
                "逗猫棒": "宠物玩具",
                "猫玩具": "宠物玩具",
                "猫猫食具": "宠物碗 宠物餐具",
                "猫狗食具": "宠物碗 宠物餐具",
                # 园艺
                "园艺工具": "园艺工具 花园工具",
                "园林资材": "园艺工具 花园",
                # 手套：过滤掉"防护"（匹配消防/建筑），保留"园艺"
                "通用手套": "园艺手套",
                "手部防护": "园艺手套",
            }
            expanded_terms = list(cat_terms)
            for term in cat_terms:
                if term in _CN_SYNONYMS:
                    for syn in _CN_SYNONYMS[term].split():
                        if syn not in expanded_terms:
                            expanded_terms.append(syn)
            source_keywords = " ".join(expanded_terms)
            keywords = source_keywords + " " + keywords
        logger.info(f"   关键词（含1688类目）: {keywords}")
    else:
        logger.info(f"   关键词: {keywords}")

    # 1b. 搜索策略：source_keywords 优先（高精度），不够再扩大
    MIN_CANDIDATES = 1  # 有 source_category 时，1 个精确结果 > 30 个噪声结果
    
    # 先用 source_keywords 做精确搜索
    if source_keywords:
        candidates = query.search_nodes(source_keywords, top_k=15, node_type="type")
        if candidates and len(candidates) >= MIN_CANDIDATES:
            logger.info(f"   ✅ 使用 source_category 精确搜索：{len(candidates)} 个候选")
        else:
            # source_keywords 不够 → 扩大到全关键词
            candidates = query.search_nodes(keywords, top_k=30, node_type="type")
    else:
        candidates = query.search_nodes(keywords, top_k=30, node_type="type")
    
    if not candidates:
        # 回退：不过滤 node_type
        if source_keywords:
            candidates = query.search_nodes(source_keywords, top_k=15, node_type=None)
            if not candidates or len(candidates) < MIN_CANDIDATES:
                candidates = query.search_nodes(keywords, top_k=30, node_type=None)
        else:
            candidates = query.search_nodes(keywords, top_k=30, node_type=None)

    if not candidates:
        # 缓存为空，调用 Ozon API 获取类目树
        logger.warning("类目缓存为空，调用 Ozon API 获取类目树...")
        tree_data = _fetch_category_tree_from_ozon(ozon_client_id, ozon_api_key)
        if tree_data:
            # 缓存并同步
            from utils.local_db_manager import LocalDBManager
            local_db = LocalDBManager()
            local_db.set_category_cache(ozon_client_id, tree_data)
            local_db.sync_category_tree_nodes(tree_data)
            # 重试搜索（优先 source_keywords）
            if source_keywords:
                candidates = query.search_nodes(source_keywords, top_k=15, node_type="type")
                if not candidates or len(candidates) < MIN_CANDIDATES:
                    candidates = query.search_nodes(keywords, top_k=30, node_type="type")
            else:
                candidates = query.search_nodes(keywords, top_k=30, node_type="type")
            if not candidates:
                candidates = query.search_nodes(keywords, top_k=30, node_type=None)

    if not candidates:
        logger.error("❌ 类目搜索无结果（Ozon API 也无数据）")
        return {"error_message": "类目匹配失败：无候选类目",
                "assembly_retry_count": (getattr(state, 'assembly_retry_count', 0) or 0) + 1}

    logger.info(f"   pg_trgm 返回 {len(candidates)} 个候选")

    # 1c. LLM 从候选中选最佳类目
    category_result = _llm_match_category(title, description, attributes_1688, candidates, token)

    if not category_result:
        # 回退：取 similarity 最高的候选
        best = candidates[0]
        category_result = {
            "description_category_id": best["description_category_id"],
            "type_id": best["type_id"],
            "category_path": best["full_path"],
            "confidence": "low",
            "reason": f"LLM 失败，回退到最高相似度候选: {best['node_name']}",
        }
        logger.warning(f"   LLM 类目匹配失败，回退到: {best['full_path']}")

    description_category_id: int = int(category_result["description_category_id"])
    type_id: int = int(category_result["type_id"])
    category_path: str = category_result.get("category_path", "")
    
    # ✅ 修正 LLM 输出：LLM 有时把 type_id 填到 description_category_id
    # 从 candidates 中查找正确的 description_category_id
    if description_category_id == type_id or description_category_id <= 0:
        for c in candidates:
            if int(c.get("type_id", 0)) == type_id and int(c.get("description_category_id", 0)) > 0:
                description_category_id = int(c["description_category_id"])
                logger.info(f"   🔧 修正 description_category_id: {type_id} → {description_category_id}")
                break
    
    logger.info(f"   ✅ 类目匹配: [{description_category_id}/{type_id}] {category_path}")

    # =====================================================
    # Step 1d: 验证类目对（防止无效 category_id/type_id 导致后续 400）
    # =====================================================
    tried_category_ids: set = {(description_category_id, type_id)}
    MAX_CATEGORY_RETRIES = 5

    # =====================================================
    # Step 2: 获取属性 Schema（PG 缓存优先，Ozon API 回退）
    # =====================================================
    progress.log_node_action(f"Step 2: 获取属性 Schema — category={description_category_id}, type={type_id}")

    attr_schema = query.get_attribute_schema(description_category_id, type_id)
    if attr_schema and isinstance(attr_schema, dict) and attr_schema.get("result"):
        attr_list: list[dict[str, Any]] = attr_schema["result"]
        logger.info(f"   ✅ PG 缓存命中: {len(attr_list)} 个属性")
    else:
        # Ozon API 回退（带候选类目自动回退）
        logger.info("   PG 缓存未命中，调用 Ozon API...")
        attr_list = _fetch_attribute_schema_from_ozon(
            ozon_client_id, ozon_api_key,
            description_category_id, type_id
        )
        
        # ✅ 自修复：API 400 时自动尝试候选类目
        retry_idx = 0
        while not attr_list and retry_idx < MAX_CATEGORY_RETRIES:
            # 从候选列表中找下一个未尝试的类目对
            fallback_found = False
            for c in candidates:
                cid = int(c.get("description_category_id", 0))
                tid = int(c.get("type_id", 0))
                if cid > 0 and tid > 0 and (cid, tid) not in tried_category_ids:
                    tried_category_ids.add((cid, tid))
                    logger.warning(
                        f"   🔄 类目对 [{description_category_id}/{type_id}] 无效，"
                        f"回退尝试候选 [{cid}/{tid}] {c.get('full_path', '')}"
                    )
                    description_category_id = cid
                    type_id = tid
                    category_path = c.get("full_path", "")
                    attr_list = _fetch_attribute_schema_from_ozon(
                        ozon_client_id, ozon_api_key, cid, tid
                    )
                    if attr_list:
                        logger.info(f"   ✅ 回退类目对有效: [{cid}/{tid}] {category_path}")
                    fallback_found = True
                    retry_idx += 1
                    break
            if not fallback_found:
                break
        
        if not attr_list:
            logger.error(f"❌ 属性 Schema 获取失败（已尝试 {len(tried_category_ids)} 个类目对）")
            return {"error_message": f"属性 Schema 获取失败: 尝试了 {len(tried_category_ids)} 个类目对均无效",
                    "assembly_retry_count": (getattr(state, 'assembly_retry_count', 0) or 0) + 1}
        logger.info(f"   ✅ Ozon API 返回: {len(attr_list)} 个属性")

    # 标记必填属性
    required_attrs = [a for a in attr_list if a.get("is_required", False)]
    logger.info(f"   其中 {len(required_attrs)} 个必填属性")

    # =====================================================
    # Step 3: 预加载字典值（PG 缓存优先，Ozon API 回退）
    # =====================================================
    logger.info("📖 Step 3: 预加载字典值")

    dict_lookup: dict[int, list[dict[str, Any]]] = {}
    for attr in attr_list:
        dict_id = attr.get("dictionary_id", 0)
        if dict_id and dict_id > 0:
            attr_id = int(attr.get("id", 0))
            values = query.get_dictionary_values(attr_id, description_category_id, type_id)
            if not values or (isinstance(values, list) and len(values) == 0):
                # PG 缓存未命中 → Ozon API 回退
                logger.info(f"   PG 缓存未命中 attr={attr_id}，调用 Ozon API...")
                values = _fetch_dict_values_from_ozon(
                    ozon_client_id, ozon_api_key,
                    description_category_id, type_id, attr_id
                )
                # 写入 PG 缓存（供后续使用）
                if values:
                    _cache_dict_values(attr_id, description_category_id, type_id, values)
            if values and isinstance(values, list) and len(values) > 0:
                dict_lookup[attr_id] = values
            elif isinstance(values, dict) and values.get("result"):
                dict_lookup[attr_id] = values["result"]

    dict_attr_count = sum(1 for a in attr_list if a.get("dictionary_id", 0) > 0)
    cached_dict_count = len(dict_lookup)
    logger.info(f"   字典属性: {dict_attr_count} 个, PG 缓存命中: {cached_dict_count} 个")

    # =====================================================
    # Step 4: 确定性组装 items（不调用 LLM）
    # =====================================================
    logger.info("🔧 Step 4: 确定性构建 /v3/product/import items JSON")

    items = _build_items_deterministically(
        draft=draft,
        description_category_id=description_category_id,
        type_id=type_id,
        attr_list=attr_list,
        dict_lookup=dict_lookup,
        images=images,
        ozon_client_id=ozon_client_id,
        ozon_api_key=ozon_api_key,
        weight_grams=weight_grams,
        dimensions=dimensions,
        price_rub=price_rub,
        old_price_rub=old_price_rub,
        currency_code=currency_code,
        token=token,
    )

    if not items:
        logger.error("❌ 确定性组装失败，返回空 items")
        return {
            "error_message": "确定性组装失败：未生成有效的 items",
            "description_category_id": str(description_category_id),
            "type_id": str(type_id),
            "attributes_schema": attr_list,
            "dictionary_values": {str(k): v for k, v in dict_lookup.items()},
            "final_attributes": [],
            "llm_attributes": [],
            "learned_attributes": {},
            "ozon_payloads": [],
        }

    logger.info(f"   ✅ 确定性生成 {len(items)} 个 item(s)")

    # =====================================================
    # Step 5: 解析 + 校验 + 补充
    # =====================================================
    logger.info("🔍 Step 5: 解析校验 LLM 输出")

    items = _validate_and_enrich_items(
        items=items,
        attr_list=attr_list,
        dict_lookup=dict_lookup,
        images=images,
        ozon_client_id=ozon_client_id,
        ozon_api_key=ozon_api_key,
        description_category_id=description_category_id,
        type_id=type_id,
        weight_grams=weight_grams,
        dimensions=dimensions,
    )

    # =====================================================
    # Step 6: 提取 final_attributes（兼容下游节点）
    # =====================================================
    # 提取第一个 item 的属性作为 final_attributes（兼容 prepare_ozon_upload）
    final_attributes: list[dict[str, Any]] = []
    if items and items[0].get("attributes"):
        for attr in items[0]["attributes"]:
            for v in (attr.get("values") or []):
                final_attributes.append({
                    "attribute_id": attr["id"],
                    "value": v.get("value", ""),
                    "dictionary_value_id": v.get("dictionary_value_id", 0),
                    "source": "llm",
                })

    # 为兼容 learning_record_node，同时设置 llm_attributes
    llm_attributes = final_attributes

    # 提取 LLM 生成的俄语标题（供 prepare_ozon_upload 使用）
    llm_name = ""
    if items and items[0].get("name"):
        llm_name = str(items[0]["name"])[:500]

    # =====================================================
    # Step 6.5: 跨类目一致性校验
    # =====================================================
    # 对比 LLM 生成的俄语标题与分配的 Ozon 类目路径，检测明显不匹配
    _check_category_consistency(llm_name, category_path, description_category_id, type_id)

    # =====================================================
    # Step 7: 返回结果 dict（LangGraph 自动合并到 GlobalState）
    # =====================================================
    progress.log_node_success(f"类目={category_path}, 属性={len(final_attributes)}个, items={len(items)}个")

    logger.info(f"✅ 统一组装完成: 类目=[{description_category_id}/{type_id}], 属性={len(final_attributes)}个, items={len(items)}个")

    return {
        "description_category_id": str(description_category_id),
        "type_id": str(type_id),
        "attributes_schema": attr_list,
        "dictionary_values": {str(k): v for k, v in dict_lookup.items()},  # ← 键必须是 str（PrepareOzonUploadInput 要求）
        "final_attributes": final_attributes,
        "llm_attributes": llm_attributes,
        "learned_attributes": {},
        "ozon_payloads": [{"items": items}],
        # 传递 LLM 生成的俄语标题
        "name": llm_name,
    }


# ==================== 辅助函数 ====================


def _extract_keywords(title: str, description: str, attributes: dict[str, Any]) -> str:
    """从产品数据中提取搜索关键词（使用 jieba 分词+词性标注，取所有有意义词）"""
    import re
    try:
        import jieba
        import jieba.posseg as pseg
    except ImportError:
        jieba = None
        pseg = None

    # 清理标题：取前 100 字符（足够覆盖产品名）
    clean = re.sub(r'[^\u4e00-\u9fff\w]', ' ', title)[:100]

    if jieba and pseg:
        # 词性标注分词，按优先级排序
        # 名词 > 动名词 > 形容词 > 其他（去噪）
        NOISE_WORDS = {'无', '手动', '全部', '展开', '参数', '厂家', '批发', '一件', '代发',
                       '跨境', '货源', '直销', '新款', '爆款', '热卖', '促销', '一件代发'}
        word_scores: list[tuple[str, float]] = []
        
        try:
            for word, flag in pseg.cut(clean):
                w = word.strip()
                if len(w) < 2:
                    continue
                if w in NOISE_WORDS:
                    continue
                # 词性权重：名词(n/ns/nr/nt/nz) = 3.0, 动名词(vn) = 2.0, 
                #           形容词(a/an) = 1.5, 其他实词 = 1.0
                if flag.startswith('n'):
                    score = 3.0
                elif flag == 'vn':
                    score = 2.0
                elif flag.startswith('a'):
                    score = 1.5
                elif flag in ('v', 'vd', 'vi'):
                    score = 1.0
                else:
                    score = 0.5
                word_scores.append((w, score))
        except Exception:
            # pseg 可能在某些平台上不可用，回退到普通分词
            words = list(jieba.cut(clean))
            meaningful = [w.strip() for w in words 
                         if len(w.strip()) >= 2 and w.strip() not in NOISE_WORDS]
            return ' '.join(meaningful[:8])
        
        # 按分数降序排列，取前 8 个（或全部如果不足 8 个）
        word_scores.sort(key=lambda x: x[1], reverse=True)
        top_words = [w for w, _ in word_scores[:8]]
        
        if not top_words:
            # 如果过滤后为空，回退取所有 >=2 字的词
            words = list(jieba.cut(clean))
            meaningful = [w.strip() for w in words 
                         if len(w.strip()) >= 2 and w.strip() not in NOISE_WORDS]
            return ' '.join(meaningful[:8])
        
        return ' '.join(top_words)
    else:
        # 回退：取前 20 个字符
        return clean[:20]


def _check_category_consistency(
    llm_name: str,
    category_path: str,
    description_category_id: int,
    type_id: int,
) -> None:
    """
    跨类目一致性校验：对比 LLM 生成的俄语产品名与分配的 Ozon 类目名称。
    
    如果二者完全不相关，很可能是类目匹配错误（如"园艺工具"→"迷你打印机"），
    记录 WARNING 日志帮助快速发现此类问题。
    """
    if not llm_name or not category_path:
        return
    
    # 提取类目路径中的关键俄语词（取最后两级，通常是最具体的分类）
    path_parts = [p.strip() for p in category_path.split(">") if p.strip()]
    leaf_keywords = set()
    for part in path_parts[-2:]:  # 最后两级
        for word in part.lower().split():
            if len(word) >= 3:
                leaf_keywords.add(word)
    
    # 检查产品名中是否包含任一类目关键词
    name_lower = llm_name.lower()
    overlap = [kw for kw in leaf_keywords if kw in name_lower]
    
    if not overlap and leaf_keywords:
        logger.warning(
            f"⚠️ 跨类目一致性警告：产品名「{llm_name[:80]}」与类目「{category_path}」"
            f" 无共同关键词。类目词: {leaf_keywords}。"
            f" 这可能导致 Ozon 审核拒绝（DESCRIPTION_DECLINE）。"
            f" 建议检查类目匹配是否正确 (desc_cat_id={description_category_id}, type_id={type_id})。"
        )
    elif overlap:
        logger.info(f"✅ 跨类目一致性通过：产品名与类目「{category_path}」匹配关键词: {overlap}")


def _llm_match_category(
    title: str,
    description: str,
    attributes: dict[str, Any],
    candidates: list[dict[str, Any]],
    token: str,
) -> Optional[dict[str, Any]]:
    """LLM 从候选类目列表中选出最佳匹配"""
    try:
        workspace = os.getenv("APP_WORKSPACE_PATH", "/app")
        cfg_path = os.path.join(workspace, "config/category_match_v2_cfg.json")

        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        llm_cfg = cfg.get("config", {})
        model_id = llm_cfg.get("model", "deepseek-v4-flash")
        sp_template = cfg.get("sp", "")
        up_template = cfg.get("up", "")

        sp_tpl = Template(sp_template)
        up_tpl = Template(up_template)

        system_prompt = sp_tpl.render({})

        # 准备模板变量
        attr_flat = {}
        if attributes:
            for k, v in attributes.items():
                if isinstance(v, (str, int, float)):
                    attr_flat[k] = str(v)

        user_prompt = up_tpl.render({
            "title": title,
            "description": description[:500] if description else "",
            "attributes": attr_flat,
            "candidates": candidates,
        })

        resp = call_mxou_chat_api(
            token=token,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model_id,
            temperature=0.0,
            max_tokens=1024,
        ) or ""

        if not resp.strip():
            logger.error("LLM 类目匹配返回空")
            return None

        # 清理 JSON
        resp = resp.replace("```json", "").replace("```", "").strip()
        # 尝试提取 JSON 对象
        match = re.search(r'\{[^{}]*"description_category_id"[^{}]*\}', resp, re.DOTALL)
        if match:
            resp = match.group(0)

        result = json.loads(resp)
        logger.info(f"   LLM 类目匹配: {result.get('category_path', '')} (confidence={result.get('confidence', '?')})")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"LLM 类目匹配 JSON 解析失败: {e}, raw={resp[:200]}")
        return None
    except Exception as e:
        logger.error(f"LLM 类目匹配异常: {e}")
        return None


def _fetch_attribute_schema_from_ozon(
    ozon_client_id: str,
    ozon_api_key: str,
    description_category_id: int,
    type_id: int,
) -> list[dict[str, Any]]:
    """从 Ozon API 获取属性 Schema（回退路径）"""
    try:
        url = "https://api-seller.ozon.ru/v1/description-category/attribute"
        headers = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "description_category_id": description_category_id,
            "type_id": type_id,
            "language": "ZH_HANS",
        }
        resp = session.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", [])
        logger.info(f"   Ozon API 返回 {len(result)} 个属性")
        return result
    except Exception as e:
        logger.error(f"Ozon 属性 API 调用失败: {e}")
        return []


def _build_items_deterministically(
    draft: dict[str, Any],
    description_category_id: int,
    type_id: int,
    attr_list: list[dict[str, Any]],
    dict_lookup: dict[int, list[dict[str, Any]]],
    images: list[str],
    ozon_client_id: str,
    ozon_api_key: str,
    weight_grams: int,
    dimensions: dict[str, int],
    price_rub: str,
    old_price_rub: str,
    currency_code: str,
    token: str,
) -> list[dict[str, Any]]:
    """
    确定性构建 /v3/product/import items JSON（不调用 LLM）。
    
    属性映射策略：
    1. 用 Ozon 属性的中文名匹配 1688 产品属性（draft.attributes）
    2. 字典属性：在 dict_lookup 中查找匹配的值，回退到 Ozon search API
    3. 自由文本属性：直接填入 1688 属性值（prepare 节点会用 LLM 翻译成俄语）
    4. 无匹配的必填属性：留空，由 _validate_and_enrich_items 填默认值
    """
    # ── 构建属性索引 ──
    attr_by_id: dict[int, dict[str, Any]] = {int(a["id"]): a for a in attr_list if "id" in a}
    
    # ── 1688 产品属性 ──
    product_attrs: dict[str, str] = {}
    raw_attrs = draft.get("attributes", {})
    if isinstance(raw_attrs, dict):
        for k, v in raw_attrs.items():
            product_attrs[str(k).strip()] = str(v).strip()
    
    # ── 属性名匹配辅助函数 ──
    def _match_product_attr(ozon_attr_name: str) -> Optional[str]:
        """用 Ozon 属性中文名匹配 1688 产品属性值"""
        name_lower = ozon_attr_name.lower().strip()
        # 精确匹配
        for pa_name, pa_val in product_attrs.items():
            if pa_name.lower() == name_lower:
                return pa_val
        # 包含匹配
        for pa_name, pa_val in product_attrs.items():
            if name_lower in pa_name.lower() or pa_name.lower() in name_lower:
                return pa_val
        # 关键词重叠匹配
        ozon_words = set(name_lower.split())
        for pa_name, pa_val in product_attrs.items():
            pa_words = set(pa_name.lower().split())
            if ozon_words & pa_words:
                return pa_val
        return None
    
    def _find_dict_value(attr_id: int, product_value: str) -> tuple[int, str]:
        """在字典值中查找匹配，返回 (dictionary_value_id, value)"""
        if not product_value:
            return (0, "")
        values = dict_lookup.get(attr_id, [])
        if not values:
            return (0, product_value)
        # 精确匹配
        pv_lower = product_value.lower().strip()
        for v in values:
            if isinstance(v, dict):
                if str(v.get("value", "")).lower().strip() == pv_lower:
                    return (v.get("id", 0), str(v.get("value", "")))
        # 包含匹配
        for v in values:
            if isinstance(v, dict):
                vv = str(v.get("value", "")).lower().strip()
                if pv_lower in vv or vv in pv_lower:
                    return (v.get("id", 0), str(v.get("value", "")))
        return (0, product_value)
    
    # ── 构建变体列表 ──
    variants = draft.get("variants", [])
    if not isinstance(variants, list):
        variants = []
    is_multi = len(variants) > 1
    
    variant_list: list[dict[str, Any]] = variants if is_multi else [{}]
    
    items: list[dict[str, Any]] = []
    
    for idx, variant in enumerate(variant_list):
        # 确定 offer_id
        if is_multi:
            offer_id = str(variant.get("sku_id", f"{draft.get('item_id', 'unknown')}_{idx}"))
            var_price = str(variant.get("price", price_rub))
            var_old_price = str(variant.get("original_price", old_price_rub))
        else:
            offer_id = str(draft.get("sku_id", draft.get("item_id", f"item_{idx}")))
            var_price = str(price_rub)
            var_old_price = str(old_price_rub)
        
        item: dict[str, Any] = {
            "description_category_id": description_category_id,
            "type_id": type_id,
            "offer_id": offer_id,
            "name": str(draft.get("title", ""))[:500],
            "price": var_price,
            "old_price": var_old_price,
            "currency_code": currency_code,
            "vat": "0",
            "dimension_unit": "mm",
            "weight_unit": "g",
            "depth": dimensions.get("length", 100),
            "width": dimensions.get("width", 100),
            "height": dimensions.get("height", 50),
            "weight": weight_grams,
            "images": (images or [])[:15],
            "primary_image": images[0] if images else "",
            "complex_attributes": [],
            "images360": [],
            "pdf_list": [],
            "barcode": "",
            "attributes": [],
        }
        
        # ── 构建属性列表 ──
        attrs: list[dict[str, Any]] = []
        for schema_attr in attr_list:
            attr_id = int(schema_attr.get("id", 0))
            if attr_id == 0:
                continue
            
            dict_id = schema_attr.get("dictionary_id", 0)
            attr_name_cn = schema_attr.get("name", "")
            
            # 跳过文本类属性（由 prepare_ozon_upload 或 _validate 处理）
            # 4191=描述, 4180=关键字, 9048=变体绑定名, 23171=hashtag
            if attr_id in (4191, 4180, 9048, 23171):
                continue
            
            # 品牌（85, 5076）— 留给 _validate_and_enrich_items 处理
            if attr_id in BRAND_ATTRIBUTE_IDS:
                continue
            # 原产国（4389）— 留给 _validate_and_enrich_items 处理
            if attr_id == COUNTRY_ATTR_ID:
                continue
            
            # 匹配 1688 产品属性
            product_value = _match_product_attr(attr_name_cn)
            
            if dict_id and dict_id > 0 and product_value:
                # 字典属性 → 查找 dictionary_value_id
                dict_val_id, dict_val = _find_dict_value(attr_id, product_value)
                if dict_val_id > 0:
                    attrs.append({
                        "complex_id": 0,
                        "id": attr_id,
                        "values": [{"dictionary_value_id": dict_val_id, "value": dict_val}],
                    })
                    logger.debug(f"   ✅ 属性映射: [{attr_id}] {attr_name_cn} = {product_value} → dict_id={dict_val_id}")
                else:
                    # 字典值未匹配，填原始值（_validate 会尝试修正）
                    attrs.append({
                        "complex_id": 0,
                        "id": attr_id,
                        "values": [{"dictionary_value_id": 0, "value": product_value}],
                    })
                    logger.debug(f"   ⚠️ 属性映射: [{attr_id}] {attr_name_cn} = {product_value} (字典值未匹配)")
            elif product_value:
                # 自由文本属性
                attrs.append({
                    "complex_id": 0,
                    "id": attr_id,
                    "values": [{"dictionary_value_id": 0, "value": product_value}],
                })
                logger.debug(f"   ✅ 文本属性: [{attr_id}] {attr_name_cn} = {product_value}")
            # 无匹配值 → 不添加，_validate_and_enrich_items 会补默认值
        
        item["attributes"] = attrs
        items.append(item)
    
    logger.info(f"   确定性构建完成: {len(items)} items, 属性映射数={sum(len(it['attributes']) for it in items)}")
    return items


def _validate_and_enrich_items(
    items: list[dict[str, Any]],
    attr_list: list[dict[str, Any]],
    dict_lookup: dict[int, list[dict[str, Any]]],
    images: list[str],
    ozon_client_id: str,
    ozon_api_key: str,
    description_category_id: int,
    type_id: int,
    weight_grams: int,
    dimensions: dict[str, int],
) -> list[dict[str, Any]]:
    """校验并补充 items 字段（属性补全、品牌修正、hashtag 生成等）"""

    # 构建属性索引
    attr_by_id: dict[int, dict[str, Any]] = {
        int(a["id"]): a for a in attr_list if "id" in a
    }
    required_attr_ids = {
        int(a["id"]) for a in attr_list
        if a.get("is_required", False) and "id" in a
    }

    validated_items: list[dict[str, Any]] = []

    for idx, item in enumerate(items):
        # === 基本字段补全 ===
        if not item.get("description_category_id"):
            item["description_category_id"] = description_category_id
        if not item.get("type_id"):
            item["type_id"] = type_id
        if not item.get("currency_code"):
            item["currency_code"] = "RUB"
        if not item.get("vat"):
            item["vat"] = "0"
        if not item.get("dimension_unit"):
            item["dimension_unit"] = "mm"
        if not item.get("weight_unit"):
            item["weight_unit"] = "g"
        if not item.get("depth") or item.get("depth") == 0:
            item["depth"] = dimensions.get("length", 100)
        if not item.get("width") or item.get("width") == 0:
            item["width"] = dimensions.get("width", 100)
        if not item.get("height") or item.get("height") == 0:
            item["height"] = dimensions.get("height", 50)
        if not item.get("weight") or item.get("weight") == 0:
            item["weight"] = weight_grams

        # 图片
        if not item.get("images"):
            item["images"] = images[:15]
        if not item.get("primary_image") and images:
            item["primary_image"] = images[0] if images else ""

        # 数组字段
        item.setdefault("complex_attributes", [])
        item.setdefault("images360", [])
        item.setdefault("pdf_list", [])
        item.setdefault("barcode", item.get("barcode", ""))

        # === 属性校验 ===
        attrs = item.get("attributes", [])
        seen_ids: set[int] = set()

        validated_attrs: list[dict[str, Any]] = []
        for attr in attrs:
            if not isinstance(attr, dict):
                continue

            attr_id = int(attr.get("id", 0))
            if attr_id == 0:
                continue
            if attr_id in seen_ids:
                logger.warning(f"   重复 attribute_id={attr_id}，跳过")
                continue
            seen_ids.add(attr_id)

            # 确保有 complex_id
            if "complex_id" not in attr:
                attr["complex_id"] = 0

            # 校验 values
            values = attr.get("values", [])
            if not isinstance(values, list):
                values = [values]
            if not values:
                values = [{"dictionary_value_id": 0, "value": ""}]

            validated_values = []
            for v in values:
                if not isinstance(v, dict):
                    continue
                dict_val_id = v.get("dictionary_value_id", 0)
                value = v.get("value", "")

                # 字典属性校验 dictionary_value_id
                schema_attr = attr_by_id.get(attr_id, {})
                dict_id = schema_attr.get("dictionary_id", 0)

                if dict_id and dict_id > 0 and dict_val_id == 0:
                    # 尝试从 dict_lookup 中查找匹配
                    dict_vals = dict_lookup.get(attr_id, [])
                    if isinstance(dict_vals, list):
                        for dv in dict_vals:
                            if isinstance(dv, dict) and dv.get("value", "").lower() == str(value).lower():
                                dict_val_id = dv.get("id", 0)
                                logger.info(f"   ✅ 修正 dictionary_value_id: attr={attr_id}, value='{value}' → id={dict_val_id}")
                                break

                validated_values.append({
                    "dictionary_value_id": int(dict_val_id) if dict_val_id else 0,
                    "value": str(value),
                })

            attr["values"] = validated_values
            validated_attrs.append(attr)

        # === 补充缺失的必填属性 ===
        present_ids = {int(a["id"]) for a in validated_attrs if "id" in a}
        missing_required = required_attr_ids - present_ids

        for missing_id in sorted(missing_required):
            schema_attr = attr_by_id.get(missing_id, {})
            if not schema_attr:
                continue

            dict_id = schema_attr.get("dictionary_id", 0)
            new_attr: dict[str, Any] = {
                "complex_id": 0,
                "id": missing_id,
                "values": [],
            }

            if dict_id and dict_id > 0:
                # 字典属性 → 取第一个可用值
                dict_vals = dict_lookup.get(missing_id, [])
                if isinstance(dict_vals, list) and dict_vals:
                    first = dict_vals[0]
                    if isinstance(first, dict):
                        new_attr["values"] = [{
                            "dictionary_value_id": first.get("id", 0),
                            "value": str(first.get("value", "")),
                        }]
                elif isinstance(dict_vals, dict) and dict_vals.get("result"):
                    first = dict_vals["result"][0] if dict_vals["result"] else {}
                    if first:
                        new_attr["values"] = [{
                            "dictionary_value_id": first.get("id", 0),
                            "value": str(first.get("value", "")),
                        }]
                else:
                    new_attr["values"] = [{"dictionary_value_id": 0, "value": ""}]
            else:
                # 自由文本属性
                new_attr["values"] = [{"dictionary_value_id": 0, "value": ""}]

            validated_attrs.append(new_attr)
            logger.warning(f"   ⚠️ 补充缺失必填属性: id={missing_id} ({schema_attr.get('name', '?')})")

        # === 特殊属性修正 ===
        # 品牌（85, 5076）
        for brand_id in BRAND_ATTRIBUTE_IDS:
            brand_attr = next((a for a in validated_attrs if int(a.get("id", 0)) == brand_id), None)
            if brand_attr:
                values = brand_attr.get("values", [])
                for v in values:
                    if v.get("dictionary_value_id", 0) == 0:
                        v["dictionary_value_id"] = NO_BRAND_DICT_ID
                        v["value"] = NO_BRAND_VALUE
                        logger.info(f"   ✅ 品牌 attribute_id={brand_id} 修正为 'Нет бренда'")

        # 原产国（4389）
        country_attr = next((a for a in validated_attrs if int(a.get("id", 0)) == COUNTRY_ATTR_ID), None)
        if country_attr:
            values = country_attr.get("values", [])
            for v in values:
                if v.get("dictionary_value_id", 0) == 0:
                    v["dictionary_value_id"] = CHINA_DICT_ID
                    v["value"] = CHINA_VALUE
        else:
            # 4389 是很多类目的必填属性，如果缺失则补充
            validated_attrs.append({
                "complex_id": 0,
                "id": COUNTRY_ATTR_ID,
                "values": [{"dictionary_value_id": CHINA_DICT_ID, "value": CHINA_VALUE}],
            })

        # Hashtag #23171: 生成俄语标签（不能是品牌名！）
        hashtag_attr = next((a for a in validated_attrs if int(a.get("id", 0)) == FORCE_ATTR_23171), None)
        if hashtag_attr:
            values = hashtag_attr.get("values", [])
            for v in values:
                val = str(v.get("value", ""))
                # 如果是品牌值 "Нет бренда" 或无意义值，替换为生成的 hashtag
                if val == NO_BRAND_VALUE or val == "" or not val.startswith("#"):
                    new_tags = _generate_hashtags(item.get("name", ""))
                    v["value"] = new_tags
                    v["dictionary_value_id"] = 0  # 23171 是自由文本
                    logger.info(f"   ✅ hashtag #23171 修正为: {new_tags}")
        elif FORCE_ATTR_23171 in {int(a.get("id", 0)) for a in attr_list}:
            # Schema 中有 23171 但 LLM 没有生成，补充
            new_tags = _generate_hashtags(item.get("name", ""))
            validated_attrs.append({
                "complex_id": 0,
                "id": FORCE_ATTR_23171,
                "values": [{"dictionary_value_id": 0, "value": new_tags}],
            })
            logger.info(f"   ✅ hashtag #23171 补充生成: {new_tags}")

        # 9048（变体绑定名）= item_id，与 prepare_ozon_upload_node 逻辑一致
        if FORCE_ATTR_9048 not in present_ids and FORCE_ATTR_9048 not in {int(a["id"]) for a in validated_attrs}:
            item_id_val = item.get("offer_id", "unknown")
            validated_attrs.append({
                "complex_id": 0,
                "id": FORCE_ATTR_9048,
                "values": [{"dictionary_value_id": 0, "value": item_id_val}],
            })

        item["attributes"] = validated_attrs
        validated_items.append(item)

    return validated_items


def _fetch_category_tree_from_ozon(
    ozon_client_id: str,
    ozon_api_key: str,
) -> list[dict[str, Any]] | None:
    """从 Ozon API 获取类目树并返回原始数据"""
    try:
        url = "https://api-seller.ozon.ru/v1/description-category/tree"
        headers = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json",
        }
        payload = {"language": "ZH_HANS"}
        resp = session.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # 返回 result 列表（保持与 category_cache 存储格式一致）
        result = data.get("result", [])
        logger.info(f"✅ Ozon API 返回类目树: {len(result)} 个顶层类目")
        return result
    except Exception as e:
        logger.error(f"❌ Ozon 类目树 API 调用失败: {e}")
        return None


# ==================== Dictionary Values Helpers ====================

def _fetch_dict_values_from_ozon(
    ozon_client_id: str,
    ozon_api_key: str,
    description_category_id: int,
    type_id: int,
    attribute_id: int,
) -> list[dict[str, Any]] | None:
    """从 Ozon API 获取属性的字典值"""
    try:
        url = "https://api-seller.ozon.ru/v1/description-category/attribute/values"
        headers = {
            "Client-Id": ozon_client_id,
            "Api-Key": ozon_api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "attribute_id": attribute_id,
            "description_category_id": description_category_id,
            "type_id": type_id,
            "language": "ZH_HANS",
            "limit": 100,
        }
        resp = session.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", [])
        logger.info(f"   ✅ Ozon API 返回 attr={attribute_id} 字典值: {len(result)} 条")
        return result
    except Exception as e:
        logger.warning(f"   ⚠️ Ozon API 字典值 attr={attribute_id} 失败: {e}")
        return None


def _cache_dict_values(
    attribute_id: int,
    description_category_id: int,
    type_id: int,
    values: list[dict[str, Any]],
):
    """将字典值写入 PG 缓存"""
    try:
        from utils.local_db_manager import LocalDBManager
        local_db = LocalDBManager()
        local_db.set_dictionary_value_cache(
            attribute_id=attribute_id,
            description_category_id=description_category_id,
            type_id=type_id,
            values_data=values,
            language="ZH_HANS",
            expires_in=86400,
        )
        logger.info(f"   ✅ 字典值缓存写入成功: attr={attribute_id}, {len(values)} 条")
    except Exception as e:
        logger.warning(f"   ⚠️ 字典值缓存写入失败: {e}")


# ==================== Hashtag 生成 ====================

# 俄语关键词字典（按产品类型）
_HASHTAG_RU: dict[str, str] = {
    "секатор": "секатор сад садовый обрезка инструмент",
    "ножницы": "ножницы сад садовый обрезка инструмент",
    "грабли": "грабли сад садовый уборка листья",
    "лопата": "лопата сад садовый копка инструмент",
    "перчатки": "перчатки сад садовый защита работа",
    "шланг": "шланг сад полив вода",
    "лейка": "лейка сад полив вода",
    "горшок": "горшок цветы растения декор",
    "сеялка": "сеялка сад посадка семена",
    "удобрение": "удобрение сад растения подкормка",
}


def _generate_hashtags(name: str) -> str:
    """根据俄语标题生成 3-5 个 hashtag"""
    if not name:
        return "#товар #ozon"

    name_lower = name.lower()
    tags: list[str] = []

    # 从预定义字典匹配
    for keyword, tag_str in _HASHTAG_RU.items():
        if keyword in name_lower:
            tags = [f"#{t}" for t in tag_str.split()[:5]]
            break

    if not tags:
        # 从标题中提取俄语单词（排除短词和停用词）
        import re
        stopwords = {"для", "из", "и", "в", "на", "с", "по", "от", "не", "или", "а", "то", "как"}
        words = re.findall(r'[а-яё]{3,}', name_lower)
        meaningful = [w for w in words if w not in stopwords][:4]
        if meaningful:
            tags = [f"#{w}" for w in meaningful]
            # 补充通用标签
            tags.append("#товар")
        else:
            tags = ["#товар", "#ozon"]

    return " ".join(tags[:5])
