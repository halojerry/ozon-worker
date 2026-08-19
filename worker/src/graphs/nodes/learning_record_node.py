# 学习记录节点（上传成功后记录学习数据）
import os
import time
import logging
import requests
from typing import Dict, Any, List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

logger = logging.getLogger(__name__)

from graphs.state import (
    LearningRecordInput,
    LearningRecordOutput
)

from utils.local_db_manager import LocalDBManager


# ═══════════════════════════════════════════════════════════════════════
# T9: 上传成功回填 product_task_index（普通上传也建索引，OnSale/编辑/更新依赖）
# product_task_index 目前只有 update_images 写（T6 共享 product_index_service），
# 普通上传不写 → OnSale 货架/GET /edit 对普通上传商品查不到索引。本段在 approved
# 成功路径补回填。任何守卫缺失/写失败 → 跳过 + warning，绝不阻断学习路径。
# ═══════════════════════════════════════════════════════════════════════

def _is_real_upload_success(state) -> bool:
    """approved，或修复循环增量更新成功且商品已存在（有 product_id）。

    假成功（ozon_status_node 无有效 product_id 时置 upload_status=success + moderation=pending）
    靠 product_id 缺失拦截；修复循环成功（attributes/prices 增量 API 被 Ozon 接受）必有
    product_id，moderation 停留首传旧值 failed/declined，是真实成功。
    """
    if (getattr(state, 'moderation_status', '') or getattr(state, 'status', '') or "") == "approved":
        return True
    repair_success: bool = (getattr(state, 'upload_status', '') or "") == "success"
    if not repair_success:
        return False
    pid: str = str(getattr(state, 'product_id', '') or '')
    return bool(pid and pid not in ("0", "None"))


def _task_id_from_config(config) -> str:
    """从 LangGraph RunnableConfig 提取任务 ID（configurable.thread_id = PG 任务 ID）。

    ⚠️ 不要用 state.task_id —— 那是 ingest 节点随机生成的 UUID，与队列任务 ID 不一致
    （task_image_cache.py 已锁定该纪律：task_id 取 thread_id，task_processor 注入）。
    """
    try:
        if config is None:
            return ""
        if isinstance(config, dict):
            conf = config.get("configurable", {}) or {}
        else:
            conf = getattr(config, "configurable", None) or {}
        if isinstance(conf, dict):
            return str(conf.get("thread_id", ""))
        return str(conf or "")
    except Exception:
        return ""


def _resolve_draft_submission(task_id: str) -> tuple:
    """draft_submissions 定位草稿/凭证：submitted_task_id → (draft_id, credential_id)。

    模式对齐 image_service._resolve_draft_id（采集任务有行）；直连任务（skill 直连，凭证在
    payload 不落 credential 表）→ (None, None)。DB 异常 → (None, None)（非致命）。
    """
    if not task_id:
        return None, None
    try:
        from sqlalchemy import text as _sql
        from storage.database.db import get_engine as _get_engine
        with _get_engine().connect() as conn:
            row = conn.execute(_sql(
                "SELECT draft_id, credential_id FROM draft_submissions "
                "WHERE submitted_task_id = :task_id LIMIT 1"
            ), {"task_id": task_id}).fetchone()
        if row is None:
            return None, None
        draft_id = str(row[0]) if row[0] is not None else None
        credential_id = str(row[1]) if row[1] is not None else None
        return draft_id, credential_id
    except Exception as e:
        logger.warning("T9 索引回填: draft_submissions 查询失败（跳过）task=%s: %s", task_id, e)
        return None, None


def _resolve_credential_from_payload(tenant_id: str, task_id: str) -> str:
    """直连任务兜底：从任务 payload 反查 credential_id。

    W6: draft_submissions 无 credential_id 时（直连任务凭证在 payload / 修复前已入队的任务），
    走 ozon_product_tasks.payload.ozon_client_id → credentials(tenant_id, ozon_client_id).id 恢复。
    任一环节查不到 → None（调用方仍 skip 索引回填）。DB 异常 → None + warning（非致命）。
    """
    if not task_id or not tenant_id:
        return ""
    try:
        from sqlalchemy import text as _sql
        from storage.database.db import get_engine as _get_engine
        with _get_engine().connect() as conn:
            row = conn.execute(_sql(
                "SELECT payload->>'ozon_client_id' FROM ozon_product_tasks "
                "WHERE id::text = :task_id LIMIT 1"
            ), {"task_id": task_id}).fetchone()
        client_id = str(row[0]).strip() if row and row[0] else ""
        if not client_id:
            return ""
        with _get_engine().connect() as conn:
            cred = conn.execute(_sql(
                "SELECT id::text FROM credentials "
                "WHERE tenant_id = :tenant_id AND ozon_client_id = :client_id AND status = 'active' "
                "ORDER BY created_at DESC LIMIT 1"
            ), {"tenant_id": tenant_id, "client_id": client_id}).fetchone()
        return str(cred[0]) if cred and cred[0] else ""
    except Exception as e:
        logger.warning("T9 索引回填: task payload 反查 credential_id 失败（跳过）task=%s: %s", task_id, e)
        return ""


