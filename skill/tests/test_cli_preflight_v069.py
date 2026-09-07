#!/usr/bin/env python3
"""v0.69 Wave 3 skill 侧三小修回归（T2.3 / T2.5 / T3.1）。

T2.3 — 409 重复提交提示 + exit code：生产实证（Windows）`graph --url <同URL>`
重复提交时 worker 返回 409 DUPLICATE_SUBMIT，cli 只 logger.error 到 stderr、
stdout 静默、return 0——用户以为成功。修复：提交失败 → stdout 一行人话
（含 error_code + error 简要）+ summary.submitted=False + submit_error +
return 3（与 1=AuthError / 2=ProductValidationError 区分）；成功路径
summary.submitted=True。

T2.5 — 反爬/源失效前置拦截：反爬页抓到 46 图 0 属性仍走完整提交流程；
源失效（1688 采购价 0）仍提交。preflight 纯函数
`_source_preflight(data_or_draft) -> (ok, reason)`：
  - attributes 空/缺 且 images 非空 → 反爬嫌疑；
  - purchase_cost 缺失/<=0 → 源失效嫌疑。
直接提交被拦 → 不调 submit_envelope，走 T2.3 失败语义（exit 3）；
`--to-box` 显式放行（人工兜底通道，只打一行 warning）。

T3.1 — `--min-density` 密度拦截：泡脚包 950g/14190cm³ 密度 0.07 g/cm³
只 WARNING 照单提交。`--min-density <阈值>`（g/cm³，默认 0=不拦截行为
完全不变）设了阈值且 density < 阈值 → 拦截不提交（exit 3）。检查函数
`_check_min_density(draft, min_density) -> (ok, reason)` 纯函数返回
(ok, msg)，由 cmd_graph 判定，避免信封组装深处抛异常。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_cli_preflight_v069.py -q
"""
from __future__ import annotations

import sys
import unittest.mock as mock
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import cli, cloud_probe  # noqa: E402

IMG = "https://cbu01.alicdn.com/img/ibank/2024/O/123/456.jpg"


# ── 夹具 ─────────────────────────────────────────────────────────────────


def _draft(**over) -> dict:
    """完整可提交 draft（attributes/images/purchase_cost 齐备 → 两道闸全通过）。"""
    d: dict = {
        "item_id": "123",
        "title": "测试商品",
        "purchase_cost": 12.5,
        "weight": 0,  # weight=0 跳过预估价网络查询（除非显式覆盖）
        "dimensions": {"length": 0, "width": 0, "height": 0},
        "images": [IMG],
        "attributes": {"颜色": "红色"},
    }
    d.update(over)
    return d


def _graph(draft: dict | None = None) -> dict:
    return {
        "token": "t", "ozon_client_id": "c", "ozon_api_key": "k",
        "envelope": {
            "draft": draft if draft is not None else _draft(),
            "source": {}, "extensions": {},
        },
    }


def _args(*extra: str):
    return cli.build_arg_parser().parse_args(["graph", "--item-id", "123", *extra])


def _run_cli(args, graph=None, submit_result=None, draft_result=None):
    """跑 cmd_graph（mock 全部网络/CDP/预估价），返回 (rc, payloads, submit_calls)。

    submit_result/draft_result 模拟 worker 响应；缺省 = 提交成功。
    """
    payloads: list[dict] = []
    submit_calls: list[dict] = []

    def _fake_graph(**kw):
        return graph if graph is not None else _graph()

    def _submit(g):
        submit_calls.append(g)
        return submit_result if submit_result is not None else {"ok": True, "task_id": "T-1"}

    def _draft_submit(g):
        submit_calls.append(g)
        return draft_result if draft_result is not None else {"ok": True, "draft_id": "D-1"}

    with mock.patch("scripts.lib.config_store.preflight_check", return_value=[]), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp",
                    return_value=(True, "ok")), \
         mock.patch("scripts.cloud_probe.build_graph_envelope_with_retry", _fake_graph), \
         mock.patch("scripts.cloud_probe.submit_envelope", side_effect=_submit), \
         mock.patch("scripts.cloud_probe.submit_draft", side_effect=_draft_submit), \
         mock.patch("scripts.lib.ozon_discovery._query_logistics_from_worker",
                    return_value=None), \
         mock.patch.object(cli, "_out", lambda payload: payloads.append(payload)):
        rc = cli.cmd_graph(args)
    return rc, payloads, submit_calls


