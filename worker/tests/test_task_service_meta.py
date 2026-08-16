"""P0-2: task_service._payload_meta 上架方式字段提取测试。

验收门（docs/PRD-task-record-v0.46.md §五）：
1. update_mode = extensions.update_product_id 存在
2. parent_task_id = payload.parent_task_id（重上来源标记）
3. 敏感字段（token/api_key）绝不出现在 meta

纯函数测试，无需 PG（_payload_meta 是纯 Python 提取）。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services.task_service import _payload_meta


def _payload(**kw):
    base = {
        "token": "sk-secret-token",
        "ozon_client_id": "111111",
        "ozon_api_key": "secret-api-key",
        "envelope": {
            "draft": {"title": "测试商品", "item_id": "16880001",
                      "images": ["https://example.com/a.jpg"]},
            "extensions": {},
        },
    }
    base.update(kw)
    return base


# ============================================================
# 1. update_mode（编辑更新标记）
# ============================================================

def test_update_mode_true():
    meta = _payload_meta(_payload(
        envelope={
            "draft": {"title": "x"},
            "extensions": {"update_product_id": "5476361418"},
        }))
    assert meta["update_mode"] is True


def test_update_mode_false_default():
    meta = _payload_meta(_payload())
    assert meta["update_mode"] is False


# ============================================================
# 2. parent_task_id（重上来源标记）
# ============================================================

def test_parent_task_id_present():
    meta = _payload_meta(_payload(parent_task_id="abc-123"))
    assert meta["parent_task_id"] == "abc-123"


def test_parent_task_id_absent():
    meta = _payload_meta(_payload())
    assert meta["parent_task_id"] is None


# ============================================================
# 3. 敏感字段防护
# ============================================================

def test_no_sensitive_fields():
    meta = _payload_meta(_payload())
    assert "token" not in meta
    assert "api_key" not in meta
    assert "ozon_api_key" not in meta
    # 密文也不得出现在任何值里
    assert "sk-secret-token" not in str(meta)
    assert "secret-api-key" not in str(meta)


# ============================================================
# 4. 既有字段不回归
# ============================================================

def test_existing_fields_kept():
    meta = _payload_meta(_payload())
    assert meta["title"] == "测试商品"
    assert meta["item_id"] == "16880001"
    assert meta["ozon_client_id"] == "111111"
    assert meta["image"] == "https://example.com/a.jpg"
    assert meta["follow_sell"] is False


def test_follow_sell_still_works():
    env = {"draft": {"title": "x"}, "extensions": {"follow_sell": True}}
    meta = _payload_meta(_payload(envelope=env))
    assert meta["follow_sell"] is True


def test_malformed_payload_empty():
    assert _payload_meta(None) == {}
    assert _payload_meta("garbage") == {}