def _backfill_product_index(state, config) -> None:
    """T9: 上传成功回填 product_task_index（approved 分支内，非阻断追加）。

    守卫：product_id 存在 + credential_id 可解析 + task_id 存在；任何缺失 → 跳过。
    offer_id：跟卖 follow_{竞品id}，否则 draft.item_id / sku_id（对齐 draft_service._resolve_offer_id）。
    draft_id：draft_submissions 定位（可空，直连任务为 NULL，upsert_index 签名接受 None）。
    写失败（DB 异常）→ logger.warning 不抛（学习路径不被索引回填阻断）。
    """
    try:
        product_id = getattr(state, "product_id", None)
        if not product_id or str(product_id) in ("0", "None", ""):
            logger.info("⏭️ T9 索引回填跳过: product_id 缺失/无效")
            return
        task_id = _task_id_from_config(config)
        if not task_id:
            logger.info("⏭️ T9 索引回填跳过: task_id 缺失（thread_id 不可解析）")
            return
        tenant_id = str(getattr(state, "user_id", "") or "").strip()
        if not tenant_id:
            logger.info("⏭️ T9 索引回填跳过: tenant_id(user_id) 缺失")
            return
        draft_id, credential_id = _resolve_draft_submission(task_id)
        if not credential_id:
            # 直连任务：draft_submissions 无 credential_id → 从任务 payload 反查兜底
            credential_id = _resolve_credential_from_payload(tenant_id, task_id)
        if not credential_id:
            logger.info(
                "⏭️ T9 索引回填跳过: credential_id 不可解析（draft_submissions 与 task payload "
                "均无反查路径——直连任务未在 credentials 表登记店铺 %s）task=%s",
                tenant_id, task_id,
            )
            return

        draft = getattr(state, "draft", None) or {}
        envelope = getattr(state, "envelope", None) or {}
        extensions = envelope.get("extensions") or {}
        follow_sell = bool(extensions.get("follow_sell")) or bool(extensions.get("follow_type"))
        if follow_sell:
            competitor = str(draft.get("ozon_product_id") or "").strip()
            offer_id = f"follow_{competitor}" if competitor else ""
        else:
            offer_id = str(draft.get("item_id") or draft.get("sku_id") or "").strip()
        if not offer_id:
            logger.info("⏭️ T9 索引回填跳过: offer_id 无法解析（draft.item_id/sku_id 均空）")
            return

        from services.product_index_service import upsert_index  # 懒导入防循环
        upsert_index(
            tenant_id=tenant_id, product_id=str(product_id), offer_id=offer_id,
            task_id=task_id, credential_id=credential_id, draft_id=draft_id,
        )
        logger.info(f"✅ T9 索引回填成功: product_id={product_id} task_id={task_id} "
                    f"offer_id={offer_id} draft_id={draft_id}")
    except Exception as e:
        logger.warning(f"⚠️ T9 索引回填失败（不阻断学习）: {e}")


# ═══════════════════════════════════════════════════════════════════════
# 任务 1.4: 上传成功后回填类目佣金（approved 成功路径，非致命追加）
# /v5/product/info/prices 用真实 product_id 查 commissions → parse_prices_commissions
# → upsert_category_commission(source="prices_api") 填 fbs 对应价格段。
# 任何守卫缺失/API 异常 → 跳过 + warning，绝不阻断学习路径（mirror T9 风格）。
# ═══════════════════════════════════════════════════════════════════════

