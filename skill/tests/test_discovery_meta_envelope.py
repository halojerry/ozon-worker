#!/usr/bin/env python3
"""采集箱选品元数据 extensions.discovery_meta 透传回归（discover 漏斗 v2 · Task 1）。

背景：drafts 表只存信封 JSONB，discover 阶段的选品分析元数据（蓝海分/月销/
drr/利润率/匹配置信度）此前全部终止在 skill 本地（仅 discovery_runs 归档 20
字段白名单，与 drafts 零关联）→ 采集箱条目看不到任何选品依据。本批在
build_envelope_from_discovery 组装时注入 extensions.discovery_meta 扁平快照
（worker 零迁移——payload 整存 JSONB，webui/CSV 直接消费）。

语义约定（对齐 match_evidence 风格）：
- 字段缺失（None/空串/空 dict）省略键；0 是真实数据（月销 0/跟卖 0）保留；
- 主路径与裸信封降级路径均注入（选品元数据 ≠ 匹配证据，降级时同样有价值）；
- discovered_at 为组装时刻 ISO 时间戳。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_discovery_meta_envelope.py -q
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402
from scripts import cloud_probe  # noqa: E402

_RETRY_DEFAULT = object()  # 哨兵：默认走完整信封；传 None 强制走降级裸信封路径


def _mk_candidate(**overrides) -> ProductCandidate:
    c = ProductCandidate(
        ozon_product_id="4767514314",
        ozon_title="Автопоилка для кошек 2л",
        ozon_price=1290.0,
    )
    c.match_1688_url = "https://detail.1688.com/offer/980815374096.html"
    c.competing_sellers = 4
    c.weight_g = 0
    c.dimensions_mm = {}
    c.ozon_category = {}
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _discover_envelope(cand: ProductCandidate, *, retry_returns=_RETRY_DEFAULT) -> dict | None:
    env = {
        "token": "sk-test",
        "ozon_client_id": "123",
        "ozon_api_key": "key",
        "envelope": {
            "draft": {"item_id": "980815374096", "title": "x", "images": [],
                      "weight": 0, "dimensions": {"length": 0, "width": 0, "height": 0}},
            "source": {"purchase_url": "u", "purchase_cost": 1.0},
            "extensions": {},
        },
    }
    ret = env if retry_returns is _RETRY_DEFAULT else retry_returns
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value=ret), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        return cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "123", "api_key": "key"})


def _meta(cand: ProductCandidate) -> dict:
    return cloud_probe._assemble_discovery_meta(cand)


# ─────────────────────────── 组装函数 ───────────────────────────

def test_assemble_full_snapshot():
    """指标齐全的候选 → 全字段扁平快照。"""
    cand = _mk_candidate(
        ozon_url="https://www.ozon.ru/product/avtopoilka-4767514314/",
        blue_ocean_score=78,
        monthly_sales=120,
        monthly_revenue=154800.0,
        sales_growth=12.5,
        drr=8.0,
        create_days=90,
        rating=4.7,
        review_count=350,
        weight_g=850,
        dimensions_mm={"length": 220, "width": 180, "height": 160},
        profit_margin=22.4,
        estimated_profit_cny=38.2,
        match_confidence=0.87,
    )
    meta = _meta(cand)
    assert meta["ozon_product_id"] == "4767514314"
    assert meta["ozon_price"] == 1290.0
    assert meta["blue_ocean_score"] == 78
    assert meta["monthly_sales"] == 120
    assert meta["monthly_revenue"] == 154800.0
    assert meta["sales_growth"] == 12.5
    assert meta["drr"] == 8.0
    assert meta["create_days"] == 90
    assert meta["competing_sellers"] == 4
    assert meta["rating"] == 4.7
    assert meta["review_count"] == 350
    assert meta["weight_g"] == 850
    assert meta["dimensions_mm"] == {"length": 220, "width": 180, "height": 160}
    assert meta["profit_margin"] == 22.4
    assert meta["estimated_profit_cny"] == 38.2
    assert meta["match_confidence"] == 0.87
    assert meta["discovered_at"]  # ISO 时间戳
    assert len(json.dumps(meta, ensure_ascii=False)) < 2048  # ≤2KB 纪律


def test_assemble_zero_is_real_data():
    """0 是真实数据（月销 0/跟卖 0/蓝海 0）→ 保留，不按缺失省略。"""
    meta = _meta(_mk_candidate(monthly_sales=0, competing_sellers=0,
                               blue_ocean_score=0))
    assert meta["monthly_sales"] == 0
    assert meta["competing_sellers"] == 0
    assert meta["blue_ocean_score"] == 0


def test_assemble_missing_fields_omitted():
    """字段缺失（None/空串/空 dict）→ 键省略，不做空壳。"""
    meta = _meta(_mk_candidate(ozon_url="", blue_ocean_score=None,
                               dimensions_mm={}, match_confidence=None))
    assert "ozon_url" not in meta
    assert "blue_ocean_score" not in meta
    assert "dimensions_mm" not in meta
    assert "match_confidence" not in meta
    assert meta["ozon_product_id"] == "4767514314"  # 基础字段仍在


# ─────────────────────────── 信封注入 ───────────────────────────

def test_envelope_main_path_injects_meta():
    """主路径（AK+CDP 成功）→ extensions.discovery_meta 注入且 JSON 可序列化。"""
    cand = _mk_candidate(blue_ocean_score=78, monthly_sales=120, profit_margin=22.4)
    result = _discover_envelope(cand)
    assert result is not None
    meta = result["envelope"]["extensions"]["discovery_meta"]
    assert meta["blue_ocean_score"] == 78
    assert meta["monthly_sales"] == 120
    assert meta["profit_margin"] == 22.4
    json.dumps(result, ensure_ascii=False)  # 提交契约：整体可序列化


def test_envelope_fallback_path_injects_meta():
    """裸信封降级路径 → discovery_meta 同样注入（选品元数据 ≠ 匹配证据）。"""
    cand = _mk_candidate(blue_ocean_score=66, monthly_sales=80)
    result = _discover_envelope(cand, retry_returns=None)
    assert result is not None
    meta = result["envelope"]["extensions"]["discovery_meta"]
    assert meta["blue_ocean_score"] == 66
    assert meta["monthly_sales"] == 80


def test_envelope_meta_does_not_overwrite_existing():
    """上游/模板已带 discovery_meta → 不覆盖（对齐 follow_type setdefault 语义）。"""
    cand = _mk_candidate(blue_ocean_score=78)
    env = {
        "token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
        "envelope": {
            "draft": {"item_id": "x", "title": "t", "images": [], "weight": 0,
                      "dimensions": {"length": 0, "width": 0, "height": 0}},
            "source": {},
            "extensions": {"discovery_meta": {"blue_ocean_score": 99}},
        },
    }
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value=env), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk"):
        result = cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "1", "api_key": "k"})
    assert result["envelope"]["extensions"]["discovery_meta"]["blue_ocean_score"] == 99


if __name__ == "__main__":
    import traceback

    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
