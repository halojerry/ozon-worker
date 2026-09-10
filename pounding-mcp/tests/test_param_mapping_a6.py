#!/usr/bin/env python3
"""A6 能力对齐批（2026-09-11 repo-gov）参数映射单测。

对应 docs/audit/2026-09-11-repo-gov/A6-api-mcp-webui-alignment.md §3.3 / §6 #4 #5 #6 #8：
1. graph 补 category_id/type_id/min_density 三参 → --category-id/--type-id/--min-density
   （P2 #4：manual 权威类目直传在对话场景不可达的根治）
2. discover_task 补 expend_shop → --expend-shop（P2 #5：v0.74 拓店能力）
3. discover 补 note → --note（P3 #8：采集箱备注 agent 通道）
4. session_sync 新工具（P2 #6 / BL-13）：封装 session-sync CLI，退出码语义
   （2=缺 sc_company_id 拒传）+ 脱敏层（绝不回显 cookie 值）。

全链走真实 server 工具函数 → TaskManager.run_and_record → run_skill_command，
仅 subprocess 边界 mock 捕获 argv；任务注册表落盘用 tmp_path 隔离。
运行：cd pounding-mcp && .venv/bin/python -m pytest tests/test_param_mapping_a6.py -q
"""

from __future__ import annotations

import pytest

from pounding_mcp import server, skill_runner
from pounding_mcp.tasks import CollectTaskManager

# 夹具假值：命名刻意避开密钥关键词（防 gitleaks generic-api-key 误报）
_FAKE_VAL = "FakeValue_0123456789abcdefGHIJ"


class _FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "{}", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture()
def capture_argv(monkeypatch, tmp_path):
    """mock subprocess.run 捕获 argv + 隔离任务注册表落盘。"""
    captured: dict = {}

    def fake_run(argv, **kw):
        captured["argv"] = list(argv)
        return _FakeProc()

    monkeypatch.setattr(skill_runner.subprocess, "run", fake_run)
    mgr = CollectTaskManager(store_path=tmp_path / "tasks.json")
    monkeypatch.setattr(server, "get_manager", lambda: mgr)
    return captured


def _flag_value(argv: list[str], flag: str) -> str:
    assert flag in argv, f"argv 缺 {flag}: {argv}"
    return argv[argv.index(flag) + 1]


# ── A6 P2 #4：graph manual 权威类目直传三参 ──────────────────────────

def test_graph_category_type_density_map_to_cli(capture_argv):
    """category_id/type_id/min_density → --category-id/--type-id/--min-density。"""
    server.graph(url="https://detail.1688.com/offer/123.html",
                 category_id=15904, type_id=9262, min_density=0.1)
    argv = capture_argv["argv"]
    assert "graph" in argv
    assert _flag_value(argv, "--category-id") == "15904"
    assert _flag_value(argv, "--type-id") == "9262"
    assert _flag_value(argv, "--min-density") == "0.1"


def test_graph_new_params_default_omitted(capture_argv):
    """缺省（None）三个新参不得出现在 argv——旧行为逐字保持。"""
    server.graph(url="https://detail.1688.com/offer/123.html")
    argv = capture_argv["argv"]
    for flag in ("--category-id", "--type-id", "--min-density"):
        assert flag not in argv, f"缺省参数不应出现在 argv: {flag}"


# ── A6 P2 #5：discover_task 拓店参数 ─────────────────────────────────

def test_discover_task_expend_shop_maps_to_cli(capture_argv):
    """expend_shop → --expend-shop（v0.74 拓店模式，url 须为商品页种子）。"""
    server.discover_task(url="https://www.ozon.ru/product/xxx/",
                         expend_shop=20, to_box=True, dry_run=False)
    argv = capture_argv["argv"]
    assert "discover-task" in argv  # 别名映射（下划线命令名 ≠ CLI 连字符名）
    assert _flag_value(argv, "--expend-shop") == "20"
    assert "--to-box" in argv


def test_discover_task_expend_shop_default_omitted(capture_argv):
    """缺省（None）→ 无 --expend-shop（CLI 默认 0=关闭，行为与现状一致）。"""
    server.discover_task(keyword="留香珠")
    argv = capture_argv["argv"]
    assert "--expend-shop" not in argv


# ── A6 P3 #8：discover 采集箱备注 ────────────────────────────────────

def test_discover_note_maps_to_cli(capture_argv):
    """note → --note（to_box 入箱时随草稿存储；运营态，不进上架信封）。"""
    server.discover(keyword="留香珠", to_box=True, note="竞品跟卖候选，利润率待复核")
    argv = capture_argv["argv"]
    assert "discover" in argv
    assert _flag_value(argv, "--note") == "竞品跟卖候选，利润率待复核"
    assert "--to-box" in argv


# ── A6 P2 #6 / BL-13：session_sync 封装 ──────────────────────────────

_SESSION_SYNC_OK_STDOUT = (
    "✅ 会话已同步到 worker（脱敏摘要）\n"
    "   cookie 名单: sc_company_id, __Secure-access_token（共 2 条）\n"
    "   状态: active\n"
    "   harvested_at: 2026-09-11 10:00:00\n"
)


