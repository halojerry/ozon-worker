#!/usr/bin/env python3
"""T1 重命令串行闸 + lock_utils 提炼回归测试（fix/skill-concurrency-v1 批1）。

锁定契约（docs/PLAN-skill-concurrency-and-win-cookie-import-v1.md §A3 T1）：
  1. lock_utils.try_acquire/release/file_lock——跨平台文件锁双态语义
     （可拿 / 被占返回 None；flock 语义下同进程异 fd 互斥）；
  2. 锁文件内容不因并发 open 被截断（占用方信息对后来者可读——报错展示的前提）；
  3. 重命令串行闸：被占 fail-fast exit 4 且 stderr 含占用方信息；
     --wait 排队（持锁方释放后放行）；--force 直通；进程内 _gate_held 嵌套放行；
  4. Windows msvcrt 分支调用路径（非 Windows 平台 mock msvcrt 验证）；
  5. 真子进程持锁 + 受闸 CLI 命令 → exit 4（子进程集成，闸在重资源之前生效——
     cmd_seller 闸先于 fetch_seller_analysis，不会拉起 Chrome/网络）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_heavy_gate.py -q
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import cli
from scripts.lib import lock_utils


# ── 公共工具 ─────────────────────────────────────────────────────────────────

_HOLDER_SCRIPT = """
import json, os, sys, time
from datetime import datetime, timezone
sys.path.insert(0, {skill_root!r})
from scripts.lib import lock_utils
fd = lock_utils.try_acquire({lock!r}, timeout=5.0)
if fd is None:
    sys.exit(9)
lock_utils.write_holder_info(fd, json.dumps({{
    "pid": os.getpid(),
    "cmd": {holder!r},
    "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
}}, ensure_ascii=False))
try:
    time.sleep({hold_seconds})
finally:
    lock_utils.release(fd)
"""


def _spawn_holder(lock_path: Path, hold_seconds: float, holder: str = "pytest-holder") -> subprocess.Popen:
    """起一个真子进程持有锁 hold_seconds 秒后释放（flock 同进程异 fd 互斥，持锁方必须是异进程）。"""
    script = _HOLDER_SCRIPT.format(
        skill_root=str(Path(__file__).resolve().parent.parent),
        lock=str(lock_path),
        hold_seconds=hold_seconds,
        holder=holder,
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_lock_taken(lock_path: Path, proc: subprocess.Popen, timeout: float = 10.0) -> None:
    """轮询等待持锁子进程真正拿到锁（写出了占用方信息）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"持锁子进程提前退出 rc={proc.returncode}")
        try:
            if lock_utils.read_holder_info(lock_path) is not None:
                return
        except Exception:
            pass
        time.sleep(0.05)
    raise AssertionError("持锁子进程未在限时内拿到锁")


def _fake_args(**kw) -> argparse.Namespace:
    kw.setdefault("command", "discover")
    kw.setdefault("wait", False)
    kw.setdefault("force", False)
    return argparse.Namespace(**kw)


@pytest.fixture()
def gate_lock(tmp_path, monkeypatch):
    """把串行闸锁路径重定向到 tmp（单测隔离，不碰真实 skill/data/locks）。"""
    p = tmp_path / "heavy_cdp.lock"
    monkeypatch.setattr(cli, "HEAVY_LOCK_PATH", p)
    return p


# ── ① lock_utils 双态语义 ────────────────────────────────────────────────────

