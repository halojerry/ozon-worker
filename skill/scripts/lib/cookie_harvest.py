#!/usr/bin/env python3
"""跨浏览器 cookie 采集导入 — 扫描本机已登录浏览器的 1688/Ozon cookie 注入工具 Chrome。

v0.69 新特性（用户反馈：日常浏览器里明明登录过，为什么还要在工具窗口再登录一次？）。

背景：skill 的 Chrome 是独立自动化 profile（data/browser/profiles/1688/default；
Chrome 130+ 禁止在用户默认数据目录开 remote debugging），用户日常浏览器的登录态
对工具不可见。本模块把「别的浏览器里已有的登录 cookie」搬进来：

    扫描源（macOS）→ 解密 → 域名/过期过滤 → Storage.setCookies 注入工具 profile
    → 现有登录检测验证（probe_alibaba_login / seller sc_company_id）

支持源（macOS）：
- Chrome / Edge / Brave：Cookies sqlite + Keychain「<Browser> Safe Storage」解密
  （AES-128-CBC，PBKDF2-HMAC-SHA1 1003 次迭代，v10/v11 前缀）——解密经 openssl
  子进程完成，零新 Python 依赖。Keychain 首次访问会弹授权框，用户拒绝 → 跳过该源。
- Firefox：cookies.sqlite 明文（stdlib sqlite3）。
- Safari：Cookies.binarycookies（需「完全磁盘访问权限」，读不到自动跳过）。

Windows（feat/win-cookie-import-v1 起 per-source 可用性模型）：
- Chrome/Edge/Brave：Chrome 127+ app-bound 加密不做第三方解密（红线：IElevator COM
  伪装 / SYSTEM 提权 = infostealer 手法，AV 必报）——Windows 主通道是**副本目录
  CDP 接管**（B-T4，`_harvest_chromium_via_takeover`）：最小复制集拷到临时目录 →
  真实浏览器 exe 以非默认 --user-data-dir 启动（Chrome 自己经 elevation service
  解密 v20，本模块零解密代码/零提权）→ CDP 读明文 → 域过滤注入。
  kill-switch：SKILL_DISABLE_TAKEOVER=1 回退 unsupported_source（逃生门）。
- Firefox：cookies.sqlite 明文（stdlib sqlite3），路径 per-platform
  （%APPDATA%\\Mozilla\\Firefox / ~/.mozilla/firefox，profiles.ini 解析）——全平台可用。
- Safari：仅 macOS（Cookies.binarycookies）。
- --paste 手动粘贴 Cookie 头（CLI 通道，跨平台永久兜底，见 paste_and_import）。

目标域（紧凑集，只搬验证体系认得的登录态）：
- 1688.com：登录检测判据 cookie2/__cn_logon__（readiness.probe_alibaba_login）
- ozon.ru / ozone.ru：卖家后台会话 sc_company_id（seller.ozon.ru 是 ozon.ru 子域）
  不搬 taobao.com 等邻域 cookie——probe 永远不会为它们记成功，纯噪音。

风险：目标站点风控（DataDome）可能对异浏览器搬运的会话弹挑战——同机设备指纹
同源风险较低；失败自动回落现有人工登录流程。本模块所有失败都不 raise、不阻塞、
不改变既有降级语义。

纪律：本文件明文（compile.py AUX_FILES，对 OS 对抗性易碎代码参照 stealth.py 先例）。
"""
from __future__ import annotations

import configparser
import json
import logging
import os
import re
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CDP_URL = "http://127.0.0.1:9222"

TARGET_DOMAIN_SUFFIXES = ("1688.com", "ozon.ru", "ozone.ru")

# 冷却：readiness 自动兜底每探针 1 小时最多尝试一次（防 Keychain 授权框循环弹）
AUTO_FALLBACK_COOLDOWN_SECONDS = 3600

# ── 浏览器源定义（macOS 路径 + Keychain 服务名）──

CHROMIUM_BROWSERS: dict[str, dict[str, str]] = {
    "chrome": {
        "keychain": "Chrome Safe Storage",
        "base": "~/Library/Application Support/Google/Chrome",
    },
    "edge": {
        "keychain": "Microsoft Edge Safe Storage",
        "base": "~/Library/Application Support/Microsoft Edge",
    },
    "brave": {
        "keychain": "Brave Safe Storage",
        "base": "~/Library/Application Support/BraveSoftware/Brave-Browser",
    },
}
# macOS Firefox profile 目录（v0.69 语义保持：直接扫该目录子目录）。
# Windows/Linux 走 _firefox_root()（%APPDATA%\Mozilla\Firefox / ~/.mozilla/firefox）
# + profiles.ini 解析，见 _firefox_profile_dirs。
FIREFOX_BASE = "~/Library/Application Support/Firefox/Profiles"
SAFARI_COOKIES = ("~/Library/Containers/com.apple.Safari/Data/Library/Cookies/"
                  "Cookies.binarycookies")
ALL_SOURCES = ("chrome", "edge", "brave", "firefox", "safari")


# ═══════════ 通用工具 ═══════════


def _domain_matches(host: str) -> bool:
    host = (host or "").lstrip(".").lower()
    return any(host == s or host.endswith("." + s) for s in TARGET_DOMAIN_SUFFIXES)


def _cookie_dict(host: str, name: str, value: str, path: str,
                 expires: float, secure: bool, http_only: bool) -> dict | None:
    """统一 cookie 形状 + 域名/有效期/非空过滤。过期与 session(expires=0) 跳过。"""
    if not name or not value or not _domain_matches(host):
        return None
    # session cookie（expires=0/负）随源浏览器退出即死，搬过来只会是垃圾
    if not expires or expires <= time.time():
        return None
    domain = host if host.startswith(".") else f".{host}"
    return {
        "name": str(name),
        "value": str(value),
        "domain": domain,
        "path": path or "/",
        "expires": float(expires),
        "secure": bool(secure),
        "httpOnly": bool(http_only),
    }


