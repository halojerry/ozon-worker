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
from sqlalchemy import select, text, func, and_, or_

from storage.database.db import get_session
from storage.database.shared.model import (
    CategoryTreeNode,
    AttributeCache,
    DictionaryValueCache,
    CategoryCache,
)

try:
    import jieba
except ImportError:
    jieba = None

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
        搜索类目节点。

        中文（ZH_HANS）：jieba 分词 + LIKE 精确匹配（替代 pg_trgm，避免字符三元组噪声）
        俄语（RU）及其他：pg_trgm 模糊匹配 + ILIKE 回退

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

        # ✅ v5: ZH_HANS 使用 jieba 分词 + LIKE 精确匹配
        if language == "ZH_HANS" and jieba is not None:
            return self._search_jieba_like(query_text, top_k, node_type)

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

            # ✅ 排除无效类目节点：category 节点的 type_id 为 NULL/0
            stmt = stmt.where(
                CategoryTreeNode.type_id.isnot(None),
                CategoryTreeNode.type_id > 0,
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

    def _search_jieba_like(
        self,
        query_text: str,
        top_k: int,
        node_type: str | None,
    ) -> list[dict]:
        """v5: jieba 分词 + LIKE 精确匹配（中文类目搜索）

        原理：pg_trgm 对中文做字符三元组匹配会产生大量噪声（如"鱼桶"→"鱼钩"）。
        jieba 分词后用 LIKE 做词级匹配，准确度高得多。

        计分：匹配 token 数 × 1.0 + depth × 0.1
        """
        session = get_session()
        try:
            # 1. jieba 分词
            tokens = [w.strip() for w in jieba.cut(query_text) if len(w.strip()) >= 2]
            if not tokens:
                # 无双字词，回退到 ILIKE
                return self._search_fallback(query_text, top_k, node_type, "ZH_HANS")

            # ✅ v0.21: 泛化词（配件/用品/工具/通用等）从搜索与评分中剥离——
            # 否则 OR 条件被泛化词洪泛，LIMIT 内的候选全是泛化命中，
            # 具体词命中（如"后视镜"→"摩托车后视镜"）被挤出结果集。
            _GENERIC = {
                "运动", "休闲", "传统", "家用", "日用", "通用", "其他", "配件", "附件",
                "用品", "工具", "系列", "套装", "组合", "跨境", "新款", "爆款",
                "设备", "材料", "商品", "产品", "机械", "电器", "平板", "监测", "清洁",
            }
            _specific = [t for t in tokens if t not in _GENERIC
                         and not any(gw in t for gw in _GENERIC if len(gw) >= 2)]
            search_tokens = _specific if _specific else tokens
            if len(search_tokens) < len(tokens):
                logger.info(f"🔤 剥离泛化词后搜索 tokens: {search_tokens}")
            tokens = search_tokens

            # 去重但保持顺序
            seen = set()
            unique_tokens = []
            for t in tokens:
                if t not in seen:
                    seen.add(t)
                    unique_tokens.append(t)
            tokens = unique_tokens

            logger.info(f"🔤 jieba 分词: '{query_text[:60]}' → {tokens}")

            # 2/3. ✅ v0.21: 逐 token 查询后按 (dc, tp) 合并——
            # 单一 OR + 无排序 LIMIT 会被常见词（电动车/摩托车/踏板）灌满，
            # 稀有词命中（如"后视镜"→"摩托车后视镜"）被挤出候选集。
            # 逐 token 各取 top_k，保证每个词的命中都能进入候选。
            _cols = (
                CategoryTreeNode.description_category_id,
                CategoryTreeNode.type_id,
                CategoryTreeNode.node_name,
                CategoryTreeNode.full_path,
                CategoryTreeNode.top_level_category_name,
                CategoryTreeNode.depth,
            )
            collected: dict[tuple, dict] = {}
            for token in tokens:
                tok_cond = [
                    CategoryTreeNode.language == "ZH_HANS",
                    CategoryTreeNode.type_id.isnot(None),
                    CategoryTreeNode.type_id > 0,
                ]
                if node_type:
                    tok_cond.append(CategoryTreeNode.node_type == node_type)
                tok_cond.append(or_(
                    CategoryTreeNode.node_name.ilike(f"%{token}%"),
                    CategoryTreeNode.full_path.ilike(f"%{token}%"),
                ))
                stmt = (
                    select(*_cols)
                    .where(and_(*tok_cond))
                    .limit(top_k)
                )
                for row in session.execute(stmt).mappings().all():
                    key = (row["description_category_id"], row["type_id"])
                    if key not in collected:
                        collected[key] = dict(row)
            rows = list(collected.values())
            logger.info(f"🔤 逐token合并候选: {len(rows)} 条（tokens={len(tokens)}）")

            # 4. 计分：匹配 token 数 + depth bonus
            _GENERIC_WORDS = {
                "运动", "休闲", "传统", "家用", "日用", "通用", "其他", "配件", "附件",
                "用品", "工具", "系列", "套装", "组合", "跨境", "新款", "爆款",
                "设备", "材料", "商品", "产品",
                "机械", "电器", "平板", "监测", "清洁", "钢丝", "电器配件", "平板电脑",
            }
            scored: list[tuple[float, dict]] = []

            for row in rows:
                combined = (row["node_name"] or "") + " " + (row["full_path"] or "")
                combined_lower = combined.lower()

                matched = 0.0
                matched_tokens: list[str] = []
                for token in tokens:
                    if token.lower() in combined_lower:
                        weight = 0.3 if token in _GENERIC_WORDS else 1.0
                        matched += weight
                        matched_tokens.append(token)

                if matched == 0:
                    continue  # 跳过零匹配

                # AND 加分：所有 token 都匹配的节点额外加分（至少 2 个 token 才有意义）
                and_bonus = 0.0
                if len(matched_tokens) == len(tokens) and len(tokens) >= 2:
                    and_bonus = 1.0

                depth_bonus = min((row["depth"] or 0) * 0.1, 1.0)
                score = matched + depth_bonus + and_bonus

                scored.append((score, {
                    "description_category_id": row["description_category_id"],
                    "type_id": row["type_id"],
                    "node_name": row["node_name"],
                    "full_path": row["full_path"],
                    "top_level_category_name": row["top_level_category_name"],
                    "depth": row["depth"],
                    "similarity": round(matched / max(len(tokens), 1), 4),
                    "matched_tokens": matched_tokens,
                    "_score": score,
                    "_generic_only": all(t in _GENERIC_WORDS or any(gw in t for gw in _GENERIC_WORDS if len(gw) >= 2) for t in matched_tokens),
                }))

            # 5. 过滤：只有泛化词匹配的结果不可靠 → 返回空触发 L3 LLM
            non_generic_results = []
            for score, item in scored:
                if item.get("_generic_only") and item.get("_score", 0) < 1.5:
                    continue
                non_generic_results.append((score, item))

            if non_generic_results:
                scored = non_generic_results
            elif scored:
                # 所有结果都是泛化词匹配 → 质量太低，返回空
                logger.info(
                    f"🔤 jieba LIKE: 所有候选都是泛化词匹配（{scored[0][1].get('matched_tokens', [])}），"
                    f"返回空触发 L3 LLM fallback"
                )
                scored = []

            # 6. 按分数降序排列
            scored.sort(key=lambda x: x[0], reverse=True)
            # ✅ v0.21: top1 全泛化词命中（如"器具/用品/配件"）→ 质量太低，返回空触发 L3 LLM
            if scored and scored[0][1].get("_generic_only"):
                logger.info(
                    f"🔤 jieba LIKE: top1 为全泛化词匹配（{scored[0][1].get('matched_tokens', [])}），"
                    f"返回空触发 L3 LLM"
                )
                return []
            results = [item for _, item in scored[:top_k]]

            logger.info(
                f"🔤 jieba LIKE 搜索完成：tokens={tokens}, "
                f"候选={len(rows)}条, 匹配={len(scored)}条, 返回={len(results)}条"
            )
            if results:
                top = results[0]
                logger.info(
                    f"   🥇 Top-1: [{top['description_category_id']}/{top['type_id']}] "
                    f"{top['full_path']} (sim={top['similarity']:.2f}, tokens={top.get('matched_tokens', [])})"
                )

            # 无结果时：不fallback到ILIKE（避免pg_trgm噪声），返回空触发L3 LLM
            if not results:
                logger.info("jieba LIKE 无可靠结果，返回空触发 L3 LLM（不fallback到ILIKE避免噪声）")
                return []

            return results

        except Exception as e:
            logger.warning(f"jieba LIKE 搜索失败 ({e})，回退到 ILIKE")
            return self._search_fallback(query_text, top_k, node_type, "ZH_HANS")
        finally:
            session.close()

    def _search_fallback(
        self,
        query_text: str,
        top_k: int,
        node_type: str | None,
        language: str,
    ) -> list[dict]:
        """ILIKE 回退搜索。将查询拆分为单词，用 OR 连接多个 ILIKE 条件。
        
        增强：按匹配关键词数量降序排列，优先返回最相关的结果。
        防止"迷你"匹配到"迷你打印机"而非"园艺工具"这类问题。
        """
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

            # 获取更多候选（top_k * 3），然后在 Python 中按匹配度排序
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
                .limit(top_k * 3)
            )

            rows = session.execute(stmt).mappings().all()

            # === 按匹配关键词比率排序 ===
            # 匹配率 = 匹配关键词数 / 总关键词数（权重2.0）
            # 深度加分：更深的节点通常是更具体的类目（叶子节点）
            # v0.8.0: 泛化词（如"运动"、"休闲"）匹配权重降低，防止稀释信号
            _GENERIC_WORDS = {"运动", "休闲", "传统", "家用", "日用", "通用", "其他", "配件", "附件",
                             "спорт", "отдых", "традиционный", "домашний", "универсальный",
                             "прочее", "аксессуар", "для",
                             "用品", "工具", "系列", "套装", "设备", "材料", "商品", "产品"}
            scored_results: list[tuple[float, dict]] = []
            total_keywords = max(len([w for w in words if len(w) >= 2]), 1)

            for row in rows:
                combined = (row["node_name"] or "") + " " + (row["full_path"] or "")
                combined_lower = combined.lower()

                match_count = 0.0  # 加权匹配计数
                for word in words:
                    if len(word) >= 2 and word.lower() in combined_lower:
                        # 泛化词权重 0.3，具体词权重 1.0
                        weight = 0.3 if word.strip() in _GENERIC_WORDS or word.lower().strip() in _GENERIC_WORDS else 1.0
                        match_count += weight

                # 匹配率作为主排序因子（0~2.0），深度作为次排序因子（0~1.0）
                match_ratio = (match_count / total_keywords) * 2.0
                depth_bonus = min((row["depth"] or 0) * 0.1, 1.0)

                score = match_ratio + depth_bonus
                
                scored_results.append((score, {
                    "description_category_id": row["description_category_id"],
                    "type_id": row["type_id"],
                    "node_name": row["node_name"],
                    "full_path": row["full_path"],
                    "top_level_category_name": row["top_level_category_name"],
                    "depth": row["depth"],
                    "similarity": float(match_count) / max(len(words), 1),
                }))
            
            # 按分数降序排列
            scored_results.sort(key=lambda x: x[0], reverse=True)
            
            results = [item for _, item in scored_results[:top_k]]

            logger.info(f"🔍 ILIKE 回退搜索（已排序）：词数={len(words)}, 候选={len(rows)}条, 返回={len(results)}条")
            if results:
                top = results[0]
                logger.info(f"   🥇 Top-1: [{top['description_category_id']}/{top['type_id']}] {top['full_path']} (similarity={top['similarity']:.2f})")
            return results
        finally:
            session.close()

    def get_node_by_description_category_id(self, dc_id: int) -> dict | None:
        """
        按 description_category_id 直接查找类目节点（无需 pg_trgm 搜索）。

        用于 Worker 侧接收 Skill 传来的数字 ID 时，直接查表获取 type_id。
        """
        session = get_session()
        try:
            row = session.execute(
                select(
                    CategoryTreeNode.description_category_id,
                    CategoryTreeNode.type_id,
                    CategoryTreeNode.node_name,
                    CategoryTreeNode.full_path,
                ).where(
                    CategoryTreeNode.description_category_id == dc_id,
                    CategoryTreeNode.type_id > 0,
                ).limit(1)
            ).mappings().first()

            if row:
                return {
                    "description_category_id": row["description_category_id"],
                    "type_id": row["type_id"],
                    "node_name": row["node_name"],
                    "full_path": row["full_path"],
                }
            return None
        except Exception as e:
            logger.warning(f"get_node_by_description_category_id({dc_id}) 失败: {e}")
            return None
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
        """从 category_cache JSONB 同步到 category_tree_nodes 扁平表（持久化数据，不过期）"""
        session = get_session()
        try:
            # 类目树是持久化参考数据，不检查过期时间，取最新一条
            row = session.execute(
                select(CategoryCache).where(
                    CategoryCache.language == language,
                ).order_by(CategoryCache.created_at.desc()).limit(1)
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

    def score_candidates_by_fingerprint(
        self, candidates: list[dict], source_keywords: list[str],
    ) -> list[dict]:
        """v4: Re-rank pg_trgm candidates using keyword overlap + learned mappings + domain hints.
        Returns candidates sorted by (fingerprint_score DESC, pg_trgm_similarity DESC).
        """
        if not source_keywords or not candidates:
            return candidates

        source_kw_set = {kw.lower() for kw in source_keywords if len(kw) >= 2}

        # Load domain hints (with caching)
        domain_hints = self._load_domain_hints()
        
        # Check if any source keyword triggers a domain hint
        triggered_hints = []
        if domain_hints:
            for dh in domain_hints:
                for kw in source_kw_set:
                    if kw in dh["trigger_keywords"]:
                        triggered_hints.append(dh)
                        break
            if triggered_hints:
                logger.info(f"🎯 domain_hint触发: keywords={[kw for kw in source_kw_set if any(kw in dh['trigger_keywords'] for dh in domain_hints)]} "
                            f"→ targets={[dh['target_top_category'] for dh in triggered_hints]}")

        # Learned mappings for bonus
        learned: dict = {}
        try:
            from storage.database.db import get_session as _gs
            from storage.database.shared.model import CategoryMapping as _CM
            s = _gs()
            try:
                dc_ids = [c["description_category_id"] for c in candidates]
                tid_ids = [c["type_id"] for c in candidates if c.get("type_id")]
                if dc_ids and tid_ids:
                    rows = s.execute(
                        select(_CM.description_category_id, _CM.type_id, _CM.success_count, _CM.source_keywords)
                        .where(and_(_CM.description_category_id.in_(set(dc_ids)), _CM.type_id.in_(set(tid_ids)), _CM.is_active == True))
                    ).mappings().all()
                    for row in rows:
                        learned[(row["description_category_id"], row["type_id"])] = {
                            "success_count": row["success_count"],
                            "stored_keywords": set(row["source_keywords"] or []),
                        }
            finally:
                s.close()
        except Exception:
            pass

        def _score(c: dict) -> float:
            path = (c.get("full_path", "") or "").lower()
            name = (c.get("node_name", "") or "").lower()
            depth = c.get("depth", 0) or 0
            path_overlap = sum(1 for kw in source_kw_set if kw in path)
            name_overlap = sum(1 for kw in source_kw_set if kw in name)
            learned_bonus = 0.0
            key = (c.get("description_category_id"), c.get("type_id"))
            if key in learned:
                lk = learned[key]["stored_keywords"]
                kw_overlap = len(source_kw_set & lk)
                learned_bonus = kw_overlap * 0.5 + min(learned[key]["success_count"], 10) * 0.05

            # v4: domain_hint bonus/penalty
            domain_bonus = 0.0
            if triggered_hints:
                top_cat = (c.get("top_level_category_name", "") or "").lower()
                for dh in triggered_hints:
                    target = (dh["target_top_category"] or "").lower()
                    exclude = (dh["exclude_top_category"] or "").lower()
                    if target and target in top_cat:
                        domain_bonus += 1.5  # strong bonus for matching domain
                    if exclude and exclude in top_cat:
                        domain_bonus -= 1.5  # penalty for excluded domain

            return path_overlap * 0.5 + name_overlap * 1.0 + min(depth * 0.1, 1.0) + learned_bonus + domain_bonus

        for c in candidates:
            c["fingerprint_score"] = round(_score(c), 3)
        candidates.sort(key=lambda c: (c.get("fingerprint_score", 0), c.get("similarity", 0)), reverse=True)
        return candidates

    def get_category_mapping_by_keywords(
        self, source_keywords: list[str], min_overlap: int = 1, top_k: int = 10,
    ) -> list[dict]:
        """v4: Direct lookup of learned Ozon categories by keyword overlap."""
        if not source_keywords:
            return []
        session = get_session()
        try:
            from sqlalchemy import text as _txt
            kw_array = "{" + ",".join(f'"{kw}"' for kw in source_keywords) + "}"
            rows = session.execute(_txt("""
                SELECT id, source_category_leaf, source_category_path, source_keywords,
                       description_category_id, type_id, category_path_zh, category_path_ru,
                       confidence, success_count
                FROM category_mapping WHERE is_active = TRUE AND source_keywords && :kw::text[]
                ORDER BY success_count DESC, confidence DESC LIMIT :lim
            """), {"kw": kw_array, "lim": top_k}).mappings().all()
            results = []
            for r in rows:
                stored = set(r["source_keywords"] or [])
                overlap = len(set(source_keywords) & stored)
                results.append({
                    "source_category_leaf": r["source_category_leaf"],
                    "source_category_path": r["source_category_path"],
                    "source_keywords": r["source_keywords"],
                    "description_category_id": r["description_category_id"],
                    "type_id": r["type_id"],
                    "category_path_zh": r["category_path_zh"],
                    "category_path_ru": r["category_path_ru"],
                    "confidence": r["confidence"], "success_count": r["success_count"],
                    "keyword_overlap": overlap,
                })
            results.sort(key=lambda x: (x["keyword_overlap"], x["success_count"]), reverse=True)
            return results[:top_k]
        except Exception as e:
            logger.warning(f"category_mapping keyword lookup failed: {e}")
            return []
        finally:
            session.close()

    def _load_domain_hints(self) -> list[dict]:
        """v4: Load active domain disambiguation rules from PG. Cached per instance."""
        if hasattr(self, '_cached_domain_hints'):
            return self._cached_domain_hints
        try:
            from sqlalchemy import text as _txt
            s = get_session()
            try:
                rows = s.execute(_txt(
                    "SELECT trigger_keywords, target_top_category, exclude_top_category FROM domain_hint WHERE is_active=TRUE ORDER BY priority DESC"
                )).mappings().all()
                self._cached_domain_hints = [dict(r) for r in rows]
            finally:
                s.close()
        except Exception:
            self._cached_domain_hints = []
        return self._cached_domain_hints

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