class TestLockUtils:
    def test_try_acquire_double_state(self, tmp_path):
        p = tmp_path / "t.lock"
        fd1 = lock_utils.try_acquire(p, timeout=0.0)
        assert fd1 is not None, "空闲锁必须可拿"
        fd2 = lock_utils.try_acquire(p, timeout=0.0)
        assert fd2 is None, "被占锁必须返回 None（不抛异常）"
        lock_utils.release(fd1)
        fd3 = lock_utils.try_acquire(p, timeout=0.0)
        assert fd3 is not None, "释放后必须可再拿"
        lock_utils.release(fd3)

    def test_blocking_wait_times_out(self, tmp_path):
        p = tmp_path / "t.lock"
        fd1 = lock_utils.try_acquire(p, timeout=0.0)
        t0 = time.monotonic()
        fd2 = lock_utils.try_acquire(p, timeout=0.4)
        assert fd2 is None
        assert time.monotonic() - t0 >= 0.3, "timeout 语义=阻塞等待上限"
        lock_utils.release(fd1)

    def test_file_lock_ctx_both_modes(self, tmp_path):
        p = tmp_path / "t.lock"
        with lock_utils.file_lock(p, timeout=0.0) as fd:
            assert fd is not None, "blocking 模式空闲时拿到 fd"
            with lock_utils.file_lock(p, timeout=0.0, blocking=False) as fd2:
                assert fd2 is None, "blocking=False 被占立即 yield None"
        with lock_utils.file_lock(p, timeout=0.0) as fd3:
            assert fd3 is not None, "退出上下文必须自动释放"

    def test_holder_info_survives_failed_open(self, tmp_path):
        """并发 open 不得截断锁文件——占用方信息是后来者报错的依据（a+ 语义）。"""
        p = tmp_path / "t.lock"
        fd1 = lock_utils.try_acquire(p, timeout=0.0)
        assert fd1 is not None
        lock_utils.write_holder_info(fd1, "discover (PID 123)")
        fd2 = lock_utils.try_acquire(p, timeout=0.0)
        assert fd2 is None
        info = lock_utils.read_holder_info(p)
        assert info is not None and "discover" in info, "被拒方必须仍能读到占用方信息"
        lock_utils.release(fd1)

    def test_read_holder_info_missing_and_empty(self, tmp_path):
        p = tmp_path / "t.lock"
        assert lock_utils.read_holder_info(p) is None, "锁文件不存在 → None"
        p.write_text("", encoding="utf-8")
        assert lock_utils.read_holder_info(p) is None, "空锁文件 → None 不抛"

    def test_cli_read_lock_holder_json_and_garbage(self, tmp_path):
        """cli 侧人话化读：合法 JSON 出「cmd (PID x…)」；脏数据降级不抛。"""
        import json as _json
        p = tmp_path / "h.lock"
        assert "未知占用者" in cli.read_lock_holder(p)
        p.write_text(_json.dumps({"pid": 42, "cmd": "discover",
                                  "started_at": "2026-09-15T00:00:00+00:00"}),
                     encoding="utf-8")
        shown = cli.read_lock_holder(p)
        assert "discover" in shown and "42" in shown
        p.write_text("not-json", encoding="utf-8")
        assert "not-json" in cli.read_lock_holder(p), "脏数据 → 如实降级展示原文"

    def test_read_lock_holder_weird_started_at_types(self, tmp_path):
        """A1 审查 M3：started_at 形态不可控（dict/list/数字）→ 内层 except 含
        TypeError/ValueError，任何脏 started_at 都不得抛、时长降级为空。"""
        import json as _json
        p = tmp_path / "w.lock"
        for bad in ({"x": 1}, ["l"], 12345, "not-a-date", 3.14):
            p.write_text(_json.dumps({"pid": 1, "cmd": "c", "started_at": bad}),
                         encoding="utf-8")
            out = cli.read_lock_holder(p)
            assert out.startswith("c (PID 1") and "已运行" not in out, \
                f"脏 started_at {bad!r} 必须降级为无时长展示，实际: {out!r}"


# ── ② Windows msvcrt 分支（非 Windows 平台 mock 调用路径） ──────────────────

class TestWindowsBranch:
    def _fake_msvcrt(self, fail_first=False):
        calls = []

        def locking(fd, mode, nbytes):
            calls.append((fd, mode, nbytes))
            if fail_first and not calls[1:]:
                raise OSError("contention")
        m = type(sys)("msvcrt")
        m.locking = locking
        return m, calls

    def test_windows_acquire_and_release_path(self, tmp_path, monkeypatch):
        import scripts.lib.lock_utils as lu
        fake, calls = self._fake_msvcrt()
        monkeypatch.setattr(lu.platform, "system", lambda: "Windows")
        monkeypatch.setattr(lu, "msvcrt", fake)
        p = tmp_path / "w.lock"
        fd = lu.try_acquire(p, timeout=1.0)
        assert fd is not None
        assert calls and calls[0][1] == lu._LOCK_NBEX and calls[0][2] == 1, \
            "Windows 分支必须 msvcrt.locking(fd, LK_NBLCK, 1)"
        lu.release(fd)
        assert calls[-1][1] == lu._LOCK_UN, "release 必须 msvcrt.locking(fd, LK_UNLCK, 1)"

    def test_windows_contention_returns_none(self, tmp_path, monkeypatch):
        import scripts.lib.lock_utils as lu
        fake, _calls = self._fake_msvcrt(fail_first=True)
        monkeypatch.setattr(lu.platform, "system", lambda: "Windows")
        monkeypatch.setattr(lu, "msvcrt", fake)
        p = tmp_path / "w2.lock"
        assert lu.try_acquire(p, timeout=0.0) is None, "Windows 被占+超时 0 → None"


# ── ③ 串行闸单测（gate_lock 重定向 tmp） ─────────────────────────────────────