# ═══════════ Chromium 系（Chrome/Edge/Brave）═══════════


def _keychain_safe_storage(service: str) -> bytes | None:
    """读 Keychain「<Browser> Safe Storage」密码；用户拒绝/服务缺失 → None。"""
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", service],
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("keychain %s 查询异常: %s", service, exc)
        return None
    if proc.returncode != 0:
        logger.info("Keychain %s 不可读（rc=%d，用户拒绝或无此服务）",
                    service, proc.returncode)
        return None
    pw = proc.stdout.decode("utf-8", "replace").rstrip("\n")
    return pw.encode("utf-8") if pw else None


def _derive_chromium_key(password: bytes) -> bytes:
    return hashlib_pbkdf2_sha1(password)


def hashlib_pbkdf2_sha1(password: bytes) -> bytes:
    """Chromium v10 派生：PBKDF2-HMAC-SHA1(pw, 'saltysalt', 1003, 16)。"""
    import hashlib
    return hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, dklen=16)


def _openssl_aes_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """openssl 子进程 AES-128-CBC 解密（PKCS#7 去填充）——零新 Python 依赖。"""
    proc = subprocess.run(
        ["openssl", "enc", "-aes-128-cbc", "-d",
         "-K", key.hex(), "-iv", iv.hex()],
        input=ciphertext, capture_output=True, timeout=15,
    )
    if proc.returncode != 0:
        raise ValueError(f"openssl 解密失败 rc={proc.returncode}")
    return proc.stdout


def _decrypt_chromium_value(encrypted: bytes, key: bytes) -> bytes:
    if not encrypted:
        return b""
    if encrypted[:3] in (b"v10", b"v11"):
        return _openssl_aes_cbc_decrypt(key, b" " * 16, encrypted[3:])
    return encrypted  # 未加密旧库明文兜底


def _chrome_ts_to_unix(expires_utc: int) -> float:
    """Chrome 时间戳（1601-01-01 起 µs）→ unix 秒；0（session）原样返回 0。"""
    if not expires_utc:
        return 0
    return expires_utc / 1_000_000 - 11644473600


def _list_chromium_cookie_dbs(base: Path) -> list[Path]:
    """枚举 profile 下的 Cookies 库（Default/Profile N；跳过 System/Guest）。"""
    if not base.is_dir():
        return []
    dbs = []
    for profile in sorted(base.iterdir()):
        if not profile.is_dir():
            continue
        if profile.name.lower() in ("system profile", "guest profile"):
            continue
        db = profile / "Cookies"
        if db.is_file():
            dbs.append(db)
    return dbs


def _read_chromium_db(db_path: Path, key: bytes) -> list[dict]:
    """读单个 Cookies 库：先拷到临时目录再开（源库被运行中的浏览器锁着）。

    ⚠️ Chrome 130+（meta version ≥ 24）：加密明文 = SHA256(host_key)(32B) + value
    ——解密后必须剥掉 32 字节域哈希（实测 Chrome 152：不剥则 value 带二进制哈希
    前缀，CDP Storage.setCookies 直接拒「Invalid cookie fields」）。以哈希自校验
    判定是否剥离，天然兼容旧版无哈希库。
    """
    import hashlib as _hashlib
    cookies: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="cookie_harvest_") as tmpdir:
        tmp_db = Path(tmpdir) / "Cookies"
        shutil.copy2(db_path, tmp_db)
        # WAL 里可能有未 checkpoint 的最新登录态，一并拷贝让 sqlite 回放
        for side in ("-wal", "-shm"):
            src = Path(str(db_path) + side)
            if src.is_file():
                shutil.copy2(src, Path(str(tmp_db) + side))
        try:
            conn = sqlite3.connect(str(tmp_db))
            rows = conn.execute(
                "SELECT host_key, name, encrypted_value, value, path, "
                "expires_utc, is_secure, is_httponly FROM cookies"
            ).fetchall()
            conn.close()
        except Exception as exc:
            logger.debug("读 %s 失败: %s", db_path, exc)
            return []
    for host, name, enc_value, plain_value, path, exp, secure, httponly in rows:
        raw_value = (plain_value or "").encode("utf-8", "replace")
        if not raw_value and enc_value:
            try:
                raw = _decrypt_chromium_value(enc_value, key)
            except Exception:
                continue  # 解密失败（key 不对/损坏）→ 跳过该条
            digest = _hashlib.sha256(str(host or "").encode("utf-8")).digest()
            if len(raw) > 32 and raw[:32] == digest:
                raw_value = raw[32:]  # Chrome 130+：剥域哈希
            elif raw and not any(b < 0x20 for b in raw):
                raw_value = raw  # 旧版无哈希
            else:
                continue  # 完整性校验失败（哈希不匹配且含控制字节）→ 跳过
        value = raw_value.decode("utf-8", "replace")
        if any(ord(ch_) < 0x20 for ch_ in value):
            continue  # 控制字符 → CDP 必拒，防御性跳过
        c = _cookie_dict(host, name, value, path,
                         _chrome_ts_to_unix(int(exp or 0)), secure, httponly)
        if c:
            cookies.append(c)
    return cookies


