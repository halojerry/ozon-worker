"""fix/category-root-cause-v1 — 零交集误杀救济（validate 词表豁免 + type 级重配）+ 入箱 top-3 推荐。

背景（2026-09-26 实机 gate 取证，docs/ARCHITECTURE/09-findings.md）：
  - 任务 37ee72d9：«Блузка…»×«Рубашка»——Ozon 官方 ZH 译名错位（Блузка 译「短衫」），
    两词零词面交集被一致性闸误杀 → 词表豁免（utils/category_consistency_lexicon
    同义根词表）放行。
  - 任务 6022b0c9：«Держатель для душа…»×«Полка» 真错配，但 RU 标题 search_nodes
    （pg_trgm）一跳即中正确叶(0.567)——validate 只杀不救 → 杀之前先试 type 级重配。
  - 入箱不带候选：_final_result_blocked_to_box 恒传 candidates=[] → 采集箱「无候选
    推荐」人工改配全靠猜 → 中文源词搜 ZH 树带 top-3。

契约（本文件锁定）：
  A1 词表豁免：блузка×рубашка 零交集放行（info「lexicon 豁免」留痕，不触搜索）。
  A2 真错配仍拦：косточек×лапшерезка、держатель×полка 双保持——错误文案逐字
     含「标题与类目不一致（Ozon DESCRIPTION_DECLINE 风险）」（retry 子图按该
     文案归 LOCAL_TITLE_CATEGORY_MISMATCH，改一字即重演 v0.73 错归 BR_chinese）。
  A3 type 级重配命中：RU 标题强匹配（node_name+full_path 与标题有公共西里尔词）
     + (dc,tp) 树中有效且 ≠ 当前 → 改写 item dc/tp、不报 mismatch、warning 留痕。
  A4 type 级重配不命中（无强匹配/树中无效/树查询异常）→ 维持原拦截，dc/tp 不动。
  A5 权威来源（authoritative）仍走降级 warning，不触重配（信任序：权威类目不动）。
  B1 入箱推荐：draft.title 中文源词搜 ZH 树 top-3，形状对齐
     blocked_draft_box.category_recommendations 消费格式（dc/tp/name/similarity）；
     RU 路径拼进名称供对照；无源词/搜索失败 → []（非致命，不新增失败面）。
  B2 拦截出口把 top-3 传进 _maybe_create_blocked_draft（notice 带 Top1）。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_category_root_cause_v081.py -q
纯 mock（RU/ZH 搜索 + RU 路径 + 图片探测全 monkeypatch），无需 PG/网络。
"""
import logging
import os
import sys
from unittest.mock import patch

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest 

from graphs.state import OzonValidateInput 
from graphs.nodes import ozon_validate_node as ovn 
from graphs.nodes.ozon_validate_node import ( 
    ozon_validate_node,
    _cyr_words_len4,
    _lexicon_zero_overlap_pass,
)
import graphs.validation_retry_loop as vrl 
from graphs.validation_retry_loop import ( 
    LOCAL_TITLE_MISMATCH_BLOCK_REASON,
    ValidationRetryLoopState,
    final_result,
    _search_box_recommendations,
)

# 当前（错）类目：Дом > Мебель > Полка（6022b0c9 形态）；正确叶 (999,888)。
_DC, _TP = 17028001, 91001
_POLKA_PATH = "Дом > Мебель > Полка"
_NEW_DC, _NEW_TP = 999, 888
_NEW_PATH = "Стройматериалы > Сантехника > Держатель для душа"
# 37ee72d9 形态：Рубашка 路径 × Блузка 标题（词表豁免对）
_RUBASHKA_PATH = "Женщинам > Одежда > Рубашки"


class _Resp:
    def __init__(self, status=200, headers=None, is_redirect=False,
                 is_permanent_redirect=False):
        self.status_code = status
        self.headers = headers or {}
        self.is_redirect = is_redirect
        self.is_permanent_redirect = is_permanent_redirect


def _fake_ru_path(dc, tp):
    """(dc,tp) → RU 路径：错配 Полка + 重配目标 Держатель для душа 两行有效。"""
    if (dc, tp) == (_DC, _TP):
        return _POLKA_PATH
    if (dc, tp) == (_NEW_DC, _NEW_TP):
        return _NEW_PATH
    return ""


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """默认：图片探测 200（不依赖网络）、RU 路径按上表返回（不依赖 PG）。"""
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, port=None, *a, **k:
        [(2, 1, 6, "", ("93.184.216.34", port or 0))])
    monkeypatch.setattr("requests.request",
                        lambda *a, **k: _Resp(200, headers={},
                                              is_redirect=False,
                                              is_permanent_redirect=False))
    monkeypatch.setattr(ovn, "_fetch_ru_category_path", _fake_ru_path,
                        raising=False)


