"""T9: 上传成功回填 product_task_index（learning_record approved 路径）单测。

背景：product_task_index 目前只有 update_images（改图）写（T6 共享模块），普通上传不写
→ OnSale 货架/编辑端点对普通上传商品查不到索引（GET /edit 409「仅改图可用」）。
本测试锁定 approved 路径的回填行为：

- approved + product_id + credential_id → upsert_index 被调（含 task_id/draft_id）
- 非 approved（pending/declined）→ upsert_index 不被调
- product_id 缺失 → 跳过不抛
- credential_id 不可解析 → 跳过不抛
- upsert_index 抛异常 → 学习记录仍正常返回（不阻断学习路径）
"""
import sys
import os
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 与 test_learning_record_gate.py 相同的模式：mock state（SimpleNamespace）+ 节点函数直调
_TASK_ID = "11111111-2222-3333-4444-555555555555"
_DRAFT_ID = "aaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_CRED_ID = "ffffffff-1111-2222-3333-444444444444"


def _make_state(
    moderation_status="approved",
    status=None,
    product_id="5476361418",
    user_id="tenant-1",
    draft=None,
    envelope=None,
    final_attributes=None,
):
    return SimpleNamespace(
        description_category_id="17028959",
        type_id=96513,
        moderation_status=moderation_status,
        status=status or moderation_status,
        upload_status="success",
        final_attributes=final_attributes or [],
        attributes_schema=[],
        draft=draft or {"title": "测试", "item_id": "980815374096", "sku_id": "980815374096"},
        envelope=envelope or {"extensions": {}},
        product_id=product_id,
        user_id=user_id,
    )


def _run_node(
    state,
    task_id=_TASK_ID,
    resolve_result=(_DRAFT_ID, _CRED_ID),
    upsert_side_effect=None,
):
    """调 learning_record_node，mock DB（LocalDBManager + draft_submissions 解析 + upsert_index）。"""
    from graphs.nodes.learning_record_node import learning_record_node

    config = SimpleNamespace(configurable={"thread_id": task_id})
    runtime = SimpleNamespace(context=SimpleNamespace())
    with patch("graphs.nodes.learning_record_node.LocalDBManager") as mock_db, \
         patch(
            "graphs.nodes.learning_record_node._resolve_draft_submission",
            return_value=resolve_result,
         ) as mock_resolve, \
         patch(
            "services.product_index_service.upsert_index",
            side_effect=upsert_side_effect,
         ) as mock_upsert:
        mock_db.return_value = mock_db
        out = learning_record_node(state, config, runtime)
    return out, mock_upsert, mock_db, mock_resolve


def test_approved_backfills_index():
    """approved + product_id + credential_id → upsert_index 被调，参数含 task_id/draft_id/credential_id。"""
    state = _make_state()
    out, mock_upsert, _, _ = _run_node(state)

    mock_upsert.assert_called_once()
    kwargs = mock_upsert.call_args.kwargs
    assert kwargs["product_id"] == "5476361418"
    assert kwargs["offer_id"] == "980815374096"  # draft.item_id（无 follow 标记）
    assert kwargs["task_id"] == _TASK_ID
    assert kwargs["credential_id"] == _CRED_ID
    assert kwargs["draft_id"] == _DRAFT_ID
    assert kwargs["tenant_id"] == "tenant-1"
    assert out.recorded_count == 0  # 学习路径正常返回


def test_follow_sell_offer_id_uses_follow_prefix():
    """跟卖信封 → offer_id = follow_{竞品id}（对齐 draft_service._resolve_offer_id）。"""
    draft = {"title": "跟卖", "ozon_product_id": "7777777", "item_id": "980815374096"}
    envelope = {"extensions": {"follow_sell": True}}
    state = _make_state(draft=draft, envelope=envelope)
    _, mock_upsert, _, _ = _run_node(state)

    mock_upsert.assert_called_once()
    assert mock_upsert.call_args.kwargs["offer_id"] == "follow_7777777"


def test_not_approved_no_backfill():
    """pending / declined（非 approved）→ upsert_index 不被调。"""
    for status in ("pending", "declined", "imported"):
        state = _make_state(moderation_status=status, status=status)
        _, mock_upsert, _, mock_resolve = _run_node(state)
        mock_upsert.assert_not_called()
        mock_resolve.assert_not_called()  # 未进 approved 分支，连解析都不做


def test_missing_product_id_skip():
    """product_id 缺失 → 跳过回填，不抛异常，学习路径照常。"""
    state = _make_state(product_id=None)
    _, mock_upsert, _, mock_resolve = _run_node(state)

    mock_upsert.assert_not_called()
    mock_resolve.assert_not_called()


def test_missing_credential_skip():
    """credential_id 不可解析（直连任务无凭证落库）→ 跳过不抛。"""
    state = _make_state()
    out, mock_upsert, _, _ = _run_node(state, resolve_result=(_DRAFT_ID, None))

    mock_upsert.assert_not_called()
    assert out.recorded_count == 0  # 节点正常返回，未阻断学习


def test_missing_task_id_skip():
    """task_id（thread_id）缺失 → 跳过不抛（config 无 configurable 时）。"""
    state = _make_state()
    _, mock_upsert, _, mock_resolve = _run_node(state, task_id="")

    mock_upsert.assert_not_called()
    mock_resolve.assert_not_called()


def test_backfill_failure_nonblocking():
    """upsert_index 抛异常 → logger.warning 吞掉，学习记录仍正常返回。"""
    state = _make_state(final_attributes=[
        {"attribute_id": 85, "value": "Нет бренда", "dictionary_value_id": 126745801, "source": "learned_approved"}
    ])
    out, mock_upsert, mock_db, _ = _run_node(
        state, upsert_side_effect=Exception("DB write failed")
    )

    # 异常被 _backfill_product_index 吞掉，节点正常返回 LearningRecordOutput
    assert out is not None
    assert out.recorded_count == 1  # 属性映射学习照常执行
    mock_db.add_attribute_mapping.assert_called_once()  # 学习路径不被索引回填阻断
