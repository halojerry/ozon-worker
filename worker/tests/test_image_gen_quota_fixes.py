"""v0.26 生图额度/队列重跑修复回归测试。

覆盖：
- mxou 失败分类（P0-3）：轮询超时 → 不重新 POST 不降级；violation → 有界重试；HTTP 500 → 重试后成功
- 队列重跑有界化（P0-2）：stale 清理 SQL 守卫（retry_count < max_retries）

运行（Docker 内，镜像含完整依赖）：
    docker run --rm -v /Volumes/os/dev/ozon-worker/worker:/app -w /app \
      -e PYTHONPATH=/app/src -e APP_WORKSPACE_PATH=/app -e GRSAI_API_KEY= \
      ozon-worker:latest python tests/test_image_gen_quota_fixes.py
"""
from __future__ import annotations

import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class _R:
    """fake requests.Response"""
    def __init__(self, code=200, j=None):
        self.status_code = code
        self._j = j or {}
        self.text = "" if code == 200 else "err"

    def json(self):
        return self._j


class FakeSession:
    """可编程 fake session：按顺序返回预置响应。"""
    def __init__(self, responses):
        self.responses = list(responses)
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        resp = self.responses.pop(0) if self.responses else _R(500, {})
        return resp

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        resp = self.responses.pop(0) if self.responses else _R(500, {})
        return resp


def _patch_session(session):
    return mock.patch("utils.mxou_api._get_session", return_value=session)


def test_poll_timeout_no_retry_no_fallback():
    """轮询超时（任务仍 pending 到超时）→ 不重新 POST、不降级 → 返回 None，POST 恰好 1 次。

    v0.26 根因：旧逻辑主模型轮询超时后降级 fallback 再 POST → 双倍计费（Sentry 轮询超时×50）。
    """
    import utils.mxou_api as mxou

    # 第 1 个响应：POST 返回 task_id（async）→ 轮询
    # 第 2 个响应：grsai 轮询一直 pending（无成功结果）→ 超时抛 ImagePollTimeoutError
    session = FakeSession([
        _R(200, {"id": "t1", "status": "processing"}),
        _R(200, {"id": "t1", "status": "processing", "progress": 10}),
    ])
    with _patch_session(session), mock.patch.object(time, "sleep", return_value=None):
        result = mxou.call_mxou_image_api(
            token="t", prompt="测试", ref_images=["http://img/1.png"],
            model="gpt-image-2", max_retries=1,
        )

    assert result is None
    post_urls = [u for u, _ in session.post_calls]
    assert len(post_urls) == 1, f"轮询超时不应重新 POST，实际 {len(post_urls)} 次: {post_urls}"
    assert "images/generations" in post_urls[0]


def test_violation_raises_no_retry():
    """v0.62 R4: grsai 返回 violation（内容违规）→ 抛 MxouContentViolationError，
    不重试不降级（防重复烧额度；此前 violation 有界重试会重复计费）。"""
    import utils.mxou_api as mxou

    session = FakeSession([
        # POST1 → task，轮询 → violation
        _R(200, {"id": "t1", "status": "processing"}),
        _R(200, {"id": "t1", "status": "violation", "error": "content policy"}),
    ])
    with _patch_session(session), mock.patch.object(time, "sleep", return_value=None):
        try:
            mxou.call_mxou_image_api(
                token="t", prompt="测试", ref_images=["http://img/1.png"],
                model="gpt-image-2", max_retries=1,
            )
            raise AssertionError("violation 应抛 MxouContentViolationError")
        except mxou.MxouContentViolationError:
            pass

    assert len(session.post_calls) == 1, f"violation 不重试，实际 {len(session.post_calls)} 次 POST"


def test_normal_failed_bounded_retry():
    """普通 failed（非内容违规）→ 保留有界重试（随机误伤类 1 次重试，v0.62 R4 分类处理）。"""
    import utils.mxou_api as mxou

    session = FakeSession([
        _R(200, {"id": "t1", "status": "processing"}),
        _R(200, {"id": "t1", "status": "failed", "error": "upstream timeout"}),
        _R(200, {"id": "t2", "status": "succeeded", "results": [{"url": "http://img/ok.png"}]}),
    ])
    with _patch_session(session), mock.patch.object(time, "sleep", return_value=None):
        result = mxou.call_mxou_image_api(
            token="t", prompt="测试", ref_images=["http://img/1.png"],
            model="gpt-image-2", max_retries=1,
        )

    assert result == "http://img/ok.png"
    assert len(session.post_calls) == 2, f"普通 failed 应有界重试 1 次，实际 {len(session.post_calls)}"


def test_http_500_retry_then_success():
    """HTTP 500 → 重试后成功（瞬断重试合理，max_retries=1 内）。"""
    import utils.mxou_api as mxou

    session = FakeSession([
        _R(500),
        _R(200, {"id": "t1", "status": "succeeded", "results": [{"url": "http://img/ok2.png"}]}),
    ])
    with _patch_session(session), mock.patch.object(time, "sleep", return_value=None):
        result = mxou.call_mxou_image_api(
            token="t", prompt="测试", ref_images=None, model="gpt-image-2", max_retries=1,
        )

    assert result == "http://img/ok2.png"
    assert len(session.post_calls) == 2


def test_primary_fail_degrade_to_fallback():
    """主模型真失败（HTTP 500 重试耗尽）→ 降级 fallback 成功（真失败才降级，非轮询超时）。"""
    import utils.mxou_api as mxou

    session = FakeSession([
        _R(500),  # 主模型第 1 次
        _R(500),  # 主模型第 2 次（耗尽）→ 降级
        _R(200, {"id": "fb", "status": "succeeded", "results": [{"url": "http://img/fb.png"}]}),
    ])
    with _patch_session(session), mock.patch.object(time, "sleep", return_value=None):
        result = mxou.call_mxou_image_api(
            token="t", prompt="测试", ref_images=None, model="gpt-image-2", max_retries=1,
        )

    assert result == "http://img/fb.png"
    models = [kwargs.get("json", {}).get("model") for _, kwargs in session.post_calls]
    assert models == ["gpt-image-2", "gpt-image-2", "nano-banana-fast"], models


def test_stale_cleanup_sql_has_retry_guard():
    """stale 清理 SQL 必须带 retry_count < max_retries 守卫（有界重跑，v0.26 防无限重跑）。"""
    src_path = os.path.join(os.path.dirname(__file__), "..", "src", "main.py")
    with open(src_path, encoding="utf-8") as fd:
        src = fd.read()
    assert "retry_count < max_retries" in src, "stale→pending 必须守卫 retry_count"
    assert "retry_count >= max_retries" in src, "耗尽必须终止（failed）"
    assert "zombie_running_failed" in src, "启动僵尸重置同样有界化"


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