def test_session_sync_maps_flags_and_command_alias(monkeypatch):
    """credential_id/worker_url → --credential-id/--worker-url；命令名走别名表 session-sync。"""
    captured: dict = {}

    def fake_run(argv, **kw):
        captured["argv"] = list(argv)
        return _FakeProc(stdout=_SESSION_SYNC_OK_STDOUT)

    monkeypatch.setattr(skill_runner.subprocess, "run", fake_run)
    res = server.session_sync(credential_id="cred-uuid-1")
    argv = captured["argv"]
    assert "session-sync" in argv, f"命令名应经别名表映射为 session-sync: {argv}"
    assert "session_sync" not in argv
    assert _flag_value(argv, "--credential-id") == "cred-uuid-1"
    assert "--worker-url" not in argv  # 缺省空串跳过

    assert res["ok"] is True
    assert res["exit_code"] == 0
    assert res["credential_id"] == "cred-uuid-1"
    # CLI 脱敏摘要原样透传（名单+状态是 CLI 有意暴露的脱敏信息）
    assert "sc_company_id" in res["raw"]
    assert "active" in res["raw"]


def test_session_sync_worker_url_flag(monkeypatch):
    """worker_url 非空 → --worker-url 传给 CLI（本地调试用）。"""
    captured: dict = {}

    def fake_run(argv, **kw):
        captured["argv"] = list(argv)
        return _FakeProc(stdout=_SESSION_SYNC_OK_STDOUT)

    monkeypatch.setattr(skill_runner.subprocess, "run", fake_run)
    server.session_sync(credential_id="c1", worker_url="http://localhost:8080")
    assert _flag_value(captured["argv"], "--worker-url") == "http://localhost:8080"


def test_session_sync_exit2_missing_core_cookie(monkeypatch):
    """exit 2 = 缺核心 cookie sc_company_id 拒传 → ok=False + 补救提示，不 raise。"""
    def fake_run(argv, **kw):
        return _FakeProc(
            returncode=2,
            stdout="❌ 未收割到 sc_company_id：seller.ozon.ru 未登录或本机 Chrome 未加载过卖家后台。",
        )

    monkeypatch.setattr(skill_runner.subprocess, "run", fake_run)
    res = server.session_sync(credential_id="cred-uuid-1")
    assert res["ok"] is False
    assert res["exit_code"] == 2
    assert "seller.ozon.ru" in res["hint"]


def test_session_sync_exit1_worker_error(monkeypatch):
    """exit 1 = worker 不可达/鉴权失败 → ok=False + 通用提示，不 raise。"""
    def fake_run(argv, **kw):
        return _FakeProc(returncode=1, stdout="❌ 上传失败：Worker 不可达 http://x")

    monkeypatch.setattr(skill_runner.subprocess, "run", fake_run)
    res = server.session_sync(credential_id="cred-uuid-1")
    assert res["ok"] is False
    assert res["exit_code"] == 1
    assert "worker_url" in res["hint"] or "credential_id" in res["hint"]


# ── 脱敏层红线单测（绝不回显 cookie 值）───────────────────────────────

def test_redact_masks_cookie_kv_but_keeps_name_list():
    """cookie/token 键值对打码；名单行与状态保留（CLI 有意的脱敏输出）。"""
    text = (
        "✅ 会话已同步到 worker（脱敏摘要）\n"
        "   cookie 名单: sc_company_id, __Secure-access_token（共 2 条）\n"
        "   状态: active\n"
        "   harvested_at: 2026-09-11 10:00:00\n"
        "   leaked_cookie: sc_company_id=AbCdEfSECRETVALUE123456; Path=/\n"
        f"   access_token: {_FAKE_VAL}\n"
    )
    out = server._redact_secrets(text)
    assert "sc_company_id," in out            # 名单保留
    assert "__Secure-access_token" in out     # 名单保留
    assert "状态: active" in out
    assert "harvested_at: 2026-09-11 10:00:00" in out
    assert "SECRETVALUE" not in out           # 泄漏的键值对被打码
    assert _FAKE_VAL not in out
    assert server._REDACTED in out


def test_redact_masks_long_secret_blob():
    """≥48 连续密钥形态字符兜底打码（不依赖 key 命中）。"""
    blob = "Q" * 60
    out = server._redact_secrets(f"resp={blob}")
    assert blob not in out and server._REDACTED in out
    # 常规字段不打码：UUID（36）与时间戳不受影响
    keep = "id=6f9619ff-8b86-d011-b42d-00c04fc964ff at=2026-09-11T10:00:00"
    assert server._redact_secrets(keep) == keep


def test_session_sync_output_redacted_end_to_end(monkeypatch):
    """端到端：即使 CLI 异常输出 cookie 值，工具返回值也必须已打码。"""
    leaked = (
        "✅ 会话已同步\n"
        "   cookie 名单: sc_company_id（共 1 条）\n"
        "   set-cookie: __Host-abck=TOPSECRETCOOKIEVALUE9999; Path=/; Secure\n"
    )

    def fake_run(argv, **kw):
        return _FakeProc(stdout=leaked)

    monkeypatch.setattr(skill_runner.subprocess, "run", fake_run)
    res = server.session_sync(credential_id="cred-uuid-1")
    assert res["ok"] is True
    assert "TOPSECRETCOOKIEVALUE" not in str(res)
    assert "sc_company_id" in res["raw"]  # 名单保留
