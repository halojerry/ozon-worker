"""
Chrome CDP 自动启动管理器

用户零配置：自动检测系统、找到 Chrome、处理已有进程、用正确参数启动。
macOS / Windows / Linux 全平台支持。

使用用户默认 Chrome profile，保留登录态。
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

# Cross-platform file locking
if platform.system() == 'Windows':
    import msvcrt
    _LOCK_EX = 0x00000001  # _LK_LOCK equivalent
    _LOCK_UN = 0x00000000
else:
    import fcntl

logger = logging.getLogger(__name__)

# ── 默认 CDP 端口 ──
CDP_PORT = 9222
CDP_HOST = "127.0.0.1"

# ── 平台默认 Chrome profile 路径 ──
def _default_profile_dir() -> Optional[Path]:
    """返回用户默认 Chrome profile 目录（跨平台）"""
    system = platform.system()
    if system == "Darwin":
        p = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    elif system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            p = Path(local) / "Google" / "Chrome" / "User Data"
        else:
            p = Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
    else:  # Linux
        p = Path.home() / ".config" / "google-chrome"
    return p if p.exists() else None


# ── Chrome 可执行文件查找 ──
def _find_chrome_executable() -> Optional[str]:
    """跨平台查找 Chrome 可执行文件"""
    system = platform.system()

    # 1. 常见名称（PATH 搜索）
    for name in [
        "google-chrome", "google-chrome-stable", "chromium",
        "chromium-browser", "chrome",
    ]:
        found = shutil.which(name)
        if found:
            return found

    # 2. 平台特定路径
    candidates = []
    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    elif system == "Windows":
        for env in ["ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"]:
            base = os.environ.get(env, "")
            if base:
                candidates.extend([
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                    os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                ])
    else:  # Linux
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/snap/bin/chromium",
        ]

    for p in candidates:
        if os.path.exists(p):
            return p

    # 3. macOS Spotlight 搜索
    if system == "Darwin":
        try:
            result = subprocess.run(
                ["mdfind", "kMDItemCFBundleIdentifier == 'com.google.Chrome'"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    exe = os.path.join(line.strip(), "Contents", "MacOS", "Google Chrome")
                    if os.path.exists(exe):
                        return exe
        except Exception:
            pass

    return None


def _is_cdp_available(port: int = CDP_PORT) -> bool:
    """检测 CDP 端口是否可用"""
    import urllib.request
    try:
        url = f"http://{CDP_HOST}:{port}/json/version"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return "Browser" in data
    except Exception:
        return False


def _find_chrome_processes() -> list[dict]:
    """查找所有 Chrome 进程，返回 [{pid, cmd, port}]"""
    system = platform.system()
    processes = []

    try:
        if system == "Windows":
            # ⚠️ v0.22: Win11 弃用 wmic → 改用 PowerShell Get-CimInstance。
            # 旧 wmic 在 Win11 返回空 → 误判"无 Chrome" → 每次启动新实例（频繁开新窗口）。
            ps_script = (
                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, timeout=10,
            )
            import json as _json
            try:
                raw = result.stdout.strip()
                if raw:
                    data = _json.loads(raw)
                    if isinstance(data, dict):
                        data = [data]
                    for proc in data:
                        pid = int(proc.get("ProcessId") or 0)
                        cmd = str(proc.get("CommandLine") or "")
                        if pid and cmd:
                            port = None
                            m = re.search(r"--remote-debugging-port=(\d+)", cmd)
                            if m:
                                port = int(m.group(1))
                            processes.append({"pid": pid, "cmd": cmd, "port": port})
            except Exception as _ps_e:
                logger.debug("PowerShell 进程解析失败: %s", _ps_e)
        else:
            result = subprocess.run(
                ["ps", "-axo", f"pid,command"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.split("\n"):
                line = line.strip()
                if not line:
                    continue
                lower = line.lower()
                # ⚠️ 不能用裸 "chrome" 匹配：Electron 应用（ZCode/Doubao/Docker
                # Desktop 等）的 helper 进程路径含 "Electron Framework/.../Chrome
                # Helper"，会被误判为 Chrome 并误杀（会杀掉正在运行的 Agent 自身）。
                # 只匹配真正的 Google Chrome / Chromium（路径含 "google chrome" 或
                # "chromium"，且不在 Electron 框架内）。
                if "google chrome" not in lower and "chromium" not in lower:
                    continue
                if "electron" in lower:
                    continue
                if "grep" in lower or "ps -axo" in lower:
                    continue
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                try:
                    pid = int(parts[0])
                except ValueError:
                    continue
                cmd = parts[1]
                port = None
                if "--remote-debugging-port=" in cmd:
                    m = re.search(r"--remote-debugging-port=(\d+)", cmd)
                    if m:
                        port = int(m.group(1))
                processes.append({"pid": pid, "cmd": cmd, "port": port})
    except Exception as e:
        logger.debug("Failed to find Chrome processes: %s", e)

    return processes


def _kill_chrome_processes(reason: str = "", port: int | None = None) -> bool:
    """终止 Chrome 进程。返回是否有进程被终止。

    ⚠️ v0.14 D5: 仅杀带 --remote-debugging-port 的实例（port 匹配），
    旧代码杀所有 Chrome/Chromium 进程，会误杀用户日常无 debug 端口的 Chrome 窗口。
    """
    processes = _find_chrome_processes()
    if not processes:
        return False

    # 只杀目标端口实例（未指定 port 时也仅杀带 debug 参数的，绝不误杀普通 Chrome）
    targets = []
    for proc in processes:
        if port is not None:
            if proc.get("port") == port:
                targets.append(proc)
        else:
            if proc.get("port"):
                targets.append(proc)
    if not targets:
        logger.debug("无目标 Chrome 实例（带 --remote-debugging-port 的）可杀")
        return False

    logger.info("Terminating %d Chrome process(es)%s",
                len(targets), f" ({reason})" if reason else "")

    system = platform.system()
    killed = False
    for proc in targets:
        try:
            pid = proc["pid"]
            if system == "Windows":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=5)
            else:
                os.kill(pid, signal.SIGTERM)
            killed = True
            logger.debug("  Killed PID %d", pid)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    if killed:
        # Wait for processes to fully exit (poll, max 5s)
        for _ in range(10):
            time.sleep(0.5)
            if not _find_chrome_processes():
                break
        else:
            # SIGTERM 无效，尝试 SIGKILL（非 Windows）
            if platform.system() != "Windows":
                for proc in targets:
                    try:
                        os.kill(proc["pid"], signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                time.sleep(0.5)

    return killed


def _has_remote_allow_origins(port: int = CDP_PORT) -> bool:
    """检查已运行的 Chrome 是否带了 --remote-allow-origins=* 参数"""
    processes = _find_chrome_processes()
    for proc in processes:
        if proc.get("port") == port:
            return "--remote-allow-origins" in proc.get("cmd", "")
    return False


# ── 主入口 ──
def ensure_chrome_cdp(
    port: int = CDP_PORT,
    auto_restart: bool = True,
    profile_dir: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    确保 Chrome CDP 可用。用户零配置调用。

    流程:
    1. CDP 已可用 + 有 --remote-allow-origins → 直接返回成功
    2. CDP 已可用但缺少参数 → 需要重启 Chrome
    3. Chrome 在运行但没有 CDP → 需要重启 Chrome
    4. Chrome 没运行 → 直接启动

    Args:
        port: CDP 端口 (默认 9222)
        auto_restart: 是否自动重启 Chrome（终止已有进程）
        profile_dir: 自定义 profile 路径（默认使用系统默认 Chrome profile）

    Returns:
        (success, message)
    """
    # 0. CDP 已经可用？
    if _is_cdp_available(port):
        if _has_remote_allow_origins(port):
            return True, f"CDP 已就绪 (port {port})"
        else:
            # CDP 可用但缺少 --remote-allow-origins，WebSocket 会 403
            if auto_restart:
                logger.info("CDP running without --remote-allow-origins, restarting Chrome")
                _kill_chrome_processes("缺少 --remote-allow-origins 参数")
            else:
                return False, (
                    f"Chrome CDP 已启动但缺少 --remote-allow-origins=* 参数，"
                    f"WebSocket 连接会被拒绝。请重启 Chrome。"
                )

    # 1. 找 Chrome 可执行文件
    chrome_exe = _find_chrome_executable()
    if not chrome_exe:
        return False, "未找到 Chrome 浏览器，请安装 Google Chrome"

    # 2. 确定 profile 目录
    if profile_dir:
        profile_path = Path(profile_dir)
    else:
        profile_path = _default_profile_dir()

    # ⚠️ v0.22: profile 目录不存在时自动创建——缺失会导致不带 --user-data-dir
    # 启动全新浏览器（无登录态 + 每次新窗口），Windows 上体验尤其明显
    try:
        profile_path.mkdir(parents=True, exist_ok=True)
    except Exception as _mk_e:
        logger.debug("profile 目录创建失败（将继续）: %s", _mk_e)

    # Use a file lock to prevent two processes from launching Chrome simultaneously.
    # Double-check CDP after acquiring lock to avoid duplicate launches.
    lock_path = Path(tempfile.gettempdir()) / 'skill-chrome-launch.lock'
    lock_fd = None
    try:
        lock_fd = open(lock_path, 'w')
        if platform.system() == 'Windows':
            msvcrt.locking(lock_fd.fileno(), _LOCK_EX, 1)
        else:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        # Double-check CDP after acquiring lock — another process may have launched Chrome
        if _is_cdp_available(port):
            if _has_remote_allow_origins(port):
                return True, f"Chrome CDP 就绪 (port {port}, 其他进程已启动)"
            elif auto_restart:
                _kill_chrome_processes("缺少 --remote-allow-origins 参数")
            else:
                return False, (
                    f"Chrome CDP 已启动但缺少 --remote-allow-origins=* 参数，"
                    f"WebSocket 连接会被拒绝。请重启 Chrome。"
                )

        # 3. 检查是否有 Chrome 在运行（无 CDP）
        existing = _find_chrome_processes()
        if existing:
            if auto_restart:
                _kill_chrome_processes("需要启用 CDP 远程调试")
            else:
                pids = [str(p["pid"]) for p in existing[:3]]
                return False, (
                    f"Chrome 已在运行 (PID: {', '.join(pids)}) 但未启用 CDP。"
                    f"请关闭 Chrome 后重试，或设置 auto_restart=True"
                )

        # 4. 构建启动命令
        cmd = [
            chrome_exe,
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--no-pings",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            # ⚠️ v0.14 E5: 禁用弹窗拦截（1688 图搜/登录跳转的 window.open 弹窗）
            # 本 Chrome 是专用抓取实例（独立 profile + debug 端口），不影响用户日常 Chrome。
            # 与 image_search 的 window.open 覆盖（JS 层当前 tab 导航）双保险，无需手动设置站点放行。
            "--disable-popup-blocking",
        ]

        # 使用默认 profile（保留登录态）
        if profile_path and profile_path.exists():
            cmd.append(f"--user-data-dir={profile_path}")

        # 5. 启动 Chrome
        logger.info("Launching Chrome: %s", chrome_exe)
        logger.info("  Profile: %s", profile_path)
        logger.info("  CDP port: %d", port)

        try:
            if platform.system() == "Windows":
                # Windows: CREATE_NEW_PROCESS_GROUP 让 Chrome 独立运行
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except Exception as e:
            return False, f"启动 Chrome 失败: {e}"

        # 6. 等待 CDP 就绪（默认 profile 首次启动可能需要 30+ 秒）
        for i in range(40):
            time.sleep(1)
            if _is_cdp_available(port):
                logger.info("Chrome CDP ready after %d seconds", i + 1)
                return True, f"Chrome 已启动，CDP 就绪 (port {port})"
            # 每 10 秒检查 Chrome 进程是否还活着
            if i > 0 and i % 10 == 0 and not _find_chrome_processes():
                return False, "Chrome 进程已退出（可能崩溃）"

        return False, "Chrome 已启动但 CDP 未就绪（等待 40 秒超时）"
    finally:
        if lock_fd:
            try:
                if platform.system() == 'Windows':
                    lock_fd.seek(0)
                    msvcrt.locking(lock_fd.fileno(), _LOCK_UN, 1)
                else:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            lock_fd.close()


def get_cdp_url(port: int = CDP_PORT) -> str:
    """返回 CDP URL"""
    return f"http://{CDP_HOST}:{port}"


def get_chrome_info() -> dict:
    """返回当前 Chrome 状态信息（用于诊断）"""
    info = {
        "chrome_found": False,
        "chrome_path": "",
        "cdp_available": False,
        "cdp_port": CDP_PORT,
        "has_remote_allow_origins": False,
        "profile_dir": "",
        "processes": [],
    }

    chrome_exe = _find_chrome_executable()
    if chrome_exe:
        info["chrome_found"] = True
        info["chrome_path"] = chrome_exe

    info["cdp_available"] = _is_cdp_available(CDP_PORT)

    profile = _default_profile_dir()
    if profile:
        info["profile_dir"] = str(profile)

    processes = _find_chrome_processes()
    info["processes"] = [
        {"pid": p["pid"], "port": p["port"],
         "has_origins": "--remote-allow-origins" in p.get("cmd", "")}
        for p in processes[:5]
    ]

    if info["cdp_available"]:
        info["has_remote_allow_origins"] = _has_remote_allow_origins(CDP_PORT)

    return info
