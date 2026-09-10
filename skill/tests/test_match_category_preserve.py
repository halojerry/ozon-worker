#!/usr/bin/env python3
"""v0.66.2: 1688 图搜匹配类目全链路保留（aibuy/AK/CDP → match dict → 候选 → 信封 source）。

背景：aibuy 图搜返回自带 1688 类目（cate_level1_id/cate_level2_id/category_name，
ozon_image_search.py 归一化保留），但 discover 匹配组装时被丢弃。本批把 1688 类目
信息沿匹配链路透传——它是类目对齐映射（L0 学习）与错配预检的 1688 侧数据基础：

- _pick_best_match/_attach_match_meta：match dict 补规范键 category_id/category_name
  （aibuy level2 优先/无则 level1；AK 单层 category_id；CDP 无 → 空串）
- _search_1688_source：四策略返回 match dict 透传 category_id/category_name
- _process_match：候选补 match_1688_category_id / match_1688_category_name
- build_envelope_from_discovery：候选字段非空 → source["match_category_id/name"]（无值无键）

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_match_category_preserve.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402
from scripts import cloud_probe  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_cross_source_hook(monkeypatch):
    """批4 gate 校准 round3（2026-09-10）：跨源副钩 _cross_source_compare 在
    match_selected 内会拿 match_1688_title 真实触网（本机 Chrome 有登录态即真
    搜索，胜者换源改写 match_1688_* 槽位）→ 本文件单测一律 mock 掉；跨源行为
    由 test_discover_cross_source_wiring.py 专项锁定。"""
    monkeypatch.setattr(od, "_cross_source_compare", lambda *a, **k: None)

RU_DRINKER = "Автопоилка для кошек 2л"
RU_WRENCH = "Ключ комбинированный трещоточный 13 мм"
CN_CAT_NAME = "宠物自动饮水机"
LEVEL1_ID = "1019"
LEVEL2_ID = "124018001"


# ── helpers ────────────────────────────────────────────────────────────────

def _mk_candidate(**overrides) -> ProductCandidate:
    """构造 ProductCandidate（含 1688 匹配类目字段，供信封透出测试）。"""
    c = ProductCandidate(
        ozon_product_id="4767514314",
        ozon_title=RU_DRINKER,
        ozon_price=1290.0,
    )
    c.match_1688_url = "https://detail.1688.com/offer/980815374096.html"
    c.match_1688_price = 45.0
    c.match_1688_category_id = overrides.pop("match_1688_category_id", "")
    c.match_1688_category_name = overrides.pop("match_1688_category_name", "")
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _aibuy_result(**overrides) -> dict:
    """aibuy 图搜归一化候选（对齐 ozon_image_search._aibuy_image_search 键）。"""
    r = {
        "id": "1001",
        "title": "宠物自动饮水机 猫狗喂水器 2L大容量",
        "price": 45.0,
        "image": "https://img/1688/1.jpg",
        "badge": "",  # aibuy 无徽章
        "badge_score": 0,
        "normalization_score": 0.9,
        "month_sold": "500+",
        "repurchase_rate": "",
        "supplier": "义乌市宠物用品厂",
        "offer_publish_time": "",
        "cate_level1_id": LEVEL1_ID,
        "cate_level2_id": LEVEL2_ID,
        "category_name": CN_CAT_NAME,
    }
    r.update(overrides)
    return r


def _envelope(result_override=None) -> dict:
    """mock build_graph_envelope_with_retry 返回的信封。"""
    env = {
        "token": "sk-test",
        "ozon_client_id": "123",
        "ozon_api_key": "key",
        "envelope": {
            "draft": {"item_id": "980815374096", "title": "x", "images": [],
                      "weight": 0, "dimensions": {"length": 0, "width": 0, "height": 0}},
            "source": {"purchase_url": "u", "purchase_cost": 1.0},
            "extensions": {"margin_rate": 0.25, "commission_rate": 0.10, "fx_buffer": 0.05},
        },
    }
    if result_override:
        env["envelope"].update(result_override)
    return env


# ── ① match dict 保留类目（_pick_best_match → _attach_match_meta） ─────────

def test_pick_best_match_aibuy_preserves_category():
    """aibuy 候选含 cate_level2/category_name → best dict 带规范 category_id/name。"""
    best = od._pick_best_match([_aibuy_result()], RU_DRINKER, trusted_source=True)
    assert best is not None
    assert best["category_id"] == LEVEL2_ID, "aibuy 应取 level2 优先"
    assert best["category_name"] == CN_CAT_NAME


def test_pick_best_match_aibuy_level1_fallback():
    """aibuy 候选无 level2 → category_id 回退 level1。"""
    best = od._pick_best_match(
        [_aibuy_result(cate_level2_id="")], RU_DRINKER, trusted_source=True)
    assert best is not None
    assert best["category_id"] == LEVEL1_ID
    assert best["category_name"] == CN_CAT_NAME


# ── ② _search_1688_source 返回 match dict 透传 ─────────────────────────────

def test_search_1688_source_aibuy_match_dict_carries_category():
    """aibuy 图搜路径 → 返回 match dict 带 category_id/category_name。"""
    with mock.patch("scripts.lib.ozon_image_search.search_by_image_aibuy",
                    return_value=[_aibuy_result()]):
        out = od._search_1688_source(
            "http://127.0.0.1:9222", ["https://img/ozone/1.jpg"], RU_DRINKER)
    assert out is not None
    assert out["category_id"] == LEVEL2_ID
    assert out["category_name"] == CN_CAT_NAME


def test_search_1688_source_cdp_no_category_ok():
    """CDP 候选无类目字段 → category_id/name 空串，不报错。"""
    cdp_cand = {"id": "2002", "title": "宠物自动饮水机 猫咪喂水器", "price": 42.0,
                "badge": "全部符合",
                "detail_url": "https://detail.1688.com/offer/2002.html",
                "image": "https://img/1688/2.jpg"}
    with mock.patch("scripts.lib.ozon_image_search.search_by_image_aibuy",
                    return_value=[]), \
         mock.patch("scripts.lib.ozon_image_search.search_by_image_cdp",
                    return_value=[cdp_cand]):
        out = od._search_1688_source(
            "http://127.0.0.1:9222", ["https://img/ozone/1.jpg"], RU_WRENCH)
    assert out is not None
    assert out["category_id"] == ""
    assert out["category_name"] == ""


# ── ③ _process_match 透传到候选 ───────────────────────────────────────────

def _run_match_selected(match) -> list:
    cands = [od.ProductCandidate(ozon_product_id="p1", ozon_title=RU_DRINKER,
                                 ozon_price=1000.0)]
    cands[0].status = "ok"
    cands[0].ozon_images = ["https://img.ozone.ru/p1.jpg"]
    with mock.patch.object(od, "_discover_workers", return_value=1), \
         mock.patch.object(od, "_search_1688_source", return_value=match), \
         mock.patch.object(od, "_query_logistics_from_worker", return_value=None), \
         mock.patch.object(od, "_save_discovery_log"), \
         mock.patch("scripts.lib.ozon_discovery._log_review_record"), \
         mock.patch("time.sleep"):
        return od.match_selected(cands, "http://127.0.0.1:9222", min_margin_pct=1)


def test_process_match_populates_category_fields():
    """match dict 带类目 → 候选 match_1688_category_id/name 正确。"""
    match = {"url": "https://detail.1688.com/offer/1001.html",
             "title": CN_CAT_NAME, "price": 50.0, "images": [],
             "confidence": 0.7, "badge_eff": 0.0, "score": 65.0,
             "reject_reason": "", "category_id": LEVEL2_ID,
             "category_name": CN_CAT_NAME}
    result = _run_match_selected(match)
    c = result[0]
    assert c.status == "profitable"
    assert c.match_1688_category_id == LEVEL2_ID
    assert c.match_1688_category_name == CN_CAT_NAME


def test_process_match_aibuy_raw_level2_keys_fallback():
    """match dict 无规范键但带 aibuy 原键 cate_level2_id → 候选取 level2。"""
    match = {"url": "https://detail.1688.com/offer/1001.html",
             "title": CN_CAT_NAME, "price": 50.0, "images": [],
             "confidence": 0.7, "badge_eff": 0.0, "score": 65.0,
             "reject_reason": "", "cate_level2_id": LEVEL2_ID,
             "cate_level1_id": LEVEL1_ID, "category_name": CN_CAT_NAME}
    result = _run_match_selected(match)
    c = result[0]
    assert c.match_1688_category_id == LEVEL2_ID
    assert c.match_1688_category_name == CN_CAT_NAME


def test_process_match_no_category_empty_no_error():
    """CDP/AK 无类目键 → 候选类目字段空串，不报错不污染。"""
    match = {"url": "https://detail.1688.com/offer/1001.html",
             "title": CN_CAT_NAME, "price": 50.0, "images": [],
             "confidence": 0.7, "badge_eff": 0.0, "score": 65.0,
             "reject_reason": ""}
    result = _run_match_selected(match)
    c = result[0]
    assert c.match_1688_category_id == ""
    assert c.match_1688_category_name == ""


# ── ④ 信封 source 透出（cloud_probe.build_envelope_from_discovery） ────────

def test_envelope_injects_match_category():
    """候选 1688 类目非空 → source 带 match_category_id/name。"""
    cand = _mk_candidate(match_1688_category_id=LEVEL2_ID,
                         match_1688_category_name=CN_CAT_NAME)
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value=_envelope()), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        result = cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "123", "api_key": "key"})
    assert result is not None
    src = result["envelope"]["source"]
    assert src["match_category_id"] == LEVEL2_ID
    assert src["match_category_name"] == CN_CAT_NAME


def test_envelope_no_match_category_no_keys():
    """候选无 1688 类目 → source 不加 match_category_* 键。"""
    cand = _mk_candidate()
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value=_envelope()), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"):
        result = cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "123", "api_key": "key"})
    assert result is not None
    src = result["envelope"]["source"]
    assert "match_category_id" not in src
    assert "match_category_name" not in src


def test_envelope_fallback_path_injects_match_category():
    """重抓货源失败降级简单组装 → source 同样透出 match_category_*。"""
    cand = _mk_candidate(match_1688_category_id=LEVEL2_ID,
                         match_1688_category_name=CN_CAT_NAME)
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           side_effect=RuntimeError("network down")), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk-test"), \
         mock.patch("scripts.lib.config_store.get_store_profile", return_value={}):
        result = cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "123", "api_key": "key"})
    assert result is not None
    src = result["envelope"]["source"]
    assert src["match_category_id"] == LEVEL2_ID
    assert src["match_category_name"] == CN_CAT_NAME


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
