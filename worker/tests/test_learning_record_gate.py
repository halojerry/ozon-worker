"""learning_record 成功判据单测（v0.21 P0-1）。

旧逻辑：upload_status=="success" / imported / active / processed 都算成功 → 写学习记录，
导致 declined/假成功商品把错误类目与属性映射固化。新逻辑：只有 moderate_status=="approved" 才写。
"""
import sys
import os
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _make_state(moderation_status="", status="", upload_status="", ozon_upload_success=False):
    return SimpleNamespace(
        description_category_id="17028959",
        type_id=96513,
        moderation_status=moderation_status,
        status=status,
        upload_status=upload_status,
        ozon_upload_success=ozon_upload_success,
        final_attributes=[
            {"attribute_id": 85, "value": "Нет бренда", "dictionary_value_id": 126745801, "source": "hardcoded"}
        ],
        attributes_schema=[],
        draft={"title": "测试", "source_category": "成人用品 > 女用器具 > 震动棒"},
        envelope={"extensions": {}},
    )


def _run_node(state):
    from graphs.nodes.learning_record_node import learning_record_node

    runtime = SimpleNamespace(context=SimpleNamespace())
    with patch("graphs.nodes.learning_record_node.LocalDBManager") as mock_db:
        mock_db.return_value = mock_db
        learning_record_node(state, SimpleNamespace(), runtime)
    return mock_db


def test_declined_with_upload_success_not_recorded():
    """declined + upload_status=success（旧假成功路径）→ 不得写学习记录。"""
    state = _make_state(moderation_status="declined", status="variant_wait", upload_status="success")
    mock_db = _run_node(state)
    mock_db.add_category_mapping.assert_not_called()
    mock_db.add_attribute_mapping.assert_not_called()


def test_imported_status_not_recorded():
    """status=imported（导入成功但未审核）→ 不得写学习记录。"""
    state = _make_state(status="imported", upload_status="success")
    mock_db = _run_node(state)
    mock_db.add_category_mapping.assert_not_called()


def test_ozon_upload_success_flag_no_longer_trusted():
    """state.ozon_upload_success=True（上游假成功标记）不再放行。"""
    state = _make_state(status="pending", upload_status="", ozon_upload_success=True)
    mock_db = _run_node(state)
    mock_db.add_category_mapping.assert_not_called()


def test_approved_recorded():
    """moderate_status=approved → 写 category_mapping（source=learned_approved）。"""
    state = _make_state(moderation_status="approved", status="approved", upload_status="success")
    mock_db = _run_node(state)
    mock_db.add_category_mapping.assert_called_once()
    assert mock_db.add_category_mapping.call_args.kwargs["source"] == "learned_approved"


def test_should_reupload_pending_exits():
    """retry 循环对 pending/pending_moderation 应退出而非视为成功。"""
    from graphs.validation_retry_loop import should_reupload

    st = SimpleNamespace(upload_status="pending_moderation", retry_count=0, max_retries=3,
                         product_id="123", errors=[])
    assert should_reupload(st) == "exit"
    st2 = SimpleNamespace(upload_status="pending", retry_count=0, max_retries=3,
                          product_id="123", errors=[])
    assert should_reupload(st2) == "exit"
