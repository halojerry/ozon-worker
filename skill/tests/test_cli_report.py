"""v0.70 错误报告通道进 skill —— `report` 子命令单测。

覆盖：请求体组装（_build_error_report_body）、argparse 注册、cmd_report
成功/4xx/无 token 三分支。纯 mock，不发网络请求。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.cli import _build_error_report_body, build_arg_parser, cmd_report  # noqa: E402


def _args(**over) -> argparse.Namespace:
    base = dict(
        title="测试问题上报",
        severity="medium",
        category="upload_failed",
        description="期望过审，实际拒单",
        step=["第一步", "第二步"],
        repro_command="cli.py graph --url x",
        expect="过审",
        actual="INCORRECT_DIMENSION",
        task_ids="id-1, id-2",
        draft_ids="",
        item_id="623236101606",
        offer_id="",
        ozon_product_id="",
        error_codes="INCORRECT_DIMENSION, VALUE_MAX_LIMIT",
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_body_full_assembly():
    body = _build_error_report_body(_args())
    assert body["title"] == "测试问题上报"
    assert body["severity"] == "medium"
    assert body["category"] == "upload_failed"
    assert body["description"] == "期望过审，实际拒单"
    assert body["reproduction"]["steps"] == ["第一步", "第二步"]
    assert body["reproduction"]["command"] == "cli.py graph --url x"
    assert body["reproduction"]["expect"] == "过审"
    assert body["reproduction"]["actual"] == "INCORRECT_DIMENSION"
    ev = body["evidence"]
    assert ev["task_ids"] == ["id-1", "id-2"]  # 逗号切分 + 去空白
    assert ev["error_codes"] == ["INCORRECT_DIMENSION", "VALUE_MAX_LIMIT"]
    assert ev["item_id"] == "623236101606"
    assert ev["platform"]  # 自动补 platform
    assert "draft_ids" not in ev and "offer_id" not in ev  # 空值键省略


def test_body_minimal_only_title():
    body = _build_error_report_body(_args(
        severity="medium", category="cli_bug", description="", step=[],
        repro_command="", expect="", actual="", task_ids="", draft_ids="",
        item_id="", offer_id="", ozon_product_id="", error_codes="",
    ))
    assert body["title"] == "测试问题上报"
    assert "reproduction" not in body
    assert set(body["evidence"]) <= {"platform", "skill_version"}  # 仅自动补项


def test_parser_registration():
    ns = build_arg_parser().parse_args([
        "report", "--title", "t",
        "--step", "a", "--step", "b",
        "--task-ids", "x1,x2",
    ])
    assert ns.command == "report"  # ⚠️ --command dest 撞名回归位：子命令名不被清空
    assert ns.repro_command == ""
    assert ns.title == "t"
    assert ns.step == ["a", "b"]
    assert ns.task_ids == "x1,x2"
    assert ns.severity == "medium"  # 缺省
    assert ns.category == "other"  # 缺省
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["report"])  # --title 必填


def test_cmd_report_success(monkeypatch, capsys):
    import scripts.lib.config_store as cs

    calls = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["url"] = url
        calls["body"] = json
        calls["auth"] = headers.get("Authorization", "")
        class _R:
            status_code = 200
            def json(self):
                return {"ok": True, "report_id": "rid-123", "tasks_attached": 2}
        return _R()

    monkeypatch.setattr(cs, "_require_auth", lambda: None)
    monkeypatch.setattr(cs, "get_mxou_token", lambda: "tok-test")
    monkeypatch.setattr("requests.post", fake_post)
    rc = cmd_report(_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "rid-123" in out
    assert calls["url"].endswith("/api/v1/error_reports")
    assert calls["auth"] == "Bearer tok-test"
    assert calls["body"]["evidence"]["task_ids"] == ["id-1", "id-2"]


def test_cmd_report_http_error(monkeypatch, capsys):
    import scripts.lib.config_store as cs

    def fake_post(url, json=None, headers=None, timeout=None):
        class _R:
            status_code = 422
            def json(self):
                return {"detail": "title 必填"}
        return _R()

    monkeypatch.setattr(cs, "_require_auth", lambda: None)
    monkeypatch.setattr(cs, "get_mxou_token", lambda: "tok-test")
    monkeypatch.setattr("requests.post", fake_post)
    assert cmd_report(_args()) == 1
    assert "上报失败" in capsys.readouterr().out


def test_cmd_report_no_token(monkeypatch, capsys):
    import scripts.lib.config_store as cs

    def _raise():
        raise cs.AuthError("缺少 MXOU_TOKEN")

    monkeypatch.setattr(cs, "_require_auth", _raise)
    assert cmd_report(_args()) == 1
    assert "缺少 MXOU_TOKEN" in capsys.readouterr().out
