#!/usr/bin/env python3
"""T2 Chrome 探活三态回归测试（fix/skill-concurrency-v1 批1，竞态 #1 互杀）。

背景（互杀竞态）：原 `_is_cdp_available` 单次 HTTP 3s 超时、任何异常 → False。
B 进程 `ensure_chrome_cdp` 锁内二次探活恰逢 A 进程正在用 Chrome（HTTP 慢/繁忙）
→ 误判「CDP 不可用」→ `_find_chrome_processes` 发现 Chrome 存在 → 杀掉重启
—— 把 A 正在用的 Chrome 杀了。

修复契约（本文件锁定，方案 §A3 T2）：
  1. `_probe_cdp_state` 三态：
     - "refused"：TCP connect 被拒（ConnectionRefusedError，Windows 的
       WinError 10061 在 CPython 同映射为该异常）→ 端口确实没人听；
     - "busy"：TCP 通但 /json/version HTTP 超时/失败 → 有进程占着端口没应答；
     - "up"：HTTP 200 且含 "Browser" → CDP 可用；
  2. `_is_cdp_available` 是 bool 薄封装（== "up"），既有调用方零改动；
  3. `ensure_chrome_cdp` 锁内二次检查：busy → 重试 3 次 × 2s，仍 busy → 按
     「CDP 已就绪（繁忙）」**成功返回，绝不杀**；仅 refused + Chrome 进程
     存在才走 kill+重启；
  4. 锁外入口维持「up 即用」；busy 与现状同语义（不可用 → 进锁路径），
     不在锁外做新重试。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_chrome_probe_states.py -q
"""
from __future__ import annotations

import json
import socket
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import chrome_launcher as cl  # noqa: E402


# ── 公共 mock 工具 ───────────────────────────────────────────────────────────

class _FakeSock:
    """create_connection 的替身（只证明「TCP 层通了」）。"""

    def close(self):
        pass


class _FakeResp:
    """urlopen 的替身（上下文管理器 + read）。"""

    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def _patch_probe_socket(monkeypatch, *, refuse=False, tcp_error=None):
    """patch socket.create_connection：refuse=True → 连接拒绝；tcp_error → 通用 OSError。"""
    calls = []

    def fake_connect(addr, timeout=None):
        calls.append({"addr": addr, "timeout": timeout})
        if refuse:
            raise ConnectionRefusedError(10061, "由于目标计算机积极拒绝，无法连接。")
        if tcp_error is not None:
            raise tcp_error
        return _FakeSock()

    monkeypatch.setattr(socket, "create_connection", fake_connect)
    return calls


# ── ① _probe_cdp_state 三态 ──────────────────────────────────────────────────

class TestProbeStates:
    def test_refused_when_tcp_connection_refused(self, monkeypatch):
        """TCP 连接拒绝（含 WinError 10061 语义）→ "refused"，且 TCP 超时为秒级短超时。"""
        calls = _patch_probe_socket(monkeypatch, refuse=True)
        assert cl._probe_cdp_state(9222) == "refused"
        assert calls and calls[0]["addr"] == (cl.CDP_HOST, 9222)
        assert calls[0]["timeout"] == 1.0, "TCP 层必须用 ~1s 短超时（快速确认没人听）"

    def test_busy_when_tcp_ok_but_http_times_out(self, monkeypatch):
        """TCP 通但 HTTP 超时 → "busy"（端口有人听，只是没应答——绝不按 refused 杀）。"""
        _patch_probe_socket(monkeypatch)

        def slow_http(req, timeout=None):
            raise TimeoutError("another process is using CDP")

        monkeypatch.setattr(urllib.request, "urlopen", slow_http)
        assert cl._probe_cdp_state(9222) == "busy"

    def test_busy_when_tcp_ok_but_http_garbage(self, monkeypatch):
        """TCP 通但 HTTP 返回非 CDP 内容 → "busy"。"""
        _patch_probe_socket(monkeypatch)
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda req, timeout=None: _FakeResp({"error": "not cdp"}))
        assert cl._probe_cdp_state(9222) == "busy"

    def test_up_when_http_200_with_browser(self, monkeypatch):
        _patch_probe_socket(monkeypatch)
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda req, timeout=None: _FakeResp({"Browser": "Chrome/126.0"}))
        assert cl._probe_cdp_state(9222) == "up"

    def test_tcp_generic_oserror_is_busy_not_refused(self, monkeypatch):
        """TCP 层普通 OSError（超时/不可达）：无法确认「没人听」→ 保守 "busy"，绝不 refused。"""
        _patch_probe_socket(monkeypatch, tcp_error=OSError("timed out"))
        assert cl._probe_cdp_state(9222) == "busy"

    def test_is_cdp_available_is_thin_bool_wrapper(self, monkeypatch):
        """_is_cdp_available == (_probe_cdp_state == "up")——既有调用方零改动。"""
        seen = []

        def fake_state(port=cl.CDP_PORT):
            seen.append(port)
            return ["up", "refused", "busy"][len(seen) - 1]

        monkeypatch.setattr(cl, "_probe_cdp_state", fake_state)
        assert cl._is_cdp_available(9333) is True
        assert cl._is_cdp_available(9333) is False
        assert cl._is_cdp_available(9333) is False
        assert seen == [9333, 9333, 9333]