class TestHeavyGate:
    def test_gate_acquires_writes_and_releases(self, gate_lock):
        """空闲时过闸：写占用方信息、放行执行、结束后释放。"""
        ran = []

        @cli._heavy_gate
        def fake_cmd(args):
            ran.append(1)
            # 闸持有期间锁文件应有本进程占用方信息
            info = lock_utils.read_holder_info(gate_lock)
            assert info is not None and "discover" in info
            return 0

        assert fake_cmd(_fake_args()) == 0
        assert ran
        assert lock_utils.try_acquire(gate_lock, timeout=0.0) is not None, \
            "命令结束后闸必须已释放"

    def test_gate_fail_fast_exit4(self, gate_lock, capsys):
        proc = _spawn_holder(gate_lock, hold_seconds=6)
        try:
            _wait_lock_taken(gate_lock, proc)

            @cli._heavy_gate
            def fake_cmd(args):
                raise AssertionError("闸被占时不得进入命令体")

            t0 = time.monotonic()
            with pytest.raises(SystemExit) as ei:
                fake_cmd(_fake_args())
            assert ei.value.code == 4, "缺省 fail-fast 必须 exit 4"
            assert time.monotonic() - t0 < 3.0, "fail-fast 不应长等"
            err = capsys.readouterr().err
            assert "pytest-holder" in err and "--wait" in err and "--force" in err, \
                f"报错须含占用方与出路提示，实际: {err!r}"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_gate_wait_queues_then_proceeds(self, gate_lock):
        proc = _spawn_holder(gate_lock, hold_seconds=2)
        try:
            _wait_lock_taken(gate_lock, proc)
            ran = []

            @cli._heavy_gate
            def fake_cmd(args):
                ran.append(time.monotonic())
                return 0

            t0 = time.monotonic()
            assert fake_cmd(_fake_args(wait=True)) == 0
            assert ran, "--wait 排队后持锁方释放必须放行"
            assert ran[0] - t0 >= 1.0, "放行时机应晚于持锁方释放（~2s）"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_gate_wait_heartbeat_on_poll(self, gate_lock, capsys, monkeypatch):
        """--wait 轮询空转一拍 → 心跳含占用方信息。"""
        seq = {"n": 0}

        def fake_try_acquire(path, timeout=30.0):
            seq["n"] += 1
            return None if seq["n"] == 1 else object()

        monkeypatch.setattr(cli.lock_utils, "try_acquire", fake_try_acquire)
        monkeypatch.setattr(cli, "read_lock_holder", lambda path: "discover (PID 999)")
        fd = cli._acquire_heavy_lock_waiting("discover")
        assert fd is not None
        err = capsys.readouterr().err
        assert "PID 999" in err, f"心跳须含占用方信息，实际: {err!r}"

    def test_gate_force_bypasses(self, gate_lock):
        proc = _spawn_holder(gate_lock, hold_seconds=4)
        try:
            _wait_lock_taken(gate_lock, proc)
            ran = []

            @cli._heavy_gate
            def fake_cmd(args):
                ran.append(1)
                return 0

            t0 = time.monotonic()
            assert fake_cmd(_fake_args(force=True)) == 0
            assert ran, "--force 必须跳闸直通"
            assert time.monotonic() - t0 < 1.0, "--force 不应等待"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_gate_creates_missing_lock_dir(self, tmp_path, monkeypatch):
        """首装锁目录（data/locks/）不存在 → 惰性 mkdir，闸不得误报「被占」exit 4。"""
        deep = tmp_path / "locks" / "heavy_cdp.lock"  # 父目录刻意不存在
        monkeypatch.setattr(cli, "HEAVY_LOCK_PATH", deep)
        ran = []

        @cli._heavy_gate
        def fake_cmd(args):
            ran.append(1)
            return 0

        assert fake_cmd(_fake_args()) == 0, "锁目录缺失时闸必须照常放行（惰性建目录）"
        assert ran and deep.exists()

    def test_gate_mkdir_failure_reports_env_problem_not_holder(self, tmp_path, monkeypatch,
                                                              capsys):
        """A1 审查 M2：锁目录不可创建（父路径是文件）→ 报「锁目录不可创建」，
        绝不误导为「闸被占」（用户去找根本不存在的占用方）。"""
        blocker = tmp_path / "afile"
        blocker.write_text("not a dir", encoding="utf-8")
        monkeypatch.setattr(cli, "HEAVY_LOCK_PATH", blocker / "locks" / "heavy_cdp.lock")

        @cli._heavy_gate
        def fake_cmd(args):
            raise AssertionError("目录建不出来时不得进入命令体")

        with pytest.raises(SystemExit) as ei:
            fake_cmd(_fake_args())
        assert ei.value.code == 4
        err = capsys.readouterr().err
        assert "锁目录不可创建" in err, f"必须如实报环境问题，实际: {err!r}"
        assert "闸被占" not in err, "不得误报为闸被占"

    def test_gate_held_flag_nested_passthrough(self, gate_lock):
        """进程内已持闸（_gate_held）→ 嵌套调用直接放行（防 flock 自死锁）。"""
        proc = _spawn_holder(gate_lock, hold_seconds=4)
        try:
            _wait_lock_taken(gate_lock, proc)
            cli._gate_held = True
            try:
                ran = []

                @cli._heavy_gate
                def fake_cmd(args):
                    ran.append(1)
                    return 0

                assert fake_cmd(_fake_args()) == 0
                assert ran, "_gate_held 置位时嵌套调用必须放行"
            finally:
                cli._gate_held = False
        finally:
            proc.terminate()
            proc.wait(timeout=5)


