#!/usr/bin/env python3
"""fix/semantic-gate-coverage v082 — worker assemble 图搜语义分歧硬闸（最后一道网）。

审计锚点：v0.81 follow 语义闸只覆盖 follow 一条腿，discover/batch_test 复用链上
错货信封（Ozon 竞品卡=A、1688 匹配到语义不符的 B）直进 worker 类目链。skill 侧
v082 已把分歧写进 extensions.match_evidence.divergent；本文件锁定 assemble 消费：

  ① divergent=True 且采纳来源非权威（对齐 _is_skill_authoritative 白名单；
     widget 命名空间未 path 精配视为非权威——面包屑 hint 不是真实卡类目采纳）
     → _blocked_exit 入采集箱（v0.69 先例：tenant+item_id 幂等），
     failed_stage=category_match，在类目查询前止损；
  ② divergent=True 但采纳来源权威（what_to_sell/manual/mapping，真实 Ozon 卡
     背书）→ 放行（节点继续走后续既有闸链）；
  ③ semantic_unknown（语义闸前提缺失未复核）→ 不拦（只在信封留证，防 CDP
     降级时生产停摆）。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_semantic_gate_divergent_v082.py -q
纯 mock，不连 PG/不触网/不跑 CDP。
"""
from __future__ import annotations

import os
import sys
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes import assemble_ozon_product_node as asm  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# ① 纯函数判定 _divergent_match_block_reason
# ═══════════════════════════════════════════════════════════════════════

def _mev(**kw) -> dict:
    m = {"method": "aibuy", "trusted": True, "confidence": 0.5}
    m.update(kw)
    return m


class TestDivergentBlockReason:
    def test_no_match_evidence_passes(self):
        assert asm._divergent_match_block_reason({}, {}) == ""
        assert asm._divergent_match_block_reason(None, None) == ""

    def test_divergent_missing_passes(self):
        assert asm._divergent_match_block_reason(
            {"match_evidence": _mev()}, {}) == ""

    def test_divergent_non_dict_envelope_passes(self):
        assert asm._divergent_match_block_reason(
            {"match_evidence": "divergent"}, {"source": "search_kw"}) == ""

    def test_divergent_without_category_source_blocks(self):
        """无 draft.ozon_category → 无权威背书 → 拦。"""
        reason = asm._divergent_match_block_reason(
            {"match_evidence": _mev(divergent=True)}, {})
        assert "语义分歧" in reason and "人工确认" in reason

    def test_divergent_search_kw_blocks(self):
        reason = asm._divergent_match_block_reason(
            {"match_evidence": _mev(divergent=True)},
            {"source": "search_kw", "namespace": "seller"})
        assert "语义分歧" in reason

    def test_divergent_page_widget_hint_blocks(self):
        """page+widget（面包屑 hint，未 path 精配）→ 非权威 → 拦。

        设计要点：discover 错货场景恰是「竞品面包屑=A、1688=B」——面包屑是
        分歧判定的语料本身，不是卡片类目采纳，不能因 source=page 豁免。
        """
        reason = asm._divergent_match_block_reason(
            {"match_evidence": _mev(divergent=True)},
            {"source": "page", "namespace": "widget"})
        assert "语义分歧" in reason

    def test_divergent_what_to_sell_no_block(self):
        """v083: 权威来源不再硬拦（降级阶梯在 _divergent_match_verdict 层表达）。"""
        assert asm._divergent_match_block_reason(
            {"match_evidence": _mev(divergent=True)},
            {"source": "what_to_sell", "namespace": "seller"}) == ""

    def test_divergent_manual_no_block(self):
        assert asm._divergent_match_block_reason(
            {"match_evidence": _mev(divergent=True)},
            {"source": "manual", "namespace": "seller",
             "description_category_id": "17027907", "type_id": "92359"}) == ""

    def test_divergent_mapping_no_block(self):
        assert asm._divergent_match_block_reason(
            {"match_evidence": _mev(divergent=True)},
            {"source": "mapping", "namespace": "seller"}) == ""

    def test_semantic_unknown_only_passes(self):
        """semantic_unknown 不拦（只在信封留证，CDP 降级不停摆）。"""
        assert asm._divergent_match_block_reason(
            {"match_evidence": _mev(semantic_unknown=True)},
            {"source": "search_kw"}) == ""


