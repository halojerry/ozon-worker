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
import subprocess
import time
from pathlib import Path

from scripts.lib.utils import safe_unlink

# Cross-platform file locking
if platform.system() == 'Windows':
    import msvcrt
    _LOCK_NBEX = 0x00000002  # _LK_NBLCK: 非阻塞尝试（循环控制等待时机）
    _LOCK_UN = 0x00000000
else:
    import fcntl

logger = logging.getLogger(__name__)

# ── 默认 CDP 端口 ──
CDP_PORT = 9222
CDP_HOST = "127.0.0.1"

# ── 平台默认 Chrome profile 路径 ──
def _default_profile_dir() -> Path:
    """返回工具 Chrome 的独立 profile 目录（跨平台, 不碰用户 Chrome）。

    v0.28.6: 改为独立 profile —— 解决「用户 Chrome 占用默认 profile → 单实例
    冲突 → 工具 Chrome 反复启动失败/被杀重启」（用户电脑实测复现）。

    放 skill 包 data/browser/ 下（与 PID 文件同目录）而非用户主目录:
    - macOS ~/Library/Application Support/ 受 TCC 保护, 沙箱/未签名进程写入被拒
    - data/ 随 skill 更新保留(updater 保留 data/), 登录态不丢
    - 打包分发自带 data/ 空目录, 运行时自动创建

    PR-4: 统一为 profiles/1688/default（与 cli._chrome_profile_dir / service._profile_dir
    一致）—— 消除双轨（旧 data/browser/profile）导致的重复 Chrome 实例 + 登录态错位。
    """
    return Path(__file__).resolve().parent.parent.parent / "data" / "browser" / "profiles" / "1688" / "default"


# ── Chrome 可执行文件查找 ──
def _find_chrome_executable() -> str | None:
    """跨平台查找 Chrome 可执行文件。

    统一策略（v0.35.x）：先复用 service.find_browser_executable 的富实现
    （mdfind 多浏览器/运行中浏览器探测/Playwright 兜底/自动安装），失败再回退
    本函数精简逻辑（5 名字 + 平台路径 + mdfind）。消除「service 判定有浏览器但
    chrome_launcher 启动不了」的分裂。

    ⚠️ 必须 lazy import service：service.py 在多个函数内 import 本模块，
    顶层 import 会循环。
    """
    try:
        from scripts.capabilities.browser_probe.service import (
            _candidate_browser_paths,
            find_browser_executable,
        )
    except Exception:
        _candidate_browser_paths = None
        find_browser_executable = None

    if find_browser_executable is not None:
        try:
            # ⚠️ 禁用自动安装（Phase 4）：chrome_launcher 路径只探测不装浏览器。
            # service.find_browser_executable 找不到浏览器时默认触发
            # _auto_install_browser（下载 300MB Playwright Chromium），
            # 在无 Chrome 环境（Docker/CI/慢网络）会让每个命令挂起数分钟。
            # 自动安装保留给显式入口（enrich_product_with_cdp 的
            # check_cdp_prerequisites / install-browser 命令）。
            from scripts.capabilities.browser_probe import service as _service_mod
            _orig_auto = _service_mod._auto_install_browser
            _service_mod._auto_install_browser = lambda: False
            try:
                resolved = find_browser_executable(None)
                if resolved:
                    return resolved
            finally:
                _service_mod._auto_install_browser = _orig_auto
        except Exception:
            pass

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
            # macOS 非标准安装位置（~/Applications 等用户级安装）兜底
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            os.path.expanduser("~/Applications/Chromium.app/Contents/MacOS/Chromium"),
            os.path.expanduser("~/Library/Application Support/Google/Chrome"),
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

    # 与 service._candidate_browser_paths 共享同一候选列表（含 Playwright Chromium）
    if _candidate_browser_paths is not None:
        try:
            candidates.extend(_candidate_browser_paths())
        except Exception:
            pass

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
                ["ps", "-axo", "pid,command"],
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


# ── Chrome profile 并发锁 ──
# Q15: 原 tempdir 全局锁 + flock LOCK_NB（非阻塞）有两个问题：
#   1) 第二个并发进程拿不到锁直接抛 BlockingIOError（未捕获）→ ensure_chrome_cdp 崩溃
#   2) 锁与 profile 无关——不同 profile 无谓串行
# 改为：per-profile 锁（data/browser/.profile-{name}.lock，仿 updater.py
# data/.update.lock 模式）+ 阻塞等待带超时 + 超时优雅降级。

def _profile_lock_path(profile_dir: Path) -> Path:
    """per-profile 启动锁路径（与 .launched_chrome.pid 同目录）。

    标准结构 data/browser/profiles/<name>/<sub> → .profile-{name}.lock
    （如 profiles/1688/default → .profile-1688.lock）; 其他路径取末两段
    （父目录-目录名），保证不同 profile 互不冲突。
    """
    profile_dir = Path(profile_dir)
    parts = profile_dir.parts
    if len(parts) >= 3 and parts[-3] == "profiles":
        name = parts[-2]
    else:
        name = "-".join(parts[-2:]) if len(parts) >= 2 else profile_dir.name
    return Path(__file__).resolve().parent.parent.parent / "data" / "browser" / f".profile-{name}.lock"