# ── ④ 真子进程集成：受闸 CLI 命令被外部持锁 → exit 4 ─────────────────────────

class TestSubprocessIntegration:
    def test_gated_cli_command_exit4_under_real_lock(self):
        """真子进程持「真实路径」锁 + 另一子进程跑受闸命令 cmd_seller → exit 4。

        cmd_seller 的闸（装饰器）先于 fetch_seller_analysis 执行——不会拉起
        Chrome/网络，是最快退出的受闸命令路径。
        ⚠️ 前提（A1 审查 M4）：本用例写**真实** data/locks/heavy_cdp.lock——
        若开发机恰有受闸命令（discover/graph/follow/seller…）在跑，本用例会
        假失败（它才是真占用方）。重跑前确认无受闸命令即可。
        """
        real_lock = cli.HEAVY_LOCK_PATH
        real_lock.parent.mkdir(parents=True, exist_ok=True)
        holder = _spawn_holder(real_lock, hold_seconds=10, holder="integration-holder")
        try:
            _wait_lock_taken(real_lock, holder)
            victim = subprocess.run(
                [
                    sys.executable, "-c",
                    "import sys; sys.path.insert(0, {root!r}); "
                    "from scripts import cli; "
                    "p = cli.build_arg_parser(); "
                    "args = p.parse_args(['seller', '--seller-id', '123']); "
                    "sys.exit(cli.cmd_seller(args))".format(
                        root=str(Path(__file__).resolve().parent.parent)
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert victim.returncode == 4, \
                f"受闸命令须 exit 4，实际 {victim.returncode}；stderr={victim.stderr!r}"
            assert "integration-holder" in victim.stderr, \
                f"报错须含占用方信息；stderr={victim.stderr!r}"
            assert "--wait" in victim.stderr and "--force" in victim.stderr
        finally:
            holder.terminate()
            holder.wait(timeout=5)

    def test_gated_cli_main_exit4_under_real_lock(self):
        """A1 审查 M1：经 cli.main() 真实路径（argparse 全链）的 exit 4 集成用例。

        与上方 cmd_seller 直调版的差别：main() 走 build_arg_parser().parse_args()
        → _preflight_runtime → _silent_update_check → args.func(=装饰后 cmd) 全链，
        闸语义在完整 CLI 入口上验证。子进程设 SKILL_AUTO_UPDATE=0（简报口径），
        并在子进程内旁路 updater.check_update——源码布局下 0 档恰走 check_update
        真网络（5s 超时），stub 掉保证测试零网络。
        ⚠️ 前提（M4）：写真实 data/locks/heavy_cdp.lock——开发机恰有受闸命令在跑
        会假失败，重跑前确认无受闸命令。
        """
        real_lock = cli.HEAVY_LOCK_PATH
        real_lock.parent.mkdir(parents=True, exist_ok=True)
        holder = _spawn_holder(real_lock, hold_seconds=10, holder="main-path-holder")
        try:
            _wait_lock_taken(real_lock, holder)
            victim = subprocess.run(
                [
                    sys.executable, "-c",
                    "import sys; sys.path.insert(0, {root!r}); "
                    "sys.argv = ['cli.py', 'seller', '--seller-id', '123']; "
                    "from scripts.lib import updater; "
                    "updater.check_update = lambda *a, **k: None; "  # 零网络
                    "from scripts import cli; "
                    "sys.exit(cli.main())".format(
                        root=str(Path(__file__).resolve().parent.parent)
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ, "SKILL_AUTO_UPDATE": "0"},
            )
            assert victim.returncode == 4, \
                f"经 cli.main() 的受闸命令须 exit 4，实际 {victim.returncode}；" \
                f"stderr={victim.stderr!r}"
            assert "main-path-holder" in victim.stderr, \
                f"报错须含占用方信息；stderr={victim.stderr!r}"
            assert "--wait" in victim.stderr and "--force" in victim.stderr
        finally:
            holder.terminate()
            holder.wait(timeout=5)
