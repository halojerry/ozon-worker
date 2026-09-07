"""v0.69 T0.3 — LLM Top1+置信度分层采纳 + 低置信/弃权自动入采集箱（TDD RED→GREEN）。

生产背景（实机反馈）：
  ①并列候选（Канистра для ГСМ/для воды/универсальная score 全 5）LLM 弃权 → 全阻断。
    用户拍板：「宁可给 Top1+置信度让用户决定」。
  ②水暖风机类好单因 LLM 俄语标题×中文候选树弃权转阻断（Wave D 已知缺口）。

修复契约：
  2a  _llm_rank_categories prompt 要求 {"top_index", "confidence", "reason"}；仅当所有
      候选与商品明显无关才允许 top_index=-1 弃权。解析向后兼容旧格式（candidate_index，
      旧格式照旧走现行四段判据，_llm_confidence=None）；新格式缺 confidence → 保守 0.0。
  2b  _r2b_confirm_adoption 分层采纳（返回 4 元组，末位 adopt_meta）：
        a) 同大类 + confidence ≥ R2B_ADOPT_CONF_SAME_TOP(0.5) → 采纳（现行第四段强化版）；
        b) 跨大类（池内源词命中锚点顶层大类均不同）+ confidence ≥
           R2B_ADOPT_CONF_CROSS_TOP(0.75) → 采纳，adopt_meta={"cross_top_high_confidence": True}；
        c) 其余（置信度不足/abstain/旧格式未达标）→ 不自动采纳 → 走入箱。
      R1 veto / R2b 仲裁池边界 / search_kw 非权威 / Step6.5 豁免语义均不动。
  2c  低置信/歧义/弃权阻断出口 → _create_blocked_draft 自动入采集箱（幂等：同 tenant +
      同 item_id 已存在未提交 draft → 复用），notice 追加
      「已入采集箱 draft_id=<id>，推荐类目 Top1=<名>(置信度 x.xx)」；
      R1 veto 出口与标题为空出口不入箱（前者需资质不自动推荐，后者 create_draft 必 400）；
      入箱失败非致命；follow 层门控仲裁不建 draft（只在 assemble 层做一次）。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_category_gate_draft_v069.py -q
全部纯 mock，不连 PG/不触网。
"""
import os
import sys
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes import assemble_ozon_product_node as asm

# ── 场景锚（复用 test_r2b_overlap_v069 生产取证形态）──
_AC = {"description_category_id": 17039635, "type_id": 90414,
       "node_name": "空调", "full_path": "家用电器 > 空调设备 > 空调", "similarity": 0.46}
_HEATER = {"description_category_id": 91448, "type_id": 90415,
           "node_name": "加热器", "full_path": "家用电器 > 空调设备 > 加热器", "similarity": 0.38}
_WATER_FAN = {"description_category_id": 971109685, "type_id": 90416,
              "node_name": "水暖风机", "full_path": "家用电器 > 空调设备 > 水暖风机",
              "similarity": 0.35}
_OTHER_TOP = {"description_category_id": 99001, "type_id": 99002,
              "node_name": "暖风机", "full_path": "工业设备 > 取暖设备 > 暖风机", "similarity": 0.30}
_IM_DRAFT = {"item_id": "im-1", "images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"],
             "title": "暖风机"}
_POOL_SAME_TOP = [_AC, _HEATER, _WATER_FAN]
_POOL_CROSS_TOP = [_AC, _HEATER, _OTHER_TOP]


def _conf(c: dict, conf: float) -> dict:
    out = dict(c)
    out["_llm_confidence"] = conf
    return out


class _FakeState:
    """模拟 GlobalState — 只含入箱路径读取的字段。"""

    def __init__(self, user_id="u-1", envelope=None, retry=0):
        self.user_id = user_id
        self.envelope = envelope if envelope is not None else {"draft": _IM_DRAFT, "extensions": {}}
        self.assembly_retry_count = retry
        self.task_id = "t-1"


