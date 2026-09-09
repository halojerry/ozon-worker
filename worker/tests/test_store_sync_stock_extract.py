"""extract_available_stock 纯单测：/v3/product/info/list stocks 嵌套形状解析。

背景（2026-09-10 生产 stock 全 null 根因修复）：/v3/product/info/list 的 stocks 是
双层嵌套 {has_stock, stocks: [{present, reserved, sku, source}]}，旧代码对外层取
present（`stocks.get("present")`）恒 None → 缓存 stock 列全 null。
⚠️ present 含 reserved（ozon MCP live-verified 2026-04-13）——可用 = present − reserved。
纯 mock，不依赖 PG。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services.store_sync_service import extract_available_stock


def test_nested_single_source_present_minus_reserved():
    info = {"stocks": {"has_stock": True,
                       "stocks": [{"present": 9, "reserved": 2, "sku": 1, "source": "fbo"}]}}
    assert extract_available_stock(info) == 7


def test_nested_multi_source_summed_per_source_clamped():
    info = {"stocks": {"has_stock": True, "stocks": [
        {"present": 9, "reserved": 2, "source": "fbo"},
        {"present": 5, "reserved": 5, "source": "fbs"},   # 钳 0
        {"present": 3, "source": "rfbs"},                  # reserved 缺省 0
    ]}}
    assert extract_available_stock(info) == 10


def test_nested_reserved_over_present_clamped_not_negative():
    info = {"stocks": {"stocks": [{"present": 2, "reserved": 9, "source": "fbo"}]}}
    assert extract_available_stock(info) == 0


def test_flat_legacy_shape_fallback():
    """防御：扁平旧形 {present: n}（无 reserved 可减，原样取值）。"""
    assert extract_available_stock({"stocks": {"present": 7}}) == 7


def test_missing_or_unknown_shape_returns_none():
    """结构不识别/无数据 → None（绝不编造 0）。"""
    assert extract_available_stock({}) is None
    assert extract_available_stock({"stocks": {}}) is None
    assert extract_available_stock({"stocks": {"has_stock": False}}) is None
    assert extract_available_stock({"stocks": {"stocks": []}}) is None
    assert extract_available_stock({"stocks": "weird"}) is None
    assert extract_available_stock({"stocks": None}) is None


def test_garbage_values_tolerated():
    assert extract_available_stock({"stocks": {"stocks": [{"present": "abc"}]}}) is None
    assert extract_available_stock({"stocks": {"present": "abc"}}) is None
    # 字符串数字可解析
    assert extract_available_stock({"stocks": {"stocks": [{"present": "12", "reserved": "3"}]}}) == 9
