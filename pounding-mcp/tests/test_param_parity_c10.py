#!/usr/bin/env python3
"""v0.75 收口批 C10：pounding-mcp 30 工具参数差分核查——①类漂移修复的映射单测。

对照 skill/scripts/cli.py argparse 全集逐工具差分（docs/MCP-SERVER.md
「参数差分核查表（v0.75）」）后修的 4 工具 7 参：
1. search 补 export/threads → --export/--threads（threads 缺省 None=不进 argv，
   CLI 默认 3 逐字保持）
2. image_search 补 ozon_product_id → --ozon-product-id（货源匹配工作台上报）
3. queries 补 export/output → --export/--output
4. session_sync 补 status/cdp_url → --status/--cdp-url

锁定两条不变式：新参传值必进 argv（正确 flag 名）；缺省绝不进 argv
（旧行为逐字保持，对齐 B1 手法）。登记类（③语义漂移）不在此锁：
cleanup 硬编码 --all --dry-run 与 discover_task dry_run 默认 True 见核查表。

全链走真实 server 工具函数 → TaskManager.run_and_record / run_skill_command_capture，
仅 subprocess 边界 mock 捕获 argv；任务注册表落盘用 tmp_path 隔离。
运行：cd pounding-mcp && .venv/bin/python -m pytest tests/test_param_parity_c10.py -q
"""

from __future__ import annotations

import pytest

from pounding_mcp import server, skill_runner
from pounding_mcp.tasks import CollectTaskManager

# 夹具假值：命名刻意避开密钥关键词（防 gitleaks generic-api-key 误报）
_FAKE_PATH = "/tmp/fake_parity_c10_out"
_FAKE_PID = "FakeProductId-0123456789"


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


# ── search：+export / +threads ────────────────────────────────────────

def test_search_export_threads_map_to_cli(capture_argv):
    """export/threads → --export/--threads（query 仍走位置参数）。"""
    server.search(query="手机壳", export=f"{_FAKE_PATH}.csv", threads=4)
    argv = capture_argv["argv"]
    assert "search" in argv
    assert "手机壳" in argv  # 位置参数不回归（test_search_positional 的锁定面）
    assert _flag_value(argv, "--export") == f"{_FAKE_PATH}.csv"
    assert _flag_value(argv, "--threads") == "4"


def test_search_new_params_default_omitted(capture_argv):
    """缺省（export=""/threads=None）两 flag 不进 argv——旧行为逐字保持。"""
    server.search(query="手机壳")
    argv = capture_argv["argv"]
    assert "--export" not in argv, f"缺省 export 不应出现在 argv: {argv}"
    assert "--threads" not in argv, f"缺省 threads 不应出现在 argv: {argv}"


# ── image_search：+ozon_product_id ────────────────────────────────────

def test_image_search_ozon_product_id_maps_to_cli(capture_argv):
    """ozon_product_id → --ozon-product-id（图搜结果上报货源匹配工作台）。"""
    server.image_search(image="https://cbu01.alicdn.com/img/fake.jpg",
                        ozon_product_id=_FAKE_PID)
    argv = capture_argv["argv"]
    assert "image_search" in argv
    assert _flag_value(argv, "--ozon-product-id") == _FAKE_PID


def test_image_search_ozon_product_id_default_omitted(capture_argv):
    """缺省（""）→ 无 --ozon-product-id。"""
    server.image_search(image="https://cbu01.alicdn.com/img/fake.jpg")
    assert "--ozon-product-id" not in capture_argv["argv"]


# ── queries：+export / +output ────────────────────────────────────────

def test_queries_export_output_map_to_cli(capture_argv):
    """export/output → --export/--output（export="json" 不落盘=结构化 JSON）。"""
    server.queries(type="all-queries", export="json", output=f"{_FAKE_PATH}.json")
    argv = capture_argv["argv"]
    assert "queries" in argv
    assert _flag_value(argv, "--export") == "json"
    assert _flag_value(argv, "--output") == f"{_FAKE_PATH}.json"


def test_queries_new_params_default_omitted(capture_argv):
    """缺省（""/""）→ 两 flag 不进 argv（CLI 默认 csv 打 stdout，行为不变）。"""
    server.queries(type="ozon-bestsellers")
    argv = capture_argv["argv"]
    assert "--export" not in argv
    assert "--output" not in argv


# ── session_sync：+status / +cdp_url ──────────────────────────────────

def test_session_sync_status_and_cdp_url_map_to_cli(monkeypatch):
    """status/cdp_url → --status/--cdp-url（status=True 只查状态不收割）。"""
    captured: dict = {}

    def fake_run(argv, **kw):
        captured["argv"] = list(argv)
        return _FakeProc(stdout="会话状态（脱敏，不含 cookie 值）\n   状态: active\n")

    monkeypatch.setattr(skill_runner.subprocess, "run", fake_run)
    server.session_sync(credential_id="cred-uuid-c10", status=True,
                        cdp_url="http://127.0.0.1:9333")
    argv = captured["argv"]
    assert "session-sync" in argv  # 别名表映射
    assert "--status" in argv
    assert _flag_value(argv, "--cdp-url") == "http://127.0.0.1:9333"


def test_session_sync_new_params_default_omitted(monkeypatch):
    """缺省（status=False/cdp_url=""）→ 两 flag 不进 argv（A6 行为逐字保持）。"""
    captured: dict = {}

    def fake_run(argv, **kw):
        captured["argv"] = list(argv)
        return _FakeProc(stdout="{}")

    monkeypatch.setattr(skill_runner.subprocess, "run", fake_run)
    server.session_sync(credential_id="cred-uuid-c10")
    argv = captured["argv"]
    assert "--status" not in argv
    assert "--cdp-url" not in argv
