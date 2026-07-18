"""API schemas 和错误码基础测试。"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_error_codes_unique():
    """错误码值不重复。"""
    from api.errors import WorkerErrorCode
    values = [e.value for e in WorkerErrorCode]
    assert len(values) == len(set(values)), f"重复错误码: {values}"


def test_error_response_format():
    """错误响应格式正确。"""
    from api.errors import error_response, WorkerErrorCode
    resp = error_response(WorkerErrorCode.TOKEN_INVALID, "bad token")
    assert resp.status_code == 401
    body = resp.body.decode()
    assert '"ok":false' in body.replace(" ", "")
    assert "TOKEN_INVALID" in body
    assert "bad token" in body


def test_submit_task_request_validation():
    """SubmitTaskRequest 必填字段校验。"""
    from api.schemas import SubmitTaskRequest
    import pydantic

    # 缺少必填字段应报错
    try:
        SubmitTaskRequest(token="test")
        assert False, "应该抛出 ValidationError"
    except pydantic.ValidationError:
        pass

    # 完整字段应通过
    req = SubmitTaskRequest(
        token="sk-test",
        ozon_client_id="123",
        ozon_api_key="abc",
        envelope={"draft": {}},
    )
    assert req.token == "sk-test"
    assert req.timeout_seconds == 1800


def test_task_status_enum():
    """TaskStatus 枚举值完整。"""
    from api.schemas import TaskStatus
    assert TaskStatus.PENDING == "pending"
    assert TaskStatus.RUNNING == "running"
    assert TaskStatus.COMPLETED == "completed"
    assert TaskStatus.FAILED == "failed"
    assert TaskStatus.CANCELLED == "cancelled"
