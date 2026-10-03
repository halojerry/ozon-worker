"""T19(race-M4): 部署重启默认不复活 failed 任务——SKIP_FAILED_REVIVE 语义翻转。

旧行为（v0.30.0）：默认复活 failed（retry_count<max_retries → pending 且
retry_count 归零）——每次发版对用户发起无人同意的重新上架、烧生图/上传额度。
新行为：默认**不**复活；仅显式 SKIP_FAILED_REVIVE=0 恢复旧行为（应急用）。

纯函数级测试（runtime.startup_checks._revive_failed_enabled，W3c 自 main 迁出），
不触 DB；import 沿用全仓测试惯例放函数内（避免 collection 期注入容器风格 env
影响其他用例）。
"""


def test_default_unset_no_revive(monkeypatch):
    """未设 env → 不复活（翻转后的安全默认）。"""
    monkeypatch.delenv("SKIP_FAILED_REVIVE", raising=False)
    from runtime.startup_checks import _revive_failed_enabled
    assert _revive_failed_enabled() is False


def test_explicit_zero_revives(monkeypatch):
    """SKIP_FAILED_REVIVE=0 → 显式恢复旧行为（复活 failed）。"""
    monkeypatch.setenv("SKIP_FAILED_REVIVE", "0")
    from runtime.startup_checks import _revive_failed_enabled
    assert _revive_failed_enabled() is True


def test_one_no_revive(monkeypatch):
    """SKIP_FAILED_REVIVE=1 → 不复活（旧测试/compose.test 既有设值仍安全）。"""
    monkeypatch.setenv("SKIP_FAILED_REVIVE", "1")
    from runtime.startup_checks import _revive_failed_enabled
    assert _revive_failed_enabled() is False


def test_empty_string_no_revive(monkeypatch):
    """SKIP_FAILED_REVIVE=（空串，compose `${VAR:-}` 默认透传形态）→ 不复活。"""
    monkeypatch.setenv("SKIP_FAILED_REVIVE", "")
    from runtime.startup_checks import _revive_failed_enabled
    assert _revive_failed_enabled() is False