# ── T2.5: _source_preflight 纯函数 ───────────────────────────────────────


class TestSourcePreflight:
    """反爬/源失效判定口径（生产实证锚点：46 图 0 属性 / 采购价 0）。"""

    def test_anti_crawl_46_images_zero_attrs(self):
        """生产锚点：46 real images but 0 attributes → 反爬嫌疑，含图数。"""
        ok, msg = cloud_probe._source_preflight(
            {"images": [f"u{i}.jpg" for i in range(46)], "attributes": {}})
        assert ok is False
        assert "反爬" in msg
        assert "46" in msg and "0 属性" in msg

    def test_anti_crawl_missing_attrs_key(self):
        """attributes 键整体缺失（非空图）→ 反爬嫌疑。"""
        ok, msg = cloud_probe._source_preflight({"images": [IMG]})
        assert ok is False
        assert "反爬" in msg

    def test_dead_source_zero_cost(self):
        """采购价 0（属性齐备）→ 源失效嫌疑。"""
        ok, msg = cloud_probe._source_preflight(
            {"images": [IMG], "attributes": {"颜色": "红"}, "purchase_cost": 0})
        assert ok is False
        assert "源失效" in msg

    def test_dead_source_negative_cost(self):
        """采购价负数（脏数据）→ 源失效嫌疑。"""
        ok, msg = cloud_probe._source_preflight(
            {"images": [IMG], "attributes": {"颜色": "红"}, "purchase_cost": -3.2})
        assert ok is False
        assert "源失效" in msg

    def test_dead_source_missing_cost_key(self):
        """purchase_cost 键缺失 → 源失效嫌疑（缺失视同 0）。"""
        ok, msg = cloud_probe._source_preflight(
            {"images": [IMG], "attributes": {"颜色": "红"}})
        assert ok is False
        assert "源失效" in msg

    def test_dead_source_non_numeric_cost(self):
        """purchase_cost 非数值（str 脏数据）→ 源失效嫌疑，不炸。"""
        ok, msg = cloud_probe._source_preflight(
            {"images": [IMG], "attributes": {"a": "b"}, "purchase_cost": "abc"})
        assert ok is False
        assert "源失效" in msg

    def test_healthy_data_passes(self):
        """图/属性/价格齐备 → 放行。"""
        ok, msg = cloud_probe._source_preflight(
            {"images": [IMG, IMG], "attributes": {"颜色": "红"}, "purchase_cost": 12.5})
        assert ok is True
        assert msg == ""

    def test_empty_images_not_anti_crawl_signal(self):
        """0 图 0 属性不是反爬签名（图空走 cost 判定）→ 源失效而非反爬。"""
        ok, msg = cloud_probe._source_preflight({"attributes": {}, "purchase_cost": 0})
        assert ok is False
        assert "反爬" not in msg
        assert "源失效" in msg

    def test_none_and_non_dict_input_pass(self):
        """None/非 dict 输入不炸不拦（防御）。"""
        assert cloud_probe._source_preflight(None) == (True, "")
        assert cloud_probe._source_preflight("junk") == (True, "")


# ── T3.1: _check_min_density 纯函数 ─────────────────────────────────────


