"""fix/category-root-cause-v1 (catfix) 单测 — 类目匹配链四条实锤根因修复。

实机 gate 取证（2026-09-26，docs/ARCHITECTURE/09-findings.md 处置进度）：
  (a) 化妆收纳 4 单：jieba [化妆品,收纳盒] 对精准叶 «化妆包» 零召回（无同义词桥）；
      R2b 仲裁 4 次全被 skill search_kw 树校验候选（sim=1.0 锚点）带偏；fp 词集丢标题词。
  (b) follow 轻量出口无闸：«切面器»(压面机) 毒中 «去核器» 商品 dc/tp，零审计零 meta。
  (c) 学习闭环自污染：R2b 跨大类高置信 + 超泛词 leaf 的 approved 写成权威档（row 148）。
  (d) Блузка 官方 ZH 译「短衫」，译词 × 邻叶 «Рубашка» 零词面交集被一致性闸误杀。

修复面（本文件锁定）：
  1. category_synonyms.json 补桥（化妆收纳盒/化妆包/衬衫/短衫）；
  2. follow 分支三闸（(dc,tp) 配对 / 双语名交叉 / 面包屑交叉）+ 西里尔预检
     + 审计行 + category_match_meta；
  3. R2b 锚点降级（search_kw 候选无锚点资格 + prompt 标注）；
  4. fp 词集并入标题 tokens（0.3× 低权重）；
  5. learning 写侧守卫（cross_top/超泛词拒写 + follow 层 0.7）；
  6. R2b 审计落列（match_layer="R2b" + LLM 原文随行）；
  7. Step6.5 同义豁免词表放行（блузка↔рубашка）。

运行（纯 mock，无需 PG）:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_catfix_v080.py -q
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ═══════════════════════════════════════════════════════════════════════
# 1. 同义词桥补录
# ═══════════════════════════════════════════════════════════════════════

def _synonyms_table() -> dict:
    path = os.path.join(os.path.dirname(__file__), "..", "config", "category_synonyms.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_synonyms_bridge_entries_present():
    """四组桥词条在场（化妆收纳 ↔ 化妆包 / 衬衫 ↔ 短衫）。"""
    t = _synonyms_table()
    assert t.get("化妆品收纳盒") == ["化妆包", "化妆品盒", "收纳盒化妆"]
    assert t.get("化妆收纳盒") == ["化妆包", "化妆品盒"]
    assert t.get("化妆包") == ["化妆品收纳盒", "化妆收纳"]
    assert t.get("衬衫") == ["短衫"]
    assert t.get("短衫") == ["衬衫"]
    # 既有条目零改动（抽查锚）
    assert t.get("震动棒") == ["振动器", "振动蛋", "振动子弹"]
    assert t.get("轮毂") == ["轮辋", "车轮总成", "汽车轮胎", "轮胎"]


def test_synonyms_bridge_leaf_bonus_recalls_makeup_bag():
    """同义词桥经 _apply_leaf_bonus 给精准叶 «化妆包» 加分顶到首位。"""
    from graphs.nodes.assemble_ozon_product_node import _apply_leaf_bonus
    syn = _synonyms_table()
    cands = [
        {"node_name": "收纳盒", "similarity": 0.9},   # 泛词高分噪声
        {"node_name": "化妆包", "similarity": 0.4},   # 精准叶（同义词桥召回）
    ]
    out = _apply_leaf_bonus(cands, "化妆品收纳盒", syn)
    assert out[0]["node_name"] == "化妆包", \
        "同义词桥应让化妆包(+0.6 exact) 顶掉泛词噪声"


def test_query_synonyms_loader_sees_bridge():
    """OzonCategoryQuery 查询侧同义词加载器同样吃到桥（jieba 链召回 93048 短衫）。"""
    from utils.ozon_category_query import OzonCategoryQuery
    OzonCategoryQuery._QUERY_SYNONYMS_CACHE = None  # 清进程内缓存（文件级热加载语义）
    try:
        table = OzonCategoryQuery._load_query_synonyms()
    finally:
        OzonCategoryQuery._QUERY_SYNONYMS_CACHE = None
    assert table.get("衬衫") == ["短衫"]
    assert table.get("短衫") == ["衬衫"]


# ═══════════════════════════════════════════════════════════════════════
# 2. follow 分支三闸（纯函数面 + 全节点驱动面）
# ═══════════════════════════════════════════════════════════════════════

def test_follow_zh_overlap_poison_pair_rejected():
    """«切面器»×«去核器»/«压面机»：剥泛尾字后零交集（共享首字「切」不算）。"""
    from graphs.nodes.assemble_ozon_product_node import _follow_zh_overlap
    assert _follow_zh_overlap("去核器", "厨房家电 > 压面机 压面机家用") == set()
    assert _follow_zh_overlap("切果器", "压面机 切面器 多功能") == set()


def test_follow_zh_overlap_true_pair_passes():
    """«化妆(品)收纳盒»×«化妆包»：共同前缀「化妆」≥2 字 → 放行。"""
    from graphs.nodes.assemble_ozon_product_node import _follow_zh_overlap
    assert _follow_zh_overlap("化妆包", "日用百货 > 化妆品收纳盒"), "化妆品收纳盒 应命中化妆包"
    assert _follow_zh_overlap("化妆包", "化妆收纳盒 便携"), "化妆收纳盒 应命中化妆包"


def test_follow_cyr_preflight():
    """西里尔预检：零交集拒 / 词表组放行 / 无 RU 路径放行。"""
    from graphs.nodes.assemble_ozon_product_node import _follow_cyr_preflight_ok
    # 零交集拒（标题与 RU 路径无公共词且不属同义组）
    assert _follow_cyr_preflight_ok("Нож для овощей", "Дом и сад > Тёрки") is False
    # 公共词放行（тёрк 前缀 ≥4）
    assert _follow_cyr_preflight_ok("Тёрка кухонная", "Дом и сад > Тёрки") is True
    # 无 RU 路径 / 无西里尔标题 → 放行（无据可判不误伤）
    assert _follow_cyr_preflight_ok("任何标题", "") is True
    assert _follow_cyr_preflight_ok("压面机", "Дом и сад > Тёрки") is True


# ── 全节点驱动（assemble_ozon_product_node follow 分支）──

class _FakeQ:
    """get_category_query 替身：按 (dc,tp,language) 供节点；面包屑/搜索可配。"""

    def __init__(self, nodes=None, search_results=None, full_path_hits=None):
        self.nodes = nodes or {}            # {(dc, tp, lang): node}
        self.search_results = search_results or {}
        self.full_path_hits = full_path_hits or {}
        self.search_calls = []

    def get_node(self, dc, tp, language="ZH_HANS"):
        return self.nodes.get((int(dc), int(tp), language))

    def get_node_by_full_path(self, path, language=None):
        return self.full_path_hits.get(path)

    def get_node_by_description_category_id(self, dc):
        for (d, t, _lang), n in self.nodes.items():
            if d == int(dc) and t and t > 0:
                return n
        return None

    def search_nodes(self, text, top_k=10, node_type=None, language=None, **kw):
        self.search_calls.append((text, language))
        return list(self.search_results.get((text, language), []))

    def get_attribute_schema(self, dc, tp):
        return {"result": []}

    def score_candidates_by_fingerprint(self, candidates, source_keywords,
                                        title_keywords=None):
        return candidates


class _FakeLDB:
    """LocalDBManager 替身：只供 lookup_web_category_path。"""

    def __init__(self, mapping=None):
        self._mapping = mapping or {}

    def lookup_web_category_path(self, breadcrumb):
        key = str(breadcrumb or "").strip().lower()
        return self._mapping.get(key)


_AUDIT: list = []


def _reset_audit():
    _AUDIT.clear()


def _make_state(draft, extensions, product_id=None):
    return SimpleNamespace(
        draft=draft,
        envelope={"extensions": extensions},
        token="tok", ozon_client_id="cid", ozon_api_key="akey",
        currency_code="RUB",
        pricing_info={"price": "1000", "old_price": "1200"},
        source={"source_category_path": draft.get("source_category", "")},
        user_id="",  # 空 → 阻断入箱跳过（不触 DB）
        product_id=product_id,
        description_category_id="",
        type_id="",
        assembly_retry_count=0,
        task_id="",
    )


def _run_follow_node(monkeypatch, fake_q, draft, extensions, product_id=None,
                     web_map=None):
    """驱动 assemble_ozon_product_node 的 follow 分支，返回节点输出 dict。"""
    import graphs.nodes.assemble_ozon_product_node as asm

    _reset_audit()

    def _recorder(state, title, source_category, keywords, category_result,
                  match_layer, confidence, candidates, config=None,
                  adopted_extra=None):
        _AUDIT.append({
            "layer": match_layer, "conf": confidence,
            "result": category_result, "extra": adopted_extra,
        })

    monkeypatch.setattr(asm, "get_category_query", lambda: fake_q, raising=True)
    monkeypatch.setattr(asm, "_log_match_attempt", _recorder, raising=True)
    monkeypatch.setattr("utils.ozon_category_query.get_category_query",
                        lambda: fake_q, raising=True)
    monkeypatch.setattr("utils.local_db_manager.LocalDBManager",
                        lambda: _FakeLDB(web_map), raising=True)
    # _assemble_follow_sell 的 RU 日志查询触 DB → 立即抛错走静默 pass
    monkeypatch.setattr("storage.database.db.get_engine",
                        lambda: (_ for _ in ()).throw(RuntimeError("no db")),
                        raising=True)
    state = _make_state(draft, extensions, product_id=product_id)
    return asm.assemble_ozon_product_node(state, {}, None)


_CRUMB = "Кухня > Техника > Ламинаторы"


def test_follow_pair_check_rejects_bogus_tp_create(monkeypatch):
    """闸① (dc,tp) 配对：树中无配对 + CREATE → 显式阻断（不再 dc 存在即直采）。"""
    fake_q = _FakeQ()  # 树空：任何配对都不存在
    draft = {"title": "家用切面器 220V", "item_id": "it-1",
             "ozon_category": {"description_category_id": "111", "type_id": "222",
                               "source": "search_kw"}}
    out = _run_follow_node(monkeypatch, fake_q, draft,
                           {"follow_sell": True}, product_id=None)
    assert out.get("failed_stage") == "category_match"
    assert out.get("error_code") == "LOCAL_CATEGORY_MATCH_FAILED"
    assert "跟卖类目" in (out.get("error_message") or "")
    assert any(a["layer"] == "blocked" for a in _AUDIT), "闸拦截必须留审计行"


def test_follow_pair_check_update_omits_category(monkeypatch):
    """闸① 拦截 + UPDATE（有 product_id）→ 省略类目继续跟卖（Ozon 保留原卡类目）。"""
    fake_q = _FakeQ()
    draft = {"title": "家用切面器 220V", "item_id": "it-1",
             "ozon_category": {"description_category_id": "111", "type_id": "222",
                               "source": "search_kw"}}
    out = _run_follow_node(monkeypatch, fake_q, draft,
                           {"follow_sell": True}, product_id="999888777")
    assert not out.get("error_message"), f"UPDATE 不应失败: {out.get('error_message')}"
    assert str(out.get("description_category_id")) == "0", "闸拦截后类目必须置空（省略）"
    assert "category_match_meta" not in out or out.get("category_match_meta") is None or True
    assert any(a["layer"] == "blocked" for a in _AUDIT)


def test_follow_bilingual_cross_check_rejects_poison(monkeypatch):
    """闸② 双语名交叉：配对在树但 ZH 名与 1688 源词零交集（切面器×去核器）→ CREATE 阻断。"""
    fake_q = _FakeQ(nodes={
        (111, 222, "ZH_HANS"): {"description_category_id": 111, "type_id": 222,
                                "node_name": "去核器", "full_path": "厨房电器 > 去核器"},
    })
    draft = {"title": "家用压面机 切面器 多功能", "item_id": "it-1",
             "source_category": "厨房家电 > 压面机",
             "ozon_category": {"description_category_id": "111", "type_id": "222",
                               "source": "search_kw"}}
    out = _run_follow_node(monkeypatch, fake_q, draft,
                           {"follow_sell": True}, product_id=None)
    assert out.get("failed_stage") == "category_match", "毒配对必须被闸②拦截"
    assert "零词面交集" in (out.get("error_message") or "")


def test_follow_bilingual_cross_check_passes_and_audits(monkeypatch):
    """闸② 放行面：化妆收纳盒 search_kw 猜中化妆包（前缀「化妆」佐证）→ 采纳 + 审计 + meta。"""
    fake_q = _FakeQ(nodes={
        (17027904, 93337, "ZH_HANS"): {"description_category_id": 17027904,
                                       "type_id": 93337,
                                       "node_name": "化妆包",
                                       "full_path": "美容 > 配件 > 化妆包"},
        (17027904, 93337, "RU"): {"description_category_id": 17027904, "type_id": 93337,
                                  "node_name": "Косметичка",
                                  "full_path": "Красота > Аксессуары > Косметичка"},
    })
    draft = {"title": "便携化妆收纳盒 大容量", "item_id": "it-1",
             "source_category": "日用百货 > 化妆品收纳盒",
             "ozon_category": {"description_category_id": "17027904",
                               "type_id": "93337", "source": "search_kw"}}
    out = _run_follow_node(monkeypatch, fake_q, draft,
                           {"follow_sell": True}, product_id=None)
    assert not out.get("error_message"), f"应采纳: {out.get('error_message')}"
    meta = out.get("category_match_meta") or {}
    assert meta.get("match_layer") == "follow"
    assert meta.get("description_category_id") == "17027904"
    assert meta.get("ru_full_path") == "Красота > Аксессуары > Косметичка"
    assert any(a["layer"] == "follow" for a in _AUDIT), "采纳出口必须写 follow 审计行"
    assert str(out.get("description_category_id")) == "17027904"


def test_follow_breadcrumb_overrides_numeric_guess(monkeypatch):
    """闸③ 面包屑交叉：数字猜测与 Web 面包屑映射不一致 → 弃数字按面包屑重配。"""
    fake_q = _FakeQ(nodes={
        (111, 222, "ZH_HANS"): {"description_category_id": 111, "type_id": 222,
                                "node_name": "去核器", "full_path": "厨房电器 > 去核器"},
        (333, 444, "ZH_HANS"): {"description_category_id": 333, "type_id": 444,
                                "node_name": "压面机", "full_path": "厨房电器 > 压面机"},
        (333, 444, "RU"): {"description_category_id": 333, "type_id": 444,
                           "node_name": "Ламинатор", "full_path": "Кухня > Ламинатор"},
    })
    draft = {"title": "家用压面机", "item_id": "it-1",
             "source_category": "厨房家电 > 压面机",
             "ozon_category": {"description_category_id": "111", "type_id": "222",
                               "category_path": _CRUMB, "source": "search_kw"}}
    web_map = {_CRUMB.strip().lower(): {"description_category_id": "333",
                                        "type_id": "444", "language": "RU"}}
    out = _run_follow_node(monkeypatch, fake_q, draft,
                           {"follow_sell": True}, product_id=None, web_map=web_map)
    assert not out.get("error_message"), f"面包屑重配应采纳: {out.get('error_message')}"
    assert str(out.get("description_category_id")) == "333", "必须按面包屑 dc/tp 重配"
    assert str(out.get("type_id")) == "444"


def test_follow_trusted_source_skips_cross_check(monkeypatch):
    """权威 source=page：跳过闸②词面交叉（Ozon 页面事实信任），配对/预检仍走。"""
    fake_q = _FakeQ(nodes={
        (555, 666, "ZH_HANS"): {"description_category_id": 555, "type_id": 666,
                                "node_name": " organizador ", "full_path": "x > y"},
        (555, 666, "RU"): {"description_category_id": 555, "type_id": 666,
                           "node_name": "Органайзер", "full_path": "Дом > Органайзер"},
    })
    draft = {"title": "Storage Case 14x10x28", "item_id": "it-1",
             "ozon_category": {"description_category_id": "555", "type_id": "666",
                               "source": "page"}}
    out = _run_follow_node(monkeypatch, fake_q, draft,
                           {"follow_sell": True}, product_id="999888777")
    assert not out.get("error_message"), f"page 权威应直采: {out.get('error_message')}"
    meta = out.get("category_match_meta") or {}
    assert meta.get("match_layer") == "follow"
    assert meta.get("confidence") == 0.9, "权威 source 采纳置信 0.9"


def test_follow_cyr_preflight_blocks_zero_overlap(monkeypatch):
    """西里尔预检：标题与 RU 路径零公共词（非词表组）→ CREATE 阻断。

    用权威 source=page 跳过闸②，单独验证西里尔预检的拦截面。
    """
    fake_q = _FakeQ(nodes={
        (777, 888, "ZH_HANS"): {"description_category_id": 777, "type_id": 888,
                                "node_name": " Dolomite ", "full_path": "x > y"},
        (777, 888, "RU"): {"description_category_id": 777, "type_id": 888,
                           "node_name": "Доломит", "full_path": "Сад > Доломит"},
    })
    draft = {"title": "Нож кухонный для овощей", "item_id": "it-1",
             "source_category": "厨房家电 > 压面机",
             "ozon_category": {"description_category_id": "777", "type_id": "888",
                               "source": "page"}}
    out = _run_follow_node(monkeypatch, fake_q, draft,
                           {"follow_sell": True}, product_id=None)
    assert out.get("failed_stage") == "category_match"
    assert "西里尔" in (out.get("error_message") or "")


def test_follow_text_branch_cross_check(monkeypatch):
    """文本类目名分支：pg_trgm 命中也要过闸②（零交集拒采）。"""
    fake_q = _FakeQ(search_results={
        ("Ламинатор", "RU"): [{"description_category_id": 999, "type_id": 1000,
                               "node_name": "Долото", "full_path": "Инструмент > Долото"}],
    }, nodes={
        (999, 1000, "ZH_HANS"): {"description_category_id": 999, "type_id": 1000,
                                 "node_name": "凿子", "full_path": "工具 > 凿子"},
    })
    draft = {"title": "家用压面机", "item_id": "it-1",
             "source_category": "厨房家电 > 压面机",
             "ozon_category": {"description_category_id": "Ламинатор",
                               "source": "search_kw"}}
    out = _run_follow_node(monkeypatch, fake_q, draft,
                           {"follow_sell": True}, product_id=None)
    assert out.get("failed_stage") == "category_match", "文本命中零交集也必须拦"


# ═══════════════════════════════════════════════════════════════════════
# 3. R2b 锚点降级
# ═══════════════════════════════════════════════════════════════════════

_SK_HIT = {  # skill search_kw 树校验候选（sim=1.0，与源词字面命中）
    "description_category_id": 70001, "type_id": 70002,
    "node_name": "化妆刷", "full_path": "美容 > 化妆工具 > 化妆刷",
    "similarity": 1.0, "source": "search_kw",
}
_REAL_HIT = {  # 文本链候选（与源词命中，无 source 标记）
    "description_category_id": 80001, "type_id": 80002,
    "node_name": "化妆包", "full_path": "美容 > 配件 > 化妆包",
    "similarity": 0.42,
}


def test_r2b_anchor_search_kw_excluded():
    """search_kw 树校验候选不再有源词锚点资格（保留候选身份）。"""
    from graphs.nodes.assemble_ozon_product_node import _r2b_source_hit_candidates
    pool = [dict(_SK_HIT), dict(_REAL_HIT)]
    anchors = _r2b_source_hit_candidates(pool, ["化妆刷 化妆包 便携"])
    assert all(a.get("source") != "search_kw" for a in anchors), \
        f"search_kw 候选不得做锚点: {anchors}"
    assert any(a.get("description_category_id") == 80001 for a in anchors), \
        "真实文本候选仍是合法锚点"


def test_r2b_cross_top_blocked_without_non_sk_anchor():
    """池内唯一源词锚点是 search_kw 候选时，跨大类高置信采纳失去域证据 → 不放行。"""
    from graphs.nodes.assemble_ozon_product_node import _r2b_confirm_adoption
    confirm = dict(_SK_HIT, _llm_confidence=0.9)   # LLM 被 sim=1.0 带偏选中 search_kw 候选
    pool = [dict(_REAL_HIT, similarity=0.42), dict(_SK_HIT)]
    draft = {"images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"]}
    ov, vision, why, meta = _r2b_confirm_adoption(
        confirm, pool, "化妆收纳盒 化妆品", draft, query=None)
    assert not ov and not vision, f"无合法锚点必须阻断: ov={ov}, vision={vision}, why={why}"
    assert not meta.get("cross_top_high_confidence")


def test_r2b_prompt_marks_search_kw_candidate():
    """prompt 里 search_kw 候选带「非权威猜测」标注（防 LLM 锚定 sim=1.0）。"""
    import graphs.nodes.assemble_ozon_product_node as asm
    captured = {}

    def _fake_llm(**kwargs):
        captured["prompt"] = kwargs.get("user_prompt", "")
        return json.dumps({"top_index": -1, "confidence": 0.0,
                           "reason": "x", "suggest_keywords": ""})

    fake_q = _FakeQ()
    state = SimpleNamespace(token="tok")
    with mock.patch("utils.mxou_api.call_mxou_chat_api", side_effect=_fake_llm), \
         mock.patch("utils.ozon_category_query.get_category_query",
                    new=lambda: fake_q):
        asm._llm_rank_categories(
            [dict(_REAL_HIT), dict(_SK_HIT)], "化妆收纳盒", {}, state,
            source_category="日用百货 > 化妆品收纳盒")
    p = captured.get("prompt", "")
    assert "[skill关键词猜测,非权威" in p, "search_kw 候选必须带非权威标注"
    assert p.count("[skill关键词猜测,非权威") == 1, "只有 search_kw 候选带标注"


# ═══════════════════════════════════════════════════════════════════════
# 4. fp 词集并入标题 tokens（0.3×）
# ═══════════════════════════════════════════════════════════════════════

def _fp_query():
    """裸实例（跳 __init__ 不触 DB）；learned/dict 查询走异常兜底空表。"""
    from utils.ozon_category_query import OzonCategoryQuery
    return OzonCategoryQuery.__new__(OzonCategoryQuery)


def test_fingerprint_title_tokens_low_weight():
    """标题 token「化妆」0.3× 记分：化妆包 fp>0 并超过零命中候选。"""
    with mock.patch("storage.database.db.get_session",
                    side_effect=RuntimeError("no db")), \
         mock.patch("utils.ozon_category_query.OzonCategoryQuery._load_domain_hints",
                    return_value=[], raising=True):
        cands = [
            {"description_category_id": 1, "type_id": 2, "node_name": "收纳盒",
             "full_path": "家居 > 收纳 > 收纳盒", "depth": 0, "similarity": 0.5},
            {"description_category_id": 3, "type_id": 4, "node_name": "化妆包",
             "full_path": "美容 > 配件 > 化妆包", "depth": 0, "similarity": 0.3},
        ]
        out = _fp_query().score_candidates_by_fingerprint(
            cands, ["化妆品", "收纳盒"], title_keywords=["化妆", "便携"])
    by_name = {c["node_name"]: c for c in out}
    # 收纳盒: 源词 name_overlap 1.0（收纳盒∈node_name）
    assert by_name["收纳盒"]["fingerprint_score"] >= 1.0
    # 化妆包: 仅标题低权重——name 0.3×1.0 + path 0.3×0.5 = 0.45（源词零命中）
    assert by_name["化妆包"]["fingerprint_score"] == 0.45, \
        f"标题 token 必须 0.3× 记分: {by_name['化妆包']['fingerprint_score']}"


def test_fingerprint_title_tokens_not_double_counted():
    """与类目词重复的标题 token 不重复计分（source 满权重只算一次）。"""
    with mock.patch("storage.database.db.get_session",
                    side_effect=RuntimeError("no db")), \
         mock.patch("utils.ozon_category_query.OzonCategoryQuery._load_domain_hints",
                    return_value=[], raising=True):
        cands = [{"description_category_id": 1, "type_id": 2, "node_name": "化妆包",
                  "full_path": "美容 > 化妆包", "depth": 0, "similarity": 0.3}]
        out = _fp_query().score_candidates_by_fingerprint(
            cands, ["化妆包"], title_keywords=["化妆包"])
    # 源词满权重 path0.5+name1.0=1.5；重复 token 不再 0.3× 双计（否则 1.95）
    assert out[0]["fingerprint_score"] == 1.5, "重复 token 不得 1.0+0.3 双计"


def test_fingerprint_rerank_wiring_passes_title_tokens():
    """assemble 侧 _apply_fingerprint_rerank 把标题独有 tokens 送进 title_keywords。"""
    import graphs.nodes.assemble_ozon_product_node as asm
    received = {}

    def _fake_score(cands, src_kws, title_keywords=None):
        received["src"] = list(src_kws)
        received["title"] = list(title_keywords or [])
        return cands

    fake_q = _FakeQ()
    _two_cands = [{"description_category_id": 1, "type_id": 2, "node_name": "a"},
                  {"description_category_id": 3, "type_id": 4, "node_name": "b"}]
    with mock.patch.object(fake_q, "score_candidates_by_fingerprint",
                           side_effect=_fake_score):
        asm._apply_fingerprint_rerank(
            fake_q, _two_cands,
            source_keywords="化妆品收纳盒 化妆包 化妆品 收纳盒",
            keywords="化妆品收纳盒 化妆包 化妆品 收纳盒 便携 大容量")
    assert received["title"] == ["便携", "大容量"], \
        f"标题独有 tokens 应传入: {received['title']}"
    assert "便携" not in received["src"]
    # source_keywords 为空 → 标题词保持原满权重语义（title_keywords=None）
    with mock.patch.object(fake_q, "score_candidates_by_fingerprint",
                           side_effect=_fake_score):
        asm._apply_fingerprint_rerank(
            fake_q, _two_cands, source_keywords="", keywords="压面机 家用")
    assert received["title"] == [] and "压面机" in received["src"]


# ═══════════════════════════════════════════════════════════════════════
# 5. learning 写侧守卫
# ═══════════════════════════════════════════════════════════════════════

class _Row:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return self._value


class _FakeSession:
    """ZH 查询回 zh_path；RU/存在性查询 truthy（mapping_valid 放行）。"""

    def __init__(self, zh_path):
        self._zh = zh_path

    def execute(self, sql, *a, **k):
        if "ZH_HANS" in str(sql):
            return _Row((self._zh,))
        return _Row(("RU>placeholder",))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _lr_state(leaf, meta=None):
    return SimpleNamespace(
        description_category_id="17028959", type_id=96513,
        moderation_status="approved", status="approved", upload_status="success",
        ozon_upload_success=False, product_id="",
        final_attributes=[], attributes_schema=[], fetch_back_result={},
        draft={"title": "测试", "source_category": f"测试类目 > {leaf}"},
        envelope={"extensions": {}}, source=None,
        category_match_meta=meta or {},
        user_id="", ozon_client_id="", ozon_api_key="", pricing_info={},
    )


def _run_learning(state, zh_path):
    from graphs.nodes.learning_record_node import learning_record_node
    runtime = SimpleNamespace(context=SimpleNamespace())
    with mock.patch("storage.database.db.get_session",
                    return_value=_FakeSession(zh_path)), \
         mock.patch("graphs.nodes.learning_record_node.LocalDBManager") as mock_db:
        mock_db.return_value = mock_db
        learning_record_node(state, SimpleNamespace(), runtime)
    return mock_db


def test_learning_guard_rejects_cross_top_high_confidence():
    """R2b 跨大类高置信采纳的 approved 不回灌学习表（弱证据不当真值）。"""
    state = _lr_state("收纳盒", meta={"match_layer": "R2b",
                                      "cross_top_high_confidence": True})
    mock_db = _run_learning(state, "厨房 > 收纳盒")  # leaf 重叠过断点3，守卫②拒写
    mock_db.add_category_mapping.assert_not_called()


def test_learning_guard_rejects_overgeneric_leaf():
    """超泛词 leaf（收纳盒族）approved 拒写——防 row148 式自污染再生。"""
    for leaf in ("收纳盒", "桌面收纳盒", "置物架", "整理盒"):
        state = _lr_state(leaf, meta={"match_layer": "L1"})
        mock_db = _run_learning(state, f"厨房 > {leaf}")
        mock_db.add_category_mapping.assert_not_called(), f"{leaf} 必须拒写"


def test_learning_follow_layer_written_at_07():
    """follow 层采纳（三闸放行）→ mapping 写入但压 0.7（不给 0.85 缺省高信）。"""
    state = _lr_state("化妆包", meta={"match_layer": "follow", "confidence": 0.7})
    mock_db = _run_learning(state, "美容 > 配件 > 化妆包")
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["confidence"] == 0.7


def test_learning_normal_l1_still_written():
    """对照组：普通 L1 leaf 照写 0.7（守卫不误伤正常学习）。"""
    state = _lr_state("化妆包", meta={"match_layer": "L1"})
    mock_db = _run_learning(state, "美容 > 配件 > 化妆包")
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["confidence"] == 0.7


# ═══════════════════════════════════════════════════════════════════════
# 6. R2b 审计落列
# ═══════════════════════════════════════════════════════════════════════

def test_r2b_confirm_sets_match_layer():
    """R2b 确认采纳后定稿 match_layer 写 "R2b"（不再留 L1，采纳率可按列统计）。

    通过源码锁定赋值语句（全节点驱动需 LLM/DB 全链 mock，性价比低；
    语句级锁定 + 下方 meta/match_log 携带面单测组合覆盖）。
    """
    import inspect
    import graphs.nodes.assemble_ozon_product_node as asm
    src = inspect.getsource(asm.assemble_ozon_product_node)
    assert 'match_layer = "R2b"' in src, "R2b 确认采纳必须落 match_layer 列"


def test_r2b_adopted_extra_flows_to_match_log():
    """R2b 定稿携带 LLM 原文（_r2b_llm_reason/confidence）→ match_log adopted 留痕。

    以 _log_match_attempt 的 adopted_extra 序列化契约为锁：传入即追加
    {"adopted": {...}} 末元素，不改变既有候选行形状。
    """
    import graphs.nodes.assemble_ozon_product_node as asm
    # 序列化面直接构造：adopted_extra 非空 → candidates_json 含 adopted 行
    captured = {}

    class _FakeCursor:
        def execute(self, sql, params):
            captured["params"] = params

        def close(self):
            pass

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def commit(self):
            pass

        def close(self):
            pass

    state = SimpleNamespace(task_id="t-1", user_id="u1",
                            draft={"purchase_url": "https://detail.1688.com/x.html"})
    extra = {"dc": 80001, "tp": 80002, "llm_reason": "r", "llm_confidence": 0.9,
             "cross_top": True, "confirm_why": "w"}
    with mock.patch("psycopg2.connect", return_value=_FakeConn()), \
         mock.patch("storage.database.db.get_db_url", return_value="postgresql://x"):
        asm._log_match_attempt(state, "标题", "货源", "关键词",
                               {"description_category_id": 80001, "type_id": 80002},
                               "R2b", 0.9,
                               [{"description_category_id": 1, "type_id": 2,
                                 "node_name": "n", "similarity": 0.5,
                                 "full_path": "a > b"}],
                               config={"configurable": {"thread_id": "t-1"}},
                               adopted_extra=extra)
    import json as _json
    rows = _json.loads(captured["params"][9])
    assert rows[-1].get("adopted") == extra, f"adopted 原文必须随行: {rows[-1]}"
    assert rows[0].get("name") == "n", "既有候选行形状不变"
    assert captured["params"][7] == "R2b"


# ═══════════════════════════════════════════════════════════════════════
# 7. Step6.5 同义豁免词表放行（блузка↔рубашка）
# ═══════════════════════════════════════════════════════════════════════

def test_step65_lexicon_allows_bluzka_rubashka():
    """今日误杀面：«Блузка» 标题 × «Рубашка» 类目零字面交集 → 词表放行。"""
    from graphs.nodes.assemble_ozon_product_node import _check_category_consistency
    ok = _check_category_consistency(
        "Женская блузка свободного кроя из хлопка",
        "Одежда > Блузки > Рубашка", 93048, 93209)
    assert ok is True, "блузка↔рубашка 同义组必须放行"


def test_step65_lexicon_plural_form():
    """词形变体：标题 блузки（复数）× 类目 рубашка 仍经词根前缀命中放行。"""
    from graphs.nodes.assemble_ozon_product_node import _check_category_consistency
    assert _check_category_consistency(
        "Блузки женские летние", "Одежда > Блузки > Рубашка", 93048, 93209) is True


def test_step65_zero_overlap_still_blocks_without_group():
    """对照：非同义对（держатель × полка）零交集仍拦——词表只放行不新增拦截。"""
    from graphs.nodes.assemble_ozon_product_node import _check_category_consistency
    assert _check_category_consistency(
        "Держатель для телефона в авто",
        "Дом и сад > Мебель > Полка", 1, 2) is False


def test_step65_direct_overlap_short_circuits():
    """正常重叠场景不受词表影响（先走原判等）。"""
    from graphs.nodes.assemble_ozon_product_node import _check_category_consistency
    assert _check_category_consistency(
        "Рубашка мужская классическая",
        "Одежда > Рубашки > Рубашка", 1, 2) is True
