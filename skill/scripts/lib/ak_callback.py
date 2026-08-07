#!/usr/bin/env python3
"""
1688 AK 获取回调服务器

通过浏览器获取 1688 AK，写入本地配置文件。
仅支持 AK 模式（非 OAuth），无外部依赖。

用法:
  from scripts.lib.ak_callback import get_ak_via_browser
  result = get_ak_via_browser(timeout=300)
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

logger = logging.getLogger(__name__)

# ── 常量 ──
AUTHORIZE_ENDPOINT = "https://air.1688.com/app/tai/oauth_page/index.html"
CALLBACK_HOST = "localhost"
CALLBACK_BIND_ADDRESS = "127.0.0.1"
CALLBACK_PORT_START = 10000
CALLBACK_PORT_RETRIES = 10

# AK 写入路径（与 ak_1688_client.py 的 get_ak_from_file 兼容）
from scripts._const import SKILL_ROOT

_AK_STORE_DIR = SKILL_ROOT / ".1688-AK"
_AK_STORE_FILE = _AK_STORE_DIR / ".ak_store.json"


def _resolve_ak_store_path() -> Path:
    """确定 AK 存储文件路径，优先已有目录。"""
    candidates = [
        SKILL_ROOT / ".1688-AK",
        Path.home() / ".1688-AK",
        Path.home() / "workspace" / ".1688-AK",
    ]
    for d in candidates:
        if (d / ".ak_store.json").exists():
            return d / ".ak_store.json"
    _AK_STORE_DIR.mkdir(parents=True, exist_ok=True)
    return _AK_STORE_FILE


AK_STORE_PATH = _resolve_ak_store_path()


def _validate_ak(ak: str) -> tuple[bool, str]:
    """校验 AK 格式"""
    if not ak:
        return False, "AK 不能为空"
    if len(ak) < 32:
        return False, f"AK 长度不足 (当前 {len(ak)}, 至少 32 位)"
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-=")
    if not all(c in allowed for c in ak):
        return False, "AK 包含非法字符"
    return True, ""


def _save_ak(ak: str) -> tuple[bool, str]:
    """保存 AK 到配置文件"""
    try:
        # 保留 .1688-AK/.ak_store.json（与 get_ak_from_file 兼容）
        AK_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(AK_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump({"ak": ak}, f, ensure_ascii=False, indent=2)
        # 写入 config_store (settings.json)
        from scripts.lib.config_store import set_ali_1688_ak
        set_ali_1688_ak(ak)
        return True, str(AK_STORE_PATH)
    except Exception as e:
        return False, str(e)


def _error_html(error_message: str, error_code: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>AK 设置失败</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;background:#fef2f2}}
.card{{text-align:center;padding:3rem 2.5rem;border-radius:1rem;background:#fff;box-shadow:0 4px 24px rgba(0,0,0,.08);max-width:480px;width:90%}}
.icon{{font-size:3rem;margin-bottom:1rem}}
h1{{color:#dc2626;font-size:1.5rem;margin-bottom:.75rem}}
p{{color:#666;line-height:1.6}}
code{{font-family:monospace;background:#fee2e2;color:#991b1b;padding:.25rem .5rem;border-radius:.25rem;font-size:.875rem}}
</style></head><body><div class="card">
<div class="icon">&#10008;</div><h1>AK 设置失败</h1>
<p>{error_message}</p><p><code>{error_code}</code></p>
</div></body></html>"""


