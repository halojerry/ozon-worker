"""v0.70 P1/P2 — manual 用户指定类目权威补洞（TDD）。

两缺口（探索取证）：
- P1 树校验静默回落：draft.ozon_category source=manual 的 dc/tp 不在
  category_tree_nodes 时，`_resolve_skill_category` 返回 None → 整条 pg_trgm/LLM
  链自动匹配，用户指定被无感知丢弃。修复：manual 校验失败 → `_blocked_exit`
  显式阻断（failed_stage=category_match，文案含 dc/tp），非 manual source 不变。
- P2 弱档 L0 仲裁边缘：manual 与弱档 L0（learned succ==1）dc/tp 相同时，
  `_skill_precedence_over_l0` 返回 False 保留 L0 → 仲裁失败丢成 L1 文本匹配。
  修复：manual 恒接管（match_layer=Skill 0.95），page/what_to_sell/mapping 不变。

红线：品牌强制 Нет бренда / R1 veto / R4 拒审换类目 / Step 6.5 豁免语义零改动。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_manual_category_authority_v070.py -q
全部纯 mock，不连 PG/不触网/不调 LLM。
"""
import os
import sys
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes import assemble_ozon_product_node as asm  # noqa: E402


_L0_HIT = {"description_category_id": 1, "type_id": 2}
_SKILL_HIT = {"description_category_id": 1, "type_id": 2,
              "full_path": "Дом и сад > Хранение", "similarity": 1.0}
_OTHER_HIT = {"description_category_id": 9, "type_id": 8}
# search_nodes 返回的普通候选（回落自动匹配场景用，键形状对齐 pg_trgm 输出）
_PLAIN_CAND = {"description_category_id": 99990002, "type_id": 88880002,
               "node_name": "收纳盒", "full_path": "Дом и сад > Хранение > 收纳盒",
               "similarity": 0.9, "matcher": "jieba"}


# ═══════════════ P2: `_skill_precedence_over_l0` manual 恒接管 ═══════════════

def test_01_manual_same_dc_tp_takes_over():
    """manual 与弱档 L0 dc/tp 相同 → 恒接管 True（此前 False 是缺口）。"""
    assert asm._skill_precedence_over_l0(
        dict(_L0_HIT), dict(_SKILL_HIT), "manual", "") is True


def test_02_manual_conflict_and_no_l0_takes_over():
    """manual 冲突/无 L0 → True（与原行为一致，回归锁定）。"""
    assert asm._skill_precedence_over_l0(
        dict(_OTHER_HIT), dict(_SKILL_HIT), "manual", "") is True
    assert asm._skill_precedence_over_l0(
        None, dict(_SKILL_HIT), "manual", "") is True


def test_03_page_same_dc_tp_keeps_l0_unchanged():
    """page（及一切非 manual 权威）与 L0 一致 → 仍返回 False（v0.66 语义不变）。"""
    for src in ("page", "what_to_sell", "mapping"):
        assert asm._skill_precedence_over_l0(
            dict(_L0_HIT), dict(_SKILL_HIT), src, "") is False, src


# ═══════════════ P1: manual 树校验失败 → 显式阻断 ═══════════════

class _FakeQuery:
    def __init__(self):
        self.schema_calls = 0

    def search_nodes(self, *_a, **_k):
        return [dict(_PLAIN_CAND)]

    def score_candidates_by_fingerprint(self, candidates, _kw):
        return candidates

    def get_attribute_schema(self, *_a, **_k):
        self.schema_calls += 1
        return None

    def get_dictionary_values(self, *_a, **_k):
        return []


class _FakeState:
    def __init__(self, ozon_category):
        self.draft = {"item_id": "gz-1", "sku_id": "gz-1", "title": "普通收纳盒",
                      "images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"],
                      "weight": 500, "dimensions": {"length": 200, "width": 120, "height": 80},
                      "purchase_cost": 10.0, "purchase_url": "https://detail.1688.com/1.html",
                      "ozon_category": ozon_category}
        self.token = ""
        self.ozon_client_id = "c"
        self.ozon_api_key = "k"
        self.currency_code = "RUB"
        self.pricing_info = {"price": "990", "old_price": "1290"}
        self.envelope = {"draft": self.draft, "source": {}, "extensions": {}}
        self.source = {}
        self.user_id = "u-1"
        self.task_id = ""
        self.assembly_retry_count = 0


