#!/usr/bin/env python3
"""arch-findings #5: search 批量 _submit_one 门禁对齐（纯 mock 回归）。

原实现 search --auto-submit/--to-box 批量腿绕过全部门禁（preflight/min-margin/
min-density）且失败不影响 exit 0——反爬页/失效源/低利润信封静默直上 worker。
对齐后（与 graph 单腿同口径）：
- preflight 反爬/失效源闸无条件生效；
- --min-margin/--min-density 缺省 0=不拦截（与 graph argparse 默认一致）；
- 被拦项按「✗ 跳过（门禁拦截）」计，不提交；
- 全部被拦/全部失败 → exit 3；部分成功 → 0。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_search_gates_v080.py -q
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import ExitStack
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.cli as cli  # noqa: E402

IMG = "https://cbu01.alicdn.com/img/ibank/2024/O/123/456.jpg"


def _envelope(item_id: str, *, dirty: bool = False) -> dict:
    """可过闸的最小信封；dirty=True → 反爬脏 draft（有图无属性）。"""
    draft = {
        "item_id": item_id, "title": "Перчатки",
        "purchase_cost": 0 if dirty else 5.0,
        "weight": 500,
        "images": [IMG],
        "attributes": {} if dirty else {"颜色": "红色"},
    }
    return {"token": "t", "envelope": {"draft": draft, "source": {}, "extensions": {}}}


def _run_search(products, envelopes: dict[str, dict], *, submit_ok=True,
                submit_resp=None, **argkw):
    """跑 cmd_search（搜索/构建/提交全 mock）。

    envelopes: product_id → build_graph_envelope_with_retry 返回值；
    submit_resp 缺省 = 成功响应。
    """
    submit_resp = submit_resp or {"ok": True, "task_id": "T-1"}
    submitted: list = []
    drafted: list = []

    def _build(item_id="", detail_url="", store_id=""):
        return envelopes.get(item_id) or _envelope(item_id)

    def _submit(env):
        submitted.append(env)
        return dict(submit_resp)

    def _draft_submit(env):
        drafted.append(env)
        return {"ok": True, "draft_id": f"d{len(drafted)}"}

    args = cli.build_arg_parser().parse_args(
        ["search", "手套", "--auto-submit", *(argkw.pop("extra", []))])
    for k, v in argkw.items():
        setattr(args, k, v)
    with ExitStack() as stack:
        for pat, val in [
            ("scripts.lib.ak_1688_client.search_products", lambda q, page_size=5: products),
            ("scripts.lib.config_store.get_ozon_credentials",
             lambda store="": {"margin_rate": 0.25, "commission_rate": 0.10}),
            ("scripts.lib.ozon_discovery._query_logistics_from_worker",
             lambda w, dims_mm=None: None),
            ("scripts.cloud_probe.build_graph_envelope_with_retry", _build),
            ("scripts.cloud_probe.submit_envelope", _submit),
            ("scripts.cloud_probe.submit_draft", _draft_submit),
        ]:
            stack.enter_context(mock.patch(pat, val))
        out = io.StringIO()
        stack.enter_context(mock.patch("sys.stdout", out))
        rc = cli.cmd_search(args)
    return rc, out.getvalue(), submitted, drafted


def _prod(pid: str) -> dict:
    return {"product_id": pid, "title": f"Товар {pid}", "price": 5.0}


def test_preflight_blocks_dirty_all_exit3():
    """①全部脏 draft（0 属性反爬页）：逐条跳过计 ✗，不提交，exit 3。"""
    envs = {"1001": _envelope("1001", dirty=True),
            "1002": _envelope("1002", dirty=True)}
    rc, out, submitted, drafted = _run_search([_prod("1001"), _prod("1002")], envs)
    assert rc == 3
    assert submitted == [] and drafted == [], "被拦项不得提交"
    assert out.count("✗ 跳过（门禁拦截）") == 2
    assert "反爬嫌疑" in out
    assert "门禁跳过 2" in out


def test_dead_source_blocked():
    """②失效源 draft（采购价 0）：同样被 preflight 拦截。"""
    envs = {"1003": {"token": "t", "envelope": {"draft": {
        "item_id": "1003", "title": "x", "purchase_cost": 0, "weight": 500,
        "images": [IMG], "attributes": {"颜色": "红"}},
        "source": {}, "extensions": {}}}}
    rc, out, submitted, _ = _run_search([_prod("1003")], envs)
    assert rc == 3
    assert submitted == []
    assert "源失效" in out


def test_partial_blocked_exit0():
    """③一净一脏：净的照常提交，脏的跳过，部分成功 → exit 0。"""
    envs = {"1001": _envelope("1001"),
            "1002": _envelope("1002", dirty=True)}
    rc, out, submitted, _ = _run_search([_prod("1001"), _prod("1002")], envs)
    assert rc == 0
    assert len(submitted) == 1
    assert "✓ 已提交" in out
    assert "✗ 跳过（门禁拦截）" in out
    assert "门禁跳过 1" in out


def test_min_margin_flag_blocks_low_margin():
    """④--min-margin 99%：正常利润率被拦（阈值口径与 graph 同源 _min_margin_block_reason）。"""
    rc, out, submitted, _ = _run_search(
        [_prod("1001")], {"1001": _envelope("1001")}, extra=["--min-margin", "99"])
    assert rc == 3
    assert submitted == []
    assert "预估利润率" in out and "跳过（门禁拦截）" in out


def test_min_density_flag_blocks_low_density():
    """⑤--min-density 高阈值：低密度 draft 被拦（泡脚包锚点：950g/14190cm³）。"""
    env = {"1001": {"token": "t", "envelope": {"draft": {
        "item_id": "1001", "title": "x", "purchase_cost": 5.0, "weight": 950,
        "images": [IMG], "attributes": {"颜色": "红"},
        "dimensions": {"length": 330, "width": 330, "height": 130}},
        "source": {}, "extensions": {}}}}
    rc, out, submitted, _ = _run_search(
        [_prod("1001")], env, extra=["--min-density", "0.15"])
    assert rc == 3
    assert submitted == []
    assert "密度" in out


def test_all_success_exit0():
    """⑥全部干净：逐条提交成功，exit 0（原成功路径零变化）。"""
    rc, out, submitted, _ = _run_search(
        [_prod("1001"), _prod("1002")],
        {"1001": _envelope("1001"), "1002": _envelope("1002")})
    assert rc == 0
    assert len(submitted) == 2
    assert out.count("✓ 已提交") == 2


def test_submit_failure_all_exit3():
    """⑦全提交失败（非门禁）：不再 exit 0 假成功 → exit 3。"""
    rc, out, _, _ = _run_search(
        [_prod("1001")], {"1001": _envelope("1001")},
        submit_resp={"ok": False, "error": "Worker unreachable"})
    assert rc == 3
    assert "✗ 提交失败" in out


def test_search_margin_flags_in_argparse():
    """⑧search 子命令带 --min-margin/--min-density 旗标（缺省 0=不拦截）。"""
    parser = cli.build_arg_parser()
    a = parser.parse_args(["search", "k"])
    assert a.min_margin == 0.0 and a.min_density == 0.0
    b = parser.parse_args(["search", "k", "--min-margin", "12", "--min-density", "0.2"])
    assert b.min_margin == 12.0 and b.min_density == 0.2


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)
