# -*- coding: utf-8 -*-
"""v0.81 安全批 — SSRF/证书/假凭据加固（Mimosa 扫描 5×high 修复）回归测试。

四项修复的行为锁定：
1. task_processor webhook SSRF 加固：TASK_NOTIFY_URL 过 _validate_notify_url
   （scheme 白名单 + 内网/元数据/localhost/.internal 拒绝，默认生效；
   TASK_NOTIFY_ALLOW_PRIVATE=1 为运维显式逃生门）——通知是非致命旁路，
   被拒只 warning + 跳过发送，绝不抛出；async 版经 to_thread 复用同一校验。
2. repair_cards.py 证书校验：全文件无 verify=False（requests 默认 certifi 校验），
   且不再 import urllib3 压警告。
3. api/schemas.py 假凭据示例：全部 _examples 值无 sk-/AKIA/-----BEGIN 特征
   （OpenAPI 示例是文档面，占位值必须明显非密钥）。
4. fx_rate_service：模块加载断言 _FX_API_URL 为合法 http(s) URL
   （构建时常量、非用户输入，加载期断言给扫描器留复核证据）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_security_hardening_v081.py -q
"""
import os
import sys
import urllib.parse
from pathlib import Path
from unittest import mock

import pytest

os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

WORKER_ROOT = Path(__file__).resolve().parents[1]

from utils.task_processor import _send_task_notify, _validate_notify_url  # noqa: E402


# ══════════════ 1. webhook SSRF 校验（task_processor._validate_notify_url） ══════════════

@pytest.mark.parametrize("url", [
    "https://oapi.dingtalk.com/robot/send?access_token=abc",
    "http://sctapi.ftqq.com/SCT123456.send",
    "https://notify.example.com/hook",
    "https://worker.mxou.cn/api/v1/notify",
])
def test_notify_url_public_passes(url):
    """公网 http(s) URL 一律放行（返回 None=无拒绝理由）。"""
    assert _validate_notify_url(url) is None


@pytest.mark.parametrize("url", [
    "http://localhost:8080/hook",
    "http://localhost/hook",
    "https://my.internal/hook",
    "https://svc.ozon.internal:8000/notify",
    "http://foo.localhost/hook",
    # 元数据/loopback/内网段字面 IP
    "http://127.0.0.1:9000/hook",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.1.2.3/hook",
    "http://192.168.1.10/hook",
    "http://172.16.0.9/hook",
    "http://[::1]:8080/hook",
    "http://[fe80::1]/hook",
    "http://0.0.0.0/hook",
    # IPv4-mapped IPv6 内网绕过
    "http://[::ffff:10.0.0.1]/hook",
])
def test_notify_url_rejects_private_and_metadata(url):
    """内网主机名/元数据/私网段 IP 默认一律拒绝，返回非空拒绝理由。"""
    reason = _validate_notify_url(url)
    assert reason, f"应拒绝内网/元数据 URL {url}"


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/hook",
    "gopher://127.0.0.1:70/x",
    "",
    "not-a-url",
    "https://",
])
def test_notify_url_rejects_bad_scheme_and_unparseable(url):
    """scheme 白名单外 / 解析失败 / 缺 host 一律拒绝。"""
    assert _validate_notify_url(url), f"应拒绝非法 URL {url!r}"


def test_validate_never_raises():
    """校验函数本身对任意垃圾输入绝不抛异常（返回拒绝理由字符串）。"""
    for garbage in [None, 123, "\x00", "http://[", "https://用户@内网:99999/x", "http://"]:
        try:
            result = _validate_notify_url(garbage)  # type: ignore[arg-type]
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"_validate_notify_url({garbage!r}) 抛出异常: {exc}")
        assert result is None or isinstance(result, str)


def test_send_notify_blocked_skips_post(caplog):
    """内网目标默认跳过发送：requests.post 不被调用、函数不抛出（非致命旁路语义保持）。"""
    with mock.patch("utils.task_processor.requests.post") as post:
        with mock.patch.dict(os.environ, {"TASK_NOTIFY_URL": "http://169.254.169.254/latest/"}):
            os.environ.pop("TASK_NOTIFY_ALLOW_PRIVATE", None)
            # 不抛异常即通过（函数自身吞掉一切）
            _send_task_notify("t-1", "completed", {"product_id": "p1"}, {"notify": True})
        post.assert_not_called()


def test_send_notify_private_allowed_with_env():
    """TASK_NOTIFY_ALLOW_PRIVATE=1 → 内网目标放行（逃生门），且保持 allow_redirects=False。"""
    captured = {}

    def _fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)

        class _R:
            def raise_for_status(self):
                return None

        return _R()

    with mock.patch("utils.task_processor.requests.post", side_effect=_fake_post):
        with mock.patch.dict(os.environ, {
            "TASK_NOTIFY_URL": "http://10.0.0.5:8080/hook",
            "TASK_NOTIFY_ALLOW_PRIVATE": "1",
        }):
            _send_task_notify("t-2", "completed", {"product_id": "p2"}, {"notify": True})
    assert captured.get("url") == "http://10.0.0.5:8080/hook"
    assert captured.get("allow_redirects") is False  # v0.38.1 防重定向语义不得回退
    assert captured.get("timeout") == 5


