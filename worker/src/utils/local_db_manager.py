"""
PostgreSQL 缓存管理器 — 替代旧 SQLite 本地缓存
用途：缓存高频查询数据，降低 Ozon API 调用延迟
策略：PostgreSQL 优先查询 + Ozon API Fallback
"""

import json
import time
import logging
from typing import Optional, Dict, Any, List

from sqlalchemy import select, and_, func, update, case
from sqlalchemy.dialects.postgresql import insert as pg_insert

from storage.database.db import get_session
from storage.database.shared.model import (
    AttributeCache,
    DictionaryValueCache,
    CategoryCache,
    CategoryTreeNode,
    LogisticsRate,
    ExchangeRate,
    OzonAttributeMapping,
    GatewayTask,
    CategoryMapping,
    AttributeSynonym,
)

logger = logging.getLogger(__name__)

# PR-6: 一次性迁移标记 — ALTER TABLE ADD COLUMN source 只跑一次/进程
_source_column_migrated = False


def _ensure_source_column() -> None:
    """PR-6: 幂等迁移 — 给 ozon_attribute_mappings 加 source 列（老库无此列）。"""
    global _source_column_migrated
    if _source_column_migrated:
        return
    try:
        from sqlalchemy import text as _text
        with get_session() as _s:
            _s.execute(_text(
                "ALTER TABLE ozon_attribute_mappings "
                "ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'learned_approved'"
            ))
            _s.commit()
        _source_column_migrated = True
        logger.info("✅ PR-6 迁移: ozon_attribute_mappings.source 列已就位")
    except Exception as _e:
        logger.warning(f"⚠️ PR-6 迁移 ozon_attribute_mappings.source 失败（非致命）: {_e}")
        _source_column_migrated = True  # 避免反复重试