# ── ② ensure_chrome_cdp 锁内三态语义 ─────────────────────────────────────────

class TestEnsureCdpThreeStates:
    def _patch(self, monkeypatch, tmp_path, probe_results, found_processes=None):
        """公共 mock：锁必得、探活按序列返回（耗尽后停在最后一个）、杀/启全程记录。

        返回 (kills, launches, acquire_calls)。
        """
        kills, launches, acquire_calls = [], [], []
        seq = iter(list(probe_results))

        def fake_state(port=cl.CDP_PORT):
            return next(seq, probe_results[-1])

        monkeypatch.setattr(cl, "_probe_cdp_state", fake_state)
        monkeypatch.setattr(cl, "_try_acquire_lock",
                            lambda path, timeout=30.0: acquire_calls.append(path) or object())
        monkeypatch.setattr(cl, "_release_lock", lambda fd: None)
        monkeypatch.setattr(cl, "_find_chrome_executable", lambda: "/usr/bin/fake-chrome")
        monkeypatch.setattr(cl, "_find_chrome_processes", lambda: list(found_processes or []))
        monkeypatch.setattr(cl, "_kill_chrome_processes",
                            lambda reason="", port=None: kills.append(reason) or True)
        monkeypatch.setattr(cl, "_write_launched_pid", lambda pid: None)

        class _FakeProc:
            pid = 424242  # 启动成功路径会读 .pid 记录 PID 文件（已 mock 掉写入）

            def poll(self):
                return None

        monkeypatch.setattr(
            cl.subprocess, "Popen",
            lambda cmd, **kw: launches.append(cmd) or _FakeProc())
        monkeypatch.setattr(cl.time, "sleep", lambda s: None)  # busy 重试/等待不真睡
        return kills, launches, acquire_calls

    def test_busy_persists_returns_success_never_kills(self, monkeypatch, tmp_path):
        """核心互杀回归：持续 busy（含 Chrome 进程在场的表象）→ 成功返回、零杀零启。"""
        chrome_proc = {"pid": 111, "cmd": ".../google-chrome --remote-debugging-port=9222",
                       "port": 9222}
        kills, launches, _ = self._patch(
            monkeypatch, tmp_path, ["busy"], found_processes=[chrome_proc])
        ok, msg = cl.ensure_chrome_cdp(port=9222, profile_dir=str(tmp_path / "prof"))
        assert ok is True, f"busy 必须按「CDP 已就绪（繁忙）」成功返回，实际: {msg!r}"
        assert "繁忙" in msg
        assert kills == [], "busy 永不触发 _kill_chrome_processes（互杀竞态回归）"
        assert launches == [], "busy 也不得重启 Chrome"

    def test_busy_retries_then_succeeds_on_recovery(self, monkeypatch, tmp_path):
        """busy → 重试中恢复 up → 成功复用（重试机制生效）。"""
        kills, launches, _ = self._patch(
            monkeypatch, tmp_path, ["busy", "busy", "busy", "busy", "up"])
        ok, msg = cl.ensure_chrome_cdp(port=9222, profile_dir=str(tmp_path / "prof"))
        assert ok is True and kills == [] and launches == []

    def test_busy_retry_sleeps_between_attempts(self, monkeypatch, tmp_path):
        """busy 重试按 2s 间隔进行（sleep 被调用 ≥3 次——重试 3 次的凭据）。"""
        sleeps = []
        self._patch(monkeypatch, tmp_path, ["busy"])
        monkeypatch.setattr(cl.time, "sleep", lambda s: sleeps.append(s))  # 覆盖 _patch 的 no-op
        ok, _msg = cl.ensure_chrome_cdp(port=9222, profile_dir=str(tmp_path / "prof"))
        assert ok is True
        assert sleeps.count(2) >= 3, \
            f"busy 重试应 ≥3 次 × 2s，实际 sleeps={sleeps!r}"

    def test_refused_with_chrome_processes_kills_and_relaunches(self, monkeypatch, tmp_path):
        """refused（端口确实没人听）+ Chrome 进程存在 → 才走 kill+重启。"""
        chrome_proc = {"pid": 222, "cmd": ".../google-chrome --remote-debugging-port=9222",
                       "port": 9222}
        kills, launches, _ = self._patch(
            monkeypatch, tmp_path, ["refused"], found_processes=[chrome_proc])
        ok, _msg = cl.ensure_chrome_cdp(port=9222, profile_dir=str(tmp_path / "prof"),
                                        auto_restart=True)
        assert len(kills) == 1, "refused + 进程存在必须触发杀（既有重启路径保持）"
        assert len(launches) == 1, "杀后必须重新拉起 Chrome"

    def test_refused_without_processes_launches_fresh_no_kill(self, monkeypatch, tmp_path):
        """refused + 无 Chrome 进程 → 直接新启（零杀）。"""
        kills, launches, _ = self._patch(
            monkeypatch, tmp_path, ["refused", "refused", "up"])
        ok, msg = cl.ensure_chrome_cdp(port=9222, profile_dir=str(tmp_path / "prof"))
        assert ok is True
        assert kills == []
        assert len(launches) == 1

    def test_entry_up_short_circuits_without_lock(self, monkeypatch, tmp_path):
        """锁外入口 up 即用：不进锁、不探测多次（既有行为保持）。"""
        kills, launches, acquire_calls = self._patch(
            monkeypatch, tmp_path, ["up"])
        ok, msg = cl.ensure_chrome_cdp(port=9222, profile_dir=str(tmp_path / "prof"))
        assert ok is True
        assert acquire_calls == [], "入口 up 即用，不得获取 profile 锁"
        assert kills == [] and launches == []

    def test_entry_busy_goes_into_lock_path(self, monkeypatch, tmp_path):
        """入口 busy（现状语义=不可用）→ 进锁路径；锁内 busy 语义兜底成功、不杀。"""
        chrome_proc = {"pid": 333, "cmd": ".../google-chrome --remote-debugging-port=9222",
                       "port": 9222}
        kills, launches, acquire_calls = self._patch(
            monkeypatch, tmp_path, ["busy"], found_processes=[chrome_proc])
        ok, msg = cl.ensure_chrome_cdp(port=9222, profile_dir=str(tmp_path / "prof"))
        assert len(acquire_calls) == 1, "入口 busy 必须进锁路径（现状语义）"
        assert ok is True and kills == [] and launches == []

    def test_lock_free_reuse_message_on_up(self, monkeypatch, tmp_path):
        """锁内二次检查 up（其他进程刚启动）→ 复用成功（既有消息语义保持）。"""
        kills, launches, _ = self._patch(monkeypatch, tmp_path, ["busy", "up"])
        ok, msg = cl.ensure_chrome_cdp(port=9222, profile_dir=str(tmp_path / "prof"))
        assert ok is True and "其他进程已启动" in msg
        assert kills == [] and launches == []
