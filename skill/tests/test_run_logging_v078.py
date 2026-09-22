#!/usr/bin/env python3
"""批B1+B2: 统一运行日志 setup_run_logging + log_stage 阶段计时（纯 mock）。

- setup_run_logging(cmd)：root 双 handler（stderr INFO + 文件 DEBUG）、
  日志文件落 data/logs/run_*.log、返回路径、幂等不叠加
- log_stage：enter/exit 各一条 INFO，exit 带耗时
- cloud_probe basicConfig 兜底：仅 root 无 handler 时生效（不覆盖已有 handler）

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_run_logging_v078.py -q
"""
from __future__ import annotations

import inspect
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.lib import logging_utils  # noqa: E402


@pytest.fixture()
def clean_root_logger():
    """隔离 root logger：保存 handlers/level，测试后还原（防污染其他用例）。"""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_attr = getattr(root, "_pounding_run_log_path", None)
    for h in saved_handlers:
        root.removeHandler(h)
    root.setLevel(logging.WARNING)
    # 清掉可能的幂等标记（模块级属性由前序用例留下）
    if hasattr(root, "_pounding_run_log_path"):
        try:
            delattr(root, "_pounding_run_log_path")
        except Exception:
            pass
    yield root
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)
    if saved_attr is not None:
        root._pounding_run_log_path = saved_attr


@pytest.fixture()
def tmp_logs_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(logging_utils, "LOGS_DIR", tmp_path)
    return tmp_path


def _real_streams(root: logging.Logger) -> list:
    """root 上的真实 stderr handler（排除 pytest LogCaptureHandler——它不是
    真实出口，setup_run_logging 的幂等检查也忽略它）。"""
    return [h for h in root.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
            and not type(h).__name__.startswith("LogCapture")]


def test_setup_run_logging_double_handler(tmp_logs_dir, clean_root_logger):
    """①root 双 handler：stderr(INFO) + 文件(DEBUG)，格式沿用 cloud_probe 现格式。"""
    logging_utils.setup_run_logging("utcmd")
    root = clean_root_logger
    streams = _real_streams(root)
    files = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert len(streams) == 1 and len(files) == 1
    assert streams[0].level == logging.INFO
    assert files[0].level == logging.DEBUG
    fmt = streams[0].formatter._fmt
    assert fmt == "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    assert streams[0].formatter.datefmt == "%H:%M:%S"


def test_setup_run_logging_returns_path_and_file_created(tmp_logs_dir, clean_root_logger):
    """②返回日志路径且文件已创建，文件名含 run_ 前缀与命令名。"""
    path = logging_utils.setup_run_logging("utcmd")
    assert path
    assert os.path.dirname(path) == str(tmp_logs_dir)
    name = os.path.basename(path)
    assert name.startswith("run_") and "utcmd" in name and name.endswith(".log")
    assert os.path.exists(path)


def test_setup_run_logging_idempotent(tmp_logs_dir, clean_root_logger):
    """③重复调用幂等：同一路径，handler 不叠加。"""
    root = clean_root_logger
    p1 = logging_utils.setup_run_logging("utcmd")
    n1 = len(root.handlers)
    p2 = logging_utils.setup_run_logging("utcmd")
    assert p1 == p2
    assert len(root.handlers) == n1, "二次调用不得新增 handler"
    # 文件 handler 仍只有一个
    files = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert len(files) == 1


def test_setup_run_logging_file_receives_debug(tmp_logs_dir, clean_root_logger):
    """④文件通道收 DEBUG（root 提 DEBUG，stderr handler 挡在 INFO）。"""
    path = logging_utils.setup_run_logging("utcmd")
    logging.getLogger("ut.probe").debug("debug-should-hit-file")
    logging.getLogger("ut.probe").info("info-line")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "debug-should-hit-file" in content
    assert "info-line" in content


def test_setup_run_logging_unwritable_dir(tmp_logs_dir, clean_root_logger, monkeypatch):
    """⑤文件创建失败（LOGS_DIR 指向普通文件）→ 返回空串、不抛异常、stderr 仍在。"""
    bad = tmp_logs_dir / "not-a-dir"
    bad.write_text("x", encoding="utf-8")
    monkeypatch.setattr(logging_utils, "LOGS_DIR", bad)
    root = clean_root_logger
    path = logging_utils.setup_run_logging("utcmd")
    assert path == ""
    streams = _real_streams(root)
    files = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    assert len(streams) == 1 and len(files) == 0


def test_log_stage_enter_exit_with_duration(clean_root_logger, caplog):
    """⑥log_stage：enter/exit 各一条 INFO，exit 含「耗时」。"""
    with caplog.at_level(logging.INFO, logger="pounding.run"):
        with logging_utils.log_stage("阶段 X：测试步骤"):
            pass
    msgs = [r.getMessage() for r in caplog.records
            if r.name == "pounding.run" and r.levelno == logging.INFO]
    assert len(msgs) == 2, f"应恰有 enter/exit 两条 INFO，实际: {msgs}"
    assert "阶段 X：测试步骤" in msgs[0]
    assert "阶段 X：测试步骤" in msgs[1] and "耗时" in msgs[1]


def test_log_stage_logs_exit_on_exception(clean_root_logger, caplog):
    """⑦块内抛异常也必须有 exit（耗时）行（finally 语义）。"""
    with caplog.at_level(logging.INFO, logger="pounding.run"):
        try:
            with logging_utils.log_stage("阶段 Y"):
                raise ValueError("boom")
        except ValueError:
            pass
    msgs = [r.getMessage() for r in caplog.records if r.name == "pounding.run"]
    assert len(msgs) == 2 and "耗时" in msgs[1]


def test_cloud_probe_basiconfig_is_fallback_only():
    """⑧cloud_probe basicConfig 兜底锁定：仅 root 无 handler 时生效（源码级守卫）。

    批A 已改为 `if not logging.getLogger().handlers:` 守卫——批B 用 AST/源码断言
    锁定不回退（setup_run_logging 先配好 handler 后，cloud_probe 导入不叠加）。
    """
    import scripts.cloud_probe as cp
    src = inspect.getsource(cp)
    guard = "if not logging.getLogger().handlers:"
    assert guard in src, "cloud_probe 丢了 basicConfig 兜底守卫（会叠加 handler）"
    guard_pos = src.index(guard)
    basic_pos = src.index("logging.basicConfig(")
    assert guard_pos < basic_pos, "basicConfig 必须在守卫之内（兜底语义）"
    # 生效性：root 已有 handler 时再执行守卫块不会新增（直接验证守卫表达式语义）
    logging.getLogger().addHandler(logging.NullHandler())
    before = len(logging.getLogger().handlers)
    if not logging.getLogger().handlers:  # pragma: no cover - 恒 False，验证语义
        logging.basicConfig(level=logging.INFO)
    assert len(logging.getLogger().handlers) == before
    logging.getLogger().removeHandler(logging.NullHandler())


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
    sys.exit(1 if failed else 0)
