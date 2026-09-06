"""审核拒绝原文留存回归（wave ①号缺陷 TDD）。

根因：parse_error_node 消费即删 + revalidate 清 error_message + _build_notice 传错参。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_DECLINE = {"code": "DESCRIPTION_DECLINE", "level": "ERROR_LEVEL_ERROR",
            "texts": {"message": "Категория не соответствует товару"}}


def test_accumulate_dedup_and_cap():
    from graphs.validation_retry_loop import _accumulate_decline_errors
    state = SimpleNamespace(decline_errors=[])
    _accumulate_decline_errors(state, [_DECLINE, _DECLINE])   # 同轮去重
    assert len(state.decline_errors) == 1
    _accumulate_decline_errors(state, [{"code": "MISSING_REQUIRED_ATTRIBUTE",
                                        "texts": {"message": "必填属性缺失: 类型"}}])
    assert len(state.decline_errors) == 2                      # 跨轮累积
    for i in range(60):                                        # cap 50
        _accumulate_decline_errors(state, [{"code": "X", "texts": {"message": f"m{i}"}}])
    assert len(state.decline_errors) == 50


def test_build_notice_error_code_hits_map():
    """传 error_code（而非 error_type）时 ERROR_NOTICE_MAP 必须生效（此前是死代码）。"""
    from graphs.validation_retry_loop import ERROR_NOTICE_MAP, _build_notice
    assert ERROR_NOTICE_MAP, "映射表不应为空"
    code = next(iter(ERROR_NOTICE_MAP))
    assert _build_notice(code, "", "failed") == ERROR_NOTICE_MAP[code]


def test_build_notice_fallback_uses_decline_texts():
    """error_message 被清空后，兜底文案必须携带 decline_errors 里的俄语原文。"""
    from graphs.validation_retry_loop import _build_notice
    got = _build_notice("", "", "failed",
                        decline_errors=[_DECLINE])
    assert "Категория не соответствует товару" in got


def test_build_notice_backward_compat():
    """旧签名行为不变：error_type 不在映射 → 有 msg 用 msg，无 msg 用泛化兜底。"""
    from graphs.validation_retry_loop import _build_notice
    assert _build_notice("fixable", "somе msg", "failed") == "Ozon 审核拒绝(fixable): somе msg"
    assert _build_notice("fixable", "", "failed") == "Ozon 审核拒绝(fixable),重试后仍未通过"


def test_writer_records_moderation_texts(monkeypatch):
    """graph_result.decline_errors 必须落 moderation_texts 列（假 session 捕获 insert）。"""
    import utils.listing_result_log as lrl

    captured = {}

    class _FakeResult:
        def scalar(self): return 1
        def fetchone(self): return None

    class _FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def close(self): pass
        def commit(self): pass
        def execute(self, stmt):
            captured["params"] = stmt.compile().params
            return _FakeResult()

    monkeypatch.setattr("storage.database.db.get_session", lambda: _FakeSession())
    lrl.write_listing_result_log(
        payload={"envelope": {"draft": {"purchase_url": "https://detail.1688.com/offer/1.html",
                                         "title": "t", "weight": 80, "dimensions": {"length": 1, "width": 2, "height": 3}},
                              "source": {}, "extensions": {}}},
        graph_result={"upload_status": "failed", "errors": [{"code": "KEEP_OLD_SEMANTICS"}],
                      "decline_errors": [_DECLINE], "pricing_info": {}},
        task_db_id="11111111-1111-1111-1111-111111111111",
        tenant_id="u1",
    )
    assert captured["params"]["moderation_texts"] == [_DECLINE]