# ═══════════════════════════════════════════════════════════════════════
# ①b v083 降级阶梯 _divergent_match_verdict → (reason, downgrade)
# ═══════════════════════════════════════════════════════════════════════

class TestDivergentLadder:
    @staticmethod
    def _cat(source: str, **kw) -> dict:
        d = {"source": source, "namespace": "seller"}
        d.update(kw)
        return d

    def test_authoritative_both_corpora_downgrades(self):
        """权威 + 1688 类目名 + 竞品面包屑齐备 → 降级（走全闸链，不拦不直通）。"""
        reason, downgrade = asm._divergent_match_verdict(
            {"match_evidence": _mev(divergent=True)},
            self._cat("what_to_sell", category_path="Дом > Перчатки"),
            {"match_category_name": "家务手套"})
        assert reason == "" and downgrade is True

    def test_authoritative_source_path_corpus_downgrades(self):
        reason, downgrade = asm._divergent_match_verdict(
            {"match_evidence": _mev(divergent=True)},
            self._cat("mapping", category_path="Дом > Перчатки"),
            {"source_category_path": "家居 > 家务手套"})
        assert reason == "" and downgrade is True

    def test_authoritative_1688_corpus_missing_no_downgrade(self):
        """权威 + 1688 类目名缺 → 留证不拦（不降级）。"""
        reason, downgrade = asm._divergent_match_verdict(
            {"match_evidence": _mev(divergent=True)},
            self._cat("what_to_sell", category_path="Дом > Перчатки"),
            {})
        assert reason == "" and downgrade is False

    def test_authoritative_breadcrumb_missing_no_downgrade(self):
        """权威 + 竞品面包屑缺 → 留证不拦（不降级）。"""
        reason, downgrade = asm._divergent_match_verdict(
            {"match_evidence": _mev(divergent=True)},
            self._cat("what_to_sell"),
            {"match_category_name": "家务手套"})
        assert reason == "" and downgrade is False

    def test_manual_with_digits_exempt(self):
        """manual + dc/tp 数字 → 恒豁免（R1 veto 仍由下游硬）。"""
        reason, downgrade = asm._divergent_match_verdict(
            {"match_evidence": _mev(divergent=True)},
            self._cat("manual", description_category_id="17027907",
                      type_id="92359", category_path="任何路径"),
            {"match_category_name": "手套"})
        assert reason == "" and downgrade is False

    def test_manual_without_digits_blocks(self):
        """裸 manual（无 dc/tp 数字）不豁免——防绕树校验。"""
        reason, downgrade = asm._divergent_match_verdict(
            {"match_evidence": _mev(divergent=True)},
            self._cat("manual"),
            {"match_category_name": "手套"})
        assert "语义分歧" in reason and downgrade is False

    def test_non_authoritative_blocks(self):
        reason, downgrade = asm._divergent_match_verdict(
            {"match_evidence": _mev(divergent=True)},
            self._cat("search_kw", category_path="Дом > Перчатки"),
            {"match_category_name": "手套"})
        assert "语义分歧" in reason and downgrade is False

    def test_no_divergent_no_downgrade(self):
        reason, downgrade = asm._divergent_match_verdict(
            {"match_evidence": _mev()},
            self._cat("what_to_sell", category_path="Дом"),
            {"match_category_name": "手套"})
        assert reason == "" and downgrade is False


# ═══════════════════════════════════════════════════════════════════════
# ② assemble 节点接线
# ═══════════════════════════════════════════════════════════════════════

class _FakeQuery:
    """类目树桩——闸命中时不得被调用（止损断言用）。"""

    def __init__(self):
        self.search_calls = 0

    def search_nodes(self, *_a, **_k):
        self.search_calls += 1
        return []

    def get_node(self, *_a, **_k):
        return None


class _FakeState:
    """模拟 GlobalState — assemble 顶部 + 入箱路径读取的字段。"""

    def __init__(self, extensions=None, ozon_category=None, follow=False):
        self.draft = {"item_id": "gz-1", "sku_id": "gz-1",
                      "title": "Домашние резиновые перчатки",
                      "images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"],
                      "weight": 500,
                      "dimensions": {"length": 200, "width": 120, "height": 80},
                      "purchase_cost": 10.0,
                      "purchase_url": "https://detail.1688.com/1.html"}
        if ozon_category is not None:
            self.draft["ozon_category"] = ozon_category
        self.token = ""
        self.ozon_client_id = "c"
        self.ozon_api_key = "k"
        self.currency_code = "RUB"
        self.pricing_info = {"price": "990", "old_price": "1290"}
        self.envelope = {"draft": self.draft, "source": {},
                         "extensions": extensions if extensions is not None else {}}
        self.source = {}
        self.user_id = "u-1"
        self.task_id = ""  # 空 → _log_match_attempt 直接跳过（不触 PG）
        self.assembly_retry_count = 0
        self.description_category_id = ""
        self.type_id = ""