def _run(items, category_source=None):
    kwargs = {"category_source": category_source} if category_source is not None else {}
    state = OzonValidateInput(
        ozon_payload={"items": items},
        ozon_client_id="c",
        ozon_api_key="k",
        attributes_schema=[],
        **kwargs,
    )
    runtime = type("R", (), {"context": None})()
    return ozon_validate_node(state, {}, runtime)


def _item(**over):
    base = {
        "name": "Держатель для душа угловой", "offer_id": "sku1", "price": "1990",
        "old_price": "2390", "vat": "0", "weight": 300, "weight_unit": "g",
        "depth": 100, "width": 100, "height": 50, "dimension_unit": "mm",
        "images": ["https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/x.jpg"],
        "primary_image": "https://example.com/img.jpg",
        "description_category_id": _DC, "type_id": _TP,
        "attributes": [],
    }
    base.update(over)
    return base


def _mismatch_errors(out):
    return [e for e in out.validation_errors if "标题与类目不一致" in e]


# ═══════════════ A1：词表豁免（блузка×рубашка 放行）═══════════════

def test_lexicon_pure_functions():
    """纯函数：词表扩词相交只放行 Блузка×Рубашка 这类实机证据对；非同义对恒 False。"""
    assert _lexicon_zero_overlap_pass("Блузка женская оверсайз", _RUBASHKA_PATH)
    assert _lexicon_zero_overlap_pass("Рубашки мужские", "Одежда > Блузки")
    # 词形变体（前缀≥4）：блузки/рубашку 天然覆盖
    assert _lexicon_zero_overlap_pass("Блузки из шелка", "Одежда > Рубашку выбрать")
    # 真错配对（红线：держатель↔полка 严禁入表——防线在干活）
    assert not _lexicon_zero_overlap_pass("Держатель для душа", _POLKA_PATH)
    assert not _lexicon_zero_overlap_pass("Носки женские теплые",
                                          "Инструменты > Трещотка")
    # 短词不参与（для/и 恒不算，防虚词假相交）
    assert _cyr_words_len4("Дом и для") == set()


def test_lexicon_exemption_passes_with_info_log(caplog, monkeypatch):
    """①Блузка×Рубашка 零交集 → 词表豁免放行：不报错、is_valid=True、info 留痕
    「lexicon 豁免」，且不触类目搜索（豁免优先于重配）。"""
    monkeypatch.setattr(ovn, "_fetch_ru_category_path",
                        lambda dc, tp: _RUBASHKA_PATH if (dc, tp) == (_DC, _TP) else "",
                        raising=False)
    search_mock = patch("utils.ozon_category_query.OzonCategoryQuery.search_nodes",
                        return_value=[])
    with caplog.at_level(logging.INFO, logger="graphs.nodes.ozon_validate_node"), search_mock as mk:
        out = _run([_item(name="Блузка женская оверсайз из вискозы")])
    assert not _mismatch_errors(out), f"词表豁免必须放行: {out.validation_errors}"
    assert out.is_valid is True
    infos = [r for r in caplog.records
             if r.levelno == logging.INFO and "lexicon 豁免" in r.getMessage()]
    assert infos, "放行必须有「lexicon 豁免」info 留痕"
    assert "Блузка" in infos[0].getMessage()
    assert mk.called is False, "词表豁免命中后不得再触发重配搜索"


def test_lexicon_exemption_requires_both_sides(monkeypatch):
    """②单侧命中不算：标题含 рубашка 但路径零相关 → 仍拦（词表是成对豁免）。"""
    monkeypatch.setattr(
        "utils.ozon_category_query.OzonCategoryQuery.search_nodes",
        lambda self, q, **k: [])
    out = _run([_item(name="Рубашка и брюки набор")])  # 路径仍是 Полка
    assert _mismatch_errors(out), "词表只对两侧同组词放行，单侧命中必须仍拦"
    assert out.is_valid is False


# ═══════════════ A2：真错配仍拦（双保持）═══════════════

