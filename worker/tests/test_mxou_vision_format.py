"""v0.64: call_mxou_chat_api 视觉内容格式单测。

验证：
(a) image_urls=None → payload content 为纯字符串（向后兼容）
(b) image_urls=["url1"] → content 为 OpenAI Vision array 格式
(c) image_urls 超过 4 张 → 截断为前 4 张
(d) 空列表 → 等价于 None（纯字符串）

运行：
    cd worker && PYTHONPATH=src python3 -m pytest tests/test_mxou_vision_format.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _setup_module(monkeypatch):
    """初始化 mxou_api 模块（绕过余额检查 + session 单例）。

    ⚠️ 必须走 monkeypatch（测试结束后自动还原）：此前裸替换模块函数且不还原，
    污染同进程后续测试（test_mxou_balance_precheck / test_out_of_quota_fatal
    在全量跑时被 _check_balance_cached 的永久 Mock 打穿）。
    """
    from utils import mxou_api
    monkeypatch.setattr(mxou_api, "_check_balance_cached", mock.Mock(return_value=999.0))
    monkeypatch.setattr(mxou_api, "_get_session", mock.Mock())
    monkeypatch.setattr(mxou_api, "mxou_acquire", mock.Mock())  # 跳过限流器
    return mxou_api


def _capture_payload(mxou_api, token="sk-test", image_urls=None):
    """调用 call_mxou_chat_api 并捕获发送的 payload。"""
    captured = {}

    class _FakeResp:
        status_code = 200
        text = '{"choices":[{"message":{"content":"ok"}}]}'
        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _FakeResp()

    mock_session = mock.Mock()
    mock_session.post = _fake_post
    mxou_api._get_session.return_value = mock_session

    mxou_api.call_mxou_chat_api(
        token=token,
        system_prompt="sys",
        user_prompt="hello",
        image_urls=image_urls,
    )
    return captured["payload"]


def test_no_images_string_content(monkeypatch):
    """(a) image_urls=None → content 为纯字符串。"""
    mod = _setup_module(monkeypatch)
    payload = _capture_payload(mod, image_urls=None)
    user_msg = payload["messages"][1]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], str)
    assert user_msg["content"] == "hello"


def test_with_images_array_content(monkeypatch):
    """(b) image_urls → content 为 Vision array。"""
    mod = _setup_module(monkeypatch)
    payload = _capture_payload(mod, image_urls=["https://img1.jpg", "https://img2.jpg"])
    user_msg = payload["messages"][1]
    assert isinstance(user_msg["content"], list)
    # 第一项是文本
    assert user_msg["content"][0] == {"type": "text", "text": "hello"}
    # 后续是图片
    assert user_msg["content"][1] == {"type": "image_url", "image_url": {"url": "https://img1.jpg"}}
    assert user_msg["content"][2] == {"type": "image_url", "image_url": {"url": "https://img2.jpg"}}


def test_images_capped_at_four(monkeypatch):
    """(c) 超过 4 张 → 截断。"""
    mod = _setup_module(monkeypatch)
    urls = [f"https://img{i}.jpg" for i in range(10)]
    payload = _capture_payload(mod, image_urls=urls)
    user_msg = payload["messages"][1]
    # 1 text + 4 images = 5 items
    assert len(user_msg["content"]) == 5


def test_empty_list_equals_none(monkeypatch):
    """(d) 空列表 → 纯字符串。"""
    mod = _setup_module(monkeypatch)
    payload = _capture_payload(mod, image_urls=[])
    user_msg = payload["messages"][1]
    assert isinstance(user_msg["content"], str)


def test_system_prompt_always_string(monkeypatch):
    """system prompt 不受 image_urls 影响。"""
    mod = _setup_module(monkeypatch)
    payload = _capture_payload(mod, image_urls=["https://img1.jpg"])
    sys_msg = payload["messages"][0]
    assert isinstance(sys_msg["content"], str)
    assert sys_msg["content"] == "sys"


def test_default_model_is_vision(monkeypatch):
    """默认 model 应为 deepseek-v4-flash-vision-exp。"""
    mod = _setup_module(monkeypatch)
    payload = _capture_payload(mod, image_urls=None)
    assert payload["model"] == "deepseek-v4-flash-vision-exp"
