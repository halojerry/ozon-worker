"""1688→Ozon 类目映射学习入口（v0.25 T1）。

复用 LocalDBManager 既有 category_mapping 读写；本模块提供
「数字 ID 优先、leaf 兜底」的统一查询与回写，供 assemble/follow/learning 接入。
"""
from __future__ import annotations

import json
from pathlib import Path
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


# ── v0.63: curated 1688→Ozon 类目映射种子 ─────────────────────────
# 高冲突品类（汽车/摩托轮毂等）预置高置信映射，让 `source_category_id`/`leaf_name`
# 第一次就能命中，不必等 L0 学习累计 3 次。只收无歧义项，避免污染。
_SEED_PATH = Path(__file__).resolve().parents[2] / "config" / "category_mapping_seed.json"


def load_curated_seed() -> dict[str, dict[str, Any]]:
    """读取 curated 映射种子（纯函数，无 DB）。返回 {leaf_name: {dc,tp,confidence,path}}。"""
    try:
        data = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception as e:
        logger.warning("curated seed 加载失败（跳过）: %s", e)
        return {}


def seed_curated_mapping() -> int:
    """幂等写入 curated 映射到 category_mapping（success_count=5, source=curated）。"""
    from utils.local_db_manager import LocalDBManager
    seed = load_curated_seed()
    if not seed:
        return 0
    ldb = LocalDBManager()
    from storage.database.db import get_session
    from storage.database.shared.model import CategoryMapping
    from sqlalchemy import select
    written = 0
    for leaf, info in seed.items():
        dc = int(info.get("description_category_id") or 0)
        tp = int(info.get("type_id") or 0)
        if not dc or not tp:
            continue
        try:
            # 幂等：exists → 提升 success_count/confidence；new → success_count=5
            ldb.add_category_mapping(
                source_category_leaf=leaf,
                description_category_id=dc,
                type_id=tp,
                source_category_path=leaf or "",
                category_path_zh=info.get("category_path_zh") or "",
                confidence=float(info.get("confidence", 0.9)),
                source="curated",
            )
            # v0.63: curated 直接信任 → success_count 抬到 ≥5（L0 立即可用，无需累计）
            try:
                with get_session() as s:
                    row = s.execute(
                        select(CategoryMapping).where(
                            CategoryMapping.source_category_leaf == leaf,
                            CategoryMapping.description_category_id == dc,
                            CategoryMapping.type_id == tp,
                        )
                    ).scalar_one_or_none()
                    if row:
                        row.success_count = max(row.success_count or 0, 5)
                        row.confidence = max(row.confidence or 0, float(info.get("confidence", 0.9)))
                        row.source = "curated"
                        s.commit()
            except Exception as bump_e:
                logger.warning("curated seed success_count bump 失败 leaf=%s: %s", leaf, bump_e)
            written += 1
        except Exception as e:
            logger.warning("curated seed 写入失败 leaf=%s: %s", leaf, e)
    logger.info("✅ curated 类目映射种子写入完成: %d 条", written)
    return written
