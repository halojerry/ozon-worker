"""1688→Ozon 类目映射学习入口（v0.25 T1）。

复用 LocalDBManager 既有 category_mapping 读写；本模块提供
「数字 ID 优先、leaf 兜底」的统一查询与回写，供 assemble/follow/learning 接入。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# v0.27: 学习映射须累计成功 3 次才被视为可信(甩脂机/手串 success_count=1 即固化 → 66% 污染率实证)
MIN_SUCCESS_COUNT = 3
MIN_CONFIDENCE = 0.6


def lookup_mapping(
    source_category_id: Optional[int] = None,
    leaf_name: str = "",
) -> Optional[dict]:
    """按 1688 类目查询已学习的 Ozon 类目：数字 ID 优先，其次末级名。

    返回 {"dc","tp","confidence"}；命中但 success/confidence 不足返回 None。
    """
    from utils.local_db_manager import LocalDBManager
    ldb = LocalDBManager()
    rows: list = []
    if source_category_id:
        try:
            rows = ldb.get_category_mapping_by_source_id(int(source_category_id))
            if rows:
                logger.info("类目映射按 1688类目ID=%s 命中 %d 条", source_category_id, len(rows))
        except Exception as e:
            logger.warning("类目映射按 ID 查询失败: %s", e)
            rows = []
    if not rows and leaf_name:
        try:
            rows = ldb.get_category_mapping_by_leaf(leaf_name)
        except Exception as e:
            logger.warning("类目映射按 leaf 查询失败: %s", e)
            rows = []
    if not rows:
        return None
    best = rows[0]
    if (best.get("success_count") or 0) < MIN_SUCCESS_COUNT or (best.get("confidence") or 0) < MIN_CONFIDENCE:
        logger.info("类目映射命中但置信不足: succ=%s conf=%s",
                    best.get("success_count"), best.get("confidence"))
        return None
    return {
        "dc": str(best["description_category_id"]),
        "tp": str(best["type_id"]),
        "confidence": float(best.get("confidence") or 0.7),
    }


def record_mapping(
    source_category_id: Optional[int],
    leaf_name: str,
    dc: int,
    tp: int,
    path_zh: str = "",
    path_ru: str = "",
) -> None:
    """上传成功（approved）后回写/累计映射。"""
    from utils.local_db_manager import LocalDBManager
    try:
        LocalDBManager().add_category_mapping(
            source_category_leaf=leaf_name,
            source_category_id=int(source_category_id) if source_category_id else None,
            description_category_id=int(dc),
            type_id=int(tp),
            source_category_path=leaf_name or "",
            category_path_zh=path_zh or None,
            category_path_ru=path_ru or None,
            confidence=0.85,
            source="learned_approved",
        )
        logger.info("📝 类目映射已记录: %s(%s) → dc=%s tp=%s", leaf_name, source_category_id, dc, tp)
    except Exception as e:
        logger.warning("类目映射回写失败（非致命）: %s", e)