def test_real_mismatch_polka_still_blocked(monkeypatch):
    """③держатель×полка：搜索无强匹配 → 维持原拦截，文案逐字，dc/tp 不动。"""
    monkeypatch.setattr(
        "utils.ozon_category_query.OzonCategoryQuery.search_nodes",
        lambda self, q, **k: [])  # 树里搜不到强匹配
    out = _run([_item()])
    errs = _mismatch_errors(out)
    assert errs, f"真错配必须仍拦: {out.validation_errors}"
    # 红线：错误文案逐字保持（retry 子图按「标题与类目不一致（Ozon DESCRIPTION_DECLINE
    # 风险）」归 LOCAL_TITLE_CATEGORY_MISMATCH，改一字即重演 v0.73 错归 BR_chinese）
    assert "标题与类目不一致（Ozon DESCRIPTION_DECLINE 风险）" in errs[0], errs[0]
    assert "Полка" in errs[0], f"错误需附类目路径: {errs[0]}"
    assert out.is_valid is False
    assert out.ozon_payload["items"][0]["description_category_id"] == _DC
    assert out.ozon_payload["items"][0]["type_id"] == _TP


def test_real_mismatch_kostochki_still_blocked(monkeypatch):
    """④косточек×лапшерезка：候选不满足强匹配判据 → 仍拦。"""
    monkeypatch.setattr(
        "utils.ozon_category_query.OzonCategoryQuery.search_nodes",
        lambda self, q, **k: [
            {"description_category_id": 555, "type_id": 444,
             "node_name": "Лапшерезка механическая",
             "full_path": "Дом > Кухня > Лапшерезка", "similarity": 0.41},
        ])
    out = _run([_item(name="Сушилка для косточек фруктов")])
    assert _mismatch_errors(out), f"弱相似度候选不得放行: {out.validation_errors}"
    assert out.is_valid is False


# ═══════════════ A3：type 级重配命中 ═══════════════

def test_recategorize_hit_rewrites_dc_tp(caplog, monkeypatch):
    """⑤держатель×полka + RU 搜索一跳中正确叶 → 改写 dc/tp、不报 mismatch、
    warning「validate 级类目重配」留痕；其他校验照跑（is_valid=True）。"""
    monkeypatch.setattr(
        "utils.ozon_category_query.OzonCategoryQuery.search_nodes",
        lambda self, q, **k: [
            {"description_category_id": _NEW_DC, "type_id": _NEW_TP,
             "node_name": "Держатель для душа",
             "full_path": "Стройматериалы > Сантехника > Держатель для душа",
             "similarity": 0.567},
        ])
    with caplog.at_level(logging.WARNING, logger="graphs.nodes.ozon_validate_node"):
        out = _run([_item()])
    assert not _mismatch_errors(out), f"重配命中不得再报 mismatch: {out.validation_errors}"
    assert out.is_valid is True
    _it = out.ozon_payload["items"][0]
    assert _it["description_category_id"] == _NEW_DC, f"dc 必须改写: {_it}"
    assert _it["type_id"] == _NEW_TP, f"tp 必须改写: {_it}"
    warns = [r for r in caplog.records
             if r.levelno == logging.WARNING and "validate 级类目重配" in r.getMessage()]
    assert warns, "重配命中必须 warning 留痕"


def test_recategorize_skips_same_dc_tp(monkeypatch):
    """⑥候选 (dc,tp) 与当前相同 → 不算重配（无意义改写），维持拦截。"""
    monkeypatch.setattr(
        "utils.ozon_category_query.OzonCategoryQuery.search_nodes",
        lambda self, q, **k: [
            {"description_category_id": _DC, "type_id": _TP,
             "node_name": "Держатель для душа",
             "full_path": "Дом > Сантехника > Держатель для душа",
             "similarity": 0.6},
        ])
    out = _run([_item()])
    assert _mismatch_errors(out), "同值候选必须维持拦截"
    assert out.is_valid is False


def test_recategorize_skips_invalid_tree_node(monkeypatch):
    """⑦候选不在树中（_fetch_ru_category_path 空）→ 跳过，维持拦截
    （无效类目直传会撞 description_category_invalid 400）。"""
    monkeypatch.setattr(
        "utils.ozon_category_query.OzonCategoryQuery.search_nodes",
        lambda self, q, **k: [
            {"description_category_id": 777, "type_id": 666,
             "node_name": "Держатель для душа",
             "full_path": "Дом > Сантехника > Держатель для душа",
             "similarity": 0.5},
        ])  # (777,666) 不在 _fake_ru_path 白名单 → 树中无效
    out = _run([_item()])
    assert _mismatch_errors(out), "树中无效候选必须跳过维持拦截"
    assert out.is_valid is False


