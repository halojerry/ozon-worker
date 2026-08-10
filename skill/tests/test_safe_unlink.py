#!/usr/bin/env python3
"""safe_unlink / safe_rmtree 安全删除回归（Windows 沙箱 fail-open）。

背景：Windows 沙箱/AppLocker 策略可能禁止删除非临时文件（安全删除钩子
fail-closed），skill 多处裸 `.unlink()`/`shutil.rmtree()` 在受控环境会抛
PermissionError 直接崩溃（缓存清理/更新回滚/临时文件清理）。safe_unlink
fail-open：unlink 失败降级 os.remove，仍失败只 warning 不 raise。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_safe_unlink.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib.utils import safe_rmtree, safe_unlink  # noqa: E402


def test_safe_unlink_success():
    """正常文件 → 删除成功返回 True。"""
    p = Path("/tmp/fake_safe_unlink_ok.txt")
    with mock.patch("scripts.lib.utils.Path.unlink", return_value=None) as unlink:
        assert safe_unlink(p) is True
        unlink.assert_called_once_with(missing_ok=True)


def test_safe_unlink_falls_back_to_os_remove():
    """unlink 抛 PermissionError → 降级 os.remove，返回 True。"""
    p = Path("/tmp/fake_safe_unlink.txt")
    with mock.patch("scripts.lib.utils.Path.unlink", side_effect=PermissionError("denied")), \
         mock.patch("scripts.lib.utils.os.remove", return_value=None) as rm, \
         mock.patch("scripts.lib.utils.logger.warning"):
        assert safe_unlink(p) is True
        rm.assert_called_once_with(p)


def test_safe_unlink_both_fail_returns_false():
    """unlink + os.remove 都失败 → 不 raise，返回 False。"""
    p = Path("/tmp/fake_safe_unlink2.txt")
    with mock.patch("scripts.lib.utils.Path.unlink", side_effect=PermissionError("denied")), \
         mock.patch("scripts.lib.utils.os.remove", side_effect=OSError("also denied")), \
         mock.patch("scripts.lib.utils.logger.warning") as warn:
        assert safe_unlink(p) is False
        warn.assert_called_once()


def test_safe_unlink_missing_ok():
    """文件不存在 → missing_ok=True 静默视为成功，返回 True。"""
    p = Path("/tmp/definitely_missing_safe_unlink.txt")
    assert safe_unlink(p) is True


def test_safe_rmtree_success():
    """目录删除成功 → 返回 True。"""
    with mock.patch("scripts.lib.utils.shutil.rmtree", return_value=None) as rmtree:
        assert safe_rmtree("/tmp/fake_dir") is True
        rmtree.assert_called_once_with("/tmp/fake_dir", ignore_errors=True)


def test_safe_rmtree_fails_returns_false():
    """rmtree 抛异常 → 不 raise，返回 False。"""
    with mock.patch("scripts.lib.utils.shutil.rmtree", side_effect=OSError("denied")), \
         mock.patch("scripts.lib.utils.logger.warning"):
        assert safe_rmtree("/tmp/fake_dir") is False


def test_safe_unlink_path_string_input():
    """str 路径输入 → 兼容 Path 转换。"""
    with mock.patch("scripts.lib.utils.Path.unlink") as unlink:
        unlink.return_value = None
        assert safe_unlink("/tmp/str_path.txt") is True
        unlink.assert_called_once()
