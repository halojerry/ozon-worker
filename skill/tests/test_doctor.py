# -*- coding: utf-8 -*-
"""doctor（skill 整体体检）核心检查器单测——v0.84.0。

六区检查器全部纯函数化 + ``_skill_root`` 可注入 tmp，零网络（requests/socket
全 mock）。运行：
    cd skill && .venv314/bin/python -m pytest tests/test_doctor.py -q
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import doctor
from scripts.lib.doctor import (FAIL, PASS, SKIP, WARN, DoctorResult,
                                check_credentials_present, check_doc_sync,
                                check_envelope_parity, check_mcp_tools,
                                check_version_consistency, check_worker_url,
                                render_json, render_text, run_doctor)


# ────────────────── 工具：tmp skill 根 ──────────────────


def _mk_root(tmp_path, version="0.84.0", front=None, cli_cmds=("graph",), doc_cmds=("graph",),
             keys_json=None, script_src="", mcp_src=None, mcp_doc=None):
    """构造最小 skill 根布局；front=None 用 version 同值。"""
    root = tmp_path / "skill"
    (root / "scripts" / "lib").mkdir(parents=True, exist_ok=True)
    if version is not None:
        (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    front = front if front is not None else version
    (root / "SKILL.md").write_text(
        f"---\nname: t\nversion: \"{front}\"\n---\n\n"
        + ("\n".join(f"| `{c}` | x |" for c in doc_cmds) if doc_cmds else ""),
        encoding="utf-8")
    (root / "scripts" / "cli.py").write_text(
        "\n".join(f'sub.add_parser("{c}")' for c in cli_cmds), encoding="utf-8")
    if script_src:
        (root / "scripts" / "cloud_probe.py").write_text(script_src, encoding="utf-8")
    if keys_json is not None:
        api = tmp_path / "api-integration"
        api.mkdir(exist_ok=True)
        (api / "envelope-keys.json").write_text(json.dumps(keys_json), encoding="utf-8")
    if mcp_src is not None:
        pkg = tmp_path / "pounding-mcp" / "pounding_mcp"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "server.py").write_text(mcp_src, encoding="utf-8")
    if mcp_doc is not None:
        docs = tmp_path / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "MCP-SERVER.md").write_text(mcp_doc, encoding="utf-8")
    return root


@pytest.fixture()
def patch_root(monkeypatch):
    def _patch(root):
        monkeypatch.setattr(doctor, "_skill_root", lambda: str(root))
    return _patch


# ────────────────── 1. WORKER_URL 语义 ──────────────────


def test_worker_url_placeholder_host_fails(monkeypatch):
    """EY 病机复现：WORKER_URL=http://x → 语义层 FAIL + NEXT 给正确地址。"""
    monkeypatch.setenv("WORKER_URL", "http://x")
    r = check_worker_url()
    assert r.status == FAIL and "localhost" in r.next_hint


def test_worker_url_no_scheme_fails(monkeypatch):
    monkeypatch.setenv("WORKER_URL", "worker.mxou.cn")
    r = check_worker_url()
    assert r.status == FAIL


def test_worker_url_localhost_passes_with_mock_health(monkeypatch):
    monkeypatch.setenv("WORKER_URL", "http://localhost:8080")

    class _Resp:
        status_code = 200

    import scripts.lib.doctor as d
    monkeypatch.setattr("requests.get", lambda *a, **kw: _Resp(), raising=False)
    r = check_worker_url()
    assert r.status == PASS


def test_worker_url_unreachable_is_warn_not_fail(monkeypatch):
    """语义合法但网络不通 → WARN（网络抖动≠配置错）。"""
    monkeypatch.setenv("WORKER_URL", "http://localhost:9999")

    def _boom(*a, **kw):
        raise ConnectionError("refused")

    import requests
    monkeypatch.setattr(requests, "get", _boom)
    r = check_worker_url()
    assert r.status == WARN


# ────────────────── 2. 版本一致性 ──────────────────


def test_version_match_pass(tmp_path, patch_root):
    patch_root(_mk_root(tmp_path, version="0.84.0"))
    assert check_version_consistency().status == PASS


def test_version_mismatch_fail(tmp_path, patch_root):
    patch_root(_mk_root(tmp_path, version="0.84.0", front="0.83.1"))
    r = check_version_consistency()
    assert r.status == FAIL and "0.83.1" in r.detail


# ────────────────── 3. 文档同步 ──────────────────


def test_doc_sync_ok(tmp_path, patch_root):
    patch_root(_mk_root(tmp_path, cli_cmds=("graph", "doctor"), doc_cmds=("graph", "doctor")))
    assert check_doc_sync().status == PASS