# ═══════════════════════════════════════════════════════════════════════
# 2a：_llm_rank_categories 新格式解析 + 旧格式兼容
# ═══════════════════════════════════════════════════════════════════════
def test_01_new_format_parsed_with_confidence():
    """新格式 {"top_index","confidence","reason"} → 返回候选副本带 _llm_confidence/_llm_reason。"""
    captured = {}

    def _fake_llm(*args, **kwargs):
        captured["system"] = kwargs.get("system_prompt", "")
        captured["user"] = kwargs.get("user_prompt", "")
        return '{"top_index": 2, "confidence": 0.8, "reason": "带图更像加热器"}'

    cands = [_AC, _HEATER, _WATER_FAN]
    state = type("S", (), {"token": ""})()
    with mock.patch("utils.mxou_api.call_mxou_chat_api", side_effect=_fake_llm):
        r = asm._llm_rank_categories(cands, "暖风机", _IM_DRAFT, state)
    assert r is not None and r.get("type_id") == 90415
    assert r.get("_llm_confidence") == 0.8, f"新格式必须带置信度: {r}"
    assert r.get("_llm_reason") == "带图更像加热器"
    # 不污染池内原 dict
    assert "_llm_confidence" not in cands[1]
    # prompt 已要求 top_index+confidence+reason，且 -1 弃权语义
    assert "top_index" in captured["user"] and "confidence" in captured["user"]
    assert "-1" in captured["user"] and "reason" in captured["user"]


def test_02_legacy_format_still_works():
    """旧格式 {"candidate_index": N} 兼容：_llm_confidence=None（照旧走现行四段）。"""
    state = type("S", (), {"token": ""})()
    with mock.patch("utils.mxou_api.call_mxou_chat_api",
                    return_value='{"candidate_index": 1, "suggest_keywords": ""}'):
        r = asm._llm_rank_categories([_AC, _HEATER], "kw", _IM_DRAFT, state)
    assert r is not None and r.get("type_id") == 90414
    assert "_llm_confidence" not in r or r.get("_llm_confidence") is None
    # suggest 标记语义不变
    with mock.patch("utils.mxou_api.call_mxou_chat_api",
                    return_value='{"candidate_index": 0, "suggest_keywords": "обогреватель"}'):
        r2 = asm._llm_rank_categories([_AC], "kw", _IM_DRAFT, state)
    assert r2 is not None and r2.get("_llm_suggest") is True


def test_03_new_format_missing_confidence_conservative_zero():
    """新格式（带 top_index）缺 confidence → 保守取 0.0（不得当旧格式放行）。"""
    state = type("S", (), {"token": ""})()
    with mock.patch("utils.mxou_api.call_mxou_chat_api",
                    return_value='{"top_index": 1, "reason": "x"}'):
        r = asm._llm_rank_categories([_AC, _HEATER], "kw", _IM_DRAFT, state)
    assert r is not None
    assert r.get("_llm_confidence") == 0.0, f"缺 confidence 必须保守 0.0: {r}"


def test_04_new_format_explicit_abstain_minus_one():
    """top_index=-1（明显无关才允许）→ 走 abstain 路径（suggest/None），绝不返回候选。"""
    state = type("S", (), {"token": ""})()
    with mock.patch("utils.mxou_api.call_mxou_chat_api",
                    return_value='{"top_index": -1, "confidence": 0.9, "suggest_keywords": "канистра"}'):
        r = asm._llm_rank_categories([_AC], "kw", _IM_DRAFT, state)
    assert r is not None and r.get("_llm_suggest") is True
    with mock.patch("utils.mxou_api.call_mxou_chat_api",
                    return_value='{"top_index": -1, "confidence": 0.9, "reason": "全部无关"}'):
        r2 = asm._llm_rank_categories([_AC], "kw", _IM_DRAFT, state)
    assert r2 is None


# ═══════════════════════════════════════════════════════════════════════
# 2b：_r2b_confirm_adoption 置信度分层采纳（4 元组 + adopt_meta）
# ═══════════════════════════════════════════════════════════════════════
def test_05_same_top_confidence_060_adopted():
    """① 同大类 + conf 0.6(≥0.5) + 带图 → 采纳；adopt_meta 无跨大类标记。"""
    ov, vision, why, meta = asm._r2b_confirm_adoption(
        _conf(_HEATER, 0.6), _POOL_SAME_TOP, "暖风机 取暖器", _IM_DRAFT, query=None)
    assert ov == set() and vision is True, f"同大类 0.6 应采纳: {why}"
    assert not meta.get("cross_top_high_confidence")


