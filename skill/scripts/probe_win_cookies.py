#!/usr/bin/env python3
"""Windows 1688/Ozon cookie 只读探针（feat/win-cookie-import-v1 B-T0，方案 §B3）。

目的：在 Windows 真机采集「源浏览器 / 加密形态 / 通道可用性」事实，产出 W-B 判定
矩阵，为 B-T4（副本目录 CDP 接管通道）定稿提供依据。macOS 上运行也合法（多数源
not_installed、Firefox 正常探测）——便于本机自测与测试。

⚠️ 只读红线（硬约束，改本文件前先读）：
- 只读用户浏览器目录：sqlite 一律先拷到临时目录再只读打开（``?mode=ro``），
  绝不写用户目录、不提权、不装任何东西；
- 不做任何解密：``v20``/app-bound 只统计 ``encrypted_value`` 前缀形态，绝不尝试
  解开；Local State 的 ``encrypted_key``/``app_bound_encrypted_key`` 只看 base64
  解码后的 ``DPAPI``/``APPB`` 前缀字节——绝不实现 DPAPI/CryptUnprotectData/
  IElevator 任何解密调用（那是 infostealer 手法，见 cookie_harvest B-T1 注释）；
- cookie 值（明文或解密后）绝不落报告/日志——只统计数量与前缀形态。

判定矩阵（B-T4 定稿输入）：
- 某 profile 抽样 ``v20`` 占比 == 0 → DPAPI v10 直解通道可用（W-B' 分支）；
- ``v20`` 占比 > 0 → 需副本接管通道（W-B 主通道）；
- 无抽样行 → undetermined（不可判定）。

``--takeover-test``（默认关，B-T4 可行性试验）：挑目标域 cookie 最多的 Chromium
profile，最小复制集（Local State + <profile>/Cookies* 含 -wal/-shm）→ 临时目录 →
``chrome_launcher._find_chrome_executable()`` 找到的真实浏览器以
``--user-data-dir=<临时目录> --remote-debugging-port=<动态端口避开 9222>
--no-first-run [--headless=new]`` 启动 → CDP ``/json/version`` 探活 →
``Network.getAllCookies``/``Storage.getCookies`` 统计目标域明文 cookie 数 →
finally 兜底关闭浏览器进程 + 删临时目录。试验只复制源文件 + 读 CDP，不碰用户
浏览器进程与目录；失败不异常退出，作为报告里的一行结论。

用法：
    python scripts/probe_win_cookies.py [--takeover-test] [--out <path>]
    python scripts/cli.py probe-win-cookies [--takeover-test] [--out <path>]

报告：JSON 落 ``data/probe/win_cookies_<ts>.json``（目录惰性创建），stdout 打印
人话摘要。exit 0 正常完成（含「无源浏览器」这种合法结果）；exit 1 只留给脚本
自身异常。

依赖：stdlib（tempfile/sqlite3/urllib/base64/json/socket）；``--takeover-test``
的 CDP 读数复用仓内 cdp_client（websocket-client 是 skill 既有依赖，零新增）。
打包：compile.py COPY_FILES 明文（cli.py/batch_test.py 先例——入口脚本不编译）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

# Ensure scripts/ 的父目录（skill 根）在 sys.path（scripts 包可导入，
# 对齐 cli.py:36 / probe_what_to_sell_fields.py 先例）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import chrome_launcher  # noqa: E402
from scripts.lib import cookie_harvest as ch  # noqa: E402

# skill 根/data（脚本位于 <skill>/scripts/ 下；dist 分发后 = 包根/data）
DATA_ROOT = Path(__file__).resolve().parent.parent / "data"

CHROMIUM_SOURCES = ("chrome", "edge", "brave")
# 与 cookie_harvest._list_chromium_cookie_dbs 同口径：System/Guest 非用户 profile
PROFILE_SKIP_NAMES = ("system profile", "guest profile")

# Windows User Data 根（参照 chrome_launcher._find_chrome_executable 的
# %LOCALAPPDATA% 候选写法，Chrome 130+ 禁默认目录开调试——接管通道本就用非默认目录）
CHROMIUM_USER_DATA_REL: dict[str, tuple[str, ...]] = {
    "chrome": ("Google", "Chrome", "User Data"),
    "edge": ("Microsoft", "Edge", "User Data"),
    "brave": ("BraveSoftware", "Brave-Browser", "User Data"),
}

SAMPLE_TARGET_LIMIT = 200   # 目标域抽样上限（简报：≤200 行）
SAMPLE_OTHER_LIMIT = 50     # 非目标域对照抽样上限
CDP_WAIT_TIMEOUT_S = 30.0
TAKEOVER_PORT_BANNED = 9222  # 主工具实例端口，探针试验必须避开


# ═══════════ 路径解析（测试单点 monkeypatch）═══════════


def _resolve_user_data(source: str, platform_name: str | None = None) -> Path | None:
    """Chromium 系 User Data 根（win32 主目标；darwin 复用 cookie_harvest 常量
    便于本机自测；linux 探针未覆盖 → None）。"""
    plat = platform_name if platform_name is not None else sys.platform
    if plat == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return None
        return Path(local).joinpath(*CHROMIUM_USER_DATA_REL[source])
    if plat == "darwin":
        cfg = ch.CHROMIUM_BROWSERS.get(source)
        if not cfg:
            return None
        return Path(os.path.expanduser(cfg["base"]))
    return None


def _firefox_root() -> Path | None:
    """Firefox 数据根——薄代理 B1 的 ``cookie_harvest._firefox_root``
    （Windows %APPDATA%\\Mozilla\\Firefox / Linux ~/.mozilla/firefox / darwin
    Profiles 目录），复用既有解析不复制粘贴；测试单点 monkeypatch。"""
    return ch._firefox_root()


def _firefox_profiles() -> list[Path]:
    """Firefox profile 目录清单：非 darwin 复用 B1 ``_firefox_profile_dirs``
    （profiles.ini：Install* Default 优先 / Profile* Path 回退 / ini 缺失兜底）；
    darwin 沿用 v0.69「扫 FIREFOX_BASE 子目录」语义（与 _harvest_firefox 一致）。"""
    root = _firefox_root()
    if root is None or not root.is_dir():
        return []
    if sys.platform == "darwin":
        return sorted(p for p in root.iterdir()
                      if p.is_dir() and (p / "cookies.sqlite").is_file())
    return [d for d in ch._firefox_profile_dirs(root)
            if (d / "cookies.sqlite").is_file()]


# ═══════════ 源枚举（Chromium profile 清单）═══════════


def _read_local_state(user_data: Path) -> dict:
    """只读解析 Local State JSON；缺失/损坏 → {}（形态记为未知，不崩）。"""
    path = user_data / "Local State"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _crypt_shape(local_state: dict) -> dict[str, bool]:
    """os_crypt 密钥前缀形态（红线：只看 base64 解码后的前缀字节，绝不解密）。

    - ``encrypted_key`` = base64(DPAPI ‖ key) → DPAPI 旧式加密（v10/v11）；
    - ``app_bound_encrypted_key`` = base64(APPB ‖ blob) → Chrome 127+ app-bound。
    """
    os_crypt = local_state.get("os_crypt") or {}

    def _has_prefix(field: str, tag: bytes) -> bool:
        # 字段缺失/损坏 → False（该密钥形态不可用/未启用；本探针只消费 True）
        raw = os_crypt.get(field)
        if not isinstance(raw, str) or not raw:
            return False
        try:
            blob = base64.b64decode(raw)
        except Exception:
            return False
        return blob[:len(tag)] == tag

    return {"dpapi_legacy_key": _has_prefix("encrypted_key", b"DPAPI"),
            "app_bound_key": _has_prefix("app_bound_encrypted_key", b"APPB")}


def _cookies_db_rel(profile_dir: Path) -> str | None:
    """profile 下 Cookies 库相对路径：``Network/Cookies``（现行布局）或
    ``Cookies``（旧版布局）或 None。"""
    if (profile_dir / "Network" / "Cookies").is_file():
        return "Network/Cookies"
    if (profile_dir / "Cookies").is_file():
        return "Cookies"
    return None


def _chromium_profiles(user_data: Path) -> tuple[list[dict], list[str]]:
    """枚举 profile（跳过 System/Guest）。返回 (保留清单, 跳过名单)。

    ``Local State`` 的 ``profile.info_cache`` 优先（User Data 下的组件目录
    ——Crashpad/OptimizationHints 等——不是 profile，实机实证全靠 info_cache
    过滤）；info_cache 缺失/为空才兜底扫目录。info_cache 可用但某目录带
    Cookies 库却未登记（异常态/新建未登记）→ 仍补录，报告不失真。
    """
    info_cache: dict = {}
    local_state = _read_local_state(user_data)
    if local_state:
        info_cache = (local_state.get("profile") or {}).get("info_cache") or {}
    kept: list[dict] = []
    skipped: list[str] = []
    if not user_data.is_dir():
        return kept, skipped
    entries = {e.name: e for e in sorted(user_data.iterdir()) if e.is_dir()}

    def _row(name: str) -> dict:
        return {"dir": name,
                "display_name": str((info_cache.get(name) or {}).get("name") or ""),
                "cookies_db": _cookies_db_rel(entries[name])}

    for name in entries:
        if name.lower() in PROFILE_SKIP_NAMES:
            skipped.append(name)
    if info_cache:
        names = [str(n) for n in info_cache if str(n) in entries]
        for name in names:
            kept.append(_row(name))
        for name in entries:  # 未登记但带 Cookies 库 → 补录
            if name not in names and name.lower() not in PROFILE_SKIP_NAMES \
                    and _cookies_db_rel(entries[name]):
                kept.append(_row(name))
    else:
        for name in entries:
            if name.lower() not in PROFILE_SKIP_NAMES:
                kept.append(_row(name))
    return kept, skipped


# ═══════════ 加密形态抽样（前缀统计，绝不解密）═══════════


def _target_host_sql() -> str:
    """目标域 SQL 谓词——与 ``cookie_harvest._domain_matches`` 同语义
    （精确等于或 ``.suffix`` 结尾；``not1688.com``/``ozon.ru.evil.com`` 不命中）。"""
    conds = []
    for suffix in ch.TARGET_DOMAIN_SUFFIXES:
        conds.append(f"host_key = '{suffix}' OR host_key LIKE '%.{suffix}'")
    return "(" + " OR ".join(conds) + ")"


def _classify_encrypted(enc: bytes, plain: str) -> str:
    """单行 encrypted_value 前缀分类（只取 3 字节前缀，内容不出本函数）。"""
    if plain and not enc:
        return "plain"
    if not enc:
        return "empty"
    head = bytes(enc[:3])
    if head == b"v10":
        return "v10"
    if head == b"v11":
        return "v11"
    if head == b"v20":
        return "v20"
    return "other"


def _copy_cookies_db(db: Path, tmp_db: Path) -> None:
    """Cookies 库拷到临时目录（含 -wal/-shm，未 checkpoint 的登录态一并回放）。
    只读红线：源目录只读不写。"""
    shutil.copy2(db, tmp_db)
    for side in ("-wal", "-shm"):
        src = Path(str(db) + side)
        if src.is_file():
            shutil.copy2(src, Path(str(tmp_db) + side))


def _sample_prefix_distribution(db: Path) -> dict:
    """抽 ≤200 目标域 + ≤50 非目标域行，统计 encrypted_value 前缀分布。

    红线：拷临时目录后 sqlite 只读打开；只统计前缀/数量，cookie 值与加密
    blob 绝不出本函数。失败返回 error 行（不 raise，探针逐项独立）。
    """
    counts = {"v10": 0, "v11": 0, "v20": 0, "plain": 0, "other": 0, "empty": 0}
    fail = {"target_rows": 0, "other_rows": 0, "prefix_counts": dict(counts),
            "v20_ratio": None, "error": None}
    where = _target_host_sql()
    with tempfile.TemporaryDirectory(prefix="probe_win_cookies_") as tmpdir:
        tmp_db = Path(tmpdir) / "Cookies"
        try:
            _copy_cookies_db(db, tmp_db)
            conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
            target = conn.execute(
                f"SELECT encrypted_value, value FROM cookies WHERE {where} LIMIT ?",
                (SAMPLE_TARGET_LIMIT,)).fetchall()
            others = conn.execute(
                f"SELECT encrypted_value, value FROM cookies WHERE NOT {where} "
                "LIMIT ?", (SAMPLE_OTHER_LIMIT,)).fetchall()
            conn.close()
        except Exception as exc:
            fail["error"] = f"读 Cookies 库失败: {type(exc).__name__}: {exc}"
            return fail
    for enc, plain in target + others:
        counts[_classify_encrypted(bytes(enc or b""), plain or "")] += 1
    filled = sum(counts[k] for k in ("v10", "v11", "v20", "other", "plain"))
    return {"target_rows": len(target), "other_rows": len(others),
            "prefix_counts": counts,
            "v20_ratio": (counts["v20"] / filled) if filled else 0.0,
            "error": None}


def _channel_for(sample: dict) -> str:
    """判定矩阵行：v20 占比 0（有抽样）→ DPAPI v10 直解可用（W-B'）；
    >0 → 副本接管通道（W-B）；零抽样 → 不可判定。"""
    counts = sample.get("prefix_counts") or {}
    filled = sum(counts.get(k, 0) for k in ("v10", "v11", "v20", "other", "plain"))
    if not filled:
        return "undetermined"
    return "takeover" if counts.get("v20") else "dpapi_v10"


# ═══════════ Firefox 通道（W-A 可用性）═══════════


def _firefox_profile_stats(profile: Path) -> dict:
    """逐 profile 数 moz_cookies 目标域 cookie（判定 W-A 可用性）。
    Firefox cookies 是明文——红线：只计数，值不读出本函数。"""
    now = time.time()
    stats: dict[str, Any] = {"path": str(profile), "target_cookies": 0,
                             "target_cookies_unexpired": 0, "error": None}
    with tempfile.TemporaryDirectory(prefix="probe_win_cookies_ff_") as tmpdir:
        tmp_db = Path(tmpdir) / "cookies.sqlite"
        try:
            shutil.copy2(profile / "cookies.sqlite", tmp_db)
            conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
            rows = conn.execute("SELECT host, expiry FROM moz_cookies").fetchall()
            conn.close()
        except Exception as exc:
            stats["error"] = f"读 cookies.sqlite 失败: {type(exc).__name__}: {exc}"
            stats["target_cookies"] = None
            return stats
    for host, expiry in rows:
        if ch._domain_matches(host or ""):
            stats["target_cookies"] += 1
            if expiry and float(expiry) > now:
                stats["target_cookies_unexpired"] += 1
    return stats


# ═══════════ 副本接管可行性试验（--takeover-test，默认关）═══════════


def _pick_dynamic_port() -> int:
    """动态端口（bind 0 让 OS 分配），避开主工具实例 9222。"""
    for _ in range(20):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port != TAKEOVER_PORT_BANNED:
            return port
    raise RuntimeError("动态端口分配失败")


def _wait_cdp(port: int, timeout_s: float = CDP_WAIT_TIMEOUT_S) -> bool:
    """CDP ``/json/version`` 探活（stdlib urllib）。"""
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/json/version"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                data = json.loads(resp.read())
                if "Browser" in data:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _cdp_all_cookies(port: int) -> list[dict]:
    """CDP 读全量 cookie（Chrome 自己解密出的明文；Storage.getCookies 优先，
    Network.getAllCookies 兜底）。红线：只统计数量，cookie 值不落日志/报告。"""
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


def _popen_browser(cmd: list[str]) -> subprocess.Popen:
    """启动试验浏览器（Windows CREATE_NEW_PROCESS_GROUP / posix 独立会话，
    均 chrome_launcher 先例）。独立函数便于测试 mock（绝不真启浏览器）。"""
    kwargs: dict[str, Any] = {"stdout": subprocess.DEVNULL,
                              "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def _terminate_browser(proc: subprocess.Popen) -> None:
    """关试验浏览器进程（只关本探针 Popen 启的，绝不碰用户浏览器进程）。"""
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_takeover_test(candidate: dict, base_dir: Path | None = None,
                      wait_timeout_s: float = CDP_WAIT_TIMEOUT_S) -> dict:
    """B-T4 可行性试验：最小复制集 → 临时目录 → 真实浏览器 headless 开 CDP →
    统计目标域明文 cookie 数 → finally 关进程 + 删临时目录。

    只读红线：只复制 ``Local State`` + ``<profile>/Cookies*``（含 -wal/-shm）到
    ``data/tmp/probe_takeover_<ts>/`` 临时目录，不写用户浏览器目录、不碰用户
    浏览器进程。失败不 raise——结论作为报告一行（``error`` 字段）。
    """
    result: dict[str, Any] = {
        "source": candidate.get("source"), "profile": candidate.get("profile"),
        "sampled_target_rows": candidate.get("target_rows"),
        "headless": True, "cdp_up": False, "all_cookies": None,
        "target_cookies": None, "minimal_set_sufficient": False,
        "cleaned": False, "error": None,
    }
    exe = chrome_launcher._find_chrome_executable()
    if not exe:
        result["error"] = "未找到浏览器可执行文件（chrome_launcher）"
        return result
    work_root = Path(base_dir) if base_dir is not None else (DATA_ROOT / "tmp")
    ts = time.strftime("%Y%m%d_%H%M%S")
    workdir = work_root / f"probe_takeover_{ts}"
    if workdir.exists():  # 同秒重跑防撞
        workdir = work_root / f"probe_takeover_{ts}_{time.time_ns() % 10**6}"
    proc = None
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        # 最小复制集：Local State + <profile>/Cookies*（-wal/-shm 一并，回放未
        # checkpoint 登录态）。只拷贝，绝不写源目录。
        user_data = Path(candidate["user_data_dir"])
        shutil.copy2(user_data / "Local State", workdir / "Local State")
        db_rel = candidate.get("cookies_db") or "Network/Cookies"
        dst_db = workdir / candidate["profile"] / db_rel
        dst_db.parent.mkdir(parents=True, exist_ok=True)
        _copy_cookies_db(Path(candidate["user_data_dir"]) / candidate["profile"]
                         / db_rel, dst_db)
        port = _pick_dynamic_port()
        result["port"] = port
        cmd = [
            exe,
            f"--user-data-dir={workdir}",
            f"--remote-debugging-port={port}",
            f"--profile-directory={candidate['profile']}",
            "--no-first-run",
            "--no-default-browser-check",
            # headless=new：试验默认 headless；若真机失败需对照有头差异（记录在案）
            "--headless=new",
        ]
        proc = _popen_browser(cmd)
        result["pid"] = proc.pid
        result["cdp_up"] = _wait_cdp(port, wait_timeout_s)
        if result["cdp_up"]:
            cookies = _cdp_all_cookies(port)
            result["all_cookies"] = len(cookies)
            result["target_cookies"] = sum(
                1 for c in cookies if ch._domain_matches(str(c.get("domain") or "")))
            # 最小集足够 = CDP 能起 + 目标域明文 cookie 能解出
            # （候选 profile 源行 >0；解出 0 = 最小集不足或 app-bound 绑目录，B-T4 定稿依据）
            result["minimal_set_sufficient"] = result["target_cookies"] > 0
        else:
            result["error"] = "CDP 未就绪（等待超时）"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if proc is not None:
            _terminate_browser(proc)
        shutil.rmtree(workdir, ignore_errors=True)
        result["cleaned"] = not workdir.exists()
    return result


# ═══════════ 报告编排 ═══════════


def collect_report(takeover_test: bool = False) -> dict:
    """采集全部事实（逐项独立，单项失败不影响其他项）。"""
    report: dict[str, Any] = {
        "tool": "probe_win_cookies",
        "platform": sys.platform,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "red_lines": ["只读用户浏览器目录（拷临时目录后 sqlite 只读）",
                      "不解密（v20/app-bound 只统计前缀形态）",
                      "cookie 值不落报告/日志"],
        "sources": {},
        "matrix": [],
        "takeover_test": None,
    }
    candidates: list[dict] = []
    for source in CHROMIUM_SOURCES:
        user_data = _resolve_user_data(source)
        installed = bool(user_data and user_data.is_dir())
        block: dict[str, Any] = {"installed": installed,
                                 "user_data_dir": str(user_data) if user_data else None,
                                 "crypt": {}, "profiles": [], "profiles_skipped": []}
        if installed:
            block["crypt"] = _crypt_shape(_read_local_state(user_data))
            profiles, skipped = _chromium_profiles(user_data)
            block["profiles"] = profiles
            block["profiles_skipped"] = skipped
            for prof in profiles:
                if not prof["cookies_db"]:
                    continue
                sample = _sample_prefix_distribution(
                    user_data / prof["dir"] / prof["cookies_db"])
                row = {"source": source, "profile": prof["dir"],
                       "display_name": prof["display_name"],
                       "cookies_db": prof["cookies_db"], **sample,
                       "channel": _channel_for(sample),
                       "dpapi_legacy_key": block["crypt"].get("dpapi_legacy_key"),
                       "app_bound_key": block["crypt"].get("app_bound_key")}
                report["matrix"].append(row)
                candidates.append({**row, "user_data_dir": str(user_data)})
        report["sources"][source] = block

    root = _firefox_root()
    ff_block: dict[str, Any] = {"installed": bool(root and root.is_dir()),
                                "root": str(root) if root else None,
                                "profiles": []}
    if ff_block["installed"]:
        for profile in _firefox_profiles():
            ff_block["profiles"].append(_firefox_profile_stats(profile))
    report["sources"]["firefox"] = ff_block

    if takeover_test:
        viable = [c for c in candidates if (c.get("target_rows") or 0) > 0]
        if not viable:
            report["takeover_test"] = {
                "skipped": "无含目标域 cookie 的 Chromium profile，未执行试验"}
        else:
            best = max(viable, key=lambda c: (c.get("target_rows") or 0))
            report["takeover_test"] = run_takeover_test(best)
    return report


def _default_out_path() -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return DATA_ROOT / "probe" / f"win_cookies_{ts}.json"


def write_report(report: dict, out: str | os.PathLike | None = None) -> Path:
    """JSON 落盘（目录惰性创建）。"""
    path = Path(out) if out else _default_out_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def print_summary(report: dict) -> None:
    """stdout 人话摘要：逐源一段 + 判定矩阵 + takeover 试验结论。"""
    print(f"🔎 Windows cookie 只读探针（platform={report['platform']}，"
          "不解密、只读用户目录）")
    sources = report.get("sources") or {}
    for source in CHROMIUM_SOURCES:
        block = sources.get(source) or {}
        if not block.get("installed"):
            print(f"\n[{source}] 未安装（或路径不可见）")
            continue
        print(f"\n[{source}] 已安装: {block.get('user_data_dir')}")
        crypt = block.get("crypt") or {}
        print(f"  加密形态: 旧式 DPAPI key="
              f"{_yn(crypt.get('dpapi_legacy_key'))}  app-bound(APPB)="
              f"{_yn(crypt.get('app_bound_key'))}")
        for prof in block.get("profiles") or []:
            db = prof.get("cookies_db") or "无 Cookies 库"
            label = prof["dir"] + (f"（{prof['display_name']}）"
                                   if prof.get("display_name") else "")
            print(f"  profile {label}: {db}")
    ff = sources.get("firefox") or {}
    if not ff.get("installed"):
        print("\n[firefox] 未安装（或路径不可见）")
    else:
        print(f"\n[firefox] 已安装: {ff.get('root')}")
        for prof in ff.get("profiles") or []:
            if prof.get("error"):
                print(f"  profile {prof['path']}: {prof['error']}")
                continue
            if prof.get("target_cookies"):
                print(f"  profile {prof['path']}: 目标域 cookie "
                      f"{prof['target_cookies']} 条"
                      f"（未过期 {prof['target_cookies_unexpired']} 条）→ "
                      "W-A Firefox 通道可用")
            else:
                print(f"  profile {prof['path']}: 目标域 cookie 0 条")
    print("\n判定矩阵（W-B 通道判定）:")
    matrix = report.get("matrix") or []
    if not matrix:
        print("  （无可抽样的 Chromium profile）")
    for row in matrix:
        if row.get("error"):
            print(f"  {row['source']}/{row['profile']}: 抽样失败（{row['error']}）")
            continue
        ratio = row.get("v20_ratio") or 0.0
        verdict = {"takeover": "需副本接管通道（W-B）",
                   "dpapi_v10": "DPAPI v10 直解通道可用（W-B'）",
                   "undetermined": "无抽样行，不可判定"}.get(
                       row.get("channel"), row.get("channel"))
        print(f"  {row['source']}/{row['profile']}: 抽样 目标 "
              f"{row.get('target_rows', 0)}/其他 {row.get('other_rows', 0)}，"
              f"v20 占比 {ratio * 100:.1f}%（v10 {row['prefix_counts'].get('v10', 0)}"
              f"/v11 {row['prefix_counts'].get('v11', 0)}"
              f"/v20 {row['prefix_counts'].get('v20', 0)}"
              f"/明文 {row['prefix_counts'].get('plain', 0)}）→ {verdict}")
    takeover = report.get("takeover_test")
    print("\n副本接管试验（B-T4 可行性）:")
    if not takeover:
        print("  未执行（默认关；--takeover-test 开启）")
    elif takeover.get("skipped"):
        print(f"  跳过：{takeover['skipped']}")
    else:
        print(f"  CDP 起={_yn(takeover.get('cdp_up'))}  "
              f"目标域明文 cookie={takeover.get('target_cookies')}  "
              f"最小集足够={_yn(takeover.get('minimal_set_sufficient'))}  "
              f"清理={_yn(takeover.get('cleaned'))}"
              + (f"  error={takeover.get('error')}" if takeover.get("error") else ""))


def _yn(value: Any) -> str:
    if value is None:
        return "未知"
    return "是" if value else "否"


def run_cli(takeover_test: bool = False, out: str | None = None) -> int:
    """CLI 主流程（cli.py probe-win-cookies 薄壳调用）。exit 0 正常完成
    （含无源浏览器）；exit 1 只留给脚本自身异常。"""
    try:
        report = collect_report(takeover_test=takeover_test)
        path = write_report(report, out)
    except Exception as exc:
        print(f"❌ 探针自身异常: {type(exc).__name__}: {exc}", flush=True)
        return 1
    print_summary(report)
    print(f"\n📄 报告已写入: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe-win-cookies",
        description="Windows 1688/Ozon cookie 只读探针：源浏览器/加密形态/通道"
                    "判定矩阵（不解密、不写用户目录；--takeover-test 附加副本"
                    "接管可行性试验）")
    parser.add_argument("--takeover-test", action="store_true",
                        help="附加副本接管可行性试验（默认关：最小复制集→临时目录→"
                             "真实浏览器 headless CDP，finally 清理）")
    parser.add_argument("--out", default=None,
                        help="报告 JSON 输出路径（默认 data/probe/win_cookies_<ts>.json）")
    args = parser.parse_args(argv)
    return run_cli(takeover_test=bool(args.takeover_test), out=args.out)


if __name__ == "__main__":
    sys.exit(main())