def test_doc_sync_missing_cmd_fail(tmp_path, patch_root):
    patch_root(_mk_root(tmp_path, cli_cmds=("graph", "doctor"), doc_cmds=("graph",)))
    r = check_doc_sync()
    assert r.status == FAIL and "doctor" in r.detail


# ────────────────── 4. 契约 parity ──────────────────


def test_parity_skip_without_repo_layout(tmp_path, patch_root):
    root = _mk_root(tmp_path)  # keys_json=None → 无 api-integration
    patch_root(root)
    assert check_envelope_parity().status == SKIP


def test_parity_pass(tmp_path, patch_root):
    root = _mk_root(tmp_path, keys_json={"extensions": {"ozon_client_id": {}, "margin_rate": {}}},
                    script_src='extensions["ozon_client_id"] = cid\n')
    patch_root(root)
    assert check_envelope_parity().status == PASS


def test_parity_unknown_key_fails(tmp_path, patch_root):
    root = _mk_root(tmp_path, keys_json={"extensions": {"ozon_client_id": {}}},
                    script_src='extensions["totally_new_key"] = 1\n')
    patch_root(root)
    r = check_envelope_parity()
    assert r.status == FAIL and "totally_new_key" in r.detail


def test_parity_self_file_excluded(tmp_path, patch_root):
    """doctor.py 自身注释里的示例形态不得自污染提取（实锤修过的假阳性）。"""
    root = _mk_root(tmp_path, keys_json={"extensions": {"ozon_client_id": {}}},
                    script_src='extensions["ozon_client_id"] = 1\n')
    # 仿造一个含示例注释的 doctor.py（提取器应跳过它）
    (root / "scripts" / "lib" / "doctor.py").write_text(
        'extensions["key"] = ...  # 示例注释', encoding="utf-8")
    patch_root(root)
    assert check_envelope_parity().status == PASS


# ────────────────── 5. MCP 对账 ──────────────────


def test_mcp_skip_without_sibling(tmp_path, patch_root):
    patch_root(_mk_root(tmp_path))
    assert check_mcp_tools().status == SKIP


def test_mcp_count_mismatch_fails(tmp_path, patch_root):
    root = _mk_root(tmp_path,
                    mcp_src="@mcp.tool\ndef a(): pass\n@mcp.tool\ndef b(): pass\n",
                    mcp_doc="共 30 个工具")
    patch_root(root)
    r = check_mcp_tools()
    assert r.status == FAIL and "2" in r.detail and "30" in r.detail


def test_mcp_count_match_passes(tmp_path, patch_root):
    root = _mk_root(tmp_path, mcp_src="@mcp.tool\ndef a(): pass\n", mcp_doc="共 1 个工具")
    patch_root(root)
    assert check_mcp_tools().status == PASS


# ────────────────── 6. 聚合与渲染 ──────────────────


def test_credentials_missing_fails(monkeypatch, tmp_path, patch_root):
    patch_root(_mk_root(tmp_path))

    class _FakeCS:
        @staticmethod
        def get_mxou_token():
            return ""

        @staticmethod
        def get_ali_1688_ak():
            return ""

        @staticmethod
        def list_stores():
            return []

    monkeypatch.setattr("scripts.lib.config_store.get_mxou_token", _FakeCS.get_mxou_token)
    monkeypatch.setattr("scripts.lib.config_store.get_ali_1688_ak", _FakeCS.get_ali_1688_ak)
    monkeypatch.setattr("scripts.lib.config_store.list_stores", _FakeCS.list_stores)
    r = check_credentials_present()
    assert r.status == FAIL and "MXOU_TOKEN" in r.detail


def test_run_doctor_exit_code_and_crash_isolation(monkeypatch):
    """单项检查器抛异常 → 降级 WARN 不拖垮整体；FAIL 存在 → 出口码 1。"""
    def _boom():
        raise RuntimeError("炸了")

    def _fail():
        return DoctorResult("坏项", FAIL, "x", "NEXT-X")

    def _pass():
        return DoctorResult("好项", PASS, "y")

    monkeypatch.setattr(doctor, "ALL_CHECKS", (("测试区", (_boom, _fail, _pass)),))
    results, code = run_doctor()
    assert code == 1
    by_name = {r.name: r for r in results}
    assert by_name["_boom"].status == WARN and "炸了" in by_name["_boom"].detail
    assert by_name["坏项"].status == FAIL
    text = render_text(results)
    assert "NEXT-X" in text and "坏项" in text
    payload = json.loads(render_json(results, code))
    assert payload["exit_code"] == 1 and payload["summary"]["FAIL"] >= 1


def test_render_text_all_pass_no_next_hint():
    rs = [DoctorResult("甲", PASS, "ok", section="S1")]
    text = render_text(rs)
    assert "✅" in text and "无阻塞项" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