def test_recategorize_search_failure_degrades_to_block(monkeypatch, caplog):
    """⑧树查询异常 → 降级维持原拦截（validate 不因救场新增故障面）。"""
    def _boom(self, q, **k):
        raise RuntimeError("pg down")
    monkeypatch.setattr(
        "utils.ozon_category_query.OzonCategoryQuery.search_nodes", _boom)
    with caplog.at_level(logging.WARNING, logger="graphs.nodes.ozon_validate_node"):
        out = _run([_item()])
    assert _mismatch_errors(out), "树查询失败必须降级回原拦截"
    assert out.is_valid is False
    assert out.ozon_payload["items"][0]["description_category_id"] == _DC
    warns = [r for r in caplog.records if "重配跳过" in r.getMessage()]
    assert warns, "降级路径必须留痕"


def test_recategorize_not_attempted_for_authoritative(monkeypatch):
    """⑨权威来源（authoritative）走既有降级 warning 放行——不触重配搜索
    （信任序：权威类目可能是对的，问题在标题）。"""
    search_mock = patch("utils.ozon_category_query.OzonCategoryQuery.search_nodes",
                        return_value=[])
    with search_mock as mk:
        out = _run([_item(name="Носки женские теплые")], category_source="authoritative")
    assert not _mismatch_errors(out)
    assert out.is_valid is True
    assert mk.called is False, "权威来源不得触重配搜索"


# ═══════════════ B1：入箱推荐 top-3 ═══════════════

def _zh_hits():
    return [
        {"description_category_id": 17028555, "type_id": 91555,
         "node_name": "沥水篮", "full_path": "家居 > 厨房用品 > 沥水篮",
         "similarity": 0.52},
        {"description_category_id": 17028666, "type_id": 91666,
         "node_name": "置物架", "full_path": "家居 > 收纳 > 置物架",
         "similarity": 0.38},
        {"description_category_id": 17028777, "type_id": 91777,
         "node_name": "果盘", "full_path": "家居 > 餐具 > 果盘",
         "similarity": 0.31},
        {"description_category_id": 17028888, "type_id": 91888,
         "node_name": "砧板", "full_path": "家居 > 厨房用品 > 砧板",
         "similarity": 0.28},  # 第 4 条应被裁掉
    ]


def test_box_recommendations_shape(monkeypatch):
    """⑩draft.title 中文源词 → top-3（dc/tp/名称含 ZH+RU/similarity），
    形状对齐 category_recommendations 消费格式。"""
    monkeypatch.setattr(
        "utils.ozon_category_query.OzonCategoryQuery.search_nodes",
        lambda self, q, **k: _zh_hits() if q == "沥水篮" else [])
    monkeypatch.setattr(ovn, "_fetch_ru_category_path",
                        lambda dc, tp: f"RU path for {dc}")
    from utils.blocked_draft_box import category_recommendations
    state = type("S", (), {"draft": {"title": "沥水篮"}, "envelope": {}})()
    recs = _search_box_recommendations(state)
    assert len(recs) == 3, f"必须裁到 top-3: {recs}"
    assert recs[0]["description_category_id"] == 17028555
    assert recs[0]["type_id"] == 91555
    assert "沥水篮" in recs[0]["full_path"], f"名称须含 ZH 路径: {recs[0]}"
    assert "RU path for 17028555" in recs[0]["full_path"], f"名称须含 RU 路径: {recs[0]}"
    # 对齐 blocked_draft_box 的消费格式（dc/tp 可取、name 走 full_path、confidence 取 similarity）
    view = category_recommendations(recs, limit=3)
    assert len(view) == 3
    assert view[0]["name"] == recs[0]["full_path"]
    assert view[0]["confidence"] == 0.52
    assert view[0]["description_category_id"] == 17028555


def test_box_recommendations_keywords_fallback(monkeypatch):
    """⑪draft.title 缺失 → 回落 envelope.source.keywords 拼接。"""
    monkeypatch.setattr(
        "utils.ozon_category_query.OzonCategoryQuery.search_nodes",
        lambda self, q, **k: _zh_hits()[:1] if "沥水" in q else [])
    state = type("S", (), {"draft": {},
                           "envelope": {"source": {"keywords": ["沥水", "篮"]}}})()
    recs = _search_box_recommendations(state)
    assert len(recs) == 1, f"keywords 回落必须生效: {recs}"


