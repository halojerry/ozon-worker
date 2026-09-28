"""v0.83.1 类目真值链（skill 侧）回归：信封 1688 cid 兜底 + estimate scid 透传。

病根（2026-09-28 17 单节日批实锤，卡 6474128845 学习行 source_category_id=NULL）：
- 原链 AK 详情 categories 无数字 id → 信封 source.category_id=null 而 path 在场
  → worker L0 lookup/学习回填双断 → 冷文本匹配子类翻车（970887478 配件 vs
  91672 本体，conf 0.18 拦截）。
- discover 侧 aibuy 匹配明明有 match_1688_category_id（真 1688 数字 cid），但：
  batch_test 1688-URL 路径不经 discover 复用链 → cid 从未进信封。

修复契约：
  S1  estimate_client.build_batch_item 支持 scid（空值省略纪律不变）。
  S2  ozon_discovery._build_estimate_item 透传 match_1688_category_id → scid
      （worker 侧 dc 缺席时经学习映射表反查，佣金冷启动解锁）。
  S3  batch_test.process_1688_url 信封构建后 _inject_discover_cid：按 offer URL
      从 discover 缓存反查 aibuy cid，三键齐写（source.category_id /
      source.match_category_id / draft.source_category_id），已有值零改动。
  S4  cloud_probe.build_envelope_from_discovery cid 兜底注入（ozon 复用路径，
      含 source 键缺失孤儿 dict 回写防护）。

运行：cd skill && .venv314/bin/python -m pytest tests/test_category_truth_chain_v0831.py -q
纯 mock（信封链/页面真值/数据池全 monkeypatch），无需 CDP/网络。
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

OFFER_URL = "https://detail.1688.com/offer/748320109280.html"


class _Cand:
    """最小候选桩：未知属性 __getattr__ 兜底 None（build_envelope 大量 getattr）。"""

    def __init__(self, **kw):
        self.__dict__.update({
            "match_1688_url": OFFER_URL,
            "match_1688_title": "led网灯串",
            "match_1688_price": 9.5,
            "match_1688_images": ["http://img/1.jpg"],
            "match_1688_category_id": "201303723",
            "ozon_url": "",
            "ozon_title": "Electric String Lights",
            "ozon_product_id": "5877454431",
            "ozon_images": ["http://img/2.jpg"],
            "competing_sellers": 0,
            "commission_rfbs_segments": None,
            "commission_fbp_segments": None,
            "match_confidence": 0.5,
            "match_badge_eff": 0.0,
            "match_category_divergent": False,
            "match_semantic_unknown": False,
            "weight_g": 100,
            "dimensions_mm": {},
        })
        self.__dict__.update(kw)

    def __getattr__(self, name):
        return None  # 未知属性兜底（_Cand 契约：getattr 消费方拿到 None 走默认分支）


# ═══════════════ S1: build_batch_item scid ═══════════════

def test_s1_build_batch_item_scid_passthrough():
    from scripts.lib.estimate_client import build_batch_item

    item = build_batch_item(9.5, scid="201303723")
    assert item["scid"] == "201303723"
    # 空值省略纪律：None/空串不加键
    assert "scid" not in build_batch_item(9.5)
    assert "scid" not in build_batch_item(9.5, scid="")
    assert "scid" not in build_batch_item(9.5, scid=None)
    # 非数字原样透传（worker 侧安全降级有测试锁定）
    assert build_batch_item(9.5, scid=201303723)["scid"] == "201303723"


# ═══════════════ S2: _build_estimate_item 透传 scid ═══════════════

def test_s2_build_estimate_item_carries_scid():
    from scripts.lib.ozon_discovery import _build_estimate_item

    item = _build_estimate_item(_Cand())
    assert item.get("scid") == "201303723"

    item2 = _build_estimate_item(_Cand(match_1688_category_id=None))
    assert "scid" not in item2


# ═══════════════ S3: batch_test 信封 cid 兜底注入 ═══════════════

def _envelope_fixture(cid=None):
    return {"envelope": {
        "draft": {"item_id": "748320109280", "title": "led网灯"},
        "source": {"category_id": cid, "purchase_url": OFFER_URL,
                   "source_category_path": "灯饰照明 > LED灯串 > 室外LED灯串"},
        "extensions": {},
    }}


def test_s3_inject_cid_from_discovery_cache(monkeypatch):
    from scripts import batch_test as bt

    monkeypatch.setattr(
        "scripts.lib.ozon_discovery.load_latest_discovery",
        lambda: [{"match_1688_url": OFFER_URL, "match_1688_category_id": "201303723"}])
    env = _envelope_fixture(cid=None)
    bt._inject_discover_cid(env, OFFER_URL, "748320109280")
    src = env["envelope"]["source"]
    assert src["category_id"] == 201303723
    assert src["match_category_id"] == "201303723"
    assert env["envelope"]["draft"]["source_category_id"] == "201303723"


def test_s3_inject_no_overwrite_existing_cid(monkeypatch):
    from scripts import batch_test as bt

    monkeypatch.setattr(
        "scripts.lib.ozon_discovery.load_latest_discovery",
        lambda: [{"match_1688_url": OFFER_URL, "match_1688_category_id": "999"}])
    env = _envelope_fixture(cid=111)  # 原链已抓到 cid → 零改动
    bt._inject_discover_cid(env, OFFER_URL, "748320109280")
    assert env["envelope"]["source"]["category_id"] == 111
    assert "match_category_id" not in env["envelope"]["source"]


def test_s3_inject_cache_miss_keeps_null(monkeypatch):
    from scripts import batch_test as bt

    monkeypatch.setattr(
        "scripts.lib.ozon_discovery.load_latest_discovery", lambda: [])
    env = _envelope_fixture(cid=None)
    bt._inject_discover_cid(env, OFFER_URL, "748320109280")
    assert env["envelope"]["source"]["category_id"] is None
    assert "source_category_id" not in env["envelope"]["draft"]


def test_s3_inject_url_tail_match(monkeypatch):
    """URL 尾斜杠差异不阻断命中（rstripped 归一）。"""
    from scripts import batch_test as bt

    monkeypatch.setattr(
        "scripts.lib.ozon_discovery.load_latest_discovery",
        lambda: [{"match_1688_url": OFFER_URL + "/", "match_1688_category_id": "201303723"}])
    env = _envelope_fixture(cid=None)
    bt._inject_discover_cid(env, OFFER_URL, "748320109280")
    assert env["envelope"]["source"]["category_id"] == 201303723


# ═══════════════ S4: build_envelope_from_discovery 兜底 ═══════════════

def test_s4_envelope_from_discovery_injects_cid(monkeypatch):
    from scripts import cloud_probe as cp

    fixed = {"ozon_client_id": "c1", "ozon_api_key": "k1", "envelope": _envelope_fixture(cid=None)["envelope"]}
    with mock.patch.object(cp, "build_graph_envelope_with_retry", return_value=fixed), \
         mock.patch.object(cp, "_discover_page_truth", return_value={}), \
         mock.patch.object(cp, "_apply_pool_variant_weight", lambda d, c: None), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="tok"):
        out = cp.build_envelope_from_discovery(
            _Cand(), {"client_id": "c1", "api_key": "k1", "currency": "CNY"}, "主店铺")
    assert out, "信封构建意外失败"
    src = out["envelope"]["source"]
    assert src["category_id"] == 201303723, (
        f"AK 详情 cid 缺失时应从 aibuy match 兜底注入，实际 {src.get('category_id')!r}")
    assert out["envelope"]["draft"]["source_category_id"] == 201303723


def test_s4_envelope_from_discovery_respects_existing_cid(monkeypatch):
    from scripts import cloud_probe as cp

    fixed = {"ozon_client_id": "c1", "ozon_api_key": "k1",
             "envelope": _envelope_fixture(cid=555)["envelope"]}
    with mock.patch.object(cp, "build_graph_envelope_with_retry", return_value=fixed), \
         mock.patch.object(cp, "_discover_page_truth", return_value={}), \
         mock.patch.object(cp, "_apply_pool_variant_weight", lambda d, c: None), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="tok"):
        out = cp.build_envelope_from_discovery(
            _Cand(), {"client_id": "c1", "api_key": "k1", "currency": "CNY"}, "主店铺")
    assert out["envelope"]["source"]["category_id"] == 555
