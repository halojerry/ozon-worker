"""字典值缓存三桶策略（v0.72，字典缓存撑爆 40G 盘事故根治）。

事故与定案（2026-09-09，记忆 dict-cache-disk-explosion-incident /
ozon-dict-api-semantics）：`dictionary_value_cache` 按 (attr,dc,tp) 键把全局
字典按类目整份复制——品牌 85 字典 5.18MB × 每节点一份，17% 预热即 3.83GB、
全量外推 20GB+。真实 API 实测语义：
- `category_dependent=false`（原产国/保证类）→ 跨类目逐字节一致，可全局一份；
- `category_dependent=true`（类型 8229/颜色/HS 编码）→ 按类目隔离但小字典
  KB 级，保持 (attr,dc,tp)；
- 巨型字典（品牌 85，翻 10 页 20000 值仍 has_next）→ **不物化**，运行时走
  `/values/search`（utils/ozon_dict_values.py，已实证可用）。

三桶判定（本模块唯一入口）：
- global：cat_dep=False
- scoped：cat_dep=True（缺字段/未知 → 保守按 scoped，行为与旧版一致）
- ephemeral：取值数超 `DICT_MATERIALIZE_MAX_VALUES`（即首页 limit 就
  has_next）→ 不物化不落库；体积判定天然覆盖品牌类，无需按 attr_id 硬编码。

**全局桶 = 哨兵键 (dc=0, tp=0)，零 DDL**：现有表和唯一键不动，dc=0 不会与
真实类目冲突，ON CONFLICT 正常命中（并绕开 NULL type_id 使 ON CONFLICT
永不命中的既有雷）。读侧 scoped→global 回退由
`OzonCategoryQuery.get_dictionary_values` 内置。
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# 全局桶哨兵键（写入 dictionary_value_cache 的 dc/tp 位）
GLOBAL_DC = 0
GLOBAL_TP = 0

# Ozon /values 契约上限（实测 limit=5000 被静默钳到 2000——违约调用）
DICT_PAGE_LIMIT = 2000
# 物化上限：首页即 has_next → ephemeral（不物化）。≈ 一页 2000 值。
DICT_MATERIALIZE_MAX_VALUES = DICT_PAGE_LIMIT

# 统一 TTL：30 天（warm/init_data/local_db_manager 三处一致口径）
DICT_CACHE_TTL_SECONDS = 30 * 86400

BUCKET_GLOBAL = "global"
BUCKET_SCOPED = "scoped"
BUCKET_EPHEMERAL = "skipped_ephemeral"


def is_category_dependent(attr_row: Any) -> bool:
    """schema 行的按类目隔离开关。字段缺失/无法判定 → True（保守按 scoped，
    与旧版行为一致——老缓存行与旧测试夹具均无该字段）。"""
    if not isinstance(attr_row, dict):
        return True
    return bool(attr_row.get("category_dependent", True))


def classify_bucket(attr_row: Any, truncated: bool = False,
                    value_count: int = 0) -> str:
    """三桶分类（纯函数）。truncated=首页即 has_next（dicts >2000 值）。"""
    if truncated or value_count > DICT_MATERIALIZE_MAX_VALUES:
        return BUCKET_EPHEMERAL
    return BUCKET_SCOPED if is_category_dependent(attr_row) else BUCKET_GLOBAL


def routed_get(attr_id: int, dc: int, tp: int,
               language: str = "ZH_HANS") -> Optional[List[dict]]:
    """读缓存：scoped (attr,dc,tp) 优先，未命中回退 global (attr,0,0)。

    回退逻辑内置于 OzonCategoryQuery.get_dictionary_values（所有纯缓存读者
    自动生效）。异常吞掉返回 None（调用方自行回源 API）。
    """
    try:
        from utils.ozon_category_query import get_category_query
        return get_category_query().get_dictionary_values(
            attr_id, dc, tp, language=language)
    except Exception:
        return None


def routed_set(attr_id: int, values: List[dict], dc: int, tp: int, *,
               attr_row: Any = None, category_dependent: Optional[bool] = None,
               truncated: bool = False, language: str = "ZH_HANS",
               expires_in: int = DICT_CACHE_TTL_SECONDS) -> str:
    """按桶写缓存，返回落桶结果（global/scoped/skipped_ephemeral）。

    - truncated=True（首页即 has_next，或调用方已知超上限）→ ephemeral，
      **不写库**——调用方继续用内存中的值，运行时 value→id 走 /values/search。
    - 桶由 category_dependent 决定（显式传参优先，否则从 attr_row 读，
      缺省保守按 scoped）。
    写失败仅 warning 不抛（与各调用方的非致命语义一致）。
    """
    if truncated or (values and len(values) > DICT_MATERIALIZE_MAX_VALUES):
        logger.info("⏭️ 字典 %s 值数超物化上限（%s），ephemeral 不落库",
                    attr_id, len(values or []))
        return BUCKET_EPHEMERAL
    if category_dependent is None:
        category_dependent = is_category_dependent(attr_row)
    if category_dependent:
        bucket_dc, bucket_tp, bucket = dc, tp, BUCKET_SCOPED
    else:
        bucket_dc, bucket_tp, bucket = GLOBAL_DC, GLOBAL_TP, BUCKET_GLOBAL
    try:
        from utils.local_db_manager import LocalDBManager
        LocalDBManager().set_dictionary_value_cache(
            attr_id, bucket_dc, bucket_tp, values,
            language=language, expires_in=expires_in)
        if bucket == BUCKET_GLOBAL:
            logger.info("✅ 字典 %s 落全局桶 (attr,0,0,%s)，%s 值", attr_id, language, len(values))
    except Exception as exc:
        logger.warning("字典 %s 落桶失败(%s, 非致命): %s", attr_id, bucket, exc)
        return BUCKET_EPHEMERAL
    return bucket
