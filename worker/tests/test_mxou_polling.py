"""grsai 轮询节奏单测（v0.25）— 前 30s 不轮询，之后每 5s 一次（减无效请求）。"""
from __future__ import annotations

import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils import mxou_api as mod


def test_poll_starts_after_30s_then_every_5s():
    sleeps = []
    orig_sleep = time.sleep
    time.sleep = lambda s: sleeps.append(s)

    class _Resp:
        status_code = 200
        def json(self):
            return {"status": "succeeded", "results": [{"url": "https://x/1.jpg"}]}

    session = mock.MagicMock()
    session.get.return_value = _Resp()
    with mock.patch.object(mod, "_get_session", return_value=session):
        try:
            url = mod._poll_grsai_task("t1", max_wait=40, token="k")
        finally:
            time.sleep = orig_sleep
    assert url == "https://x/1.jpg"
    assert sleeps == [30, 5], f"首等 30s + 1 次 5s 后成功返回, got {sleeps}"
    assert session.get.call_count == 1


def test_poll_respects_max_wait():
    """max_wait=90 → 首等 30s 后 12 次 5s 轮询（覆盖 90s），
    超时抛 ImagePollTimeoutError（v0.26 P0-3：轮询超时≠失败，不重试不降级防双倍计费）。"""
    sleeps = []
    orig_sleep = time.sleep
    time.sleep = lambda s: sleeps.append(s)

    class _Resp:
        status_code = 200
        def json(self):
            return {"status": "running", "progress": 10}

    session = mock.MagicMock()
    session.get.return_value = _Resp()
    raised = False
    with mock.patch.object(mod, "_get_session", return_value=session):
        try:
            try:
                mod._poll_grsai_task("t1", max_wait=90, token="k")
            except mod.ImagePollTimeoutError:
                raised = True
        finally:
            time.sleep = orig_sleep
    assert raised, "轮询超时应抛 ImagePollTimeoutError（v0.26 防双倍计费）"
    assert sleeps[0] == 30
    assert sleeps[1:] == [5] * 12
    assert session.get.call_count == 12


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
