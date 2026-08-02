"""_check_mxou_balance 单元测试（mock Supabase，无需真实库）。

背景（2026-08-02 修复）：原实现只查 tokens.remain_quota（僵尸字段），
无限额度 token（unlimited_quota=true）被误判余额不足。修复后查
users.quota - used_quota，unlimited_quota 放行。
"""
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_unlimited_quota_bypass():
    """无限额度 token 直接放行（即使 remain_quota=0 / users 表余额 0）。"""
    from main import _check_mxou_balance

    with patch("main.get_supabase_client") as mock_sb:
        mock_sb.return_value = None  # 即使 Supabase 不可用也放行
        balance, ok = _check_mxou_balance({"unlimited_quota": True, "remain_quota": 0})
        assert ok is True, f"无限额度应放行: {balance}, {ok}"


def test_users_balance_positive():
    """有余额：users.quota - used_quota > 0 → 放行。"""
    from main import _check_mxou_balance

    fake_sb = MagicMock()
    # users 表返回 quota=100, used_quota=30 → balance=70
    fake_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"quota": 100, "used_quota": 30}
    ]
    with patch("main.get_supabase_client", return_value=fake_sb):
        balance, ok = _check_mxou_balance({"user_id": "u1", "remain_quota": 0})
        assert ok is True, f"余额 70 应放行: {balance}, {ok}"
        assert balance == 70.0


def test_users_balance_negative():
    """余额耗尽：quota - used_quota <= 0 → 拒绝。"""
    from main import _check_mxou_balance

    fake_sb = MagicMock()
    fake_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"quota": 100, "used_quota": 120}
    ]
    with patch("main.get_supabase_client", return_value=fake_sb):
        balance, ok = _check_mxou_balance({"user_id": "u1"})
        assert ok is False, f"余额 -20 应拒绝: {balance}, {ok}"
        assert balance == -20.0


def test_users_no_record_fallback():
    """users 表无记录 → 降级 remain_quota（>0 放行，=0 拒绝）。"""
    from main import _check_mxou_balance

    fake_sb = MagicMock()
    fake_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    with patch("main.get_supabase_client", return_value=fake_sb):
        balance, ok = _check_mxou_balance({"user_id": "u1", "remain_quota": 50})
        assert ok is True and balance == 50.0
        balance2, ok2 = _check_mxou_balance({"user_id": "u1", "remain_quota": 0})
        assert ok2 is False and balance2 == 0.0


def test_users_query_error_fallback():
    """users 查询异常 → 降级 remain_quota（不崩溃）。"""
    from main import _check_mxou_balance

    fake_sb = MagicMock()
    fake_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = Exception("RLS blocked")
    with patch("main.get_supabase_client", return_value=fake_sb):
        balance, ok = _check_mxou_balance({"user_id": "u1", "remain_quota": 10})
        assert ok is True and balance == 10.0


def test_supabase_none_local_dev():
    """本地开发（无 Supabase）不阻断。"""
    from main import _check_mxou_balance

    with patch("main.get_supabase_client", return_value=None):
        balance, ok = _check_mxou_balance({"user_id": "u1", "remain_quota": 0})
        assert ok is True


def test_overall_exception_not_blocking():
    """整体异常不阻断（放行，避免鉴权误杀）。"""
    from main import _check_mxou_balance

    fake_sb = MagicMock()
    fake_sb.table.side_effect = Exception("boom")
    with patch("main.get_supabase_client", return_value=fake_sb):
        balance, ok = _check_mxou_balance({"unlimited_quota": None})
        assert ok is True


if __name__ == "__main__":
    # 无需 pytest 即可运行（worker/.venv312 无 pytest）：
    #   cd worker && .venv312/bin/python tests/test_mxou_balance.py
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
