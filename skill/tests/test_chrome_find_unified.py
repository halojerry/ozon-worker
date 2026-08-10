#!/usr/bin/env python3
"""chrome_launcher._find_chrome_executable 与 service.find_browser_executable 一致性单测。

背景：ak_1688_client.enrich_product_with_cdp 用 service 的 find_browser_executable 判定
「有浏览器」，但 chrome_launcher.ensure_chrome_cdp 用自己的 _find_chrome_executable 启动，
两条逻辑分裂导致「service 判定有浏览器但 chrome_launcher 启动不了」。

修复策略：_find_chrome_executable 先委托 service 富实现，失败再回退自身逻辑
（5 名字 + 平台路径 + mdfind + macOS ~/Applications 兜底）。

运行：
    cd skill && .venv314/bin/python tests/test_chrome_find_unified.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 预导入 service：其模块体依赖 scripts._const 创建真实 data/ 目录。若在 mock
# 上下文内导入，os.path.exists/Path.is_dir 被 mock 后 _const 的
# mkdir(exist_ok=True) 守卫失效抛 FileExistsError。
import scripts.capabilities.browser_probe.service  # noqa: F401  isort:skip



def _mocked(which, existing, home):
    """构造 macOS 全 mock 上下文：platform/shutil.which/os.path.exists/Path.*/
    subprocess.run/service 自动安装与 playwright 全部受控，测试零真实进程。

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


def test_service_result_is_used():
    """service 富实现（PATH 里的 brave）找到浏览器 → _find_chrome_executable 返回同一路径。"""
    home = tempfile.mkdtemp()
    with mock.patch.dict(os.environ, {"HOME": home}):
        with _enter(_mocked(
            which=lambda name: "/usr/local/bin/brave" if name == "brave" else None,
            existing=set(),
            home=home,
        )):
            from scripts.lib.chrome_launcher import _find_chrome_executable
            result = _find_chrome_executable()
    assert result == "/usr/local/bin/brave"


def test_macos_home_applications_fallback():
    """/Applications 无 Chrome 但 ~/Applications 有 → 找到（macOS 非标准安装兜底）。"""
    home = tempfile.mkdtemp()
    expected = f"{home}/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    with mock.patch.dict(os.environ, {"HOME": home}):
        with _enter(_mocked(which=lambda name: None, existing={expected}, home=home)):
            from scripts.lib.chrome_launcher import _find_chrome_executable
            result = _find_chrome_executable()
    assert result == expected


def test_returns_none_when_no_browser():
    """PATH/平台路径/mdfind/service 全部找不到 → None。"""
    home = tempfile.mkdtemp()
    with mock.patch.dict(os.environ, {"HOME": home}):
        with _enter(_mocked(which=lambda name: None, existing=set(), home=home)):
            from scripts.lib.chrome_launcher import _find_chrome_executable
            result = _find_chrome_executable()
    assert result is None


def test_probe_disables_auto_install():
    """无浏览器环境：_find_chrome_executable 不触发 service 自动安装（防 300MB Playwright 下载挂起）。

    锁死 chrome_launcher._find_chrome_executable 委托 service 时的禁用补丁
    （L84-86 临时把 service._auto_install_browser 替换为 lambda: False）。
    若该补丁被删，service.find_browser_executable 的 Phase 4 会调用真实
    _auto_install_browser（spy 被调用）→ assert_not_called 失败。
    """
    home = tempfile.mkdtemp()
    auto_install = mock.Mock(return_value=False)
    with mock.patch.dict(os.environ, {"HOME": home}):
        with _enter(_mocked(which=lambda name: None, existing=set(), home=home)):
            import scripts.capabilities.browser_probe.service as svc
            svc._auto_install_browser = auto_install  # spy：记录是否被调用
            try:
                from scripts.lib.chrome_launcher import _find_chrome_executable
                result = _find_chrome_executable()
            finally:
                # chrome_launcher 的 finally 已还原 spy；此处 del 让 mock.patch
                # 退出时恢复原始模块属性（避免残留）
                del svc._auto_install_browser
    assert result is None
    auto_install.assert_not_called()  # 禁用补丁被删时此断言失败


def _mocked_windows(which, existing, program_files):
    """构造 Windows 全 mock 上下文（ProgramFiles 路径命中场景）。"""
    existing = set(existing)

    def _os_exists(p):
        return str(p) in existing

    def _path_exists(self):
        return str(self) in existing

    return [
        mock.patch("platform.system", return_value="Windows"),
        mock.patch("shutil.which", side_effect=which),
        mock.patch("os.path.exists", side_effect=_os_exists),
        mock.patch.object(Path, "exists", _path_exists),
        mock.patch.object(Path, "is_dir", return_value=False),
        mock.patch("subprocess.run", return_value=mock.Mock(stdout="", returncode=0)),
        mock.patch("scripts.capabilities.browser_probe.service._auto_install_browser", return_value=False),
        mock.patch("scripts.capabilities.browser_probe.service._playwright_chromium_paths", return_value=[]),
        mock.patch("glob.glob", return_value=[]),
    ]


def test_windows_program_files_path():
    """Windows ProgramFiles 下 Google/Chrome/Application/chrome.exe → 找到。"""
    pf = r"C:\Program Files"
    expected = str(Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe")
    with mock.patch.dict(os.environ, {
        "ProgramFiles": pf,
        "ProgramFiles(x86)": r"C:\Program Files (x86)",
        "LOCALAPPDATA": r"C:\Users\test\AppData\Local",
    }):
        with _enter(_mocked_windows(which=lambda name: None, existing={expected}, program_files=pf)):
            from scripts.lib.chrome_launcher import _find_chrome_executable
            result = _find_chrome_executable()
    assert result == expected


def test_service_and_legacy_consistency():
    """同一环境：service.find_browser_executable 与 _find_chrome_executable 返回一致。"""
    home = tempfile.mkdtemp()
    expected = "/usr/local/bin/google-chrome"
    with mock.patch.dict(os.environ, {"HOME": home}):
        with _enter(_mocked(
            which=lambda name: expected if name in ("google-chrome", "chrome") else None,
            existing=set(),
            home=home,
        )):
            from scripts.lib.chrome_launcher import _find_chrome_executable
            from scripts.capabilities.browser_probe.service import find_browser_executable
            svc = find_browser_executable(None)
            legacy = _find_chrome_executable()
    assert svc == legacy == expected


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
