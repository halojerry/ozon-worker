#!/usr/bin/env python3
"""v0.73: skill 侧 batch 提交闸——空标题/弱匹配候选不再进管线（Issue5 上游防线）。

生产实证：¥1.5 塑料转盘（1688 item 707084355449）被批量上架为 approved 卡
（35₽）——信封 draft.title 为空（worker 侧 9048 退化裸 item_id 铁证，标题靠
LLM 从图盲生成）。缺口链：
- cloud_probe.build_envelope_from_discovery fallback 组装旁路了 graph 主路径
  的 _validate_and_fix_product_data「产品标题为空」硬闸；
- batch_test.process_ozon_url 复用 discover 分支把 fallback 信封直接提交。

覆盖：
- batch 闸：空/空白/过短标题 → 跳过不提交（不调 submit_envelope、不降级
  follow），log/统计含该 item；
- batch 闸：正常标题照常提交（锁行为）；supplier 空不拦（合法，只有 title
  才是硬闸）；
- 置信度闸：0 < match_confidence < 0.3 跳过；≥0.3 / 0.0（未知豁免）放行；
- dry-run 不拦（试跑仍预览全量信封）；
- fallback 校验门：title 空/空白 → 同一校验门拦截 → 返回 None（返回约定
  本就含 None，三个调用方均已处理）；title 合 → 信封照常返回；
- main() 统计：闸拦截计入 gate_skipped，不计 failed（退出码不受影响）。

运行:
    cd skill && .venv314/bin/python -m pytest tests/test_batch_submit_gate_v073.py -q
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import batch_test  # noqa: E402
import scripts.cloud_probe as cloud_probe  # noqa: E402
from scripts.lib.ozon_discovery import ProductCandidate  # noqa: E402

GOOD_TITLE = "Автопоилка для кошек 2л"
INCIDENT_ITEM = "707084355449"  # 生产事故 1688 item


def _graph_input(title: str, supplier: str | None = None) -> dict:
    """构造真实 GraphInput 形状的 mock 信封。"""
    draft: dict = {"title": title}
    if supplier is not None:
        draft["supplier"] = supplier
    return {
        "token": "t",
        "ozon_client_id": "cid",
        "ozon_api_key": "akey",
        "envelope": {"draft": draft, "source": {}, "extensions": {}},
    }


def _run_reuse(candidate, envelope, dry_run=False):
    """mock 复用 discover 分支三件套后跑 process_ozon_url。"""
    entry = {
        "ozon_product_id": "4767514314",
        "ozon_title": "Автопоилка для кошек",
        "ozon_price": 1500.0,
        "ozon_url": "https://www.ozon.ru/product/4767514314",
        "match_1688_url": f"https://detail.1688.com/offer/{INCIDENT_ITEM}.html",
        "match_1688_title": "宠物自动饮水器",
        "match_1688_price": 25.0,
        "status": "profitable",
    }
    with mock.patch("batch_test._find_discover_source", return_value=entry), \
         mock.patch("batch_test._product_candidate_from_dict",
                    return_value=candidate), \
         mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                    return_value=envelope), \
         mock.patch("scripts.cloud_probe.submit_envelope",
                    return_value={"ok": True, "task_id": "T-1"}) as m_submit, \
         mock.patch("scripts.cloud_probe.follow_sell_cloud") as m_follow:
        r = batch_test.process_ozon_url(
            "https://www.ozon.ru/product/slug-4767514314/",
            "4767514314",
            "cid",
            "akey",
            "http://localhost:8080",
            dry_run=dry_run,
            store_id="",
        )
    return r, m_submit, m_follow


# ── batch 闸：空标题 ──────────────────────────────────────────────────────

def test_gate_blocks_empty_title_no_submit():
    """空 title 信封 → 跳过：不提交、不降级 follow，log/结果含 item 与原因。"""
    r, m_submit, m_follow = _run_reuse(object(), _graph_input(""))
    assert r.get("success") is False
    assert r.get("skipped_by_gate") is True
    assert "空标题" in (r.get("error") or "")
    assert r.get("product_id") == "4767514314"
    m_submit.assert_not_called()
    m_follow.assert_not_called()


def test_gate_blocks_whitespace_title():
    """纯空白 title → 同样拦截（与校验门 strip 语义一致）。"""
    r, m_submit, _ = _run_reuse(object(), _graph_input("   \u3000 "))
    assert r.get("skipped_by_gate") is True
    m_submit.assert_not_called()


def test_gate_blocks_too_short_title():
    """title 短于 MIN_SUBMIT_TITLE_LEN → 拦截。"""
    assert batch_test.MIN_SUBMIT_TITLE_LEN == 4
    r, m_submit, _ = _run_reuse(object(), _graph_input("аб"))
    assert r.get("skipped_by_gate") is True
    m_submit.assert_not_called()


def test_gate_passes_normal_title_submits():
    """正常 title → 照常提交（锁行为）。"""
    r, m_submit, m_follow = _run_reuse(object(), _graph_input(GOOD_TITLE))
    assert r.get("success") is True
    assert r.get("task_id") == "T-1"
    assert r.get("skipped_by_gate") is None
    m_submit.assert_called_once()
    m_follow.assert_not_called()


# ── batch 闸：置信度 ──────────────────────────────────────────────────────

def test_gate_blocks_weak_match_confidence():
    """0 < match_confidence < 0.3 → 弱匹配拦截。"""
    cand = SimpleNamespace(match_confidence=0.2)
    r, m_submit, _ = _run_reuse(cand, _graph_input(GOOD_TITLE))
    assert r.get("skipped_by_gate") is True
    assert "弱匹配" in (r.get("error") or "")
    m_submit.assert_not_called()


def test_gate_passes_strong_match_confidence():
    """match_confidence ≥ 0.3 → 放行提交。"""
    cand = SimpleNamespace(match_confidence=0.8)
    r, m_submit, _ = _run_reuse(cand, _graph_input(GOOD_TITLE))
    assert r.get("success") is True
    m_submit.assert_called_once()


def test_gate_zero_confidence_exempt():
    """match_confidence=0.0（旧缓存无字段/可信通道 0 分）→ 豁免放行。"""
    r, m_submit, _ = _run_reuse(object(), _graph_input(GOOD_TITLE))
    assert r.get("success") is True
    m_submit.assert_called_once()


def test_gate_bad_confidence_type_exempt():
    """confidence 字段类型损坏 → 视为未知豁免，不误拦。"""
    cand = SimpleNamespace(match_confidence="not-a-number")
    r, m_submit, _ = _run_reuse(cand, _graph_input(GOOD_TITLE))
    assert r.get("success") is True
    m_submit.assert_called_once()


# ── batch 闸：supplier 不拦 / dry-run 不拦 ────────────────────────────────

def test_supplier_empty_not_blocked():
    """supplier 空（CDP 拿不到 seller 的合法形态）→ 不拦，只有 title 是硬闸。"""
    r, m_submit, _ = _run_reuse(object(), _graph_input(GOOD_TITLE, supplier=""))
    assert r.get("success") is True
    m_submit.assert_called_once()


def test_dry_run_not_gated():
    """dry-run 只组装不提交 → 闸不生效（试跑仍预览全量信封）。"""
    r, m_submit, _ = _run_reuse(object(), _graph_input(""), dry_run=True)
    assert r.get("success") is True
    assert r.get("dry_run") is True
    assert r.get("skipped_by_gate") is None
    m_submit.assert_not_called()


# ── 闸 helper 纯函数单测 ─────────────────────────────────────────────────

def test_gate_rejection_helper_unit():
    """_submit_gate_rejection 直接单测（含信封形状防御）。"""
    assert batch_test._submit_gate_rejection(object(), None) != ""
    assert batch_test._submit_gate_rejection(object(), {}) != ""
    assert batch_test._submit_gate_rejection(
        object(), {"envelope": {"draft": {}}}) != ""
    assert batch_test._submit_gate_rejection(
        SimpleNamespace(match_confidence=0.29),
        _graph_input(GOOD_TITLE)) != ""
    assert batch_test._submit_gate_rejection(
        SimpleNamespace(match_confidence=0.31),
        _graph_input(GOOD_TITLE)) == ""
    # 拦截原因含 item 可读上下文（log 用）
    reason = batch_test._submit_gate_rejection(object(), _graph_input(""))
    assert "9048" in reason


# ── main() 统计集成：闸拦截计入 gate_skipped，不计 failed ─────────────────

def test_main_counts_gate_skip_as_gate_skipped(tmp_path):
    """main() 端到端：空标题信封 → gate_skipped=1 / failed=0，summary 落盘。"""
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text(
        "https://www.ozon.ru/product/slug-4767514314/\n", encoding="utf-8")
    out_dir = tmp_path / "batch_results"
    out_dir.mkdir()
    entry = {
        "ozon_product_id": "4767514314",
        "ozon_title": "Автопоилка",
        "ozon_price": 1500.0,
        "ozon_url": "https://www.ozon.ru/product/4767514314",
        "match_1688_url": f"https://detail.1688.com/offer/{INCIDENT_ITEM}.html",
        "match_1688_title": "宠物自动饮水器",
        "match_1688_price": 25.0,
        "status": "profitable",
    }
    argv = [
        "batch_test.py", "--urls-file", str(urls_file), "--submit",
        "--client-id", "cid", "--api-key", "akey",
        "--type-filter", "ozon", "--delay", "0",
    ]
    with mock.patch.object(batch_test, "OUTPUT_DIR", out_dir), \
         mock.patch("sys.argv", argv), \
         mock.patch("batch_test._find_discover_source", return_value=entry), \
         mock.patch("batch_test._product_candidate_from_dict",
                    return_value=object()), \
         mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                    return_value=_graph_input("")), \
         mock.patch("scripts.cloud_probe.submit_envelope") as m_submit:
        rc = batch_test.main()
    assert rc == 0, "闸拦截是有意跳过，不应使整批失败退出"
    m_submit.assert_not_called()
    logs = list(out_dir.glob("batch_*.json"))
    assert len(logs) == 2, "应落盘结果 + summary 两个文件"
    summary = json.loads(
        out_dir.joinpath(
            [p.name for p in logs if p.name.endswith("_summary.json")][0]
        ).read_text(encoding="utf-8"))
    assert summary["stats"]["gate_skipped"] == 1
    assert summary["stats"]["failed"] == 0
    results = json.loads(
        [p for p in logs if not p.name.endswith("_summary.json")][0]
        .read_text(encoding="utf-8"))
    assert results[0]["skipped_by_gate"] is True
    assert results[0]["product_id"] == "4767514314"
    assert "空标题" in results[0]["error"]


# ── cloud_probe fallback 校验门 ──────────────────────────────────────────

def _fallback_candidate(match_title: str, ozon_title: str) -> ProductCandidate:
    """构造走 fallback 组装段的最小候选（build_graph_envelope 失败降级）。"""
    c = ProductCandidate(
        ozon_product_id="4767514314",
        ozon_title=ozon_title,
        ozon_price=1500.0,
    )
    c.match_1688_url = f"https://detail.1688.com/offer/{INCIDENT_ITEM}.html"
    c.match_1688_title = match_title
    c.competing_sellers = 4
    c.weight_g = 0
    c.dimensions_mm = {}
    return c


def _build_fallback(cand: ProductCandidate):
    """走 build_envelope_from_discovery 的降级组装段（retry 失败 → 本地兜底）。"""
    with mock.patch.object(cloud_probe, "build_graph_envelope_with_retry",
                           return_value=None), \
         mock.patch("scripts.lib.config_store.get_mxou_token",
                    return_value="sk-test"), \
         mock.patch("scripts.lib.config_store.get_store_profile",
                    return_value={}):
        return cloud_probe.build_envelope_from_discovery(
            cand, {"client_id": "123", "api_key": "key"})


def test_fallback_empty_title_returns_none():
    """fallback：match/ozon 标题全空 → 校验门拦截 → 返回 None（生产事故形态）。"""
    result = _build_fallback(_fallback_candidate("", ""))
    assert result is None, "空标题 fallback 信封必须被闸拦下（返回 None）"


def test_fallback_whitespace_title_returns_none():
    """fallback：match_1688_title 纯空白（or 链 truthy 逃逸）→ 校验门 strip 后拦截。"""
    result = _build_fallback(_fallback_candidate("   ", ""))
    assert result is None


def test_fallback_match_title_ok_returns_envelope():
    """fallback：match_1688_title 非空 → 信封照常组装，title 原样透传。"""
    result = _build_fallback(_fallback_candidate("宠物自动饮水器", ""))
    assert result is not None, "标题合法的 fallback 信封不应被拦"
    draft = result["envelope"]["draft"]
    assert draft["title"] == "宠物自动饮水器"
    assert draft["item_id"] == INCIDENT_ITEM


def test_fallback_ozon_title_fallback_ok():
    """fallback：match 标题空、ozon_title 兜底 → title 非空 → 放行。"""
    result = _build_fallback(_fallback_candidate("", "Автопоилка для кошек"))
    assert result is not None
    assert result["envelope"]["draft"]["title"] == "Автопоилка для кошек"


def test_fallback_supplier_empty_ok():
    """fallback：supplier 空（getattr 默认 ''）→ 不拦（合法形态）。"""
    result = _build_fallback(_fallback_candidate("宠物自动饮水器", ""))
    assert result is not None
    assert result["envelope"]["draft"].get("supplier", "") == ""


def test_fallback_none_propagation_contract():
    """None 返回约定：best_id 解析失败先例已返回 None——调用方契约不变。"""
    c = _fallback_candidate("宠物自动饮水器", "")
    c.match_1688_url = "https://detail.1688.com/no-offer-path"
    with mock.patch.object(cloud_probe, "_discover_page_truth",
                           return_value={}):
        assert cloud_probe.build_envelope_from_discovery(
            c, {"client_id": "1", "api_key": "k"}) is None


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
