"""
Ozon 类目树 PG 缓存查询助手

为 LLM 提供高效的类目搜索能力：
- pg_trgm 模糊匹配（支持中文）
- 类目树扁平化查询
- 属性 schema + 字典值缓存查询

使用方式：
    from utils.ozon_category_query import OzonCategoryQuery
    q = OzonCategoryQuery()
    candidates = q.search_nodes("园艺工具", top_k=15)
"""

import time
import logging
from typing import Optional, Any
from sqlalchemy import select, text, func, and_

from storage.database.db import get_session
from storage.database.shared.model import (
    CategoryTreeNode,
    AttributeCache,
    DictionaryValueCache,
    CategoryCache,
)

logger = logging.getLogger(__name__)


class OzonCategoryQuery:
    """Ozon 类目树查询助手（PG + pg_trgm）"""

    def search_nodes(
        self,
        query_text: str,
        top_k: int = 15,
        node_type: str | None = "type",
        language: str = "ZH_HANS",
    ) -> list[dict]:
        """
        pg_trgm 模糊搜索类目节点。

        如果 category_tree_nodes 表为空，自动尝试从 category_cache JSONB 同步。

        Args:
            query_text: 搜索关键词（中文或俄语）
            top_k: 返回结果数量
            node_type: 过滤节点类型 ("category" / "type" / None=全部)
            language: 语言代码

        Returns:
            [{description_category_id, type_id, node_name, full_path, similarity}, ...]
        """
        # 确保扁平表有数据
        self._ensure_nodes_synced(language)

        session = get_session()
        try:
            # pg_trgm similarity 查询
            similarity_expr = func.similarity(CategoryTreeNode.node_name, query_text)
            path_similarity_expr = func.similarity(CategoryTreeNode.full_path, query_text)

            stmt = (
                select(
                    CategoryTreeNode.description_category_id,
                    CategoryTreeNode.type_id,
                    CategoryTreeNode.node_name,
                    CategoryTreeNode.full_path,
                    CategoryTreeNode.top_level_category_name,
                    CategoryTreeNode.depth,
                    func.greatest(similarity_expr, path_similarity_expr).label("sim"),
                )
                .where(
                    and_(
                        CategoryTreeNode.language == language,
                        # 至少满足名称或路径任一模糊匹配
                        (
                            CategoryTreeNode.node_name.op("%")(query_text) |
                            CategoryTreeNode.full_path.op("%")(query_text)
                        ),
                    )
                )
                .order_by(text("sim DESC"))
                .limit(top_k)
            )

            if node_type:
                stmt = stmt.where(CategoryTreeNode.node_type == node_type)

            rows = session.execute(stmt).mappings().all()

            results = []
            for row in rows:
                results.append({
                    "description_category_id": row["description_category_id"],
                    "type_id": row["type_id"],
                    "node_name": row["node_name"],
                    "full_path": row["full_path"],
                    "top_level_category_name": row["top_level_category_name"],
                    "depth": row["depth"],
                    "similarity": round(float(row["sim"]), 4),
                })

            logger.info(
                f"🔍 pg_trgm 搜索完成：query='{query_text[:50]}...', "
                f"node_type={node_type}, 命中 {len(results)} 条"
            )
            
            # pg_trgm 无结果时回退到 ILIKE
            if not results:
                logger.info("pg_trgm 无结果，回退到 ILIKE")
                return self._search_fallback(query_text, top_k, node_type, language)
            
            return results

        except Exception as e:
            # pg_trgm 可能未安装，回退到 ILIKE
            logger.warning(f"pg_trgm 搜索失败 ({e})，回退到 ILIKE")
            return self._search_fallback(query_text, top_k, node_type, language)
        finally:
            session.close()

    def _search_fallback(
        self,
        query_text: str,
        top_k: int,
        node_type: str | None,
        language: str,
    ) -> list[dict]:
        """ILIKE 回退搜索。将查询拆分为单词，用 OR 连接多个 ILIKE 条件。"""
        session = get_session()
        try:
            # 拆分查询为单词，每个单词单独 ILIKE
            words = query_text.split()
            # 构建 OR 条件：每个单词对 node_name 和 full_path 做 ILIKE
            from sqlalchemy import or_
            conditions = [CategoryTreeNode.language == language]
            
            if words:
                word_conditions = []
                for word in words:
                    if len(word) >= 2:  # 跳过单字
                        pattern = f"%{word}%"
                        word_conditions.append(CategoryTreeNode.node_name.ilike(pattern))
                        word_conditions.append(CategoryTreeNode.full_path.ilike(pattern))
                if word_conditions:
                    conditions.append(or_(*word_conditions))
                else:
                    # 无有效单词，回退到整体查询
                    pattern = f"%{query_text}%"
                    conditions.append(
                        or_(
                            CategoryTreeNode.node_name.ilike(pattern),
                            CategoryTreeNode.full_path.ilike(pattern),
                        )
                    )
            else:
                pattern = f"%{query_text}%"
                conditions.append(
                    or_(
                        CategoryTreeNode.node_name.ilike(pattern),
                        CategoryTreeNode.full_path.ilike(pattern),
                    )
                )

            if node_type:
                conditions.append(CategoryTreeNode.node_type == node_type)

            stmt = (
                select(
                    CategoryTreeNode.description_category_id,
                    CategoryTreeNode.type_id,
                    CategoryTreeNode.node_name,
                    CategoryTreeNode.full_path,
                    CategoryTreeNode.top_level_category_name,
                    CategoryTreeNode.depth,
                )
                .where(and_(*conditions))
                .limit(top_k)
            )

            rows = session.execute(stmt).mappings().all()

            results = []
            for row in rows:
                results.append({
                    "description_category_id": row["description_category_id"],
                    "type_id": row["type_id"],
                    "node_name": row["node_name"],
                    "full_path": row["full_path"],
                    "top_level_category_name": row["top_level_category_name"],
                    "depth": row["depth"],
                    "similarity": 0.0,
                })

            logger.info(f"🔍 ILIKE 回退搜索：词数={len(words)}, 命中 {len(results)} 条")
            return results
        finally:
            session.close()

    # ==================== 内部方法 ====================

    def _ensure_nodes_synced(self, language: str):
        """确保 category_tree_nodes 扁平表已填充，否则从 category_cache 同步"""
        session = get_session()
        try:
            count_stmt = select(func.count(CategoryTreeNode.id)).where(
                CategoryTreeNode.language == language
            )
            node_count = session.execute(count_stmt).scalar() or 0
            if node_count > 0:
                return  # 已有数据
        finally:
            session.close()

        logger.info("category_tree_nodes 为空，尝试从 category_cache 同步...")
        self._try_sync_from_cache(language)

    def _try_sync_from_cache(self, language: str):
        """从 category_cache JSONB 同步到 category_tree_nodes 扁平表"""
        session = get_session()
        try:
            current_time = int(time.time())
            row = session.execute(
                select(CategoryCache).where(
                    and_(
                        CategoryCache.language == language,
                        CategoryCache.expires_at > current_time,
                    )
                ).order_by(CategoryCache.expires_at.desc()).limit(1)
            ).scalar_one_or_none()

            if row and row.tree_data:
                logger.info("从 category_cache JSONB 同步 category_tree_nodes...")
                # 需要在新的 session 中调用 sync（避免嵌套事务）
                self.sync_category_tree_nodes(row.tree_data, language)
                logger.info("✅ 同步完成")
            else:
                logger.warning("category_cache 中无有效数据，需通过 Ozon API 获取类目树")
        except Exception as e:
            logger.error(f"从缓存同步失败: {e}")
        finally:
            session.close()

    def _search_from_jsonb_cache(
        self,
        query_text: str,
        top_k: int,
        node_type: str | None,
        language: str,
    ) -> list[dict]:
        """
        从 category_cache JSONB 直接搜索（不依赖扁平表）。

        遍历整个类目树，匹配包含关键词的节点。
        当 category_tree_nodes 为空且无法从缓存同步时使用。
        """
        session = get_session()
        try:
            current_time = int(time.time())
            row = session.execute(
                select(CategoryCache).where(
                    and_(
                        CategoryCache.language == language,
                        CategoryCache.expires_at > current_time,
                    )
                ).order_by(CategoryCache.expires_at.desc()).limit(1)
            ).scalar_one_or_none()

            if not row or not row.tree_data:
                logger.warning("category_cache JSONB 也无数据")
                return []

            tree_data = row.tree_data
            # 兼容两种格式
            if isinstance(tree_data, dict):
                result_list = tree_data.get("result", [])
            elif isinstance(tree_data, list):
                result_list = tree_data
            else:
                logger.warning("category_cache JSONB 格式未知")
                return []

            results: list[dict] = []

            def _walk(children: list[dict], path_prefix: str, desc_cat_id: int | None, top_level: str):
                for node in children:
                    if not isinstance(node, dict):
                        continue
                    node_name = node.get("type_name", "") or node.get("category_name", "")
                    sub_children = node.get("children", [])
                    current_desc_id = node.get("description_category_id", desc_cat_id)
                    disabled = node.get("disabled", False)

                    current_path = f"{path_prefix} > {node_name}" if path_prefix else node_name

                    if sub_children:
                        # 类目节点
                        if not node_type or node_type == "category":
                            if query_text.lower() in node_name.lower():
                                results.append({
                                    "description_category_id": int(current_desc_id) if current_desc_id else 0,
                                    "type_id": None,
                                    "node_name": node_name,
                                    "full_path": current_path,
                                    "top_level_category_name": top_level or node_name,
                                    "depth": current_path.count(" > "),
                                    "similarity": 0.5,
                                })
                        _walk(sub_children, current_path, int(current_desc_id) if current_desc_id else None, top_level or node_name)
                    else:
                        # 叶子类型节点
                        type_id = node.get("type_id")
                        if type_id is not None and (not node_type or node_type == "type"):
                            if query_text.lower() in node_name.lower():
                                results.append({
                                    "description_category_id": int(current_desc_id) if current_desc_id else 0,
                                    "type_id": int(type_id),
                                    "node_name": node_name,
                                    "full_path": current_path,
                                    "top_level_category_name": top_level or node_name,
                                    "depth": current_path.count(" > "),
                                    "similarity": 0.5,
                                })

            _walk(result_list, "", None, "")
            logger.info(f"🔍 JSONB 搜索：'{query_text}' 命中 {len(results)} 条")
            return results[:top_k]
        finally:
            session.close()

    # ==================== 公共查询方法 ====================

    def get_top_categories(self, language: str = "ZH_HANS") -> list[dict]:
        """获取所有顶层类目（depth=0 的 category 节点）"""
        self._ensure_nodes_synced(language)

        session = get_session()
        try:
            rows = session.execute(
                select(
                    CategoryTreeNode.description_category_id,
                    CategoryTreeNode.node_name,
                )
                .where(
                    and_(
                        CategoryTreeNode.language == language,
                        CategoryTreeNode.node_type == "category",
                        CategoryTreeNode.depth == 0,
                    )
                )
                .order_by(CategoryTreeNode.node_name)
            ).mappings().all()

            return [
                {
                    "description_category_id": row["description_category_id"],
                    "category_name": row["node_name"],
                }
                for row in rows
            ]
        finally:
            session.close()

    def get_leaf_types_under(
        self,
        description_category_id: int,
        language: str = "ZH_HANS",
    ) -> list[dict]:
        """获取某个类目下的所有叶子类型节点"""
        session = get_session()
        try:
            # 先获取该类目的 full_path
            parent_row = session.execute(
                select(CategoryTreeNode.full_path)
                .where(
                    and_(
                        CategoryTreeNode.description_category_id == description_category_id,
                        CategoryTreeNode.language == language,
                        CategoryTreeNode.node_type == "category",
                    )
                )
            ).scalar_one_or_none()

            if not parent_row:
                return []

            # 查询所有 full_path 以该路径开头且为 type 的节点
            rows = session.execute(
                select(
                    CategoryTreeNode.description_category_id,
                    CategoryTreeNode.type_id,
                    CategoryTreeNode.node_name,
                    CategoryTreeNode.full_path,
                )
                .where(
                    and_(
                        CategoryTreeNode.language == language,
                        CategoryTreeNode.node_type == "type",
                        CategoryTreeNode.full_path.like(f"{parent_row}%"),
                    )
                )
                .order_by(CategoryTreeNode.full_path)
            ).mappings().all()

            return [
                {
                    "description_category_id": row["description_category_id"],
                    "type_id": row["type_id"],
                    "type_name": row["node_name"],
                    "full_path": row["full_path"],
                }
                for row in rows
            ]
        finally:
            session.close()

    def get_node(
        self,
        description_category_id: int,
        type_id: int | None = None,
        language: str = "ZH_HANS",
    ) -> dict | None:
        """精确查询单个节点（按 description_category_id + type_id）"""
        session = get_session()
        try:
            conditions = [
                CategoryTreeNode.description_category_id == description_category_id,
                CategoryTreeNode.language == language,
            ]
            if type_id is not None:
                conditions.append(CategoryTreeNode.type_id == type_id)
            else:
                conditions.append(CategoryTreeNode.type_id.is_(None))

            row = session.execute(
                select(CategoryTreeNode).where(and_(*conditions))
            ).scalar_one_or_none()

            if not row:
                return None

            return {
                "description_category_id": row.description_category_id,
                "type_id": row.type_id,
                "node_name": row.node_name,
                "node_type": row.node_type,
                "full_path": row.full_path,
                "top_level_category_name": row.top_level_category_name,
                "depth": row.depth,
            }
        finally:
            session.close()

    def get_attribute_schema(
        self,
        description_category_id: int,
        type_id: int,
        language: str = "ZH_HANS",
    ) -> dict | None:
        """获取指定类目+类型的属性 schema（从 attribute_cache 表）"""
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

            if row and row.attributes_schema:
                return row.attributes_schema
            return None
        finally:
            session.close()

    def get_dictionary_values(
        self,
        attribute_id: int,
        description_category_id: int,
        type_id: int,
        language: str = "ZH_HANS",
    ) -> list[dict] | None:
        """获取指定属性的字典值（从 dictionary_value_cache 表）"""
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

            if row and row.values_data:
                return row.values_data
            return None
        finally:
            session.close()

    def get_category_tree(self, ozon_client_id: str, language: str = "ZH_HANS") -> dict | None:
        """获取完整类目树 JSON（从 category_cache 表）"""
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

            if row and row.tree_data:
                return row.tree_data
            return None
        finally:
            session.close()

    # ==================== 扁平表同步 ====================

    def sync_category_tree_nodes(
        self,
        tree_data: dict,
        language: str = "ZH_HANS",
    ) -> int:
        """
        从类目树 JSON 同步 category_tree_nodes 扁平表。

        遍历 tree_data["result"] 数组，递归提取所有节点，
        按 (description_category_id, type_id, language) 去重 upsert。

        Returns:
            写入的节点总数
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        current_time = int(time.time())
        session = get_session()

        nodes_to_insert: list[dict[str, Any]] = []

        def _walk(
            children: list[dict],
            parent_desc_id: int | None,
            path_prefix: str,
            top_level_name: str,
            depth: int,
        ):
            for node in children:
                desc_id = node.get("description_category_id", 0)
                category_name = node.get("category_name", "")
                disabled = node.get("disabled", False)
                sub_children = node.get("children", [])

                if sub_children:
                    # 中间类目节点
                    current_path = f"{path_prefix} > {category_name}" if path_prefix else category_name
                    nodes_to_insert.append({
                        "description_category_id": desc_id,
                        "type_id": None,
                        "node_name": category_name,
                        "node_type": "category",
                        "parent_description_category_id": parent_desc_id,
                        "full_path": current_path,
                        "top_level_category_name": top_level_name or category_name,
                        "depth": depth,
                        "disabled": disabled,
                        "language": language,
                        "created_at": current_time,
                    })
                    _walk(
                        sub_children,
                        parent_desc_id=desc_id,
                        path_prefix=current_path,
                        top_level_name=top_level_name or category_name,
                        depth=depth + 1,
                    )
                else:
                    # 叶子类型节点（type_name + type_id + children=[]）
                    # 注意：叶子节点没有自己的 description_category_id，使用父级类目ID
                    type_name = node.get("type_name", "")
                    type_id = node.get("type_id")
                    leaf_desc_id = desc_id if desc_id and desc_id != 0 else parent_desc_id
                    if type_name and type_id is not None:
                        current_path = f"{path_prefix} > {type_name}" if path_prefix else type_name
                        nodes_to_insert.append({
                            "description_category_id": leaf_desc_id or 0,
                            "type_id": type_id,
                            "node_name": type_name,
                            "node_type": "type",
                            "parent_description_category_id": parent_desc_id,
                            "full_path": current_path,
                            "top_level_category_name": top_level_name or type_name,
                            "depth": depth + 1,
                            "disabled": disabled,
                            "language": language,
                            "created_at": current_time,
                        })

        try:
            # 兼容两种格式：{"result": [...]} 或直接是列表 [...]
            if isinstance(tree_data, dict):
                result_list = tree_data.get("result", [])
            elif isinstance(tree_data, list):
                result_list = tree_data
            else:
                logger.error(f"❌ 类目树数据格式错误: {type(tree_data)}")
                return 0

            _walk(result_list, parent_desc_id=None, path_prefix="", top_level_name="", depth=0)

            if not nodes_to_insert:
                logger.warning("⚠️ 类目树为空，未同步任何节点")
                return 0

            # 批量 upsert
            for node in nodes_to_insert:
                type_id_val = node["type_id"]
                stmt = pg_insert(CategoryTreeNode).values(**node).on_conflict_do_update(
                    constraint="uq_category_tree_nodes",
                    set_={
                        "node_name": node["node_name"],
                        "full_path": node["full_path"],
                        "top_level_category_name": node["top_level_category_name"],
                        "depth": node["depth"],
                        "disabled": node["disabled"],
                        "created_at": current_time,
                    },
                )
                session.execute(stmt)

            session.commit()
            logger.info(f"✅ category_tree_nodes 同步完成：{len(nodes_to_insert)} 个节点")
            return len(nodes_to_insert)

        except Exception as e:
            session.rollback()
            logger.error(f"❌ category_tree_nodes 同步失败: {e}")
            raise
        finally:
            session.close()


# 模块级单例
_category_query: OzonCategoryQuery | None = None


def get_category_query() -> OzonCategoryQuery:
    """获取 OzonCategoryQuery 单例"""
    global _category_query
    if _category_query is None:
        _category_query = OzonCategoryQuery()
    return _category_query
