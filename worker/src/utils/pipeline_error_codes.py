"""管线本地错误码唯一事实源（fix/dedupe-batch1 C7 常量化）。

背景（2026-10 只读审计「重复造轮子」批① C7）：LOCAL_* 码此前以裸字符串散在
graphs/validation_retry_loop.py（赋值 / 路由表键 / 比较点）与
graphs/nodes/pricing_node.py——同一码多处拼写，typo 即静默失配
（错误码对不上 → retry 路由 / block_to_box 入箱判定 / 留存表 error_code 全链失效）。
本模块只收**管线节点本地造码**（LOCAL_ 前缀，非 Ozon 官方码）。

边界（依赖方向立法 W3a）：
- 消费方是 graphs 层 → 本模块必须住 utils/（graphs↛api）。
- HTTP API 面错误码唯一入口是 api/errors.py（数量以文件为准）——两域不混，
  API 层如需引用管线码，从本模块 import，禁止再写字面量。

新增码纪律：先在本模块定义常量，再让赋值点 / REPAIR_STRATEGY / FIX_TYPE_* /
block_to_box 判定等消费方统一引用常量；新码同理，禁再写字面量。
"""
from __future__ import annotations

# ── 定价域（pricing_node）──
LOCAL_PRICING_FAILED = "LOCAL_PRICING_FAILED"          # 定价计算失败/无有效价（阻断，绝不兜底上架）
LOCAL_PRICE_GAP_BLOCKED = "LOCAL_PRICE_GAP_BLOCKED"    # 价差守卫阻断（终价 vs 选品锚价超倍数，疑货源错配）

# ── 类目域（validation_retry_loop / assemble 家族）──
LOCAL_TITLE_CATEGORY_MISMATCH = "LOCAL_TITLE_CATEGORY_MISMATCH"  # 标题-类目零交集 → block_to_box 入采集箱
LOCAL_CATEGORY_INVALID_REQUEST = "LOCAL_CATEGORY_INVALID_REQUEST"  # 请求级类目 400（unfixable，禁 LLM 修复重传）
LOCAL_CATEGORY_RECATEGORIZE_FAILED = "LOCAL_CATEGORY_RECATEGORIZE_FAILED"  # 自动重配类目无解 → 终态失败

# ── 重传/状态域（validation_retry_loop reupload/recheck）──
LOCAL_REUPLOAD_FAILED = "LOCAL_REUPLOAD_FAILED"        # 重新上传失败（import UPDATE/CREATE 异常，保留更具体 Ozon 码时省略）
LOCAL_UPLOAD_NO_TASK_ID = "LOCAL_UPLOAD_NO_TASK_ID"    # 上传未取回 Ozon task_id（空/系统 UUID/格式错）
LOCAL_STATUS_QUERY_FAILED = "LOCAL_STATUS_QUERY_FAILED"  # 轮询 task_id 状态最终失败（无 items/查询异常）

__all__ = [
    "LOCAL_PRICING_FAILED",
    "LOCAL_PRICE_GAP_BLOCKED",
    "LOCAL_TITLE_CATEGORY_MISMATCH",
    "LOCAL_CATEGORY_INVALID_REQUEST",
    "LOCAL_CATEGORY_RECATEGORIZE_FAILED",
    "LOCAL_REUPLOAD_FAILED",
    "LOCAL_UPLOAD_NO_TASK_ID",
    "LOCAL_STATUS_QUERY_FAILED",
]
