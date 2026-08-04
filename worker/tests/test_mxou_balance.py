"""_check_mxou_balance 单元测试（mock Supabase，无需真实库）。

v0.22 根因修复（2026-08-04 实测）：余额统一查用户级 users.quota：
- balance 一律来自 users.quota（不再返回 key 级 remain_quota 僵尸字段）
- unlimited_quota=true 仅影响判定（放行），balance 仍显示用户级 quota
- users 查询失败/无记录：unlimited 放行；非 unlimited 拒绝（不再降级僵尸字段）
"""
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _fake_sb(quota_rows):
    fake = MagicMock()
    fake.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = quota_rows
    return fake


def test_unlimited_balance_shows_user_quota():
    """无限额度：放行，但 balance 必须是用户级 users.quota（不是 key 级 remain_quota）。"""
    from main import _check_mxou_balance

    with patch("main.get_supabase_client", return_value=_fake_sb([{"quota": 800}])):
        balance, ok = _check_mxou_balance(
            {"unlimited_quota": True, "user_id": "u1", "remain_quota": -58083828}
        )
        assert ok is True
        assert balance == 800.0, f"balance 应为用户级 quota 800，实际 {balance}"


def test_unlimited_zero_quota_bypass():
    """无限额度 + users.quota=0 → 仍放行（无限额度不看余额）。"""
    from main import _check_mxou_balance

    with patch("main.get_supabase_client", return_value=_fake_sb([{"quota": 0}])):
        balance, ok = _check_mxou_balance({"unlimited_quota": True, "user_id": "u1"})
        assert ok is True


def test_users_balance_positive():
    """用户级余额 > 0 → 放行。"""
    from main import _check_mxou_balance

    with patch("main.get_supabase_client", return_value=_fake_sb([{"quota": 100}])):
        balance, ok = _check_mxou_balance({"user_id": "u1", "remain_quota": 0})
        assert ok is True and balance == 100.0


def test_users_balance_zero_rejected():
    """用户级余额 <= 0 → 拒绝。"""
    from main import _check_mxou_balance

    with patch("main.get_supabase_client", return_value=_fake_sb([{"quota": 0}])):
        balance, ok = _check_mxou_balance({"user_id": "u1"})
        assert ok is False and balance == 0.0


def test_users_no_record_non_unlimited_rejected():
    """users 无记录 + 非 unlimited → 拒绝（不再降级 key 级 remain_quota=50 放行）。"""
    from main import _check_mxou_balance

    with patch("main.get_supabase_client", return_value=_fake_sb([])):
        balance, ok = _check_mxou_balance({"user_id": "u1", "remain_quota": 50})
        assert ok is False, "users 无记录非 unlimited 应拒绝（不读僵尸字段）"


def test_users_no_record_unlimited_bypass():
    """users 无记录 + unlimited → 放行。"""
    from main import _check_mxou_balance

    with patch("main.get_supabase_client", return_value=_fake_sb([])):
        balance, ok = _check_mxou_balance({"user_id": "u1", "unlimited_quota": True})
        assert ok is True


def test_users_query_error_non_unlimited_rejected():
    """users 查询失败 + 非 unlimited → 拒绝（不再降级 remain_quota=10 放行）。"""
    from main import _check_mxou_balance

    fake = MagicMock()
    fake.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = Exception("RLS blocked")
    with patch("main.get_supabase_client", return_value=fake):
        balance, ok = _check_mxou_balance({"user_id": "u1", "remain_quota": 10})
        assert ok is False, "查询失败非 unlimited 应拒绝（不再用 remain_quota 降级）"


def test_users_query_error_unlimited_bypass():
    """users 查询失败 + unlimited → 放行。"""
    from main import _check_mxou_balance

    fake = MagicMock()
    fake.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = Exception("RLS blocked")
    with patch("main.get_supabase_client", return_value=fake):
        balance, ok = _check_mxou_balance({"user_id": "u1", "unlimited_quota": True})
        assert ok is True


def test_supabase_none_local_dev():
    """本地开发（无 Supabase）不阻断。"""
    from main import _check_mxou_balance

    with patch("main.get_supabase_client", return_value=None):
        balance, ok = _check_mxou_balance({"user_id": "u1", "remain_quota": 0})
        assert ok is True


def test_overall_exception_not_blocking():
    """整体异常（外层）不阻断，放行避免鉴权误杀。"""
    from main import _check_mxou_balance

    fake = MagicMock()
    fake.table.side_effect = Exception("boom")
    with patch("main.get_supabase_client", return_value=fake):
        balance, ok = _check_mxou_balance({"unlimited_quota": None})
        assert ok is True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(0 if passed == len(tests) else 1)
