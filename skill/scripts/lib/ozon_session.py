#!/usr/bin/env python3
"""Ozon session cookie 备份/恢复 — 持久化复用登录态。

skill 的 Chrome 常驻 + profile 持久化保证了 Ozon 登录态日常不丢；
本模块加一层保险: CDP 读全 Ozon 域 cookie → 原子写备份 → 登录态被清
(profile 损坏/误清理/技能重装) 时用 Storage.setCookies 恢复, 免重新登录。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

from scripts._const import CACHE_DIR

OZON_DOMAINS = (".ozon.ru", ".ozone.ru", "ozon.ru", "www.ozon.ru", "seller.ozon.ru", "sso.ozon.ru")

_BACKUP_TTL = 7 * 86400  # 备份有效期 7 天


def _backup_path() -> Path:
    return CACHE_DIR / "ozon_session_backup.json"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        tmp.replace(path)
    except OSError:
        time.sleep(0.05)
        tmp.replace(path)


def _read_backup() -> dict:
    path = _backup_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        backed = data.get("backed_at", 0) or 0
        if time.time() - backed > _BACKUP_TTL:
            return {}
        return data
    except (ValueError, OSError):
        return {}


def _from_cdp(cdp_url: str = "http://127.0.0.1:9222") -> list[dict]:
    """从常驻 Chrome 读全部 Ozon 域 cookie(含 httpOnly)，仅读不导航。"""
    conn = None
    tab = None
    try:
        from scripts.lib.cdp_client import CdpConnection
        conn = CdpConnection(cdp_url)
        tab = conn.new_tab("about:blank")
        msg_id = tab._send("Storage.getCookies", {})
        resp = tab._recv_until_id(msg_id, timeout=10) or {}
        cookies = (resp.get("result") or {}).get("cookies") or []
        _result = []
        for c in cookies:
            dom = str(c.get("domain") or "")
            if any(dom.endswith(d) for d in OZON_DOMAINS if d.startswith(".")):
                _result.append({
                    "name": c.get("name"),
                    "value": c.get("value"),
                    "domain": dom,
                    "path": c.get("path") or "/",
                    "secure": bool(c.get("secure")),
                    "httpOnly": bool(c.get("httpOnly")),
                    "sameSite": c.get("sameSite"),
                    "expires": c.get("expires"),
                })
        return _result
    except Exception as exc:
        logger.debug("ozon_session 读取 cookie 失败(%s)", exc)
        return []
    finally:
        if tab:
            try:
                tab.close()
            except Exception:
                pass


def backup(cdp_url: str = "http://127.0.0.1:9222") -> dict:
    """CDP 读 Ozon cookie → 原子写备份。返回 {"ok", "count", "message"}。"""
    cookies = _from_cdp(cdp_url)
    if not cookies:
        return {"ok": False, "count": 0, "message": "未读到 Ozon cookie(Chrome 未运行或未登录)"}
    _atomic_write(_backup_path(), {
        "backed_at": time.time(),
        "cookies": cookies,
    })
    logger.info("Ozon session 备份完成: %d 个 cookie", len(cookies))
    return {"ok": True, "count": len(cookies), "message": f"备份 {len(cookies)} 个 Ozon cookie"}


def restore(cdp_url: str = "http://127.0.0.1:9222") -> dict:
    """从备份恢复 Ozon cookie → Storage.setCookies 注入。返回 {"ok", "count", "message"}。"""
    data = _read_backup()
    cookies = data.get("cookies") or []
    if not cookies:
        return {"ok": False, "count": 0, "message": "无有效备份(过期或不存在)"}
    conn = None
    tab = None
    try:
        from scripts.lib.cdp_client import CdpConnection
        conn = CdpConnection(cdp_url)
        tab = conn.new_tab("about:blank")
        payload = []
        for c in cookies:
            item = {"name": c.get("name"), "value": c.get("value"), "domain": c.get("domain")}
            if c.get("path"):
                item["path"] = c["path"]
            if c.get("secure"):
                item["secure"] = True
            if c.get("httpOnly"):
                item["httpOnly"] = True
            if c.get("sameSite"):
                item["sameSite"] = c["sameSite"]
            payload.append(item)
        msg_id = tab._send("Storage.setCookies", {"cookies": payload})
        resp = tab._recv_until_id(msg_id, timeout=10)
        ok = bool(resp and "error" not in resp.get("error", {}))
        if ok:
            logger.info("Ozon session 恢复完成: %d 个 cookie", len(payload))
        return {"ok": ok, "count": len(payload), "message": f"恢复 {len(payload)} 个 Ozon cookie" if ok else "恢复失败"}
    except Exception as exc:
        logger.warning("ozon_session 恢复失败(%s)", exc)
        return {"ok": False, "count": 0, "message": f"恢复失败: {exc}"}
    finally:
        if tab:
            try:
                tab.close()
            except Exception:
                pass