def _try_acquire_lock(lock_path: Path, timeout: float = 30.0) -> int | None:
    """阻塞获取排他锁, 最长等待 timeout 秒。

    成功 → 返回打开的锁文件 fd; 超时/失败 → None（调用方降级, 不抛异常）。
    进程退出时 OS 自动释放锁, 锁文件残留无害。
    """
    try:
        fd = open(lock_path, "w")
    except OSError:
        return None
    deadline = time.monotonic() + timeout
    while True:
        try:
            if platform.system() == "Windows":
                msvcrt.locking(fd.fileno(), _LOCK_NBEX, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except (OSError, BlockingIOError):
            if time.monotonic() >= deadline:
                try:
                    fd.close()
                except OSError:
                    pass
                return None
            time.sleep(0.1)


def _release_lock(fd) -> None:
    """释放锁并关闭 fd。失败静默（OS 在进程退出时兜底释放）。"""
    if fd is None:
        return
    try:
        if platform.system() == "Windows":
            fd.seek(0)
            msvcrt.locking(fd.fileno(), _LOCK_UN, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fd.close()
    except Exception:
        pass


# ── 主入口 ──
def ensure_chrome_cdp(
    port: int = CDP_PORT,
    auto_restart: bool = True,
    profile_dir: str | None = None,
) -> tuple[bool, str]:
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
    # ⚠️ v0.28.3: 不再因"缺 --remote-allow-origins"杀重启——_has_remote_allow_origins
    # 依赖 ps/PowerShell 命令行解析, 任何解析失败/截断都会误判 → 每次命令杀+重启
    # Chrome(用户反馈"一直重复开启浏览器", Windows/Mac 均有发生)。工具自启实例
    # 参数必然正确(下方写死); 用户环境的 Chrome 无 CDP 不在此分支。CDP 可用即信任,
    # WebSocket 连接 403 由调用方自然报错, 绝不杀浏览器。
    if _is_cdp_available(port):
        return True, f"CDP 已就绪 (port {port})"

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

    # Use a per-profile file lock to prevent two processes from launching Chrome
    # with the same profile simultaneously. Double-check CDP after acquiring the
    # lock to avoid duplicate launches.
    lock_fd = _try_acquire_lock(_profile_lock_path(profile_path))
    if lock_fd is None:
        return False, (
            "另一个进程正在启动 Chrome（获取 profile 启动锁超时）。"
            "请稍后重试，或关闭残留的 Chrome 进程后重试。"
        )
    try:
        # Double-check CDP after acquiring lock — another process may have launched Chrome
        # ⚠️ v0.28.3: 同初始检查, 不再因 allow-origins 误判杀重启
        if _is_cdp_available(port):
            return True, f"Chrome CDP 就绪 (port {port}, 其他进程已启动)"

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
            # v0.28.6: 禁 Crashpad——它固定写用户 Chrome 目录(~/Library/Application
            # Support/Google/Chrome/Crashpad), 不随 --user-data-dir, 沙箱/权限受限
            # 环境下导致启动崩溃; 真实环境也污染用户 Chrome 目录
            "--disable-crash-reporter",
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

        launched_proc = None
        try:
            if platform.system() == "Windows":
                # Windows: CREATE_NEW_PROCESS_GROUP 让 Chrome 独立运行
                launched_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                launched_proc = subprocess.Popen(
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
                # v0.28.3: 记录本次启动的 PID, 供命令出口 close_tool_chrome() 精确关闭
                if launched_proc:
                    _write_launched_pid(launched_proc.pid)
                return True, f"Chrome 已启动，CDP 就绪 (port {port})"
            # 每 10 秒检查启动的进程是否还活着（用 Popen 对象, 不依赖 ps——
            # macOS 沙箱/Windows 命令行截断都会让 ps 解析误判）
            if i > 0 and i % 10 == 0 and launched_proc is not None and launched_proc.poll() is not None:
                return False, "Chrome 进程已退出（可能崩溃）"

        return False, "Chrome 已启动但 CDP 未就绪（等待 40 秒超时）"
    finally:
        _release_lock(lock_fd)


def get_cdp_url(port: int = CDP_PORT) -> str:
    """返回 CDP URL"""
    return f"http://{CDP_HOST}:{port}"


def _tool_pid_file() -> Path:
    """工具自启 Chrome 的 PID 记录文件(skill 根/data/browser/)"""
    return Path(__file__).resolve().parent.parent.parent / "data" / "browser" / ".launched_chrome.pid"


def _write_launched_pid(pid: int) -> None:
    """记录工具自启 Chrome 的 PID(跨平台文本文件, 供命令出口关闭)"""
    try:
        pid_file = _tool_pid_file()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(pid))
    except Exception as e:
        logger.warning("写工具 Chrome PID 文件失败: %s", e)


def close_tool_chrome() -> None:
    """关闭工具自启的 Chrome(显式调用, 命令出口不再自动调用)。

    ⚠️ v0.29.x: 默认「独立 profile + 常驻」—— 命令结束不关闭 Chrome,
    登录态常驻复用(下次命令 CDP 可用即直接使用)。close_tool_chrome 仅在
    用户需要显式关闭时调用(如手动清理残留实例)。
    ⚠️ v0.28.3: 解决"用户每次使用都重复开启浏览器"——工具自启实例
    (独立 profile + debug 端口)用完即关, 不影响用户日常 Chrome。
    仅当本进程启动过 Chrome(PID 文件存在)才动作; 复用已有 CDP 时不关。
    """
    try:
        pid_file = _tool_pid_file()
        if not pid_file.exists():
            return
        pid = int(pid_file.read_text().strip() or 0)
        safe_unlink(pid_file)  # 先删文件, 防重复执行
        if pid <= 0:
            return
        logger.info("关闭工具 Chrome (PID %d)", pid)
        if platform.system() == "Windows":
            # /T 杀进程树(Chrome 主进程+渲染子进程)
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=15,
            )
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            for _ in range(8):
                time.sleep(0.5)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except Exception as e:
        logger.warning("关闭工具 Chrome 失败: %s", e)


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