def _success_html() -> str:
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>AK 设置成功</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,sans-serif;display:flex;justify-content:center;align-items:center;min-height:100vh;background:#f8f9fa}}
.card{{text-align:center;padding:3rem 2.5rem;border-radius:1rem;background:#fff;box-shadow:0 4px 24px rgba(0,0,0,.06);max-width:480px;width:90%}}
.icon{{font-size:4rem;color:#16a34a;margin-bottom:1rem}}
h1{{color:#16a34a;font-size:1.5rem;margin-bottom:.75rem}}
p{{color:#666;line-height:1.6}}
</style></head><body><div class="card">
<div class="icon">&#10004;</div><h1>AK 设置成功！</h1>
<p>1688 AK 已保存到本地，您可以关闭此页面。</p>
</div></body></html>"""


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def get_ak_via_browser(timeout: int = 300) -> dict[str, Any]:
    """
    通过浏览器获取 1688 AK。

    1. 启动本地 HTTP 回调服务器
    2. 打开浏览器到 1688 AK 授权页
    3. 用户登录 → 回调保存 AK
    4. 返回结果

    Returns:
        {"success": bool, "ak": str (masked), "path": str}
    """
    state = secrets.token_urlsafe(32)
    success = False
    result: dict[str, Any] = {"success": False}
    done = threading.Event()
    server: HTTPServer | None = None
    port = CALLBACK_PORT_START

    # 寻找可用端口
    for attempt in range(CALLBACK_PORT_RETRIES):
        port = CALLBACK_PORT_START + attempt
        try:
            server_ref = {"success": False, "result": {}}

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    parsed = urlparse(self.path)
                    if parsed.path == "/callback":
                        params = parse_qs(parsed.query)
                        if "error" in params:
                            err = params["error"][0]
                            desc = params.get("error_description", ["用户拒绝"])[0]
                            html = _error_html(desc, err)
                            self._respond_html(200, html)
                            server_ref["result"] = {"success": False, "error": err, "error_description": desc}
                            done.set()
                            return

                        received_state = params.get("state", [None])[0]
                        if received_state != state:
                            html = _error_html("安全校验失败 (state 不匹配)", "STATE_MISMATCH")
                            self._respond_html(400, html)
                            return

                        code = params.get("code", [None])[0]
                        if not code:
                            html = _error_html("缺少 authorization_code", "MISSING_CODE")
                            self._respond_html(400, html)
                            return

                        # 验证并保存 AK
                        is_valid, err_msg = _validate_ak(code)
                        if not is_valid:
                            html = _error_html(err_msg, "AK_INVALID")
                            self._respond_html(200, html)
                            server_ref["result"] = {"success": False, "error": "AK_INVALID", "error_description": err_msg}
                            done.set()
                            return

                        ok, location = _save_ak(code)
                        if ok:
                            html = _success_html()
                            self._respond_html(200, html)
                            server_ref["success"] = True
                            server_ref["result"] = {"success": True, "ak": code[:4] + "****" + code[-4:], "path": location}
                        else:
                            html = _error_html(f"保存失败: {location}", "SAVE_FAILED")
                            self._respond_html(200, html)
                            server_ref["result"] = {"success": False, "error": "SAVE_FAILED", "error_description": location}
                        done.set()

                    elif parsed.path == "/api/shutdown":
                        self._respond_json(200, {"success": True})
                        done.set()
                    else:
                        self.send_error(404)

                def do_POST(self):
                    parsed = urlparse(self.path)
                    if parsed.path == "/api/shutdown":
                        self._respond_json(200, {"success": True})
                        done.set()
                    else:
                        self.send_error(404)

                def _respond_html(self, status, html):
                    body = html.encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def _respond_json(self, status, data):
                    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, fmt, *args):
                    logger.debug(fmt, *args)

            server = _ThreadingHTTPServer((CALLBACK_BIND_ADDRESS, port), Handler)
            break
        except OSError:
            if attempt == CALLBACK_PORT_RETRIES - 1:
                return {"success": False, "error": "PORT_UNAVAILABLE",
                        "error_description": f"端口 {CALLBACK_PORT_START}-{port} 全部占用"}

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # 打开浏览器
    redirect_uri = f"http://{CALLBACK_HOST}:{port}/callback"
    params = {"mode": "AK", "state": state, "redirect_uri": redirect_uri}
    auth_url = f"{AUTHORIZE_ENDPOINT}?{urlencode(params)}"

    print("\n🌐 正在打开浏览器获取 1688 AK...")
    print(f"   如未自动打开，请手动访问:\n   {auth_url}\n")

    opened = webbrowser.open(auth_url)
    if not opened:
        print("⚠️ 无法自动打开浏览器，请手动复制上方链接")

    print(f"⏳ 等待完成 (超时 {timeout} 秒)...")

    try:
        completed = done.wait(timeout=timeout)
    except KeyboardInterrupt:
        server.shutdown()
        return {"success": False, "error": "USER_CANCELLED", "error_description": "用户取消"}

    server.shutdown()
    thread.join(timeout=3)

    if completed:
        _success = server_ref["success"]
        result = server_ref["result"]

    if not completed:
        result = {"success": False, "error": "TIMEOUT",
                  "error_description": f"{timeout} 秒内未完成操作"}

    # 失败时打印手动获取指引
    if not result.get("success"):
        print(f"\n❌ 自动获取 AK 失败: {result.get('error_description', '未知错误')}")
        print("   请手动获取 1688 AK:")
        print("   1. 浏览器打开 https://clawhub.1688.com → 登录 → 复制 AK")
        print("   2. 设置: python3.12 scripts/cli.py set_ak --ak <你的AK>\n")

    return result