def _harvest_chromium(source: str, profile_dir_name: str = "Default") -> dict[str, Any]:
    if sys.platform == "win32":
        # B-T4：Windows 主通道 = 副本目录 CDP 接管（解密主体是 Chrome 自己，
        # 本模块零解密代码/零提权；见 _harvest_chromium_via_takeover）。
        if os.environ.get("SKILL_DISABLE_TAKEOVER"):
            # kill-switch 逃生门：接管通道整体关闭，回退 per-source 闸语义
            return {"source": source, "status": "unsupported_source",
                    "message": "接管通道已被 SKILL_DISABLE_TAKEOVER 关闭"
                               "（可用 --paste 手动粘贴 Cookie 头）",
                    "cookies": []}
        return _harvest_chromium_via_takeover(source, profile_dir_name)
    if sys.platform != "darwin":
        # B-T1 per-source 平台闸：Linux Keyring/KWallet 亦未实现（Windows 走上面
        # 接管通道）；手动兜底走 --paste。
        return {"source": source, "status": "unsupported_source",
                "message": "当前平台暂不支持 Chromium 系源自动解密"
                           "（可用 --paste 手动粘贴 Cookie 头）",
                "cookies": []}
    cfg = CHROMIUM_BROWSERS[source]
    base = Path(os.path.expanduser(cfg["base"]))
    dbs = _list_chromium_cookie_dbs(base)
    if not dbs:
        return {"source": source, "status": "not_installed", "cookies": []}
    password = _keychain_safe_storage(cfg["keychain"])
    if password is None:
        return {"source": source, "status": "keychain_denied", "cookies": []}
    key = hashlib_pbkdf2_sha1(password)
    cookies: list[dict] = []
    for db in dbs:
        cookies.extend(_read_chromium_db(db, key))
    return {"source": source, "status": "ok", "cookies": cookies}


# ═══════════ Windows 副本目录 CDP 接管通道（B-T4，win32 Chromium 主通道）═══════════
#
# 原理（方案 §B2 W-B）：Chrome 127+ app-bound 的 DPAPI blob 不绑数据目录位置——
# 把 `Local State` + 目标 profile 的 `Network/Cookies*`（含 -wal/-shm）最小复制集拷到
# 临时目录，用**真实浏览器 exe** 以 `--user-data-dir=<临时非默认目录>` 启动（Chrome
# 自己经 elevation service 解密 v20，本模块零解密代码/零提权），CDP 读明文 cookie
# → 过滤目标域 → 走现有 inject_cookies 注入。Chrome 136 起「默认用户目录禁 remote
# debugging」被非默认临时目录天然规避。
#
# 红线（改本节前必读）：
# - 零解密：绝不出现 DPAPI/CryptUnprotectData/IElevator/ctypes win32 调用——
#   解密主体永远是浏览器进程自己；
# - 不写用户浏览器目录：源侧只读拷贝，一切写入只发生在我们的临时目录；
# - cookie 明文不落日志：异常 message 只带异常类型名，不带 CDP 返回内容；
# - 动态端口 bind 0 取号，绝不占 9222 主工具实例；
# - finally 清理全路径兜底：浏览器进程（含子进程树）+ 临时目录，任何失败不 raise。

TAKEOVER_PORT_BANNED = 9222   # 主工具实例端口，接管通道绝不占用
TAKEOVER_CDP_WAIT_S = 20.0    # 单形态（headless/有头）CDP 就绪等待上限

# exe 候选（自持轻量表，参照 chrome_launcher._find_chrome_executable 与 B2 探针写法；
# 与探针的单一事实源纪律不要求跨诊断/生产两域强行合并）。接管要用**同一浏览器**的
# exe 解自己浏览器的 app-bound 数据，故按源逐个找，不跨浏览器兜底。
TAKEOVER_EXE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "chrome": ("Google/Chrome/Application/chrome.exe",),
    "edge": ("Microsoft/Edge/Application/msedge.exe",),
    "brave": ("BraveSoftware/Brave-Browser/Application/brave.exe",),
}
TAKEOVER_EXE_BASE_ENVS = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")

# 源 User Data 根（%LOCALAPPDATA% 相对路径；与 B2 探针 CHROMIUM_USER_DATA_REL 同口径）
TAKEOVER_USER_DATA_REL: dict[str, tuple[str, ...]] = {
    "chrome": ("Google", "Chrome", "User Data"),
    "edge": ("Microsoft", "Edge", "User Data"),
    "brave": ("BraveSoftware", "Brave-Browser", "User Data"),
}

# 接管临时目录根（<skill>/data/tmp；与探针 data/tmp 同区，cleanup --temp 可清）
_DATA_TMP = Path(__file__).resolve().parent.parent.parent / "data" / "tmp"


def _takeover_fail(browser: str, message: str) -> dict[str, Any]:
    """接管失败统一出口（不 raise，调用方降级提示 --paste）。"""
    return {"source": browser, "status": "takeover_failed",
            "message": message, "cookies": []}


def _takeover_find_exe(browser: str) -> str | None:
    """Windows 下找该浏览器的真实 exe；找不到 → None（调用方报 takeover_no_browser）。"""
    rels = TAKEOVER_EXE_CANDIDATES.get(browser)
    if not rels:
        return None
    for env in TAKEOVER_EXE_BASE_ENVS:
        base = os.environ.get(env)
        if not base:
            continue
        for rel in rels:
            candidate = Path(base).joinpath(*rel.split("/"))
            if candidate.is_file():
                return str(candidate)
    return None


def _takeover_user_data_root(browser: str) -> Path | None:
    """源 User Data 根（%LOCALAPPDATA% 标准路径）；LOCALAPPDATA 缺失 → None。"""
    rel = TAKEOVER_USER_DATA_REL.get(browser)
    if not rel:
        return None
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    return Path(local).joinpath(*rel)