def test_send_notify_public_url_sends():
    """公网 URL 默认路径不受加固影响：正常 POST（防回归把合法通知也拦掉）。"""
    captured = {}

    def _fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)

        class _R:
            def raise_for_status(self):
                return None

        return _R()

    with mock.patch("utils.task_processor.requests.post", side_effect=_fake_post):
        with mock.patch.dict(os.environ, {"TASK_NOTIFY_URL": "https://sctapi.ftqq.com/KEY.send"}):
            os.environ.pop("TASK_NOTIFY_ALLOW_PRIVATE", None)
            _send_task_notify("t-3", "failed", {"error_message": "x"}, {"notify": True})
    assert captured.get("url") == "https://sctapi.ftqq.com/KEY.send"


def test_send_notify_unparseable_url_no_raise():
    """env 配了垃圾 URL（scheme 非法）：跳过发送、不炸（try/except 语义保持）。"""
    with mock.patch("utils.task_processor.requests.post") as post:
        with mock.patch.dict(os.environ, {"TASK_NOTIFY_URL": "file:///etc/passwd"}):
            os.environ.pop("TASK_NOTIFY_ALLOW_PRIVATE", None)
            _send_task_notify("t-4", "completed", {}, {"notify": True})
        post.assert_not_called()


def test_escape_hatch_allows_internal_but_not_bad_scheme():
    """逃生门边界：TASK_NOTIFY_ALLOW_PRIVATE=1 放行内网主机名/IP 判定，
    但 scheme 白名单恒生效（file:// 即使开了逃生门也拒发）。"""
    assert _validate_notify_url("https://notify.internal/hook", allow_private=True) is None
    assert _validate_notify_url("http://10.0.0.5/hook", allow_private=True) is None
    assert _validate_notify_url("file:///etc/passwd", allow_private=True), (
        "scheme 校验不得被 TASK_NOTIFY_ALLOW_PRIVATE 绕过"
    )

    with mock.patch("utils.task_processor.requests.post") as post:
        with mock.patch.dict(os.environ, {
            "TASK_NOTIFY_URL": "file:///etc/passwd",
            "TASK_NOTIFY_ALLOW_PRIVATE": "1",
        }):
            _send_task_notify("t-5", "completed", {}, {"notify": True})
        post.assert_not_called()


# ══════════════ 2. repair_cards.py 证书校验（Mimosa: verify=False） ══════════════

def test_repair_cards_no_verify_false():
    """repair_cards.py 全文件禁止 verify=False（TLS 校验走 requests 默认 certifi）。"""
    src = (WORKER_ROOT / "scripts" / "repair_cards.py").read_text(encoding="utf-8")
    assert "verify=False" not in src, "repair_cards.py 不得关闭 TLS 证书校验"
    assert "disable_warnings" not in src, "不得压制 InsecureRequestWarning（无 verify=False 后不再需要）"
    assert "import urllib3" not in src, "urllib3 仅服务于 verify=False 警告压制，应一并移除"


# ══════════════ 3. api/schemas.py 假凭据示例（Mimosa: sk- 前缀踩密钥特征） ══════════════

_SECRET_MARKERS = ("sk-", "AKIA", "-----BEGIN")


def _walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(k)
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)


def test_schemas_examples_no_secret_markers():
    """所有 schema 的 OpenAPI examples 不得含密钥特征串（sk-/AKIA/-----BEGIN 前缀）。"""
    from pydantic import BaseModel

    import api.schemas as schemas_mod

    offenders = []
    checked = 0
    for name, obj in vars(schemas_mod).items():
        if not (isinstance(obj, type) and issubclass(obj, BaseModel)):
            continue
        extra = (obj.model_config or {}).get("json_schema_extra") or {}
        for s in _walk_strings(extra.get("examples", [])):
            checked += 1
            low = s.lower()
            if any(low.startswith(m.lower()) for m in _SECRET_MARKERS):
                offenders.append((name, s[:40]))
    assert checked > 20, f"应至少检查到 20 个示例字符串，实际 {checked}（遍历逻辑失效？）"
    assert not offenders, f"examples 含密钥特征值: {offenders}"


def test_schemas_source_no_sk_placeholder():
    """schemas.py 源码本身不得再出现 sk-xxxx/sk-yyyy 占位（防手改回潮；描述文案
    「带或不带 sk- 前缀」是 prose 不受影响）。"""
    src = (WORKER_ROOT / "src" / "api" / "schemas.py").read_text(encoding="utf-8")
    assert "sk-xxxx" not in src
    assert "sk-yyyy" not in src


# ══════════════ 4. fx_rate_service 出站目标（Mimosa: 构建时常量复核） ══════════════

def test_fx_api_url_scheme_legal_at_module_load():
    """模块加载即断言 _FX_API_URL 是合法 http(s) URL（import 成功本身就是断言通过）。"""
    import utils.fx_rate_service as fx

    parsed = urllib.parse.urlparse(fx._FX_API_URL)
    assert parsed.scheme in ("http", "https")
    assert parsed.netloc, "汇率 API host 不得为空"


def test_fx_api_url_is_module_constant():
    """_FX_API_URL 无任何 env/入参覆盖出口：全文件不得出现环境变量读它/拼接它。"""
    src = (WORKER_ROOT / "src" / "utils" / "fx_rate_service.py").read_text(encoding="utf-8")
    assert "environ" not in src, "汇率 API URL 必须保持构建时常量，不得从环境读入"
    assert "verify" not in src.lower(), "fx_rate_service 不得出现 verify 配置（保持 requests 默认）"
    assert "proxies" not in src.lower(), "fx_rate_service 不得出现代理配置"