def _run_node(ozon_category, resolve_return):
    state = _FakeState(ozon_category)
    query = _FakeQuery()
    with mock.patch.object(asm, "get_category_query", return_value=query), \
         mock.patch.object(asm, "_resolve_skill_category",
                           return_value=resolve_return) as rs, \
         mock.patch.object(asm, "_fetch_attribute_schema_from_ozon", return_value=[]) as fa, \
         mock.patch.object(asm, "_llm_rank_categories", return_value=None), \
         mock.patch("utils.blocked_draft_box.create_blocked_draft") as cb:
        out = asm.assemble_ozon_product_node(state, {}, None)
    return out, {"resolve": rs, "fetch_schema": fa, "box": cb, "query": query}


def test_04_manual_not_in_tree_blocks_explicitly():
    """manual dc/tp 树校验失败 → failed 终态 + 文案含 dc/tp 与「不在 Ozon 类目树」，
    且在属性 schema 前止损；不再静默退回自动匹配。"""
    bad_cat = {"description_category_id": 17027923, "type_id": 971016012,
               "source": "manual"}
    out, probe = _run_node(bad_cat, None)
    probe["resolve"].assert_called_once()
    assert out.get("failed_stage") == "category_match", f"failed 终态: {out}"
    msg = str(out.get("error_message"))
    assert "不在 Ozon 类目树" in msg, f"文案需含树校验失败: {msg}"
    assert "17027923" in msg and "971016012" in msg, f"文案需含用户 dc/tp: {msg}"
    assert out.get("match_confidence") == 0.0
    assert probe["query"].schema_calls == 0, "止损：阻断后不得取属性 schema"
    probe["fetch_schema"].assert_not_called()
    probe["box"].assert_called(), "manual 阻断走统一出口（尽力入采集箱）"


def test_05_search_kw_not_in_tree_still_falls_back():
    """对照：search_kw 校验失败维持 v0.63 语义——退回自动匹配，不阻断。"""
    kw_cat = {"description_category_id": 17027923, "type_id": 971016012,
              "source": "search_kw"}
    out, probe = _run_node(kw_cat, None)
    assert "不在 Ozon 类目树" not in str(out.get("error_message")), out
    assert out.get("failed_stage") != "category_match", f"search_kw 不得阻断: {out}"
    assert probe["query"].schema_calls == 1, "回落自动匹配应继续主流程"


def test_06_manual_valid_category_marks_skill_layer():
    """manual 校验通过 → match_layer=Skill / confidence=0.95 / dc=tp=用户指定值
    （Step 6.5/R2b 豁免的前提；用户配置即上传的入口断言）。schema mock 返回
    非空列表——空列表会触发「候选类目回退」提前 return（无 meta，非本测试路径）。"""
    man_cat = {"description_category_id": 1, "type_id": 2, "source": "manual"}
    resolved = {"description_category_id": 1, "type_id": 2,
                "full_path": "Дом и сад > Хранение", "node_name": "Хранение",
                "similarity": 1.0, "confidence": 0.95, "source": "manual",
                "_resolved_by_path": False}
    state = _FakeState(man_cat)
    query = _FakeQuery()
    with mock.patch.object(asm, "get_category_query", return_value=query), \
         mock.patch.object(asm, "_resolve_skill_category", return_value=resolved), \
         mock.patch.object(asm, "_fetch_attribute_schema_from_ozon",
                           return_value=[{"description_attribute_id": 4180,
                                          "name": "Тип", "type": "String",
                                          "required": False}]) as fa, \
         mock.patch.object(asm, "_llm_rank_categories", return_value=None), \
         mock.patch("utils.blocked_draft_box.create_blocked_draft"):
        out = asm.assemble_ozon_product_node(state, {}, None)
    fa.assert_called_once(), "manual 类目 schema 首选类目即应命中（无回退）"
    meta = out.get("category_match_meta") or {}
    assert meta.get("match_layer") == "Skill", f"manual 权威须记 Skill 档: {meta}"
    # confidence 最终挂钩真实 sim（Skill 档 sim=1.0 → 1.0；权威语义只要求高档位）
    assert (meta.get("confidence") or 0) >= 0.9, f"manual 权威须高置信: {meta}"
    assert meta.get("description_category_id") == "1" and meta.get("type_id") == "2"
