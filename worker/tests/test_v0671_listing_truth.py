"""留存表真值透传回归（wave ②号缺陷 TDD）：graph 管线 dc/tp/weight 不得回落信封猜测。

wave 实证：approved 行记着信封里 skill 猜的 200001462（成人糖果），实际管线走的是
41777465/93167（遮阳帽）——GraphOutput 缺真值通道，task_processor 层拿不到 GlobalState。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_PAYLOAD = {"envelope": {"draft": {"purchase_url": "https://detail.1688.com/offer/1.html",
                                   "title": "帽子", "weight": 1,
                                   "dimensions": {"length": 1, "width": 2, "height": 3},
                                   "ozon_category": {"description_category_id": "200001462",
                                                      "type_id": "971363842", "source": "search_kw"}},
                         "source": {}, "extensions": {}}}


def _capture_insert(monkeypatch, captured):
    """writer 在 INSERT 前还有类目树查询——只认含 task_db_id 的 INSERT 语句参数。"""
    from unittest.mock import MagicMock

    session = MagicMock()
    def _exec(stmt, *a, **kw):
        params = stmt.compile().params
        if "task_db_id" in params:
            captured["p"] = params
        return MagicMock(scalar=lambda *a: 1, fetchone=lambda *a: None)
    session.execute.side_effect = _exec
    session.__enter__ = lambda s: session
    session.__exit__ = lambda s, *a: False
    monkeypatch.setattr("storage.database.db.get_session", lambda: session)


def test_writer_prefers_graph_result_over_envelope(monkeypatch):
    import utils.listing_result_log as lrl

    captured = {}
    _capture_insert(monkeypatch, captured)
    lrl.write_listing_result_log(
        payload=_PAYLOAD,
        graph_result={"upload_status": "completed", "moderation_status": "approved",
                      "description_category_id": "41777465", "type_id": "93167",
                      "category_match_meta": {"match_layer": "L0", "confidence": 0.7},
                      "final_weight_g": 80, "final_dims_mm": {"length": 250, "width": 120, "height": 100},
                      "pricing_info": {}, "errors": []},
        task_db_id="22222222-2222-2222-2222-222222222222", tenant_id="u1",
    )
    p = captured["p"]
    assert p["description_category_id"] == 41777465 and p["type_id"] == 93167
    assert p["match_layer"] == "L0" and p["match_confidence"] == 0.7
    assert p["weight_g"] == 80 and p["dims_mm"] == {"length": 250, "width": 120, "height": 100}


def test_writer_falls_back_to_envelope_when_graph_empty(monkeypatch):
    """graph 早退（auth/类目阻断）dc/tp 为空 → 信封回落（follow/discover 语义保持）。"""
    import utils.listing_result_log as lrl

    captured = {}
    _capture_insert(monkeypatch, captured)
    lrl.write_listing_result_log(
        payload=_PAYLOAD,
        graph_result={"upload_status": "failed", "pricing_info": {}, "errors": []},
        task_db_id="33333333-3333-3333-3333-333333333333", tenant_id="u1",
    )
    assert captured["p"]["description_category_id"] == 200001462  # 信封回落，现状语义
    assert captured["p"]["weight_g"] == 1                          # 无 final_* 时回落 draft
    assert captured["p"]["match_layer"] is None                    # 无 meta 不编造


def test_graphoutput_has_truth_fields():
    """GraphOutput 契约含真值字段（output_schema 按名过滤，缺了就透传不出去）。"""
    from graphs.state import GraphOutput
    for f in ("description_category_id", "type_id", "category_match_meta",
              "final_weight_g", "final_dims_mm"):
        assert f in GraphOutput.model_fields, f"GraphOutput 缺 {f}"


def test_prepare_output_has_final_weight_fields():
    from graphs.state import PrepareOzonUploadOutput
    for f in ("final_weight_g", "final_dims_mm"):
        assert f in PrepareOzonUploadOutput.model_fields, f"PrepareOzonUploadOutput 缺 {f}"