def test_06_same_top_confidence_030_blocked():
    """同大类但 conf 0.3(<0.5) → 不自动采纳（现行第四段强化版）。"""
    ov, vision, why, _meta = asm._r2b_confirm_adoption(
        _conf(_HEATER, 0.3), _POOL_SAME_TOP, "暖风机 取暖器", _IM_DRAFT, query=None)
    assert ov == set() and vision is False, f"低置信同大类不得放行: {why}"


def test_07_cross_top_confidence_080_adopted_with_meta():
    """② 跨大类 + conf 0.8(≥0.75) → 采纳且 adopt_meta 标注 cross_top_high_confidence。"""
    ov, vision, why, meta = asm._r2b_confirm_adoption(
        _conf(_OTHER_TOP, 0.8), _POOL_CROSS_TOP, "暖风机 取暖器", _IM_DRAFT, query=None)
    assert ov == set() and vision is True, f"跨大类 0.8 应采纳: {why}"
    assert meta.get("cross_top_high_confidence") is True
    assert "cross_top_high_confidence" in why or "0.8" in why


def test_08_cross_top_confidence_060_blocked():
    """③ 跨大类 conf 0.6(<0.75) → 不采纳（置信度不足 → 走入箱）。"""
    ov, vision, why, meta = asm._r2b_confirm_adoption(
        _conf(_OTHER_TOP, 0.6), _POOL_CROSS_TOP, "暖风机 取暖器", _IM_DRAFT, query=None)
    assert ov == set() and vision is False
    assert not meta.get("cross_top_high_confidence")


def test_09_abstain_still_blocks():
    """④ abstain/建议词/无 dc → 不采纳（4 元组形状）。"""
    for bad in (None, {"_llm_suggest": True, "suggest_keywords": "x"},
                {"description_category_id": 0, "type_id": 0, "full_path": "", "node_name": ""}):
        ov, vision, _why, meta = asm._r2b_confirm_adoption(
            bad, _POOL_SAME_TOP, "暖风机 取暖器", _IM_DRAFT, query=None)
        assert ov == set() and vision is False and not meta, f"abstain 必须阻断: {bad}"


def test_10_legacy_no_confidence_same_top_still_adopted():
    """旧格式（无 _llm_confidence）同大类带图 → 照旧现行四段放行（向后兼容）。"""
    ov, vision, why, meta = asm._r2b_confirm_adoption(
        dict(_HEATER), _POOL_SAME_TOP, "暖风机 取暖器", _IM_DRAFT, query=None)
    assert ov == set() and vision is True, f"旧格式语义不得回退: {why}"
    assert not meta.get("cross_top_high_confidence")


def test_11_missing_confidence_conservative_not_adopted():
    """⑩ _llm_confidence=0.0（新格式缺 confidence）同大类带图 → 保守不采纳。"""
    ov, vision, _why, _meta = asm._r2b_confirm_adoption(
        _conf(_HEATER, 0.0), _POOL_SAME_TOP, "暖风机 取暖器", _IM_DRAFT, query=None)
    assert ov == set() and vision is False


def test_12_cross_top_requires_domain_anchor():
    """跨大类高置信也必须有池内源词命中锚点（无域证据不放行）。"""
    ov, vision, _why, _meta = asm._r2b_confirm_adoption(
        _conf(_OTHER_TOP, 0.9), [_AC, _HEATER], "暖风机 取暖器", _IM_DRAFT, query=None)
    assert ov == set() and vision is False, "无源词命中锚点不得跨大类放行"