def _takeover_profiles(user_data: Path) -> list[dict[str, str]]:
    """枚举源 User Data 下的用户 profile（口径与 B2 探针一致：名字过滤排除
    System/Guest）。Local State 的 profile.info_cache 优先（User Data 下的
    组件目录——Crashpad 等——不是 profile）；info_cache 缺失/为空兜底扫目录。"""
    info_cache: dict = {}
    local_state = user_data / "Local State"
    if local_state.is_file():
        try:
            data = json.loads(local_state.read_text(encoding="utf-8"))
            info_cache = (data.get("profile") or {}).get("info_cache") or {}
        except Exception as exc:
            logger.debug("takeover Local State 解析失败: %s", type(exc).__name__)
    profiles: list[dict[str, str]] = []
    if info_cache:
        for name, meta in info_cache.items():
            name = str(name)
            if name.lower() in ("system profile", "guest profile"):
                continue
            display = ""
            if isinstance(meta, dict):
                display = str(meta.get("name") or "")
            profiles.append({"dir": name, "display_name": display})
        return profiles
    if user_data.is_dir():
        for entry in sorted(user_data.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.lower() in ("system profile", "guest profile"):
                continue
            profiles.append({"dir": entry.name, "display_name": ""})
    return profiles


def _takeover_resolve_profile(user_data: Path, wanted: str | None) -> str:
    """--browser-profile 值（info_cache 显示名或目录名，均可）→ profile 目录名。
    空/None → Default；目录名直取（含磁盘存在性）；显示名经 info_cache 解析；
    未知名原样透传（复制步骤自然失败成 takeover_failed，用户名拼写错误可见）。"""
    wanted = (wanted or "").strip()
    if not wanted:
        return "Default"
    if (user_data / wanted).is_dir():
        return wanted
    for p in _takeover_profiles(user_data):
        if p["dir"].lower() == wanted.lower():
            return p["dir"]
    for p in _takeover_profiles(user_data):
        if wanted.lower() == (p["display_name"] or "").lower() and p["display_name"]:
            return p["dir"]
    return wanted


def _takeover_new_workdir() -> Path:
    """接管临时目录：data/tmp/takeover_<ts>_<pid>/（同秒同 pid 重跑加 ns 后缀防撞）。"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    wd = _DATA_TMP / f"takeover_{ts}_{os.getpid()}"
    if wd.exists():
        wd = _DATA_TMP / f"takeover_{ts}_{os.getpid()}_{time.time_ns() % 10 ** 6}"
    wd.mkdir(parents=True)
    return wd


def _takeover_copy_minimal_set(user_data: Path, profile: str,
                               workdir: Path) -> None:
    """最小复制集：Local State + <profile>/Cookies（含 -wal/-shm，存在才拷）。
    红线：源侧只读，一切写入只落 workdir。缺 Local State 或 Cookies → raise
    （调用方统一转 takeover_failed）。"""
    if not (user_data / "Local State").is_file():
        raise FileNotFoundError("Local State 缺失")
    src_profile = user_data / profile
    if (src_profile / "Network" / "Cookies").is_file():
        db_rel = Path("Network") / "Cookies"
    elif (src_profile / "Cookies").is_file():
        db_rel = Path("Cookies")  # 旧版布局兜底
    else:
        raise FileNotFoundError(f"源 profile 无 Cookies 库: {profile}")
    dst = workdir / profile / db_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(user_data / "Local State", workdir / "Local State")
    shutil.copy2(src_profile / db_rel, dst)
    for side in ("-wal", "-shm"):
        sidecar = Path(str(src_profile / db_rel) + side)
        if sidecar.is_file():
            shutil.copy2(sidecar, Path(str(dst) + side))


def _takeover_dynamic_port() -> int:
    """动态端口（bind 0 让 OS 分配），避开主工具实例 9222。"""
    for _ in range(20):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port != TAKEOVER_PORT_BANNED:
            return port
    raise RuntimeError("动态端口分配失败")


def _takeover_wait_cdp(port: int, timeout_s: float = TAKEOVER_CDP_WAIT_S) -> bool:
    """CDP ``/json/version`` 探活（stdlib urllib）。"""
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/json/version"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if "Browser" in json.loads(resp.read()):
                    return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _cdp_read_all_cookies(port: int) -> list[dict]:
    """CDP 读全量明文 cookie（Chrome 自己解密出的；Storage.getCookies 优先，
    Network.getAllCookies 兜底）。红线：返回值只在内存流转，绝不落日志。"""
    from scripts.lib.cdp_client import CdpConnection
    conn = CdpConnection(f"http://127.0.0.1:{port}")
    tab = conn.new_tab("about:blank")
    try:
        for method in ("Storage.getCookies", "Network.getAllCookies"):
            try:
                msg_id = tab._send(method, {})
                resp = tab._recv_until_id(msg_id, timeout=15) or {}
            except Exception:
                continue
            if resp.get("error"):
                continue
            return list(((resp.get("result") or {}).get("cookies")) or [])
        return []
    finally:
        try:
            tab.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _cdp_cookie_to_dict(c: dict) -> dict | None:
    """CDP cookie → _cookie_dict 同口径（域过滤/过期与 session 排除），另带
    控制字符防御（与 _read_chromium_db 出口同防线——CDP 必拒控制字符）。"""
    name = str(c.get("name") or "")
    value = str(c.get("value") or "")
    if any(ord(ch_) < 0x20 for ch_ in name + value):
        return None
    return _cookie_dict(str(c.get("domain") or ""), name, value,
                        str(c.get("path") or ""),
                        float(c.get("expires") or 0),
                        bool(c.get("secure")), bool(c.get("httpOnly")))


def _popen_takeover_browser(cmd: list[str]) -> subprocess.Popen:
    """启动接管浏览器（Windows CREATE_NEW_PROCESS_GROUP / posix 独立会话，探针先例）。
    独立函数便于测试 mock（绝不真启浏览器）。"""
    kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL,
                              "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def _takeover_kill_proc(proc: subprocess.Popen) -> None:
    """关接管浏览器进程（含子进程树：Windows taskkill /T，posix 进程组）。
    只关本通道 Popen 启的进程，绝不碰用户浏览器进程。"""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        elif proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _harvest_chromium_via_takeover(browser: str,
                                   profile_dir_name: str = "Default") -> dict[str, Any]:
    """Windows Chromium 源主通道：副本目录 CDP 接管（返回契约与 _harvest_chromium
    同：{source, status, cookies}）。

    状态：ok / takeover_no_browser（exe 缺失）/ takeover_failed（其余一切失败，
    message 指路 --paste）。任何失败不 raise。
    ⚠️ Windows 真机行为（headless=new 能否起 CDP/最小集是否充分）属发版 gate
    （方案 B5），本实现按双形态（headless 失败去 headless 重试一次）防御。
    """
    paste_hint = "可用 --paste 手动粘贴 Cookie 头"
    exe = _takeover_find_exe(browser)
    if not exe:
        return {"source": browser, "status": "takeover_no_browser",
                "message": f"未找到 {browser} 可执行文件，无法接管；{paste_hint}",
                "cookies": []}
    user_data = _takeover_user_data_root(browser)
    if user_data is None or not user_data.is_dir():
        return _takeover_fail(browser, f"{browser} 用户数据目录不存在"
                                       f"（可能未安装/未使用）；{paste_hint}")
    workdir: Path | None = None
    proc: subprocess.Popen | None = None
    try:
        profile = _takeover_resolve_profile(user_data, profile_dir_name)
        workdir = _takeover_new_workdir()
        _takeover_copy_minimal_set(user_data, profile, workdir)
        port = _takeover_dynamic_port()
        # 双形态启动：headless=new 失败 → 同参数去 headless 再试一次（B2 探针
        # 真机回传前的兜底；headless 下部分版本 elevator 行为有差异）
        for headless in (True, False):
            cmd = [exe,
                   f"--user-data-dir={workdir}",
                   f"--remote-debugging-port={port}",
                   f"--profile-directory={profile}",
                   "--no-first-run",
                   "--no-default-browser-check"]
            if headless:
                cmd.append("--headless=new")
            proc = _popen_takeover_browser(cmd)
            if _takeover_wait_cdp(port, TAKEOVER_CDP_WAIT_S):
                break
            _takeover_kill_proc(proc)
            proc = None
        if proc is None:
            return _takeover_fail(browser, "接管浏览器 CDP 未就绪"
                                           "（headless/有头两形态均超时）；"
                                           f"{paste_hint}")
        raw = _cdp_read_all_cookies(port)
        cookies = [c for c in (_cdp_cookie_to_dict(x) for x in raw) if c]
        return {"source": browser, "status": "ok", "cookies": cookies}
    except Exception as exc:
        # 明文红线：message 只带异常类型名，绝不带 CDP 内容/cookie 值
        logger.debug("takeover %s 失败: %s", browser, type(exc).__name__)
        return _takeover_fail(browser, f"接管通道失败（{type(exc).__name__}）；"
                                       f"{paste_hint}")
    finally:
        if proc is not None:
            _takeover_kill_proc(proc)
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


# ═══════════ Firefox ═══════════


def _firefox_root() -> Path | None:
    """Firefox 数据目录根（per-platform）。APPDATA 缺失（异常环境）→ None。"""
    if sys.platform == "darwin":
        return Path(os.path.expanduser(FIREFOX_BASE))
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None
        return Path(appdata) / "Mozilla" / "Firefox"
    return Path(os.path.expanduser("~/.mozilla/firefox"))


def _ini_profile_path(root: Path, raw: str) -> Path | None:
    """profiles.ini 的 Path=/Default= 值 → 目录：绝对路径原样；相对路径基于根拼。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        return p
    return root / p


def _firefox_profile_dirs(root: Path) -> list[Path]:
    """解析 profiles.ini 出候选 profile 目录（顺序即优先级，去重）。

    - ``[Install*]`` 段 ``Default=`` 优先（Firefox 67+ 的默认安装指示）；
    - 回退收录全部 ``[Profile*]`` 段 ``Path=``；
    - 相对路径基于 root 拼（``IsRelative=1`` 或非绝对路径）。
    ini 缺失/损坏 → 兜底浅扫 root 下 cookies.sqlite（Firefox 必写 profiles.ini，
    这只防手删；rglob 树浅、cap 50 防异常巨树）。
    """
    dirs: list[Path] = []
    ini = root / "profiles.ini"
    if ini.is_file():
        try:
            cp = configparser.ConfigParser()
            cp.read(ini, encoding="utf-8-sig")  # -sig: 容忍 BOM（Firefox 手导出可能带）
            for sec in cp.sections():
                if sec.lower().startswith("install"):
                    d = _ini_profile_path(root, cp.get(sec, "Default", fallback=""))
                    if d and d not in dirs:
                        dirs.append(d)
            for sec in cp.sections():
                if sec.lower().startswith("profile"):
                    d = _ini_profile_path(root, cp.get(sec, "Path", fallback=""))
                    if d and d not in dirs:
                        dirs.append(d)
        except Exception as exc:
            logger.debug("profiles.ini 解析失败（%s）: %s", ini, exc)
            dirs = []
    if not dirs:
        dirs = sorted({p.parent for p in root.rglob("cookies.sqlite")})[:50]
    return dirs


def _harvest_firefox() -> dict[str, Any]:
    # 平台无关（B-T2）：macOS 沿用 v0.69「扫 Profiles 子目录」语义不变；
    # Windows/Linux 经 profiles.ini 解析候选 profile。sqlite 读取逻辑全平台同一路径。
    if sys.platform == "darwin":
        base = Path(os.path.expanduser(FIREFOX_BASE))
        if not base.is_dir():
            return {"source": "firefox", "status": "not_installed", "cookies": []}
        profile_dirs = [p for p in sorted(base.iterdir())
                        if p.is_dir() and (p / "cookies.sqlite").is_file()]
    else:
        root = _firefox_root()
        if root is None or not root.is_dir():
            return {"source": "firefox", "status": "not_installed", "cookies": []}
        profile_dirs = [d for d in _firefox_profile_dirs(root)
                        if (d / "cookies.sqlite").is_file()]
    cookies: list[dict] = []
    for profile in profile_dirs:
        db = profile / "cookies.sqlite"
        # tmp 拷贝防锁（v0.69 既有模式：与 Chromium 一致走拷贝而非直读源库）
        with tempfile.TemporaryDirectory(prefix="cookie_harvest_ff_") as tmpdir:
            tmp_db = Path(tmpdir) / "cookies.sqlite"
            try:
                shutil.copy2(db, tmp_db)
                # B1 #3：-wal/-shm sidecar 一并拷（对齐 Chromium 拷贝集）——
                # 刚登录未 checkpoint 的最新登录态在 WAL 里，漏拷读的是旧态
                for side in ("-wal", "-shm"):
                    sidecar = Path(str(db) + side)
                    if sidecar.is_file():
                        shutil.copy2(sidecar, Path(str(tmp_db) + side))
                conn = sqlite3.connect(str(tmp_db))
                rows = conn.execute(
                    "SELECT host, name, value, path, expiry, isSecure, isHttpOnly "
                    "FROM moz_cookies"
                ).fetchall()
                conn.close()
            except Exception as exc:
                logger.debug("读 Firefox %s 失败: %s", db, exc)
                continue
        for host, name, value, path, expiry, secure, httponly in rows:
            c = _cookie_dict(host, name, value, path,
                             float(expiry or 0), secure, httponly)
            if c:
                cookies.append(c)
    status = "ok" if cookies else "not_installed"
    return {"source": "firefox", "status": status, "cookies": cookies}


# ═══════════ Safari（binarycookies，需完全磁盘访问权限）═══════════


def _read_binarycookies(path: Path) -> list[dict]:
    """解析 Safari Cookies.binarycookies（经典 'cook' 格式，best-effort）。"""
    data = path.read_bytes()
    if data[:4] != b"cook":
        raise ValueError("非 binarycookies 格式")
    (num_pages,) = struct.unpack(">I", data[4:8])  # 页数为大端
    page_sizes = list(struct.unpack_from(f"<{num_pages}I", data, 8))
    cookies: list[dict] = []
    offset = 8 + 4 * num_pages
    for page_size in page_sizes:
        page = data[offset:offset + page_size]
        offset += page_size
        try:
            cookies.extend(_parse_binarycookies_page(page))
        except Exception as exc:
            logger.debug("binarycookies 页解析失败（跳过）: %s", exc)
    return cookies


def _parse_binarycookies_page(page: bytes) -> list[dict]:
    (num_cookies,) = struct.unpack_from("<I", page, 4)
    offsets = list(struct.unpack_from(f"<{num_cookies}I", page, 8))
    cookies: list[dict] = []
    for off in offsets:
        (flags, _unk, url_off, name_off, path_off, value_off) = struct.unpack_from(
            "<6I", page, off + 4)
        # 布局：size(+0)/flags(+4)/unk(+8)/4 个串偏移(+12..+24)/end(+28)/
        # expiry(+32, double)/created(+40, double)
        (expiry, _created) = struct.unpack_from("<2d", page, off + 32)
        name = _bc_string(page, off + name_off)
        value = _bc_string(page, off + value_off)
        url = _bc_string(page, off + url_off)
        path = _bc_string(page, off + path_off)
        # secure flag: 0x1 = secure; httpOnly: 0x4
        c = _cookie_dict(url, name, value, path, float(expiry or 0),
                         bool(flags & 0x1), bool(flags & 0x4))
        if c:
            cookies.append(c)
    return cookies


def _bc_string(buf: bytes, off: int) -> str:
    end = buf.find(b"\x00", off)
    if end < 0:
        end = min(off + 256, len(buf))
    return buf[off:end].decode("utf-8", "replace")


def _harvest_safari() -> dict[str, Any]:
    if sys.platform != "darwin":
        # B-T1 per-source 平台闸（原整机 darwin 闸下沉）：binarycookies 是 macOS 专属。
        return {"source": "safari", "status": "unsupported_source",
                "message": "Safari 源仅 macOS 支持", "cookies": []}
    path = Path(os.path.expanduser(SAFARI_COOKIES))
    if not path.is_file():
        return {"source": "safari", "status": "not_installed", "cookies": []}
    try:
        cookies = _read_binarycookies(path)
    except PermissionError:
        return {"source": "safari", "status": "no_disk_access", "cookies": []}
    except Exception as exc:
        logger.debug("Safari cookie 解析失败（跳过）: %s", exc)
        return {"source": "safari", "status": "parse_error", "cookies": []}
    return {"source": "safari", "status": "ok", "cookies": cookies}


# ═══════════ 编排：扫描 → 注入 → 验证 ═══════════


def harvest_all(sources: list[str] | tuple[str, ...] | None = None,
                browser_profile: str | None = None) -> dict[str, Any]:
    """扫描全部/指定源。返回 {sources: {name: {status, cookies}}, total, platform}。

    平台可用性为 per-source（B-T1）：平台不支持的源返回 unsupported_source，
    不再整批拒绝——Windows/Linux 上 Firefox 源照常可用，Safari/Linux Chromium
    返回 unsupported_source（信息在各源 message 字段）。
    B-T4 起 Windows Chromium 源走副本目录 CDP 接管（状态 takeover_* 前缀），
    ``browser_profile``（CLI --browser-profile，info_cache 显示名或目录名）透传
    给接管通道，缺省 Default。
    platform 字段 = 实际平台名（sys.platform），仅供诊断展示（不再有 "unsupported"）。
    """
    wanted = tuple(sources) if sources else ALL_SOURCES
    harvesters = {
        "chrome": lambda: _harvest_chromium(
            "chrome", profile_dir_name=browser_profile or "Default"),
        "edge": lambda: _harvest_chromium(
            "edge", profile_dir_name=browser_profile or "Default"),
        "brave": lambda: _harvest_chromium(
            "brave", profile_dir_name=browser_profile or "Default"),
        "firefox": _harvest_firefox,
        "safari": _harvest_safari,
    }
    out: dict[str, Any] = {}
    total = 0
    for name in wanted:
        fn = harvesters.get(name)
        if fn is None:
            continue
        try:
            report = fn()
        except Exception as exc:
            logger.warning("cookie 采集 %s 异常（跳过）: %s", name, exc)
            report = {"source": name, "status": "error", "cookies": []}
        out[name] = report
        total += len(report.get("cookies") or [])
    return {"sources": out, "total": total, "platform": sys.platform}


def inject_cookies(cookies: list[dict],
                   cdp_url: str = CDP_URL) -> dict[str, Any]:
    """Storage.setCookies 批量注入工具 Chrome（复用 ozon_session.restore 姿势）。"""
    if not cookies:
        return {"ok": False, "count": 0, "message": "无可注入 cookie"}
    conn = None
    tab = None
    try:
        from scripts.lib.cdp_client import CdpConnection
        conn = CdpConnection(cdp_url)
        tab = conn.new_tab("about:blank")
        injected = 0
        for chunk_start in range(0, len(cookies), 50):
            payload = []
            for c in cookies[chunk_start:chunk_start + 50]:
                item = {
                    "name": c["name"], "value": c["value"], "domain": c["domain"],
                    "path": c.get("path") or "/",
                    "secure": bool(c.get("secure")),
                    "httpOnly": bool(c.get("httpOnly")),
                }
                exp = float(c.get("expires") or 0)
                # expires<=0 = 会话 cookie：CDP 语义下必须省略 expires 键
                # （显式传 0 会被当作 1970 已过期丢弃）。--paste 通道全是会话 cookie，
                # harvested cookie 恒为持久 cookie（expires>0），行为不变。
                if exp > 0:
                    item["expires"] = exp
                payload.append(item)
            msg_id = tab._send("Storage.setCookies", {"cookies": payload})
            resp = tab._recv_until_id(msg_id, timeout=10) or {}
            if resp.get("error"):
                return {"ok": False, "count": injected,
                        "message": f"注入失败: {resp['error']}"}
            injected += len(payload)
        return {"ok": True, "count": injected,
                "message": f"注入 {injected} 个 cookie"}
    except Exception as exc:
        logger.warning("cookie 注入失败: %s", exc)
        return {"ok": False, "count": 0, "message": f"注入失败: {exc}"}
    finally:
        if tab:
            try:
                tab.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def verify_after_import(cdp_url: str = CDP_URL) -> dict[str, bool]:
    """注入后用现有登录检测体系验证（与 readiness 同一判据）。"""
    result = {"1688": False, "seller": False}
    try:
        from scripts.lib.readiness import probe_alibaba_login
        result["1688"] = bool(probe_alibaba_login(cdp_url))
    except Exception as exc:
        logger.debug("1688 登录验证失败: %s", exc)
    try:
        from scripts.lib.cdp_client import CdpConnection
        from scripts.lib.ozon_seller_analytics import _read_seller_cookies_silent
        with CdpConnection(cdp_url) as conn:
            cookies = _read_seller_cookies_silent(conn)
        result["seller"] = bool(cookies.get("sc_company_id"))
    except Exception as exc:
        logger.debug("seller 登录验证失败: %s", exc)
    return result


def harvest_and_import(cdp_url: str = CDP_URL,
                       sources: list[str] | None = None,
                       browser_profile: str | None = None) -> dict[str, Any]:
    """一键：扫描 → 注入 → 验证。返回完整报告（CLI 打印 / readiness 消费用）。"""
    scan = harvest_all(sources, browser_profile=browser_profile)
    report: dict[str, Any] = {"scan": scan, "injected": None, "verified": None}
    cookies = [c for r in scan.get("sources", {}).values()
               for c in (r.get("cookies") or [])]
    if not cookies:
        report["injected"] = {"ok": False, "count": 0,
                              "message": "各浏览器未发现可导入的 1688/Ozon cookie"}
        return report
    report["injected"] = inject_cookies(cookies, cdp_url=cdp_url)
    if report["injected"].get("ok"):
        report["verified"] = verify_after_import(cdp_url)
    return report


# ═══════════ --paste 手动粘贴通道（B-T3，跨平台兜底）═══════════

# cookie 名指纹 → 落域（无 domain 信息的粘贴头只能按名字猜）。
# 1688 指纹以裁决清单为准：cookie2/__cn_logon__/_m_h5_tk(_enc)/tfstk/isg。
PASTE_NAME_FINGERPRINTS: dict[str, frozenset[str]] = {
    "1688": frozenset({"cookie2", "__cn_logon__", "_m_h5_tk", "_m_h5_tk_enc",
                       "tfstk", "isg"}),
    "ozon-seller": frozenset({"sc_company_id", "__Secure-access_token",
                              "abt_data"}),
}
PASTE_SITE_DOMAINS = {"1688": ".1688.com", "ozon-seller": ".ozon.ru"}


def parse_cookie_header(text: str) -> list[tuple[str, str]]:
    """容错解析粘贴的 Cookie 头：剥可选 ``Cookie:`` 前缀（大小写不敏感，整串首处
    与每个分号/换行后的 chunk 各剥一次——多行请求头粘贴时非首行的 ``Cookie: a=1``
    不再解析出垃圾对）、按 ``;``/换行切分、剥首尾空白，拆 ``k=v`` 对（v 可含
    ``=``）。空/无 ``=`` 的碎片跳过。⚠️ 明文红线：解析失败不回显、不落日志。"""
    text = (text or "").strip()
    if not text:
        return []
    m = re.match(r"(?is)^\s*cookie\s*:\s*", text)
    if m:
        text = text[m.end():]
    pairs: list[tuple[str, str]] = []
    for chunk in re.split(r"[;\r\n]+", text):
        chunk = chunk.strip()
        # B1 #4：行内 ``Cookie:`` 前缀再剥一次（多行请求头粘贴非首行场景）
        m_inline = re.match(r"(?is)^cookie\s*:\s*", chunk)
        if m_inline:
            chunk = chunk[m_inline.end():].strip()
        if not chunk or "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        name = name.strip()
        if name:
            pairs.append((name, value.strip()))
    return pairs


def _classify_paste_pairs(
        pairs: list[tuple[str, str]]) -> tuple[dict[str, list[tuple[str, str]]], int]:
    """按 cookie 名指纹分组落域。返回 ({site: [(name, value)...]}, 未识别数)。"""
    grouped: dict[str, list[tuple[str, str]]] = {}
    skipped = 0
    for name, value in pairs:
        hit = next((site for site, names in PASTE_NAME_FINGERPRINTS.items()
                    if name in names), None)
        if hit is None:
            skipped += 1
            continue
        grouped.setdefault(hit, []).append((name, value))
    return grouped, skipped


def paste_and_import(header: str, site: str | None = None,
                     cdp_url: str = CDP_URL) -> dict[str, Any]:
    """--paste 通道：解析粘贴的 Cookie 头 → 指纹/显式 --site 落域 → 注入 → 验证。

    返回 {cookies, sites, skipped, injected, verified} 或 {error, cookies: []}。
    cookie 构造不走 _cookie_dict：expires=0（会话级）在此是有意输入——_cookie_dict
    过滤 session cookie 是针对「源浏览器里随退随死的 harvested 垃圾」，而 paste 是
    用户显式输入，显式输入优先（工具 Chrome 常驻，会话 cookie 跨命令存活；
    inject_cookies 载荷层把 expires=0 省略为 CDP 会话语义）。
    """
    empty: dict[str, Any] = {"cookies": [], "sites": [], "skipped": 0,
                             "injected": None, "verified": None}
    pairs = parse_cookie_header(header)
    if not pairs:
        return {**empty, "error": "未解析到任何 cookie（期望形如 k=v; k2=v2，"
                                  "可带 Cookie: 前缀）"}
    if site:
        if site not in PASTE_SITE_DOMAINS:
            return {**empty, "error": f"未知 --site: {site}"
                                      "（可选 1688|ozon-seller）"}
        grouped = {site: pairs}
        skipped = 0
    else:
        grouped, skipped = _classify_paste_pairs(pairs)
        if not grouped:
            return {**empty, "error": "粘贴内容未命中 1688/Ozon cookie 名指纹，"
                                      "请用 --site 1688|ozon-seller 显式指定落域"}
    cookies: list[dict] = []
    for s, ps in grouped.items():
        domain = PASTE_SITE_DOMAINS[s]
        for name, value in ps:
            cookies.append({"name": name, "value": value, "domain": domain,
                            "path": "/", "secure": True, "httpOnly": False,
                            "expires": 0.0})
    report: dict[str, Any] = {**empty, "cookies": cookies,
                              "sites": sorted(grouped), "skipped": skipped}
    report["injected"] = inject_cookies(cookies, cdp_url=cdp_url)
    if report["injected"].get("ok"):
        report["verified"] = verify_after_import(cdp_url)
    return report


# ═══════════ readiness 自动兜底（冷却落盘）═══════════


def _cooldown_key(probe: str) -> str:
    return f"auto_fallback:{probe}"


def auto_fallback_allowed(probe: str) -> bool:
    try:
        from scripts.lib.cache import cache_get
        return cache_get("cookie_import", _cooldown_key(probe)) is None
    except Exception:
        return True


def mark_auto_fallback_attempted(probe: str) -> None:
    try:
        from scripts.lib.cache import cache_set
        cache_set("cookie_import", _cooldown_key(probe), {"at": time.time()},
                  ttl=AUTO_FALLBACK_COOLDOWN_SECONDS)
    except Exception:
        pass


def try_auto_import(probe: str, cdp_url: str = CDP_URL) -> bool:
    """readiness 未登录分支的自动兜底入口（冷却 1h；kill-switch 可关）。

    B-T1 起整机 darwin 闸废除：平台可用性由 harvest_all per-source 报告决定
    （Windows/Linux 上 Firefox 源照常尝试）。先记冷却再尝试：异常/用户拒绝
    Keychain 授权也不会循环弹框。
    ⚠️ pytest 下恒 False：readiness 有测试会摘守卫跑真探针，本函数若在测试中
    真实扫描/注入会翻转探针结果并污染真实 data/cache（v0.69 全量回归实证）。
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if os.environ.get("SKILL_DISABLE_COOKIE_HARVEST"):
        return False
    if not auto_fallback_allowed(probe):
        logger.debug("cookie 导入冷却中（%s），跳过自动兜底", probe)
        return False
    mark_auto_fallback_attempted(probe)
    try:
        report = harvest_and_import(cdp_url=cdp_url)
    except Exception as exc:
        logger.warning("cookie 自动导入异常（忽略）: %s", exc)
        return False
    domain = "seller" if probe == "seller_login" else "1688"
    return bool((report.get("verified") or {}).get(domain))
