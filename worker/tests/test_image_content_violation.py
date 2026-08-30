"""R4 (v0.62): 生图内容违规分类处理 — violation/关键词 → 抛异常不重试不降级。

覆盖：
- _is_content_violation_error：violation 状态 / 中英违规关键词命中
- _poll_grsai_task：violation → 抛 MxouContentViolationError；普通 failed → None
- _poll_mxou_task_fallback：violation → 抛异常
- call_mxou_image_api：内容违规 → 异常穿透（不降级重试）
- 生图节点（main_image_gen）：违规 → re-raise（任务明确失败）
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class _R:
    def __init__(self, code=200, j=None):
        self.status_code = code
        self._j = j or {}

    def json(self):
        return self._j


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """轮询函数有 30s 初始延迟 + 5s 间隔 — 测试中全部跳过。"""
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)


# ═══ 判定函数 ═══

def test_is_content_violation_status_violation():
    from utils.mxou_api import _is_content_violation_error

    assert _is_content_violation_error("violation", "some error") is True


def test_is_content_violation_keywords():
    from utils.mxou_api import _is_content_violation_error

    assert _is_content_violation_error("failed", "content policy violation") is True
    assert _is_content_violation_error("failed", "adult content detected") is True
    assert _is_content_violation_error("failed", "图片内容违规，请调整") is True
    assert _is_content_violation_error("failed", "内容包含敏感元素") is True


def test_is_content_violation_normal_failed_false():
    from utils.mxou_api import _is_content_violation_error

    assert _is_content_violation_error("failed", "upstream timeout") is False
    assert _is_content_violation_error("failed", "model overloaded") is False
    assert _is_content_violation_error("", "") is False


# ═══ grsai 轮询 ═══

def test_poll_grsai_violation_raises(monkeypatch):
    import utils.mxou_api as mxou_api

    class FakeSession:
        def get(self, url, params=None, timeout=None, **kwargs):
            return _R(200, {"status": "violation", "error": "content policy"})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(mxou_api, "GRSAI_API_KEY", "k")

    with pytest.raises(mxou_api.MxouContentViolationError):
        mxou_api._poll_grsai_task("task1", max_wait=35, token="tok")


def test_poll_grsai_normal_failed_returns_none(monkeypatch):
    import utils.mxou_api as mxou_api

    class FakeSession:
        def get(self, url, params=None, timeout=None, **kwargs):
            return _R(200, {"status": "failed", "error": "upstream timeout"})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(mxou_api, "GRSAI_API_KEY", "k")

    assert mxou_api._poll_grsai_task("task1", max_wait=35, token="tok") is None


def test_poll_mxou_fallback_violation_raises(monkeypatch):
    import utils.mxou_api as mxou_api

    class FakeSession:
        def get(self, url, timeout=None, **kwargs):
            return _R(200, {"status": "violation", "error": "敏感内容"})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())

    with pytest.raises(mxou_api.MxouContentViolationError):
        mxou_api._poll_mxou_task_fallback("task1", max_wait=35, token="tok")


# ═══ call_mxou_image_api 透传 ═══

def test_call_image_api_violation_propagates(monkeypatch):
    """主模型违规 → 异常穿透 call_mxou_image_api（不降级不吞掉）。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: 100.0)
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    def _boom(token, prompt, ref_images, aspect_ratio, timeout, max_retries, model):
        raise mxou_api.MxouContentViolationError("grsai 内容违规: content policy")

    monkeypatch.setattr(mxou_api, "_call_image_with_model", _boom)

    with pytest.raises(mxou_api.MxouContentViolationError):
        mxou_api.call_mxou_image_api(token="tok", prompt="测试", model="gpt-image-2")


def test_call_image_api_primary_model_chain_violation_propagates(monkeypatch):
    """主模型 + 降级链内任一模型违规 → 异常穿透（降级链不吞掉违规）。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda token: 100.0)
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda token: None)

    def _boom(token, prompt, ref_images, aspect_ratio, timeout, max_retries, model):
        raise mxou_api.MxouContentViolationError("mxou fallback 内容违规")

    monkeypatch.setattr(mxou_api, "_call_image_with_model", _boom)

    with pytest.raises(mxou_api.MxouContentViolationError):
        mxou_api.call_mxou_image_api(
            token="tok", prompt="测试", model="nano-banana-fast",
        )


# ═══ 生图节点 re-raise ═══

def test_main_image_gen_node_re_raises_violation(monkeypatch):
    """main_image_gen_node：内容违规 → 异常穿透（任务明确失败，不返回 None 吞掉）。"""
    import graphs.nodes.main_image_gen_node as mod
    from graphs.state_image_gen import MainImageInput

    import utils.mxou_api as mxou_api

    def _boom(*a, **k):
        raise mxou_api.MxouContentViolationError("grsai 内容违规: adult content")

    monkeypatch.setattr(mod, "call_mxou_image_api", _boom)
    monkeypatch.setattr(mod, "slot_enabled", lambda *a, **k: True)
    monkeypatch.setattr(mod, "get_image", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_task_id_from_config", lambda *a, **k: None)
    monkeypatch.setattr(mod, "assemble_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(mod, "merge_visual_vars", lambda *a, **k: {})
    monkeypatch.setattr(mod, "resolve_color_preset", lambda *a, **k: "")
    monkeypatch.setattr(mod, "get_image_model", lambda *a, **k: "gpt-image-2")

    state = MainImageInput(
        draft={"title": "测试商品", "images": ["http://img/1.png"]},
        token="tok",
        original_images=["http://img/1.png"],
        white_bg_image=None,
        multi_angle_image=None,
        visual_vars={},
        category_name="",
    )
    with pytest.raises(mxou_api.MxouContentViolationError):
        mod.main_image_gen_node(state, {}, mock.Mock())