# ═══════════════════════════════════════════════════════════════════════
# 2c：入箱（幂等 + notice + 非致命 + R1/标题空出口豁免）
# ═══════════════════════════════════════════════════════════════════════
def test_13_create_blocked_draft_builds_payload_and_recommendations():
    """入箱 payload：原始信封 + extensions{category_recommendations top3, blocked_reason}。"""
    captured = {}

    def _fake_create(tenant_id, body):
        captured["tenant"] = tenant_id
        captured["body"] = body
        return {"id": "d-111", "payload": body["envelope"], "source": body.get("source")}

    cands = [_AC, _HEATER, _WATER_FAN, _OTHER_TOP]
    env = {"draft": _IM_DRAFT, "source": {"purchase_url": "https://detail.1688.com/1.html"},
           "extensions": {"foo": 1}}
    with mock.patch("services.draft_service.create_draft", side_effect=_fake_create), \
         mock.patch("utils.blocked_draft_box.find_reusable_blocked_draft", return_value=None):
        info = asm._maybe_create_blocked_draft(_FakeState(envelope=env), _IM_DRAFT, cands,
                                               "低置信阻断")
    assert info and info["draft_id"] == "d-111" and info["reused"] is False
    assert info["top1_name"].find("空调") >= 0 and info["top1_confidence"] == 0.46
    assert captured["tenant"] == "u-1"
    payload = captured["body"]["envelope"]
    assert payload["draft"] is _IM_DRAFT or payload["draft"] == _IM_DRAFT
    recs = payload["extensions"]["category_recommendations"]
    assert len(recs) == 3
    assert recs[0] == {"description_category_id": 17039635, "type_id": 90414,
                       "name": "家用电器 > 空调设备 > 空调", "confidence": 0.46}
    assert payload["extensions"]["blocked_reason"] == "低置信阻断"
    assert payload["extensions"]["blocked_stage"] == "category_match"
    # 原有 extensions 键保留
    assert payload["extensions"]["foo"] == 1


def test_14_idempotent_reuses_existing_pending_draft():
    """⑥ 同 tenant+item 已存在未提交 draft → 复用不重复建（create_draft 零调用）。"""
    with mock.patch("services.draft_service.create_draft") as cd, \
         mock.patch("utils.blocked_draft_box.find_reusable_blocked_draft",
                    return_value="exist-uuid"):
        info = asm._maybe_create_blocked_draft(_FakeState(), _IM_DRAFT, [_AC], "再次阻断")
    assert cd.call_count == 0, "已存在 pending draft 不得重复建"
    assert info and info["draft_id"] == "exist-uuid" and info["reused"] is True


def test_15_idempotency_query_filters_tenant_item_and_unsubmitted():
    """幂等查询：按 tenant + payload item_id 过滤，且排除已有 submission 的 draft。"""
    from utils import blocked_draft_box as bdb

    captured = {}

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            captured["sql"], captured["params"] = str(sql), dict(params or {})

            class _R:
                def fetchone(self_inner):
                    return ("uuid-1",)

            return _R()

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

    with mock.patch.object(bdb, "get_engine", return_value=_FakeEngine()):
        got = bdb.find_reusable_blocked_draft("u-9", "item-77")
    assert got == "uuid-1"
    assert captured["params"]["t"] == "u-9" and captured["params"]["iid"] == "item-77"
    low = captured["sql"].lower()
    assert "product_drafts" in low and "draft_submissions" in low
    assert "tenant_id" in low and "item_id" in low


def test_16_blocked_exit_notice_contains_draft_id():
    """⑦ _blocked_exit：入箱成功 → notice 带「已入采集箱 draft_id=…，推荐类目 Top1=…(置信度 …)」，
    终态失败字段（failed_stage/match_confidence/assembly_retry_count）不丢。"""
    state = _FakeState(retry=2)
    with mock.patch("utils.blocked_draft_box.create_blocked_draft",
                    return_value={"draft_id": "d-9", "reused": False,
                                  "top1_name": "家用电器 > 空调设备 > 空调",
                                  "top1_confidence": 0.46}):
        out = asm._blocked_exit(state, _IM_DRAFT, [_AC],
                                "类目匹配失败：测试阻断", match_confidence=0.0)
    assert out["failed_stage"] == "category_match"
    assert out["match_confidence"] == 0.0
    assert out["assembly_retry_count"] == 3
    assert "已入采集箱 draft_id=d-9，推荐类目 Top1=家用电器 > 空调设备 > 空调(置信度 0.46)" \
        in out["notice"], f"notice 格式不符: {out.get('notice')}"


