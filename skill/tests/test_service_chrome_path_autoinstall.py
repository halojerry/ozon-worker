#!/usr/bin/env python3
"""service.py 文件链修复单测（Q1 CHROME_PATH Phase 0 + Q6 pip --break-system-packages）。

Q1: find_browser_executable 增加 Phase 0 —— 读取 CHROME_PATH 环境变量显式指定浏览器，
    优先级在 explicit 参数之后、已知路径扫描（Phase 1）之前。服务器/CI 无默认浏览器时
    用 CHROME_PATH 精确指定，跳过 mdfind / 自动下载（Phase 2~4）的慢路径。
Q6: _auto_install_browser 的 pip install 加 --break-system-packages —— Debian/Ubuntu 等
    PEP 668 externally-managed-environment 系统 Python 拒绝 pip 安装，缺此 flag 自动
    安装直接失败（首次运行被阻断）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_service_chrome_path_autoinstall.py -q
    cd skill && .venv314/bin/python tests/test_service_chrome_path_autoinstall.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 预导入 service：模块体依赖 scripts._const 创建真实 data/ 目录。若在 mock 上下文内
# 导入，os.path.exists/Path.is_dir 被 mock 后 _const 的 mkdir(exist_ok=True) 守卫
# 失效抛 FileExistsError（与 test_chrome_find_unified.py 同款）。
import scripts.capabilities.browser_probe.service  # noqa: F401  isort:skip


def _mocked(which, existing, home):
    """构造 macOS 全 mock 上下文：platform/shutil.which/os.path.exists/Path.*/
    subprocess.run/service 自动安装与 playwright 全部受控，测试零真实进程。

    - which: shutil.which 的 side_effect（命令名 → 路径）
    - existing: 视为「已存在」的绝对路径集合（os.path.exists + Path.exists 共用）
    - home: Path.home() 与 $HOME 的假主目录
    """
    existing = set(existing)

    def _os_exists(p):
        return str(p) in existing

    def _path_exists(self):
        return str(self) in existing

    return [
        mock.patch("platform.system", return_value="Darwin"),
        mock.patch("shutil.which", side_effect=which),
        mock.patch("os.path.exists", side_effect=_os_exists),
        mock.patch.object(Path, "exists", _path_exists),
        mock.patch.object(Path, "is_dir", return_value=False),
        mock.patch.object(Path, "home", return_value=Path(home)),
        mock.patch("subprocess.run", return_value=mock.Mock(stdout="", returncode=0)),
        mock.patch("scripts.capabilities.browser_probe.service._auto_install_browser", return_value=False),
        mock.patch("scripts.capabilities.browser_probe.service._playwright_chromium_paths", return_value=[]),
        mock.patch("glob.glob", return_value=[]),
    ]


def _enter(mocks):
    stack = ExitStack()
    for m in mocks:
        stack.enter_context(m)
    return stack


# ═══════════════════════════════════════════════════════════════════════
# Q1: CHROME_PATH 环境变量 Phase 0
# ═══════════════════════════════════════════════════════════════════════

def test_chrome_path_env_wins_over_known_paths():
    """CHROME_PATH 指向存在的可执行文件 → 优先返回（即使 Phase 1 已知路径也能命中）。"""
    home = tempfile.mkdtemp()
    chrome_path = "/opt/chrome/custom-chrome"
    with mock.patch.dict(os.environ, {"HOME": home, "CHROME_PATH": chrome_path}):
        with _enter(_mocked(
            which=lambda name: "/usr/local/bin/google-chrome" if name == "google-chrome" else None,
            existing={chrome_path},
            home=home,
        )):
            from scripts.capabilities.browser_probe.service import find_browser_executable
            result = find_browser_executable(None)
    assert result == chrome_path


def test_chrome_path_env_resolves_via_which():
    """CHROME_PATH 为 PATH 内命令名 → shutil.which 命中并返回。"""
    home = tempfile.mkdtemp()
    expected = "/usr/local/bin/my-chrome"
    with mock.patch.dict(os.environ, {"HOME": home, "CHROME_PATH": "my-chrome"}):
        with _enter(_mocked(
            which=lambda name: expected if name == "my-chrome" else None,
            existing=set(),
            home=home,
        )):
            from scripts.capabilities.browser_probe.service import find_browser_executable
            result = find_browser_executable(None)
    assert result == expected


def test_chrome_path_env_invalid_raises_config_error():
    """CHROME_PATH 指向不存在路径且不在 PATH → ConfigError（显式配置错误不静默忽略）。"""
    home = tempfile.mkdtemp()
    with mock.patch.dict(os.environ, {"HOME": home, "CHROME_PATH": "/nonexistent/chrome"}):
        with _enter(_mocked(which=lambda name: None, existing=set(), home=home)):
            from scripts.capabilities.browser_probe.service import ConfigError
            from scripts.capabilities.browser_probe.service import find_browser_executable
            try:
                find_browser_executable(None)
                raise AssertionError("CHROME_PATH 无效时应抛 ConfigError")
            except ConfigError:
                pass


def test_chrome_path_env_unset_falls_through():
    """CHROME_PATH 未设置 → 走原有 Phase 1 已知路径逻辑，行为不变。"""
    home = tempfile.mkdtemp()
    expected = "/usr/local/bin/google-chrome"
    with mock.patch.dict(os.environ, {"HOME": home}):
        os.environ.pop("CHROME_PATH", None)  # 隔离宿主环境可能的 CHROME_PATH
        with _enter(_mocked(
            which=lambda name: expected if name in ("google-chrome", "chrome") else None,
            existing=set(),
            home=home,
        )):
            from scripts.capabilities.browser_probe.service import find_browser_executable
            result = find_browser_executable(None)
    assert result == expected


def test_explicit_param_wins_over_chrome_path():
    """find_browser_executable(explicit=...) 显式参数优先级高于 CHROME_PATH。"""
    home = tempfile.mkdtemp()
    explicit = "/opt/custom/explicit-chrome"
    chrome_path = "/opt/chrome/env-chrome"
    with mock.patch.dict(os.environ, {"HOME": home, "CHROME_PATH": chrome_path}):
        with _enter(_mocked(
            which=lambda name: None,
            existing={explicit, chrome_path},
            home=home,
        )):
            from scripts.capabilities.browser_probe.service import find_browser_executable
            result = find_browser_executable(explicit)
    assert result == explicit


# ═══════════════════════════════════════════════════════════════════════
# Q6: _auto_install_browser pip --break-system-packages（PEP 668）
# ═══════════════════════════════════════════════════════════════════════

def _run_auto_install(mock_run):
    """执行 _auto_install_browser：mock playwright 未安装 + subprocess.run 受控。

    - mock_run: subprocess.run 的 side_effect（记录调用 / 按序抛错模拟失败）
    - find_browser_executable 固定返回真值 → Step 3 复扫成功 → 整体返回 True
    """
    from scripts.capabilities.browser_probe.service import _auto_install_browser

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright":
            raise ImportError("No module named 'playwright'")
        return real_import(name, *args, **kwargs)

    with mock.patch("builtins.__import__", side_effect=fake_import), \
         mock.patch("subprocess.run", side_effect=mock_run), \
         mock.patch("scripts.capabilities.browser_probe.service.find_browser_executable", return_value="/x/chrome"):
        return _auto_install_browser()


def test_auto_install_pip_uses_break_system_packages():
    """pip install playwright（镜像优先）必须带 --break-system-packages（PEP 668 系统 Python）。"""
    calls = []

    def mock_run(args, **kwargs):
        calls.append(args)
        return mock.Mock(stdout="", returncode=0)

    ok = _run_auto_install(mock_run)
    assert ok is True
    pip_call = calls[0]
    assert "pip" in pip_call and "install" in pip_call
    assert "playwright" in pip_call
    assert "--break-system-packages" in pip_call
    # flag 属于 pip install 命令本身（位于 -m pip install 之后）
    assert pip_call.index("--break-system-packages") > pip_call.index("install")


def test_auto_install_pip_fallback_also_uses_flag():
    """镜像安装失败 → 无镜像 fallback 的 pip install 同样带 --break-system-packages。"""
    calls = []

    def mock_run(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, args)  # 镜像 pip install 失败
        return mock.Mock(stdout="", returncode=0)

    ok = _run_auto_install(mock_run)
    assert ok is True
    assert len(calls) >= 2
    for call in calls[:2]:  # 两条 pip install 路径都必须带 flag
        if "pip" in call and "install" in call:
            assert "--break-system-packages" in call


def _main() -> int:
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"✅ {name}")
        except Exception as exc:
            failed += 1
            print(f"❌ {name}: {type(exc).__name__}: {exc}")
    total = len(fns)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