class LocalDBManager:
    """PostgreSQL 缓存管理器（保持与旧 SQLite 版本相同的方法签名）"""

    def __init__(self, db_path: str = ""):
        """初始化（db_path 参数保留兼容，实际不再使用 SQLite 文件路径）"""
        _ensure_source_column()
        logger.info("✅ PostgreSQL 缓存管理器已初始化")

    # ============================================================
    # 查询方法
    # ============================================================

    def get_attribute_cache(self, description_category_id: int, type_id: Optional[int], language: str = "ZH_HANS") -> Optional[Dict[str, Any]]:
        """查询属性缓存"""
        current_time = int(time.time())
        session = get_session()
        try:
            row = session.execute(
                select(AttributeCache).where(
                    and_(
                        AttributeCache.description_category_id == description_category_id,
                        AttributeCache.type_id == type_id,
                        AttributeCache.language == language,
                        AttributeCache.expires_at > current_time,
                    )
                )
            ).scalar_one_or_none()
            if row:
                logger.info(f"✅ PG 查询命中：attribute_cache（category_id={description_category_id}）")
                return {
                    "attributes_schema": row.attributes_schema,
                    "expires_at": row.expires_at,
                }
            logger.info(f"❌ PG 查询未命中：attribute_cache（category_id={description_category_id}）")
            return None
        finally:
            session.close()

    def get_dictionary_value_cache(self, attribute_id: int, description_category_id: int, type_id: Optional[int], language: str = "ZH_HANS") -> Optional[Dict[str, Any]]:
        """查询字典值缓存"""
        current_time = int(time.time())
        session = get_session()
        try:
            row = session.execute(
                select(DictionaryValueCache).where(
                    and_(
                        DictionaryValueCache.attribute_id == attribute_id,
                        DictionaryValueCache.description_category_id == description_category_id,
                        DictionaryValueCache.type_id == type_id,
                        DictionaryValueCache.language == language,
                        DictionaryValueCache.expires_at > current_time,
                    )
                )
            ).scalar_one_or_none()
            if row:
                logger.info(f"✅ PG 查询命中：dictionary_value_cache（attribute_id={attribute_id}）")
                return {
                    "values_data": row.values_data,
                    "expires_at": row.expires_at,
                }
            logger.info(f"❌ PG 查询未命中：dictionary_value_cache（attribute_id={attribute_id}）")
            return None
        finally:
            session.close()

    def get_category_cache(self, ozon_client_id: str, language: str = "ZH_HANS") -> Optional[Dict[str, Any]]:
        """查询类目树缓存"""
        current_time = int(time.time())
        session = get_session()
        try:
            row = session.execute(
                select(CategoryCache).where(
                    and_(
                        CategoryCache.ozon_client_id == ozon_client_id,
                        CategoryCache.language == language,
                        CategoryCache.expires_at > current_time,
                    )
                )
            ).scalar_one_or_none()
            if row:
                logger.info(f"✅ PG 查询命中：category_cache（client_id={ozon_client_id}）")
                return {
                    "tree_data": row.tree_data,
                    "expires_at": row.expires_at,
                }
            logger.info(f"❌ PG 查询未命中：category_cache（client_id={ozon_client_id}）")
            return None
        finally:
            session.close()

    def get_logistics_cost(self, weight: float, channel: str = "standard") -> Optional[Dict[str, Any]]:
        """查询物流费率"""
        session = get_session()
        try:
            row = session.execute(
                select(LogisticsRate).where(
                    and_(
                        LogisticsRate.weight_min <= weight,
                        LogisticsRate.weight_max >= weight,
                    )
                ).limit(1)
            ).scalar_one_or_none()
            if row:
                logger.info(f"✅ PG 查询命中：logistics_rates（weight={weight}g）")
                return {
                    "base_cost": row.base_cost,
                    "per_gram_rate": row.per_gram_rate,
                    "scoring_group": row.scoring_group,
                    "service_level": row.service_level,
                    "tpl_provider": row.tpl_provider,
                }
            logger.info(f"❌ PG 查询未命中：logistics_rates（weight={weight}g）")
            return None
        finally:
            session.close()

    def get_exchange_rate(self, from_currency: str = "CNY", to_currency: str = "RUB") -> Optional[float]:
        """查询汇率"""
        session = get_session()
        try:
            row = session.execute(
                select(ExchangeRate).where(
                    and_(
                        ExchangeRate.from_currency == from_currency,
                        ExchangeRate.to_currency == to_currency,
                    )
                )
            ).scalar_one_or_none()
            if row:
                current_time = int(time.time())
                if current_time - (row.updated_at or 0) < 86400:  # 24 小时有效
                    logger.info(f"✅ PG 查询命中：exchange_rates（{from_currency}→{to_currency}）")
                    return row.rate
                else:
                    logger.info(f"❌ 汇率已过期：exchange_rates（{from_currency}→{to_currency}）")
                    return None
            logger.info(f"❌ PG 查询未命中：exchange_rates（{from_currency}→{to_currency}）")
            return None
        finally:
            session.close()

    def get_attribute_mappings(self, category_id: int) -> List[Dict[str, Any]]:
        """查询属性映射学习记录"""
        session = get_session()
        try:
            rows = session.execute(
                select(OzonAttributeMapping)
                .where(OzonAttributeMapping.category_id == category_id)
                .order_by(OzonAttributeMapping.success_count.desc(), OzonAttributeMapping.last_used_at.desc())
            ).scalars().all()
            if rows:
                logger.info(f"✅ PG 查询命中：ozon_attribute_mappings（category_id={category_id}，{len(rows)}条）")
                return [
                    {
                        "id": r.id,
                        "category_id": r.category_id,
                        "attribute_id": r.attribute_id,
                        "attribute_name": r.attribute_name,
                        "source_value": r.source_value,
                        "target_value": r.target_value,
                        "dictionary_value_id": r.dictionary_value_id,
                        "source": r.source,
                        "success_count": r.success_count,
                        "fail_count": r.fail_count,
                        "last_used_at": r.last_used_at,
                        "created_at": r.created_at,
                    }
                    for r in rows
                ]
            logger.info(f"❌ PG 查询未命中：ozon_attribute_mappings（category_id={category_id}）")
            return []
        finally:
            session.close()

    def get_gateway_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """查询任务状态"""
        session = get_session()
        try:
            row = session.execute(
                select(GatewayTask).where(GatewayTask.task_id == task_id)
            ).scalar_one_or_none()
            if row:
                logger.info(f"✅ PG 查询命中：gateway_tasks（task_id={task_id}）")
                return {
                    "id": row.id,
                    "task_id": row.task_id,
                    "status": row.status,
                    "result_json": row.result_json,
                    "stages": row.stages,
                    "error": row.error,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
            logger.info(f"❌ PG 查询未命中：gateway_tasks（task_id={task_id}）")
            return None
        finally:
            session.close()

    # ============================================================
    # 写入方法
    # ============================================================

    def set_attribute_cache(self, description_category_id: int, type_id: Optional[int], attributes_schema: Dict[str, Any], language: str = "ZH_HANS", expires_in: int = 86400):
        """写入属性缓存"""
        current_time = int(time.time())
        expires_at = current_time + expires_in
        session = get_session()
        try:
            stmt = pg_insert(AttributeCache).values(
                description_category_id=description_category_id,
                type_id=type_id,
                language=language,
                attributes_schema=attributes_schema,
                expires_at=expires_at,
                created_at=current_time,
            ).on_conflict_do_update(
                constraint="uq_attribute_cache",
                set_=dict(attributes_schema=attributes_schema, expires_at=expires_at),
            )
            session.execute(stmt)
            session.commit()
            logger.info(f"✅ PG 写入成功：attribute_cache（category_id={description_category_id}，有效期{expires_in}秒）")
        finally:
            session.close()

    def set_dictionary_value_cache(self, attribute_id: int, description_category_id: int, type_id: Optional[int], values_data: List[Dict[str, Any]], language: str = "ZH_HANS", expires_in: int = 86400):
        """写入字典值缓存"""
        current_time = int(time.time())
        expires_at = current_time + expires_in
        session = get_session()
        try:
            stmt = pg_insert(DictionaryValueCache).values(
                attribute_id=attribute_id,
                description_category_id=description_category_id,
                type_id=type_id,
                language=language,
                values_data=values_data,
                expires_at=expires_at,
                created_at=current_time,
            ).on_conflict_do_update(
                constraint="uq_dict_value_cache",
                set_=dict(values_data=values_data, expires_at=expires_at),
            )
            session.execute(stmt)
            session.commit()
            logger.info(f"✅ PG 写入成功：dictionary_value_cache（attribute_id={attribute_id}，有效期{expires_in}秒）")
        finally:
            session.close()

    def set_category_cache(self, ozon_client_id: str, tree_data: Dict[str, Any], language: str = "ZH_HANS", expires_in: int = 315360000):
        """写入类目树缓存"""
        current_time = int(time.time())
        expires_at = current_time + expires_in
        session = get_session()
        try:
            stmt = pg_insert(CategoryCache).values(
                ozon_client_id=ozon_client_id,
                language=language,
                tree_data=tree_data,
                expires_at=expires_at,
                created_at=current_time,
            ).on_conflict_do_update(
                constraint="uq_category_cache",
                set_=dict(tree_data=tree_data, expires_at=expires_at),
            )
            session.execute(stmt)
            session.commit()
            logger.info(f"✅ PG 写入成功：category_cache（client_id={ozon_client_id}，有效期{expires_in}秒）")
        finally:
            session.close()

    def sync_category_tree_nodes(self, tree_data: Dict[str, Any], language: str = "ZH_HANS") -> int:
        """
        从类目树 JSON 同步 category_tree_nodes 扁平表。

        在 set_category_cache() 之后调用，确保扁平表与 JSONB 缓存同步。
        递归遍历 tree_data["result"]，按 (description_category_id, type_id, language) 去重 upsert。

        Returns:
            写入的节点总数
        """
        from utils.ozon_category_query import get_category_query
        query = get_category_query()
        return query.sync_category_tree_nodes(tree_data, language)

    def set_exchange_rate(self, from_currency: str, to_currency: str, rate: float):
        """写入汇率"""
        current_time = int(time.time())
        session = get_session()
        try:
            stmt = pg_insert(ExchangeRate).values(
                from_currency=from_currency,
                to_currency=to_currency,
                rate=rate,
                updated_at=current_time,
            ).on_conflict_do_update(
                constraint="uq_exchange_rates",
                set_=dict(rate=rate, updated_at=current_time),
            )
            session.execute(stmt)
            session.commit()
            logger.info(f"✅ PG 写入成功：exchange_rates（{from_currency}→{to_currency}={rate}）")
        finally:
            session.close()

    def set_gateway_task(self, task_id: str, status: str, result_json: Optional[Dict[str, Any]] = None, stages: Optional[Dict[str, str]] = None, error: Optional[str] = None):
        """写入任务状态"""
        current_time = int(time.time())
        session = get_session()
        try:
            stmt = pg_insert(GatewayTask).values(
                task_id=task_id,
                status=status,
                result_json=result_json,
                stages=stages,
                error=error,
                created_at=current_time,
                updated_at=current_time,
            ).on_conflict_do_update(
                constraint="gateway_tasks_task_id_key",
                set_=dict(status=status, result_json=result_json, stages=stages, error=error, updated_at=current_time),
            )
            session.execute(stmt)
            session.commit()
            logger.info(f"✅ PG 写入成功：gateway_tasks（task_id={task_id}，status={status}）")
        finally:
            session.close()

    def add_attribute_mapping(self, category_id: int, attribute_id: int, attribute_name: str, source_value: str, target_value: str, dictionary_value_id: Optional[int] = None, source: str = "learned_approved"):
        """添加属性映射学习记录（PR-6: source 标记 provenance — learned_approved/default_fallback/retry_recovered/fetch_back_corrected）

        v0.64.0 C4(N5b): 改原子 upsert（INSERT ... ON CONFLICT）替代 SELECT→INSERT
        check-then-act——并发同键（同类目两商品同 source_value）不再抛 IntegrityError
        炸掉上传成功后任务。default_fallback 冲突时 success_count 不增长（PR-6 语义）。
        """
        current_time = int(time.time())
        session = get_session()
        try:
            # default_fallback 复用不增长 success_count（切断 Goodhart 棘轮）
            _do_update: dict = {
                "target_value": target_value,
                "dictionary_value_id": dictionary_value_id,
                "attribute_name": attribute_name,
                "source": source,
                "last_used_at": current_time,
            }
            if source != "default_fallback":
                _do_update["success_count"] = OzonAttributeMapping.success_count + 1
            stmt = pg_insert(OzonAttributeMapping).values(
                category_id=category_id,
                attribute_id=attribute_id,
                attribute_name=attribute_name,
                source_value=source_value,
                target_value=target_value,
                dictionary_value_id=dictionary_value_id,
                source=source,
                success_count=1,
                last_used_at=current_time,
                created_at=current_time,
            ).on_conflict_do_update(
                constraint="uq_ozon_attr_mappings",
                set_=_do_update,
            )
            session.execute(stmt)
            session.commit()
            logger.info(
                f"✅ PG upsert 成功：ozon_attribute_mappings "
                f"(cat={category_id}, attr={attribute_id}, source={source}, conflict→update)"
            )
        except Exception as _e:
            session.rollback()
            logger.warning(f"⚠️ add_attribute_mapping upsert 失败(非致命): {_e}")
        finally:
            session.close()

    # ═══════════════════════════════════════════════════
    # v4: Category Mapping CRUD (学习缓存)
    # ═══════════════════════════════════════════════════

    def get_category_mapping_by_leaf(self, source_category_leaf: str) -> List[Dict[str, Any]]:
        session = get_session()
        try:
            # ✅ v0.66 L0: 读排序改 is_active DESC, (success_count - fail_count) DESC ——
            # 负反馈后失败多的 learned 行排名靠后（declined 降权可被新成功超过）。
            rows = session.execute(
                select(CategoryMapping)
                .where(and_(CategoryMapping.source_category_leaf == source_category_leaf, CategoryMapping.is_active == True))
                .order_by(
                    CategoryMapping.is_active.desc(),
                    (CategoryMapping.success_count - CategoryMapping.fail_count).desc(),
                    CategoryMapping.last_used_at.desc(),
                )
            ).scalars().all()
            return [{
                "id": r.id, "source_category_leaf": r.source_category_leaf,
                "source_category_path": r.source_category_path,
                "source_keywords": r.source_keywords,
                "description_category_id": r.description_category_id,
                "type_id": r.type_id,
                "category_path_zh": r.category_path_zh,
                "category_path_ru": r.category_path_ru,
                "confidence": r.confidence,
                "success_count": r.success_count, "fail_count": r.fail_count,
                "source": r.source, "is_active": r.is_active,
            } for r in rows]
        finally:
            session.close()

    def get_category_mapping_by_source_id(self, source_category_id: int) -> List[Dict[str, Any]]:
        """按 1688 类目数字 ID 查学习映射（v0.25 T1，跨店铺稳定）。"""
        session = get_session()
        try:
            # ✅ v0.66 L0: 读排序同 get_category_mapping_by_leaf（is_active + 净成功分）。
            rows = session.execute(
                select(CategoryMapping)
                .where(and_(CategoryMapping.source_category_id == int(source_category_id),
                            CategoryMapping.is_active == True))
                .order_by(
                    CategoryMapping.is_active.desc(),
                    (CategoryMapping.success_count - CategoryMapping.fail_count).desc(),
                    CategoryMapping.last_used_at.desc(),
                )
            ).scalars().all()
            return [{
                "id": r.id, "source_category_id": r.source_category_id,
                "source_category_leaf": r.source_category_leaf,
                "source_category_path": r.source_category_path,
                "source_keywords": r.source_keywords,
                "description_category_id": r.description_category_id,
                "type_id": r.type_id,
                "category_path_zh": r.category_path_zh,
                "category_path_ru": r.category_path_ru,
                "confidence": r.confidence,
                "success_count": r.success_count, "fail_count": r.fail_count,
                "source": r.source, "is_active": r.is_active,
            } for r in rows]
        finally:
            session.close()

    def add_category_mapping(self, source_category_leaf: str, description_category_id: int,
                             type_id: int, source_category_path: Optional[str] = None,
                             source_keywords: Optional[List[str]] = None,
                             category_path_zh: Optional[str] = None,
                             category_path_ru: Optional[str] = None,
                             confidence: float = 0.7, source: str = "llm",
                             source_category_id: Optional[int] = None) -> None:
        """添加/累计类目映射学习记录（v0.66 L0 复活 W3 + c9/c10）。

        改原子 upsert（INSERT ... ON CONFLICT）替代 SELECT→INSERT check-then-act——
        对齐 add_attribute_mapping（v0.64 C4 N5b 成功先例）的 pg_insert/on_conflict 写法，
        并发同键（同 leaf+dc+tp 的并发 approved 任务）不再抛 IntegrityError 炸上传任务。
        conflict → set_：success_count+1 / is_active=True / source_category_id=coalesce(传入,
        存量) / path·keywords·zh·ru 传入非空才覆盖 / confidence=greatest(传入, 存量) /
        last_used_at=now。
        ✅ v0.66 P1-1（code-review）: 冲突侧 source 不得覆写存量 curated——learned 写命中
        同 key 的 curated 行时保留 curated（否则「curated 永不自动下线」保护失效 + init_data
        seed 与 approved 写互相翻转 source）；仅存量非 curated 才写传入 source。
        ✅ v0.66 P2-1（code-review）: on_conflict 用 index_elements 列推断（PG 按 (leaf,
        dc, tp) 唯一索引仲裁）替代 constraint= PG 自动截断名——消除环境差异依赖，风格对齐
        其它显式约束名 upsert 前先实测过自动名（category_mapping_source_category_leaf_
        description_category__key）可作对照。
        """
        import datetime as _dt
        session = get_session()
        try:
            _now = _dt.datetime.now(_dt.timezone.utc)
            _src_id = int(source_category_id) if source_category_id else None
            # 冲突侧更新表达式（SQLAlchemy 列表达式 → success_count=success_count+1 等）
            _do_update: dict = {
                "success_count": CategoryMapping.success_count + 1,
                # P1-1: 存量 curated 不被 learned/llm 等非 curated 写降级
                "source": case(
                    (CategoryMapping.source == "curated", CategoryMapping.source),
                    else_=source,
                ),
                "is_active": True,
                "last_used_at": _now,
                "source_category_id": func.coalesce(_src_id, CategoryMapping.source_category_id),
                "confidence": func.greatest(confidence, CategoryMapping.confidence),
            }
            if source_category_path:
                _do_update["source_category_path"] = source_category_path
            if source_keywords:
                _do_update["source_keywords"] = source_keywords
            if category_path_zh:
                _do_update["category_path_zh"] = category_path_zh
            if category_path_ru:
                _do_update["category_path_ru"] = category_path_ru
            stmt = pg_insert(CategoryMapping).values(
                source_category_leaf=source_category_leaf,
                source_category_id=_src_id,
                source_category_path=source_category_path,
                source_keywords=source_keywords or [],
                description_category_id=description_category_id,
                type_id=type_id,
                category_path_zh=category_path_zh,
                category_path_ru=category_path_ru,
                confidence=confidence,
                source=source,
                success_count=1,
                fail_count=0,
                is_active=True,
                created_at=_now,
                last_used_at=_now,
            ).on_conflict_do_update(
                index_elements=["source_category_leaf", "description_category_id", "type_id"],
                set_=_do_update,
            )
            session.execute(stmt)
            session.commit()
            logger.info(
                f"✅ PG upsert 成功：category_mapping "
                f"(leaf={source_category_leaf}, dc={description_category_id}, tp={type_id}, "
                f"source={source}, src_id={_src_id}, conflict→update)"
            )
        except Exception as _e:
            session.rollback()
            logger.warning(f"⚠️ add_category_mapping upsert 失败(非致命): {_e}")
        finally:
            session.close()

    def mark_category_mapping_failed(self, source_category_id: Optional[int] = None,
                                     source_category_leaf: Optional[str] = None,
                                     description_category_id: Optional[int] = None,
                                     type_id: Optional[int] = None) -> int:
        """v0.66 L0 负反馈：declined/类目错任务对该 L0 学习行降权。

        匹配行 = (source_category_id+dc+tp) 或 (source_category_leaf+dc+tp) 且
        source IN ('learned_approved','curated') 且 is_active → fail_count+1 +
        last_failed_at=now。learned 行累计 fail_count>=3 → is_active=False（自动下线）；
        curated 行只 +1 不自动 inactive（人工种子信任，Ozon 类目错不该吊销人工映射）。

        Returns:
            受影响行数（0 = 无匹配行 / 非学习来源，不降权）。
        """
        import datetime as _dt
        try:
            _dc = int(description_category_id or 0) or None
            _tp = int(type_id or 0) or None
        except (TypeError, ValueError):
            return 0
        if not _dc or not _tp:
            return 0
        if not source_category_id and not source_category_leaf:
            return 0
        session = get_session()
        try:
            _match = [
                CategoryMapping.description_category_id == _dc,
                CategoryMapping.type_id == _tp,
                CategoryMapping.is_active == True,
                CategoryMapping.source.in_(("learned_approved", "curated")),
            ]
            if source_category_id:
                _match.append(CategoryMapping.source_category_id == int(source_category_id))
            if source_category_leaf:
                _match.append(CategoryMapping.source_category_leaf == source_category_leaf)
            # 单语句：fail_count+1；learned 且（旧）fail_count+1>=3 → is_active=False；
            # curated（或未达阈值 learned）→ 保持 active。
            _now = _dt.datetime.now(_dt.timezone.utc)
            _stmt = (
                update(CategoryMapping)
                .where(and_(*_match))
                .values(
                    fail_count=CategoryMapping.fail_count + 1,
                    last_failed_at=_now,
                    is_active=case(
                        (and_(CategoryMapping.source == "learned_approved",
                              CategoryMapping.fail_count + 1 >= 3), False),
                        else_=True,
                    ),
                )
            )
            _res = session.execute(_stmt)
            session.commit()
            _affected = int(_res.rowcount or 0)
            if _affected:
                logger.warning(
                    f"⚠️ category_mapping 负反馈: {_affected} 行 fail+1"
                    f"(src_id={source_category_id}, leaf={source_category_leaf}, "
                    f"dc={_dc}, tp={_tp})"
                )
            return _affected
        except Exception as _e:
            session.rollback()
            logger.warning(f"⚠️ mark_category_mapping_failed 失败(非致命): {_e}")
            return 0
        finally:
            session.close()

    def get_attr_synonyms(self, source_attr_names: List[str]) -> Dict[str, Dict[str, Any]]:
        if not source_attr_names:
            return {}
        session = get_session()
        try:
            rows = session.execute(
                select(AttributeSynonym)
                .where(AttributeSynonym.source_attr_name.in_(source_attr_names))
                .order_by(AttributeSynonym.confidence.desc())
            ).scalars().all()
            result = {}
            for r in rows:
                if r.source_attr_name not in result:
                    result[r.source_attr_name] = {
                        "target_name": r.target_ozon_attr_name,
                        "target_id": r.target_ozon_attr_id,
                        "confidence": r.confidence,
                    }
            return result
        finally:
            session.close()

    def close(self):
        """关闭连接（PG 由连接池管理，无需手动关闭）"""
        logger.info("✅ PostgreSQL 缓存管理器已关闭")
