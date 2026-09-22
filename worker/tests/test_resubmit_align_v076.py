"""race-L1（Task 26）: resubmit_task 对齐 submit_task 的余额预检与并发 409。

背景：submit_task 在入队前有 _check_mxou_balance 预检（欠费 → 402
INSUFFICIENT_BALANCE），入队 IntegrityError（并发撞部分唯一索引）→ 干净 409
DUPLICATE_SUBMIT（F-C06）。resubmit_task 此前两段皆缺：
- 欠费 token 可借重提交绕过 402（重跑生图/LLM 同样烧 MXOU 额度）；
- 并发重复重提交时 IntegrityError 冒泡进通用 except → 500（submit 同场景是 409）。

本文件锁定对齐后的两段行为（mock-only，无需 PG/GPU）：
1. 低余额 → 402，不入队（且余额函数收到剥 sk- 前缀的 key + caller 租户，
   与 submit_task 同参形态）；
2. 并发重复（submit_task 抛 IntegrityError）→ 409 DUPLICATE_SUBMIT，
   文案与 submit_task 同款，不是 500。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_resubmit_align_v076.py -q
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy.exc import IntegrityError  # noqa: E402


# ============================================================
# 复用 test_moderation_rejected.py 的替身形态
# ============================================================

class _FakeTaskStatusProcessor:
    """task_processor 替身：get_task_status + submit_task。"""

    def __init__(self, task_status):
        self.task_status = task_status
        self.submit_calls = []
        self.next_id = "task-resub-1"

    async def get_task_status(self, task_id):
        return self.task_status

    async def submit_task(self, **kwargs):
        self.submit_calls.append(kwargs)
        return self.next_id


class _ResubmitRequest:
    """携带 token 的重提交请求替身。"""

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _resubmit_body(token="sk-tok123"):
    return {"token": token}


def _rejected_task_status(payload=None, tenant_id="u1"):
    return {
        "id": "task-old",
        "tenant_id": tenant_id,
        "status": "rejected",
        "priority": 0,
        "payload": payload or {},
        "result": None,
        "error_message": None,
        "retry_count": 0,
        "max_retries": 3,
        "created_at": None,
        "updated_at": None,
        "started_at": None,
        "completed_at": None,
        "timeout_seconds": 1800,
        "progress": None,
    }


# ============================================================
# 用例 1：低余额 → 402（对齐 submit_task）
# ============================================================

def test_resubmit_low_balance_402():
    """欠费 token 重提交 → 402 INSUFFICIENT_BALANCE，不入队（余额函数同参形态）。"""
    import main as main_mod

    proc = _FakeTaskStatusProcessor(_rejected_task_status())
    balance_calls = []

    def _fake_balance(token_record):
        balance_calls.append(dict(token_record))
        return -5.0, False

    with patch.object(main_mod, "task_processor", proc), patch(
        "main._authenticate_token", return_value="u1"
    ), patch("main._check_mxou_balance", side_effect=_fake_balance):
        resp = asyncio.run(
            main_mod.http_resubmit_task("task-old", _ResubmitRequest(_resubmit_body()))
        )

    assert resp.status_code == 402
    body = json.loads(resp.body)
    assert body["ok"] is False
    assert body["error_code"] == "INSUFFICIENT_BALANCE"
    assert "MXOU 余额不足" in body["message"]
    # 鉴权/归属/状态校验通过仍不得入队——余额是硬前置
    assert proc.submit_calls == []
    # 同参形态（对齐 submit_task）：key 剥 sk- 前缀，user_id=caller 租户
    assert balance_calls == [{"key": "tok123", "user_id": "u1"}]


# ============================================================
# 用例 2：并发重复重提交 → 409（对齐 submit_task 的 F-C06 映射）
# ============================================================

def test_resubmit_concurrent_duplicate_409():
    """submit_task 抛 IntegrityError（并发撞唯一索引）→ 409 DUPLICATE_SUBMIT，不是 500。"""
    import main as main_mod

    proc = _FakeTaskStatusProcessor(_rejected_task_status())

    async def _boom(**kwargs):
        raise IntegrityError("duplicate key value violates unique constraint "
                             "uq_ozon_product_tasks_tenant_sku", None, None)

    proc.submit_task = _boom
    with patch.object(main_mod, "task_processor", proc), patch(
        "main._authenticate_token", return_value="u1"
    ), patch("main._check_mxou_balance", return_value=(100.0, True)):
        resp = asyncio.run(
            main_mod.http_resubmit_task("task-old", _ResubmitRequest(_resubmit_body()))
        )

    assert resp.status_code == 409
    body = json.loads(resp.body)
    assert body["ok"] is False
    assert body["error_code"] == "DUPLICATE_SUBMIT"
    # 与 submit_task 的 409 文案同款（grep main.py submit 段核对）
    assert body["message"] == "该商品已在提交队列（并发提交命中唯一约束），请勿重复提交"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                fails += 1
                print(f"FAIL {name}: {exc}")
    raise SystemExit(1 if fails else 0)