def test_divergent_non_authoritative_blocks_to_box():
    """divergent + 非权威 → 入采集箱 + failed 终态，且在类目查询前止损。"""
    ext = {"match_evidence": _mev(divergent=True)}
    state = _FakeState(
        extensions=ext,
        ozon_category={"source": "search_kw", "namespace": "seller"})
    query = _FakeQuery()
    with mock.patch.object(asm, "get_category_query", return_value=query), \
         mock.patch("utils.blocked_draft_box.create_blocked_draft") as cb:
        out = asm.assemble_ozon_product_node(state, {}, None)
    msg = str(out.get("error_message"))
    assert "语义分歧" in msg, f"阻断原因需含「语义分歧」: {out}"
    assert "已入采集箱待人工确认" in msg
    assert out.get("failed_stage") == "category_match"
    assert out.get("error_code") == "LOCAL_CATEGORY_MATCH_FAILED"
    assert out.get("match_confidence") == 0.0
    assert cb.called, "divergent 非权威必须入采集箱"
    reason = str(cb.call_args[0][3])
    assert "语义分歧" in reason, f"blocked_reason: {reason}"
    # 幂等入箱读取 tenant
    assert str(cb.call_args[0][0]) == "u-1"
    assert query.search_calls == 0, "止损：闸命中不得再查类目树"


def test_divergent_authoritative_passes_gate():
    """divergent + 权威（what_to_sell）→ 闸放行，节点继续走既有 follow 闸链
    （此处以 follow 闸「类目猜测未通过」出口为放行证据——错误形态与分歧闸不同；
    draft_ozon_cat 带 dc/tp 才会在 follow 分支内出口，否则落穿主路径）。
    FakeQuery.get_node 返回 None → (dc,tp) 配对校验不过 → _f_reject 出口。"""
    ext = {"follow_sell": True, "match_evidence": _mev(divergent=True)}
    state = _FakeState(
        extensions=ext,
        ozon_category={"source": "what_to_sell", "namespace": "seller",
                       "description_category_id": "17027907", "type_id": "92359"})
    query = _FakeQuery()
    with mock.patch.object(asm, "get_category_query", return_value=query), \
         mock.patch("utils.blocked_draft_box.create_blocked_draft") as cb:
        out = asm.assemble_ozon_product_node(state, {}, None)
    msg = str(out.get("error_message"))
    assert "语义分歧" not in msg, f"权威来源不得被分歧闸拦截: {out}"
    assert "跟卖类目未通过交叉校验" in msg, f"应走到 follow 既有闸: {out}"
    if cb.called:
        assert "语义分歧" not in str(cb.call_args[0][3])


def test_semantic_unknown_does_not_block():
    """semantic_unknown（未复核）不拦——节点继续（follow 既有闸出口为证）。"""
    ext = {"follow_sell": True, "match_evidence": _mev(semantic_unknown=True)}
    state = _FakeState(
        extensions=ext,
        ozon_category={"source": "search_kw", "namespace": "seller",
                       "description_category_id": "17027907", "type_id": "92359"})
    with mock.patch.object(asm, "get_category_query",
                           return_value=_FakeQuery()), \
         mock.patch("utils.blocked_draft_box.create_blocked_draft"):
        out = asm.assemble_ozon_product_node(state, {}, None)
    msg = str(out.get("error_message"))
    assert "语义分歧" not in msg
    assert "跟卖类目未通过交叉校验" in msg


if __name__ == "__main__":
    import traceback

    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
            except Exception:
                failed += 1
                traceback.print_exc()
    for _cls in (TestDivergentBlockReason, TestDivergentLadder):
        for _n, _f in sorted(vars(_cls).items()):
            if _n.startswith("test_") and callable(_f):
                total += 1
                try:
                    _f(None)
                except Exception:
                    failed += 1
                    traceback.print_exc()
    print(f"{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
