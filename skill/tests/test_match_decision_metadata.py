#!/usr/bin/env python3
"""D3 L1: 1688 匹配决策元数据透传（TDD RED→GREEN）。

- _pick_best_match 所有 PASS 出口返回携带 confidence/badge_eff/score/badge_str
- 三个 block 出口（all_filtered / badge_less_conf_weak / guardrail_blocked）
  写 review_log decision="block"（判定不静默消失）
- _search_1688_source 把元数据透传进三条策略的返回 dict（keyword 路径写 0/None/""）
- _process_match 把元数据写入候选 + 状态分配时写 review_log

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_match_decision_metadata.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import ozon_discovery as od  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402

WRENCH_RU = "Ключ комбинированный трещоточный шарнирный 13 мм"
WRENCH_CN_STRONG = "两用棘轮扳手 棘轮快速扳手 双头棘轮扳手 活头棘轮扳手"
TOWEL_CN_WEAK = "纯棉毛巾加厚家用吸水洗脸巾"


def _mk(pid, status="ok"):
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Товар {pid}",
                         ozon_price=1000.0)
    c.status = status
    c.ozon_images = [f"https://img.ozone.ru/{pid}.jpg"]
    return c


def _result(**overrides):
    r = {"title": WRENCH_CN_STRONG, "price": 15.0, "badge": "",
         "id": "1001", "detail_url": "https://detail.1688.com/offer/1001.html",
         "image": "https://img/1688/1.jpg"}
    r.update(overrides)
    return r


# ── ① _pick_best_match PASS 出口携带元数据 ────────────────────────────────

def test_pick_best_match_full_badge_carries_metadata():
    """matchBadgeFull 直接放行 → 副本携带 confidence/badge_eff/score/badge_str。"""
    best = od._pick_best_match([_result(badge="全部符合")], WRENCH_RU)
    assert best is not None
    assert best["badge_eff"] == 1.0
    assert best["badge_str"] == "全部符合"
    assert "confidence" in best and "score" in best
    assert best["reject_reason"] == ""
    # 原 dict 不被污染（返回的是副本）
    src = _result(badge="全部符合")
    od._pick_best_match([src], WRENCH_RU)
    assert "confidence" not in src, "_attach_match_meta 必须返回副本，不修改原 dict"


def test_pick_best_match_badge_less_conf_ok_carries_metadata():
    """无徽标 + 词对相关性 conf≥0.3 → 放行并携带真实 conf。"""
    best = od._pick_best_match([_result(title=WRENCH_CN_STRONG, badge="")], WRENCH_RU)
    assert best is not None
    assert best["confidence"] >= 0.3, f"conf 应≥0.3, got {best['confidence']}"
    assert best["badge_eff"] == 0.0
    assert best["score"] > 0
    assert best["reject_reason"] == ""


def test_pick_best_match_guardrail_llm_rescue_carries_metadata():
    """护栏边界 + LLM 判定同品放行 → 携带弱匹配元数据（conf<0.3, badge_eff<0.5）。"""
    with mock.patch.object(od, "_llm_semantic_match", return_value=True):
        best = od._pick_best_match(
            [_result(title=TOWEL_CN_WEAK, badge="符合1/3个条件")], WRENCH_RU)
    assert best is not None
    assert best["confidence"] < 0.3
    assert best["badge_eff"] < 0.5
    assert best["score"] > 0
    assert best["reject_reason"] == ""


def test_pick_best_match_normal_pass_carries_metadata():
    """badge_eff≥0.5（非 full）且 conf 弱 → 正常放行，携带元数据。"""
    best = od._pick_best_match(
        [_result(title=TOWEL_CN_WEAK, badge="符合2/3个条件")], WRENCH_RU)
    assert best is not None
    assert best["badge_eff"] >= 0.5
    assert "confidence" in best and "score" in best
    assert best["reject_reason"] == ""


# ── ② _pick_best_match block 出口写 review_log ────────────────────────────

def test_pick_best_match_all_filtered_writes_review_log():
    """全部被过滤（badge 0/N / 无价）→ decision=block, reason=all_filtered。"""
    results = [
        {"title": "不相关商品甲", "price": 10.0, "badge": "符合0/1个条件"},
        {"title": "不相关商品乙", "price": 0, "badge": "符合2/3个条件"},
    ]
    with mock.patch("scripts.lib.ozon_discovery._log_review_record") as m_log:
        assert od._pick_best_match(results, WRENCH_RU) is None
    m_log.assert_called_once()
    rec = m_log.call_args[0][0]
    assert rec["decision"] == "block"
    assert rec["reject_reason"] == "all_filtered"
    assert rec["ozon_title"] == WRENCH_RU
    assert rec["image_urls"] == [] or isinstance(rec["image_urls"], list)


def test_pick_best_match_guardrail_blocked_writes_review_log():
    """badge 弱 + conf 弱 + LLM 判定不同品 → decision=block, reason=guardrail_blocked。"""
    with mock.patch.object(od, "_llm_semantic_match", return_value=False), \
         mock.patch("scripts.lib.ozon_discovery._log_review_record") as m_log:
        best = od._pick_best_match(
            [_result(title=TOWEL_CN_WEAK, badge="符合1/3个条件")], WRENCH_RU)
    assert best is None
    m_log.assert_called_once()
    rec = m_log.call_args[0][0]
    assert rec["decision"] == "block"
    assert rec["reject_reason"] == "guardrail_blocked"
    assert rec["match_title"] == TOWEL_CN_WEAK


# ── ③ _search_1688_source 元数据透传 ──────────────────────────────────────

def test_search_1688_source_cdp_carries_metadata():
    """CDP 图搜路径：_pick_best_match 的元数据透传进返回 dict。"""
    results = [_result(badge="全部符合", id="1001",
                       detail_url="https://detail.1688.com/offer/1001.html")]
    with mock.patch("scripts.lib.ozon_image_search.search_by_image_cdp",
                    return_value=results):
        out = od._search_1688_source(
            "http://127.0.0.1:9222", ["https://img/ozone/1.jpg"], WRENCH_RU)
    assert out is not None
    assert out["badge_eff"] == 1.0
    assert out["confidence"] >= 0.0
    assert out["score"] > 0
    assert out["reject_reason"] == ""


def test_search_1688_source_keyword_writes_zero_metadata():
    """AK 关键词兜底路径（无图搜）：元数据写 0 / None / ''（无护栏判定）。"""
    with mock.patch.object(od, "_extract_search_keywords", return_value="扳手"), \
         mock.patch("scripts.lib.ak_1688_client.search_products",
                    return_value=[{"title": "棘轮扳手", "price": "15.0",
                                   "detail_url": "https://detail.1688.com/offer/9.html"}]):
        out = od._search_1688_source("http://127.0.0.1:9222", [], "Ключ трещоточный")
    assert out is not None
    assert out["confidence"] == 0.0
    assert out["badge_eff"] == 0.0
    assert out["score"] is None
    assert out["reject_reason"] == ""


# ── ④ _process_match 元数据写回候选 + review_log ──────────────────────────


def test_process_match_populates_decision_metadata():
    """匹配 dict 的 confidence/badge_eff/score/reject_reason 写入候选字段。"""
    cands = [_mk("p1")]
    match = {"url": "https://detail.1688.com/offer/1001.html",
             "title": "棘轮扳手", "price": 50.0, "images": ["https://i/1.jpg"],
             "confidence": 0.7, "badge_eff": 0.667, "score": 65.0,
             "reject_reason": ""}
    with mock.patch.object(od, "_discover_workers", return_value=1), \
         mock.patch.object(od, "_search_1688_source", return_value=match), \
         mock.patch.object(od, "_query_logistics_from_worker", return_value=None), \
         mock.patch.object(od, "_save_discovery_log"), \
         mock.patch("scripts.lib.ozon_discovery._log_review_record"), \
         mock.patch("time.sleep"):
        result = od.match_selected(cands, "http://127.0.0.1:9222", min_margin_pct=1)
    c = result[0]
    assert c.status == "profitable"
    assert c.match_confidence == 0.7
    assert c.match_badge_eff == 0.667
    assert c.match_reject_reason == ""
    assert c.match_1688_images == ["https://i/1.jpg"]


def test_process_match_writes_review_log_at_status_assignment():
    """状态分配（profitable/rejected/no_match）各写一条 review_log 记录。"""
    cands = [_mk("p1"), _mk("p2"), _mk("p3")]
    match = {"url": "https://detail.1688.com/offer/1001.html",
             "title": "棘轮扳手", "price": 50.0, "images": [],
             "confidence": 0.7, "badge_eff": 0.667, "score": 65.0,
             "reject_reason": ""}
    with mock.patch.object(od, "_discover_workers", return_value=1), \
         mock.patch.object(od, "_search_1688_source",
                           side_effect=[match, match, None]), \
         mock.patch.object(od, "_query_logistics_from_worker", return_value=None), \
         mock.patch.object(od, "_save_discovery_log"), \
         mock.patch("scripts.lib.ozon_discovery._log_review_record") as m_log, \
         mock.patch("time.sleep"):
        od.match_selected(cands, "http://127.0.0.1:9222",
                          min_margin_pct=99.0)  # 利润不足 → auto_reject

    recs = [call.args[0] for call in m_log.call_args_list]
    by_pid = {r["product_id"]: r for r in recs}
    assert by_pid["p1"]["decision"] == "auto_reject", recs
    assert by_pid["p1"]["confidence"] == 0.7, "记录应携带决策元数据"
    assert by_pid["p1"]["reject_reason"], "auto_reject 应带拒绝原因"
    assert by_pid["p3"]["decision"] == "no_match", recs


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
