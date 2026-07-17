"""
PostgreSQL 缓存管理器 — 替代旧 SQLite 本地缓存
用途：缓存高频查询数据，降低 Ozon API 调用延迟
策略：PostgreSQL 优先查询 + Ozon API Fallback
"""

import json
import time
import logging
from typing import Optional, Dict, Any, List

from sqlalchemy import select, and_
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
)

logger = logging.getLogger(__name__)


class LocalDBManager:
    """PostgreSQL 缓存管理器（保持与旧 SQLite 版本相同的方法签名）"""

    def __init__(self, db_path: str = ""):
        """初始化（db_path 参数保留兼容，实际不再使用 SQLite 文件路径）"""
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

    def add_attribute_mapping(self, category_id: int, attribute_id: int, attribute_name: str, source_value: str, target_value: str, dictionary_value_id: Optional[int] = None):
        """添加属性映射学习记录"""
        current_time = int(time.time())
        session = get_session()
        try:
            # 检查是否已存在
            existing = session.execute(
                select(OzonAttributeMapping).where(
                    and_(
                        OzonAttributeMapping.category_id == category_id,
                        OzonAttributeMapping.attribute_id == attribute_id,
                        OzonAttributeMapping.source_value == source_value,
                    )
                )
            ).scalar_one_or_none()

            if existing:
                existing.success_count = (existing.success_count or 0) + 1
                existing.last_used_at = current_time
                session.commit()
                logger.info(f"✅ PG 更新成功：ozon_attribute_mappings（映射已存在，success_count+1）")
            else:
                new_mapping = OzonAttributeMapping(
                    category_id=category_id,
                    attribute_id=attribute_id,
                    attribute_name=attribute_name,
                    source_value=source_value,
                    target_value=target_value,
                    dictionary_value_id=dictionary_value_id,
                    success_count=1,
                    last_used_at=current_time,
                    created_at=current_time,
                )
                session.add(new_mapping)
                session.commit()
                logger.info(f"✅ PG 写入成功：ozon_attribute_mappings（新映射）")
        finally:
            session.close()

    def close(self):
        """关闭连接（PG 由连接池管理，无需手动关闭）"""
        logger.info("✅ PostgreSQL 缓存管理器已关闭")