def _backfill_category_commission(state) -> None:
    """任务 1.4: 上传成功后回填 category_commission（approved 分支内，非阻断追加）。

    守卫：product_id + description_category_id + 凭证（ozon_client_id/ozon_api_key，从
    运行时合并 GlobalState 读，LearningRecordInput 不含）都存在，否则跳过。
    /v5/product/info/prices 用真实 product_id 查询（不是空 offer_id），
    parse_prices_commissions 取 items[0].commissions.sales_percent_rfbs 比例；
    upsert 按 pick_price_band(售价) 填 fbs 对应段（百分比）；售价未知/非 RUB →
    中性段 fbs_leq_5000。任何异常 → logger.warning，不抛（学习路径不被佣金回填阻断）。
    """
    try:
        product_id = getattr(state, "product_id", None)
        if not product_id or str(product_id) in ("0", "None", ""):
            logger.info("⏭️ 佣金回填跳过: product_id 缺失/无效")
            return
        description_category_id = getattr(state, "description_category_id", None)
        if not description_category_id:
            logger.info("⏭️ 佣金回填跳过: description_category_id 缺失")
            return
        ozon_client_id = str(getattr(state, "ozon_client_id", "") or "").strip()
        ozon_api_key = str(getattr(state, "ozon_api_key", "") or "").strip()
        if not ozon_client_id or not ozon_api_key:
            logger.info("⏭️ 佣金回填跳过: ozon_client_id/ozon_api_key 缺失（凭证不在 state）")
            return

        from utils.ozon_client import ozon_post  # 懒导入（模块级 import 会拖 PG/Supabase 依赖）
        from utils.commission_resolver import (
            parse_prices_commissions,
            pick_price_band,
            upsert_category_commission,
        )

        resp = ozon_post(
            client_id=ozon_client_id,
            api_key=ozon_api_key,
            endpoint="/v5/product/info/prices",
            body={"filter": {"product_id": [str(product_id)]}, "limit": 1},
        )
        commission_ratio = parse_prices_commissions(resp)
        if commission_ratio is None:
            logger.info("⏭️ 佣金回填跳过: prices 响应无 commissions（product_id=%s）", product_id)
            return

        # 选段：RUB 售价 → pick_price_band 选 fbs 对应段；售价未知/CNY → 中性段 leq_5000
        pricing_info = getattr(state, "pricing_info", None) or {}
        currency_code = str(pricing_info.get("currency_code") or "").upper()
        price_rub = float(pricing_info.get("price") or 0) if currency_code == "RUB" else None
        band = pick_price_band(price_rub) if price_rub and price_rub > 0 else "leq_5000"
        segment = f"fbs_{band}"
        pct = round(commission_ratio * 100.0, 4)  # 比例 → 百分比（upsert 段值约定）

        upsert_category_commission(
            int(description_category_id),
            source="prices_api",
            **{segment: pct},
        )
        logger.info("✅ 佣金回填成功: dc=%s %s=%.2f%% product_id=%s",
                    description_category_id, segment, commission_ratio * 100.0, product_id)
    except Exception as e:
        logger.warning(f"⚠️ 佣金回填失败（不阻断学习）: {e}")


