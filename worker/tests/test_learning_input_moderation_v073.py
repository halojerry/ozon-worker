"""v0.73 W8: LearningRecordInput 补 moderation_status——langgraph 按 Input 过滤
channel，此前未声明 → learning_record_node 里 approved 分支恒不可达，全靠
upload_status=success+product_id 兜底（ozon_status_node approved 时恰好都写）。"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import LearningRecordInput
from graphs.nodes.learning_record_node import _is_real_upload_success


def test_input_declares_moderation_status():
    assert "moderation_status" in LearningRecordInput.model_fields
    # brief 原文为 LearningRecordInput() 无参构造，但 description_category_id 是必填字段
    #（Field(...)），无参会 ValidationError——补必填参，断言意图不变（缺省 moderation_status=""）。
    assert LearningRecordInput(description_category_id="0").moderation_status == ""


def test_approved_branch_reachable_with_declared_field():
    class _S:
        moderation_status = "approved"
        status = "imported"
        upload_status = "success"
        product_id = "123"
    assert _is_real_upload_success(_S()) is True


def test_approved_branch_not_shadowed_by_default():
    """moderation_status 缺省（旧信封）时行为不变：走 status/upload_status 回退。"""
    class _S:
        moderation_status = ""
        status = "imported"
        upload_status = "success"
        product_id = "123"
    assert _is_real_upload_success(_S()) is True
