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

Windows：Chrome 127+ 对 cookies 启用 app-bound 加密，第三方解密不可行——明确提示
暂不支持（Firefox 路线后续可选）。

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

import logging
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
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


def _harvest_chromium(source: str) -> dict[str, Any]:
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


# ═══════════ Firefox ═══════════


def _harvest_firefox() -> dict[str, Any]:
    base = Path(os.path.expanduser(FIREFOX_BASE))
    if not base.is_dir():
        return {"source": "firefox", "status": "not_installed", "cookies": []}
    cookies: list[dict] = []
    for profile in sorted(base.iterdir()):
        db = profile / "cookies.sqlite"
        if not db.is_file():
            continue
        with tempfile.TemporaryDirectory(prefix="cookie_harvest_ff_") as tmpdir:
            tmp_db = Path(tmpdir) / "cookies.sqlite"
            try:
                shutil.copy2(db, tmp_db)
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


def harvest_all(sources: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    """扫描全部/指定源。返回 {sources: {name: {status, cookies}}, total}。

    平台守卫：非 macOS 直接返回 unsupported（调用方按未修复处理）。
    """
    if sys.platform != "darwin":
        return {"sources": {}, "total": 0,
                "platform": "unsupported",
                "message": "跨浏览器 cookie 导入暂仅支持 macOS（Windows Chrome 127+ "
                           "app-bound 加密不可解）"}
    wanted = tuple(sources) if sources else ALL_SOURCES
    harvesters = {
        "chrome": lambda: _harvest_chromium("chrome"),
        "edge": lambda: _harvest_chromium("edge"),
        "brave": lambda: _harvest_chromium("brave"),
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
    return {"sources": out, "total": total, "platform": "macos"}


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
            payload = [{
                "name": c["name"], "value": c["value"], "domain": c["domain"],
                "path": c.get("path") or "/",
                "secure": bool(c.get("secure")),
                "httpOnly": bool(c.get("httpOnly")),
                "expires": float(c.get("expires") or 0),
            } for c in cookies[chunk_start:chunk_start + 50]]
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
                       sources: list[str] | None = None) -> dict[str, Any]:
    """一键：扫描 → 注入 → 验证。返回完整报告（CLI 打印 / readiness 消费用）。"""
    scan = harvest_all(sources)
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
    """readiness 未登录分支的自动兜底入口（冷却 1h；仅 macOS；kill-switch 可关）。

    先记冷却再尝试：异常/用户拒绝 Keychain 授权也不会循环弹框。
    ⚠️ pytest 下恒 False：readiness 有测试会摘守卫跑真探针，本函数若在测试中
    真实扫描/注入会翻转探针结果并污染真实 data/cache（v0.69 全量回归实证）。
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if os.environ.get("SKILL_DISABLE_COOKIE_HARVEST"):
        return False
    if sys.platform != "darwin":
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
