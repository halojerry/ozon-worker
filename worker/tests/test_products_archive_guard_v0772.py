# -*- coding: utf-8 -*-
"""
v0.77.2 — 商品同步失败禁止归档全店（_archive_missing 空集地雷拆除）。

根因（生产分析副产物，2026-09-18）：`_sync_products` 首页拉取失败 → break 时
`seen_ids` 为空集 → 无条件调用 `_archive_missing(tenant, cid, set())` →
`archived = TRUE WHERE NOT (product_id = ANY('{}'))` 匹配全部行 → **一次网络抖动
把全店缓存商品标记归档**（软删）。部分分页失败同理（剩余真实在售品被误归档）。

契约：
- 同步失败（error 非空）→ **绝不**调用 _archive_missing（数据保真优先：宁可
  archived 状态滞后，不可错杀）；
- 同步成功（含「店铺真 0 商品」合法场景，seen_ids 空）→ 照常归档。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_products_archive_guard_v0772.py -q
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import services.store_sync_service as sss  # noqa: E402


def _run_products(ozon_post_side_effect):
    """驱动 _sync_products：mock 拉取 + 归档/upsert/错误落库 spy。返回 archive 调用参数。"""
    archive_calls = []

    def _archive_spy(t, c, seen):
        archive_calls.append(set(seen))

    with patch("utils.ozon_client.ozon_post", side_effect=ozon_post_side_effect), \
         patch.object(sss, "_fetch_info_map", return_value={}), \
         patch.object(sss, "_upsert_products"), \
         patch.object(sss, "_archive_missing", side_effect=_archive_spy), \
         patch.object(sss, "_set_products_error_no_watermark"), \
         patch.object(sss, "_set_sync_error"):
        out = sss._sync_products("t1", "c1", "cid", "key")
    return out, archive_calls


# ── ① 首页拉取失败 → 绝不归档（空 seen_ids 全店误杀地雷）──
def test_first_page_failure_never_archives():
    def _boom(*a, **kw):
        raise RuntimeError("OzonValidationError: boom")

    out, archive_calls = _run_products(_boom)
    assert out["error"], "失败必须返回 error"
    assert archive_calls == [], (
        f"同步失败禁止 _archive_missing（空集=全店归档），实际被调 {archive_calls}"
    )


# ── ② 部分分页失败（第2页炸）→ 已见商品保留、绝不归档 ──
def test_partial_failure_never_archives():
    page = int(sss._PRODUCT_PAGE)

    def _two_pages_then_boom(*a, **kw):
        body = (a[3] if len(a) > 3 else (kw.get("body") or {}))
        offset = (body.get("offset") if isinstance(body, dict) else 0) or 0
        if offset == 0:
            return {"result": {"total": page * 2, "items": [
                {"product_id": 100 + i} for i in range(page)]}}
        raise RuntimeError("page2 network reset")

    out, archive_calls = _run_products(_two_pages_then_boom)
    assert out["error"], "部分失败必须返回 error"
    assert archive_calls == [], "部分失败同样禁止归档（剩余真实在售品不可错杀）"


# ── ③ 同步成功 → 照常归档（_seen 集传参不变）──
def test_success_archives_normally():
    def _ok(*a, **kw):
        return {"result": {"total": 2, "items": [{"product_id": 1}, {"product_id": 2}]}}

    out, archive_calls = _run_products(_ok)
    assert not out["error"]
    assert archive_calls and archive_calls[0] == {"1", "2"}


# ── ④ 店铺真 0 商品（成功+空集）→ 归档照常（合法语义）──
def test_empty_store_success_still_archives():
    def _empty(*a, **kw):
        return {"result": {"total": 0, "items": []}}

    out, archive_calls = _run_products(_empty)
    assert not out["error"]
    assert archive_calls == [set()], "空店归档是合法语义（店已无在售）"