class TestCheckMinDensity:
    """密度阈值检查（锚点：泡脚包 950g / 14190cm³ = 0.07 g/cm³）。"""

    def _paojiao_draft(self) -> dict:
        # 330×330×130mm = 14157cm³ → 950/14157 ≈ 0.067 g/cm³
        return _draft(weight=950, dimensions={"length": 330, "width": 330, "height": 130})

    def test_below_threshold_blocked(self):
        ok, msg = cloud_probe._check_min_density(self._paojiao_draft(), 0.15)
        assert ok is False
        assert "密度" in msg and "g/cm" in msg
        assert "0.07" in msg

    def test_default_zero_threshold_never_blocks(self):
        """阈值 0（默认）恒放行——行为与现状完全一致。"""
        ok, msg = cloud_probe._check_min_density(self._paojiao_draft(), 0)
        assert ok is True and msg == ""

    def test_none_threshold_never_blocks(self):
        assert cloud_probe._check_min_density(self._paojiao_draft(), None) == (True, "")

    def test_equal_threshold_passes(self):
        """density == 阈值不拦（严格小于才拦）。"""
        # 14157cm³ 阈值取 0.067 → 950/14157=0.06706 ≥ 0.067 → 放行
        ok, _ = cloud_probe._check_min_density(self._paojiao_draft(), 0.067)
        assert ok is True

    def test_missing_dims_or_weight_pass(self):
        """缺重量/尺寸不判（与原密度 warning 前提一致，不拦）。"""
        ok, _ = cloud_probe._check_min_density(_draft(weight=0), 0.15)
        assert ok is True
        ok, _ = cloud_probe._check_min_density(
            _draft(dimensions={"length": 0, "width": 0, "height": 0}), 0.15)
        assert ok is True

    def test_normal_density_above_threshold_passes(self):
        ok, _ = cloud_probe._check_min_density(
            _draft(weight=950, dimensions={"length": 120, "width": 80, "height": 60}), 0.15)
        assert ok is True

    def test_non_dict_draft_pass(self):
        assert cloud_probe._check_min_density(None, 0.15) == (True, "")


# ── T2.3 + T2.5 + T3.1: cmd_graph CLI 语义 ───────────────────────────────


class TestCliSubmitFailureSemantics:
    """T2.3: 409 重复提交 → stdout 人话 + summary.submitted=False + exit 3。"""

    def test_409_duplicate_stdout_hint_summary_exit3(self, capsys):
        rc, payloads, calls = _run_cli(_args(), submit_result={
            "ok": False,
            "error": "该商品已在提交队列，请勿重复提交",
            "error_code": "DUPLICATE_SUBMIT",
            "http_status": 409,
        })
        assert rc == 3, f"409 重复提交应 return 3, got {rc}"
        assert len(calls) == 1, "提交确实发出（worker 拒绝），不应拦截在本地"
        out = capsys.readouterr().out
        assert "❌ 提交失败 [DUPLICATE_SUBMIT]" in out, f"stdout 缺人话提示:\n{out}"
        assert "请勿重复提交" in out
        summary = payloads[0]["summary"]
        assert summary["submitted"] is False
        assert summary["submit_error"] == "DUPLICATE_SUBMIT"
        assert payloads[0]["submit_result"]["ok"] is False

    def test_generic_submit_failure_also_exit3(self, capsys):
        """非 409 失败（如连接失败无 error_code）→ 同样失败语义，code 兜底 UNKNOWN。"""
        rc, payloads, _ = _run_cli(_args(), submit_result={
            "ok": False, "error": "Worker unreachable: http://x"})
        assert rc == 3
        assert payloads[0]["summary"]["submitted"] is False
        assert payloads[0]["summary"]["submit_error"] == "UNKNOWN"
        assert "提交失败" in capsys.readouterr().out

    def test_success_summary_submitted_true_exit0(self, capsys):
        rc, payloads, calls = _run_cli(_args())
        assert rc == 0
        assert len(calls) == 1
        assert payloads[0]["summary"]["submitted"] is True
        assert "提交失败" not in capsys.readouterr().out