def learning_record_node(
    state: LearningRecordInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> LearningRecordOutput:
    """
    title: 学习记录节点
    desc: 上传成功后记录成功的属性映射到数据库（学习闭环，让数据库越用越智能）
    integrations: Supabase
    """
    ctx = runtime.context
    
    logger.info("📚 开始记录学习数据（属性映射闭环）")
    
    # 从LearningRecordInput提取数据
    description_category_id_str: str = state.description_category_id
    final_attributes: List[Dict[str, Any]] = state.final_attributes or []
    attributes_schema: List[Dict[str, Any]] = state.attributes_schema or []
    draft: Dict[str, Any] = state.draft or {}
    # ✅ v0.25 T1: 1688 类目数字 ID（Skill 侧提取，供类目学习回写）
    _src_cat_id: Any = draft.get("source_category_id")
    if not _src_cat_id:
        _src_cat_id = (getattr(state, "source", None) or {}).get("category_id")
    
    # ✅ 构建attribute_id → attribute_name映射
    attr_name_map: Dict[int, str] = {}
    for schema_attr in attributes_schema:
        if isinstance(schema_attr, dict):
            attr_id: Any = schema_attr.get("id")
            if attr_id is not None:
                attr_name_map[int(attr_id)] = str(schema_attr.get("name", ""))
    
    # ✅ 从draft中提取常见字段值，用于匹配原始中文源值
    draft_text_values: List[str] = []
    for v in draft.values():
        if isinstance(v, str) and len(v) > 0:
            draft_text_values.append(v)
        elif isinstance(v, (int, float)):
            draft_text_values.append(str(v))
    
    # 合并draft所有文本为一个搜索池
    draft_text_pool: str = " ".join(draft_text_values)
    
    # ✅ 类型转换：str → int（LocalDBManager需要int类型）
    try:
        description_category_id: int = int(description_category_id_str)
    except (ValueError, TypeError) as e:
        logger.warning(f"⚠️ 类目ID转换失败：{description_category_id_str} → {e}")
        return LearningRecordOutput(
            recorded_count=0,
            progress_counter=24
        )
    
    # ✅ v0.21 (P0-1): 只有审核 approved 才算成功，才允许写学习记录。
    # imported/active/processed 只是"导入成功"，不代表审核通过；
    # upload_status=="success" 是假成功来源，一律不再放行。
    ozon_upload_success: bool = _is_real_upload_success(state)
    
    logger.info(f"📊 Ozon状态：{getattr(state, 'moderation_status', '') or getattr(state, 'status', '')} → 是否上传成功：{ozon_upload_success}")
    
    # 判断是否上传成功
    if not ozon_upload_success:
        logger.info("❌ 上传失败，跳过学习记录")
        return LearningRecordOutput(
            recorded_count=0,
            progress_counter=24  # ← 固定进度计数器（24号节点）
        )
    
    # ✅ 记录成功的属性映射到 PG 数据库
    local_db = LocalDBManager()
    recorded_count: int = 0

    # ✅ PR-0 (R6): 学习门 — fetch-back 回读结果决定哪些属性「真的被 Ozon 接受」。
    # 被擦除的（erased）与 Ozon 自动填默认的（defaulted_by_ozon）属性不写入学习，
    # 否则「Ozon 没查这个字段」被学习成「这个值是对的」（Goodhart 棘轮）。
    _fb: Dict[str, Any] = getattr(state, "fetch_back_result", None) or {}
    _fb_erased: set = {int(x) for x in (_fb.get("erased") or [])}
    _fb_defaulted: set = {int(x) for x in (_fb.get("defaulted_by_ozon") or [])}

    logger.info(f"📝 开始记录{len(final_attributes)}个属性映射（PostgreSQL）...")
    
    for attr in final_attributes:
        # 验证attr是否为dict类型
        if not isinstance(attr, dict):
            logger.warning(f"⚠️ 属性格式错误（非dict类型），跳过：{type(attr)}")
            continue
        
        # 提取属性字段
        attribute_id: Any = attr.get("attribute_id")
        value: Any = attr.get("value")
        dictionary_value_id: Any = attr.get("dictionary_value_id", 0)
        
        # 验证attribute_id是否为int类型
        if attribute_id is None:
            logger.warning(f"⚠️ 属性ID缺失，跳过")
            continue
        
        # 类型转换和验证
        try:
            attribute_id_int: int = int(attribute_id)
            dictionary_value_id_int: int = int(dictionary_value_id) if dictionary_value_id else 0
            value_str: str = str(value) if value else ""
        except (ValueError, TypeError) as e:
            logger.warning(f"⚠️ 属性类型转换失败，跳过：{e}")
            continue

        # ✅ PR-0 (R6): 学习门 — 被 Ozon 擦除 / 自动填默认的属性不学习
        if attribute_id_int in _fb_erased:
            logger.info(f"⏭️ PR-0 学习门: attr={attribute_id_int} 被 Ozon 擦除，不写入学习")
            continue
        if attribute_id_int in _fb_defaulted:
            logger.info(f"⏭️ PR-0 学习门: attr={attribute_id_int} 由 Ozon 自动填默认，不写入学习")
            continue
        
        # 获取属性名称（从attr_name_map）
        attribute_name: str = attr_name_map.get(attribute_id_int, "")
        
        # ✅ 提取原始中文源值：从draft中搜索与当前属性值相关的原始文本
        # 策略：如果属性值能在draft文本中找到子串，说明它来源于draft
        source_value: str = ""
        for draft_val in draft_text_values:
            # 如果draft中的值是当前属性值的子串（或反过来），则认为是源值
            if value_str and (value_str in draft_val or draft_val in value_str):
                source_value = draft_val
                break
        
        # 如果未找到匹配，使用属性值本身但标注来源
        if not source_value:
            source_value = f"[{attribute_name or 'unknown'}]" if attribute_name else ""

        # ✅ PR-6: provenance 标记 —
        # 真 1688 源值匹配 → learned_approved（可复用且增长置信）
        # fabricated `[{name}]` 兜底 → default_fallback（可出场但 success_count 不增长，非真实映射）
        _source_marker: str = "learned_approved"
        if source_value.startswith("[{") or source_value.startswith("["):
            _source_marker = "default_fallback"
        
        # 跳过硬编码属性（如品牌"无品牌"），无学习价值
        attr_source: Any = attr.get("source", "")
        if attr_source == "hardcoded":
            logger.debug(f"⏭️ 跳过硬编码属性: attr_id={attribute_id_int}")
            continue

        # ✅ 写入 PG（替代旧 SQLite + Supabase 双写）
        local_db.add_attribute_mapping(
            category_id=int(description_category_id),
            attribute_id=attribute_id_int,
            attribute_name=attribute_name,
            source_value=source_value,
            target_value=value_str,
            dictionary_value_id=dictionary_value_id_int,
            source=_source_marker,
        )
        
        recorded_count += 1
        logger.info(f"✅ 属性映射记录成功：attr_id={attribute_id_int}, value={value_str}, dictionary_value_id={dictionary_value_id_int}")
    
    logger.info(f"✅ 学习记录完成：{recorded_count}个属性映射已写入 PostgreSQL")
    
    # ═══════════════════════════════════════════════════════
    # v4: 写入 category_mapping（类目学习缓存）
    # ⚠️ 跟卖跳过 — 类目来自Ozon面包屑，source_category是1688图搜噪音
    # ═══════════════════════════════════════════════════════
    is_follow = False
    try:
        is_follow = bool(getattr(state, 'envelope', {}).get("extensions", {}).get("follow_sell", False))
    except Exception:
        pass
    source_category = draft.get("source_category", "") if draft else ""
    if source_category and not is_follow:
        try:
            import re as _re
            cleaned = _re.sub(r'[>、/→]', ' ', source_category)
            cat_terms = [t.strip() for t in cleaned.split() if len(t.strip()) >= 2]
            leaf = cat_terms[-1] if cat_terms else ""
            if leaf and description_category_id:
                tp_val = int(state.type_id or 0)
                # jieba 关键词
                try:
                    import jieba as _jieba
                    jieba_kws = list({w for w in _jieba.cut(leaf) if len(w) >= 2})
                except Exception:
                    jieba_kws = [leaf]
                # 查 ZH + RU 路径
                cat_zh = ""; cat_ru = ""
                try:
                    from sqlalchemy import text as _sql_t
                    from storage.database.db import get_session as _gs
                    with _gs() as _s:
                        _r = _s.execute(_sql_t(
                            "SELECT full_path FROM category_tree_nodes WHERE description_category_id=:dc AND type_id=:tp AND language='ZH_HANS' LIMIT 1"
                        ), {"dc": int(description_category_id), "tp": tp_val}).fetchone()
                        if _r: cat_zh = _r[0]
                        _r2 = _s.execute(_sql_t(
                            "SELECT full_path FROM category_tree_nodes WHERE description_category_id=:dc AND type_id=:tp AND language='RU' LIMIT 1"
                        ), {"dc": int(description_category_id), "tp": tp_val}).fetchone()
                        if _r2: cat_ru = _r2[0]
                except Exception:
                    pass
                # ⚠️ v0.27: dc/type 存在性校验 — 防品牌页 ID/错配固化
                # (实证: 甩脂机 type_id=101029485 是品牌 Luxhommè 的 ID,树中无此节点仍被写入 → 毒化同款上架)
                _mapping_valid = False
                try:
                    from sqlalchemy import text as _sql_t3
                    from storage.database.db import get_session as _gs3
                    with _gs3() as _s3:
                        _mapping_valid = bool(_s3.execute(_sql_t3(
                            "SELECT 1 FROM category_tree_nodes WHERE description_category_id=:dc AND type_id=:tp LIMIT 1"
                        ), {"dc": int(description_category_id), "tp": tp_val}).fetchone())
                except Exception as _mapping_err:
                    logger.warning(f"category_mapping 存在性校验异常(跳过写入): {_mapping_err}")
                if _mapping_valid:
                    local_db.add_category_mapping(
                        source_category_leaf=leaf,
                        source_category_id=int(_src_cat_id) if _src_cat_id else None,
                        description_category_id=int(description_category_id),
                        type_id=tp_val,
                        source_category_path=source_category,
                        source_keywords=jieba_kws,
                        category_path_zh=cat_zh, category_path_ru=cat_ru,
                        confidence=0.85, source="learned_approved",
                    )
                    logger.info(f"📚 category_mapping: '{leaf}' → [{description_category_id}/{tp_val}]")
                else:
                    logger.warning(f"category_mapping 跳过写入: dc/tp 树中不存在 ({leaf} → [{description_category_id}/{tp_val}]),疑似品牌页 ID/错配")
        except Exception as e:
            logger.warning(f"category_mapping写入失败（非致命）: {e}")
    
    # ✅ T9: 上传成功（approved）回填 product_task_index — 非阻断，任何缺失/异常仅 warning
    _backfill_product_index(state, config)
    # ✅ 任务 1.4: 上传成功（approved）回填类目佣金 — 非阻断，任何缺失/异常仅 warning
    _backfill_category_commission(state)
    
    return LearningRecordOutput(
        recorded_count=recorded_count,
        progress_counter=24  # ← 固定进度计数器（24号节点）
    )