def test_17_draft_creation_failure_non_fatal():
    """⑧ 入箱抛异常 → _blocked_exit 仍返回 failed 形状（无 notice），任务正常落库。"""
    state = _FakeState()
    with mock.patch("utils.blocked_draft_box.create_blocked_draft",
                    side_effect=RuntimeError("pg down")):
        out = asm._blocked_exit(state, _IM_DRAFT, [_AC],
                                "类目匹配失败：测试阻断", match_confidence=0.0)
    assert out["failed_stage"] == "category_match" and out["error_message"].startswith("类目匹配失败")
    assert "notice" not in out
    with mock.patch("utils.blocked_draft_box.create_blocked_draft",
                    side_effect=RuntimeError("pg down")):
        assert asm._maybe_create_blocked_draft(state, _IM_DRAFT, [_AC], "x") is None


def test_18_no_user_id_skips_box():
    """state.user_id 为空（无租户归属）→ 不入箱也不崩。"""
    with mock.patch("utils.blocked_draft_box.create_blocked_draft") as cb:
        info = asm._maybe_create_blocked_draft(_FakeState(user_id=""), _IM_DRAFT, [_AC], "x")
    assert info is None and cb.call_count == 0


def test_19_r1_veto_and_title_empty_exits_do_not_box():
    """⑤ R1 veto 出口与标题为空出口不入箱（源码级：出口片段内无 _maybe_create_blocked_draft）。"""
    import inspect
    src = inspect.getsource(asm)
    # R1 veto 出口片段：从 R1 判定行到其 return 结束（下一个 "R2b/P1-3" 注释前）
    i_r1 = src.index("if _r1_veto(category_result")
    i_r1_end = src.index("# ✅ v0.65.1 R2b/P1-3", i_r1)
    r1_block = src[i_r1:i_r1_end]
    assert "failed_stage" in r1_block and "category_match" in r1_block, "R1 出口形状被改"
    assert "_maybe_create_blocked_draft" not in r1_block and "_blocked_exit" not in r1_block, \
        "R1 veto 出口不得入箱（需资质类不自动推荐）"
    # 标题为空出口（无 title 无法建 draft，create_draft 必 400）
    i_title = src.index("产品标题为空，无法进行类目匹配")
    title_block = src[i_title:i_title + 400]
    assert "_blocked_exit" not in title_block and "_maybe_create_blocked_draft" not in title_block
    # 其余出口已接线入箱（共 7 处；v0.69 Wave4 T2.4 新增受限品类双命中出口，
    # 经 _restricted_category_exit 统一走 _blocked_exit）
    assert src.count("asm._blocked_exit(") == 0  # 模块内不带前缀调用
    assert src.count("_blocked_exit(") - src.count("def _blocked_exit(") == 7, \
        "阻断出口入箱接线数应为 7"


def test_20_follow_layer_never_creates_draft():
    """确认入箱只在 assemble 层做一次：follow 门控/节点源码不含入箱调用。"""
    import inspect
    from graphs.nodes import follow_sell_import_node as fsin
    src = inspect.getsource(fsin)
    assert "create_blocked_draft" not in src and "_maybe_create_blocked_draft" not in src
    assert "create_draft" not in src, "follow 层不得直接建 draft"


def test_21_adopted_category_result_carries_cross_top_meta_flag():
    """② 采纳分支：cross_top_high_confidence 旗标进入 category_match_meta（定稿透传）。"""
    import inspect
    src = inspect.getsource(asm)
    assert "cross_top_high_confidence" in src, "跨大类旗标未实现"
    # meta 构造处必须条件携带旗标（learning/审计可溯源）
    i_meta = src.index('"category_match_meta"')
    meta_block = src[max(0, i_meta - 600):i_meta + 500]
    assert "cross_top_high_confidence" in meta_block or "_r2b_cross_top" in src


def test_22_thresholds_exported():
    """阈值常量在模块层可单测（0.5 / 0.75）。"""
    from utils.blocked_draft_box import R2B_ADOPT_CONF_SAME_TOP, R2B_ADOPT_CONF_CROSS_TOP
    assert R2B_ADOPT_CONF_SAME_TOP == 0.5
    assert R2B_ADOPT_CONF_CROSS_TOP == 0.75


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception:
            traceback.print_exc()
            print(f"  FAIL {fn.__name__}: 异常")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