class TestCliSourcePreflightGate:
    """T2.5: 反爬/源失效在 cmd_graph 提交段的统一闸。"""

    def test_zero_attrs_blocked_before_submit(self, capsys):
        draft = _draft(attributes={})
        rc, payloads, calls = _run_cli(_args(), graph=_graph(draft))
        assert rc == 3
        assert calls == [], "被拦时不得调 submit_envelope"
        assert "反爬" in capsys.readouterr().out
        assert payloads[0]["summary"]["submitted"] is False
        assert payloads[0]["summary"]["submit_error"] == "SOURCE_PREFLIGHT"

    def test_zero_cost_blocked_before_submit(self, capsys):
        draft = _draft(purchase_cost=0)
        rc, payloads, calls = _run_cli(_args(), graph=_graph(draft))
        assert rc == 3
        assert calls == []
        assert "源失效" in capsys.readouterr().out
        assert payloads[0]["summary"]["submitted"] is False

    def test_to_box_bypasses_gate_with_warning(self, capsys):
        """--to-box 人工兜底通道：同样脏数据只 warning 不拦，照常入箱。"""
        draft = _draft(attributes={})
        rc, payloads, calls = _run_cli(_args("--to-box"), graph=_graph(draft))
        assert rc == 0
        assert len(calls) == 1, "to-box 应放行（submit_draft 被调）"
        out = capsys.readouterr().out
        assert "⚠️" in out and "反爬" in out, f"缺放行 warning:\n{out}"
        assert payloads[0]["summary"]["submitted"] is True

    def test_no_submit_mode_unaffected(self):
        """--no-submit 不提交不涉及，保持 exit 0（纯组装模式零变化）。"""
        draft = _draft(attributes={}, purchase_cost=0)
        rc, payloads, calls = _run_cli(_args("--no-submit"), graph=_graph(draft))
        assert rc == 0
        assert calls == []
        assert "submitted" not in payloads[0]["summary"]


class TestCliMinDensityGate:
    """T3.1: --min-density 密度拦截接线。"""

    def _paojiao_graph(self):
        return _graph(_draft(
            weight=950, purchase_cost=12.5,
            dimensions={"length": 330, "width": 330, "height": 130}))

    def test_min_density_blocks_submit(self, capsys):
        rc, payloads, calls = _run_cli(_args("--min-density", "0.15"),
                                       graph=self._paojiao_graph())
        assert rc == 3
        assert calls == [], "密度低于阈值不得提交"
        out = capsys.readouterr().out
        assert "密度" in out and "g/cm" in out
        assert payloads[0]["summary"]["submitted"] is False
        assert payloads[0]["summary"]["submit_error"] == "LOW_DENSITY"

    def test_default_regression_low_density_still_submits(self):
        """⑦回归：不设 --min-density（默认 0）→ 低密度仍照常提交（现状不变）。"""
        rc, payloads, calls = _run_cli(_args(), graph=self._paojiao_graph())
        assert rc == 0
        assert len(calls) == 1
        assert payloads[0]["summary"]["submitted"] is True


class TestArgparseAndDoc:
    """⑧argparse 新参数解析 + exit code 文档。"""

    def test_min_density_default_zero(self):
        args = cli.build_arg_parser().parse_args(["graph", "--item-id", "1"])
        assert args.min_density == 0.0

    def test_min_density_parses_float(self):
        args = cli.build_arg_parser().parse_args(
            ["graph", "--item-id", "1", "--min-density", "0.15"])
        assert args.min_density == pytest.approx(0.15)

    def test_min_density_help_mentions_unit(self):
        parser = cli.build_arg_parser()
        graph_action = next(
            a for a in parser._subparsers._group_actions  # noqa: SLF001
            if a.dest == "command")
        graph_parser = graph_action.choices["graph"]
        help_text = next(
            a.help for a in graph_parser._actions if a.dest == "min_density")
        assert "g/cm" in help_text
        assert "0.1" in help_text

    def test_module_docstring_documents_exit_codes(self):
        doc = cli.__doc__ or ""
        assert "Exit codes" in doc, "模块 docstring 应有 exit code 说明"
        assert "409" in doc and "3" in doc


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