def test_box_recommendations_no_words_no_search(monkeypatch):
    """⑫无源词 → 直接 []，不触搜索（零失败面）。"""
    search_mock = patch("utils.ozon_category_query.OzonCategoryQuery.search_nodes",
                        return_value=_zh_hits())
    state = type("S", (), {"draft": {}, "envelope": {}})()
    with search_mock as mk:
        assert _search_box_recommendations(state) == []
    assert mk.called is False


def test_box_recommendations_search_failure_returns_empty(monkeypatch):
    """⑬搜索失败（PG 不可用等）→ [] 非致命（拦截路径不受影响）。"""
    def _boom(self, q, **k):
        raise RuntimeError("pg down")
    monkeypatch.setattr(
        "utils.ozon_category_query.OzonCategoryQuery.search_nodes", _boom)
    state = type("S", (), {"draft": {"title": "沥水篮"}, "envelope": {}})()
    assert _search_box_recommendations(state) == []


# ═══════════════ B2：拦截出口带 top-3 ═══════════════

def _blocked_state(**kw):
    base = dict(
        ozon_payload={"items": [{"name": "Держатель для душа", "offer_id": "x"}]},
        draft={"item_id": "681352312", "title": "沥水篮"},
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id=str(_DC), type_id=str(_TP),
        user_id="tenant-1",
        envelope={"draft": {"item_id": "681352312"}, "source": {}, "extensions": {}},
        error_code="LOCAL_TITLE_CATEGORY_MISMATCH",
        error_type="fixable",
    )
    base.update(kw)
    return ValidationRetryLoopState(**base)


def test_blocked_to_box_passes_top3_candidates(monkeypatch):
    """⑭拦截出口把 top-3 传进 _maybe_create_blocked_draft；notice 带 Top1 推荐。"""
    _recs = [{"description_category_id": 17028555, "type_id": 91555,
              "node_name": "沥水篮", "full_path": "家居 > 厨房用品 > 沥水篮",
              "similarity": 0.52}]
    monkeypatch.setattr(vrl, "_search_box_recommendations", lambda s: _recs)
    with patch("utils.blocked_draft_box.create_blocked_draft") as mk:
        mk.return_value = {"draft_id": "d-1", "reused": False,
                           "top1_name": "家居 > 厨房用品 > 沥水篮",
                           "top1_confidence": 0.52}
        out = final_result(_blocked_state())
    assert mk.called is True
    _tenant, _envelope, _candidates, _reason = mk.call_args.args
    assert _tenant == "tenant-1"
    assert _candidates == _recs, f"top-3 必须透传入箱: {_candidates}"
    assert _reason == LOCAL_TITLE_MISMATCH_BLOCK_REASON
    assert out.upload_status == "blocked"
    assert out.is_valid is False
    assert "已拦截" in out.notice
    assert "Top1" in out.notice, f"入箱成功时 notice 须带 Top1 推荐: {out.notice}"


def test_blocked_to_box_search_failure_keeps_empty_candidates(monkeypatch):
    """⑮搜索失败维持 []：拦截终态与入箱行为与旧行为一致（负向回归）。"""
    monkeypatch.setattr(vrl, "_search_box_recommendations", lambda s: [])
    with patch("utils.blocked_draft_box.create_blocked_draft") as mk:
        mk.return_value = {"draft_id": "d-2", "reused": False,
                           "top1_name": "", "top1_confidence": 0.0}
        out = final_result(_blocked_state())
    _tenant, _envelope, _candidates, _reason = mk.call_args.args
    assert _candidates == []
    assert out.upload_status == "blocked"
    assert "已拦截" in out.notice


def test_box_rec_l0_negative_feedback_still_skipped(monkeypatch):
    """⑯红线回归：拦截出口刻意不记 L0 负反馈（零交集是本地启发式，人工改配前
    不给学习行记负反馈）——v0.73 语义保持。"""
    monkeypatch.setattr(vrl, "_search_box_recommendations", lambda s: [])
    with patch("utils.blocked_draft_box.create_blocked_draft") as mk, \
         patch.object(vrl, "_mark_category_negative_feedback") as neg:
        mk.return_value = None
        final_result(_blocked_state())
    assert neg.called is False


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:
            traceback.print_exc()
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
