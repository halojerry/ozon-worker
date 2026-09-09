#!/usr/bin/env python3
"""discover-task --expend-shop 拓店模式（P4-A3，shopbang §6.5 方法论）。

覆盖：①参数校验（与 --keyword 互斥 / 缺 --url / 非 商品页 URL / depth>3 护栏）；
②N→预算映射纯函数（expend_shop_fission_plan）；③run_fission 调用参数传递
（mock CdpConnection，含 session_id=task_id / checkpoint_dir / min_seller_rating）；
④卖家评分过滤（min_seller_rating 引擎层，默认 None 向后兼容）；⑤=0 回归
（行为与现状完全一致）；⑥拓店×--resume 组合 + checkpoint 断点落盘。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_discover_task_expend_shop.py -q
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cli
from scripts.lib import ozon_discovery as od
from scripts.lib import ozon_fission
from scripts.lib.ozon_discovery import ProductCandidate

PRODUCT_URL = "https://www.ozon.ru/product/phone-case-1654983021/"


# ── ② 映射纯函数 ──────────────────────────────────────────────────────────

def test_plan_small_n_floors_at_60():
    """N=10 → max(40,60)=60；depth 缺省 1；时间预算缺省 600。"""
    plan = cli.expend_shop_fission_plan(10)
    assert plan == {"max_depth": 1, "max_total_products": 60,
                    "time_budget": 600.0}


def test_plan_scales_with_n():
    """N=20 → 80；N=1 → 下限 60。"""
    assert cli.expend_shop_fission_plan(20)["max_total_products"] == 80
    assert cli.expend_shop_fission_plan(1)["max_total_products"] == 60
    assert cli.expend_shop_fission_plan(100)["max_total_products"] == 400


def test_plan_explicit_overrides_win():
    """显式 --max-depth/--max-total-products/--time-budget 覆盖 N 派生值。"""
    plan = cli.expend_shop_fission_plan(
        10, max_depth=2, max_total_products=33, time_budget=90.0)
    assert plan == {"max_depth": 2, "max_total_products": 33,
                    "time_budget": 90.0}


def test_plan_depth_guard():
    """depth 缺省 1（一跳）；3 默认放行；>3 需 --allow-depth-3（同 discover 护栏）。"""
    assert cli.expend_shop_fission_plan(10, max_depth=3)["max_depth"] == 3
    try:
        cli.expend_shop_fission_plan(10, max_depth=4)
        raise AssertionError("depth=4 无 allow_depth_3 应 ValueError")
    except ValueError as exc:
        assert "--allow-depth-3" in str(exc)
    assert cli.expend_shop_fission_plan(
        10, max_depth=4, allow_depth_3=True)["max_depth"] == 4


# ── ① 参数校验（exit code）───────────────────────────────────────────────

def _val_args(**kw) -> argparse.Namespace:
    base = {"url": "", "keyword": "", "expend_shop": 0, "max_depth": None,
            "allow_depth_3": False, "max_total_products": None,
            "time_budget": None, "filters": ""}
    base.update(kw)
    return argparse.Namespace(**base)


def test_expend_shop_keyword_mutually_exclusive(capsys):
    """--expend-shop 与 --keyword 互斥 → 退出码 2。"""
    rc = cli.cmd_discover_task(_val_args(
        expend_shop=10, url=PRODUCT_URL, keyword="手机壳"))
    assert rc == 2
    assert "互斥" in capsys.readouterr().out


def test_expend_shop_requires_url(capsys):
    """--expend-shop 无 --url → 退出码 2。"""
    rc = cli.cmd_discover_task(_val_args(expend_shop=10, keyword=""))
    assert rc == 2
    assert "--url" in capsys.readouterr().out


def test_expend_shop_url_must_be_product_page(capsys):
    """--expend-shop 的 --url 非 商品页（highlight）→ 退出码 2。"""
    rc = cli.cmd_discover_task(_val_args(
        expend_shop=10,
        url="https://www.ozon.ru/highlight/tovary-iz-kitaya-935133/"))
    assert rc == 2
    assert "商品页" in capsys.readouterr().out


def test_expend_shop_depth_guard_exit_1(capsys):
    """--expend-shop --max-depth 4 未给 --allow-depth-3 → 退出码 1（同 discover）。"""
    rc = cli.cmd_discover_task(_val_args(
        expend_shop=10, url=PRODUCT_URL, max_depth=4))
    assert rc == 1
    assert "--allow-depth-3" in capsys.readouterr().out


# ── ③ run_fission 调用参数传递 ────────────────────────────────────────────

def _mk_ok_seed(pid: str) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"种子{pid}",
                         ozon_price=1500.0)
    c.status = "ok"
    c.competing_seller_list = []
    return c


def test_collect_expend_shop_passes_plan_params(tmp_path):
    """_collect_expend_shop → run_fission：plan 预算/session_id/checkpoint_dir
    逐一正确传递；种子无评级卖家 → 降级为 None（不限评分）。"""
    seed = _mk_ok_seed("1654983021")
    with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls, \
         mock.patch.object(od, "_analyze_product", return_value=seed) as ap, \
         mock.patch.object(ozon_fission, "run_fission",
                           return_value=[seed]) as rf:
        out = cli._collect_expend_shop(
            "http://127.0.0.1:9222", "1654983021",
            plan={"max_depth": 1, "max_total_products": 80, "time_budget": 300.0},
            expend_shop=20, brand_filter="nobrand", min_price=0, max_price=0,
            filter_profile="off", session_id="20260909_120000",
            checkpoint_dir=str(tmp_path / "fission"))
    assert out == [seed]
    ap.assert_called_once()
    assert ap.call_args.args[2] == "1654983021"      # 种子 pid
    kwargs = rf.call_args.kwargs
    assert kwargs["seed_products"] == [seed]
    assert kwargs["max_depth"] == 1
    assert kwargs["max_total_products"] == 80
    assert kwargs["time_budget"] == 300.0
    assert kwargs["session_id"] == "20260909_120000"  # task_id 可续跑定位
    assert kwargs["checkpoint_dir"] == str(tmp_path / "fission")
    assert kwargs["min_seller_rating"] is None       # 无评级种子 → 降级不限评分
    conn_cls.assert_called_once_with("http://127.0.0.1:9222")


def test_collect_expend_shop_rated_seed_enables_rating_filter(tmp_path):
    """种子跟卖里有评分≥4.0 的卖家 → 评级优先：min_seller_rating=4.0 传引擎。"""
    seed = _mk_ok_seed("1654983021")
    seed.competing_seller_list = [
        {"seller_id": "90001", "seller_name": "无分卖家"},
        {"seller_id": "90002", "seller_name": "好卖家", "rating": 5},
    ]
    with mock.patch("scripts.lib.cdp_client.CdpConnection"), \
         mock.patch.object(od, "_analyze_product", return_value=seed), \
         mock.patch.object(ozon_fission, "run_fission",
                           return_value=[seed]) as rf:
        cli._collect_expend_shop(
            "http://127.0.0.1:9222", "1654983021",
            plan={"max_depth": 1, "max_total_products": 60, "time_budget": 600.0},
            expend_shop=10, brand_filter="nobrand", min_price=0, max_price=0,
            filter_profile="off", session_id="t1b",
            checkpoint_dir=str(tmp_path))
    assert rf.call_args.kwargs["min_seller_rating"] == 4.0   # 评级优先门槛生效


def test_collect_expend_shop_seed_error_returns_empty(tmp_path):
    """种子商品分析失败（status != ok）→ 返回 []（调用方任务终止）。"""
    bad = ProductCandidate(ozon_product_id="1654983021", ozon_title="",
                           ozon_price=0.0)
    bad.status = "error"
    bad.error = "no title returned"
    with mock.patch("scripts.lib.cdp_client.CdpConnection"), \
         mock.patch.object(od, "_analyze_product", return_value=bad):
        out = cli._collect_expend_shop(
            "http://127.0.0.1:9222", "1654983021",
            plan={"max_depth": 1, "max_total_products": 60, "time_budget": 600.0},
            expend_shop=10, brand_filter="nobrand", min_price=0, max_price=0,
            filter_profile="off", session_id="t1",
            checkpoint_dir=str(tmp_path))
    assert out == []


def test_collect_expend_shop_filters_fission_candidates(tmp_path):
    """粗筛只作用于裂变候选（chain_depth>0）：品牌命中 → filtered；种子不动。"""
    seed = _mk_ok_seed("SEED")
    branded = _mk_ok_seed("BRANDED")
    branded.chain_depth = 1
    branded.brand = "SomeBrand"
    with mock.patch("scripts.lib.cdp_client.CdpConnection"), \
         mock.patch.object(od, "_analyze_product", return_value=seed), \
         mock.patch.object(ozon_fission, "run_fission",
                           return_value=[seed, branded]), \
         mock.patch.object(od, "_is_branded", return_value=True), \
         mock.patch.object(od, "_is_known_brand", return_value=False):
        out = cli._collect_expend_shop(
            "http://127.0.0.1:9222", "SEED",
            plan={"max_depth": 1, "max_total_products": 60, "time_budget": 600.0},
            expend_shop=10, brand_filter="nobrand", min_price=0, max_price=0,
            filter_profile="off", session_id="t2", checkpoint_dir=str(tmp_path))
    assert [c.ozon_product_id for c in out] == ["SEED", "BRANDED"]
    assert seed.status == "ok"                        # 种子（用户显式入口）不过滤
    assert branded.status == "filtered"               # 裂变候选补跑粗筛


# ── ④ 卖家评分过滤（引擎层，真实 run_fission 全 mock）────────────────────

def _fission_mock_env(store_products, store_sellers=None):
    """e2e mock 标准环境（同 test_fission_e2e_mock 风格）：CDP/深抓/浅抓全 mock。"""
    def _decorator(fn):
        def _runner(*a, **kw):
            with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls, \
                 mock.patch.object(od, "fetch_seller_products",
                                   side_effect=lambda **k: store_products.get(k["seller_id"], [])), \
                 mock.patch.object(od, "_analyze_product",
                                   side_effect=lambda cdp_url, cdp, pid: _mk_ok_seed(pid)), \
                 mock.patch("scripts.lib.ozon_widget.fetch_competing_sellers",
                            side_effect=lambda cdp_url, pid, cdp: {
                                "count": 0, "min_price": 0,
                                "sellers": (store_sellers or {}).get(pid, [])}), \
                 mock.patch.object(ozon_fission, "fetch_seller_products_shallow",
                                   return_value=[]):
                conn_cls.return_value = mock.MagicMock()
                return fn(*a, **kw)
        return _runner
    return _decorator


def _mk_rating_seed(pid: str, sellers: list) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"种子{pid}",
                         ozon_price=1500.0)
    c.status = "ok"
    c.competing_sellers = len(sellers)
    c.competing_seller_list = sellers
    return c


@_fission_mock_env({"10001": ["P1"], "10002": ["P2"]})
def test_min_seller_rating_filters_low_rating_sellers():
    """min_seller_rating=4.0：评分 3.2 卖家店铺不展开；4.5 卖家展开。"""
    seed = _mk_rating_seed("SEED", [
        {"seller_id": "10001", "seller_name": "好卖家", "rating": 4.5},
        {"seller_id": "10002", "seller_name": "差卖家", "rating": 3.2},
    ])
    result = ozon_fission.run_fission(
        seed_products=[seed], max_depth=1, max_total_products=20,
        max_sellers_per_product=10, max_products_per_seller=5,
        time_budget=30, min_seller_rating=4.0)
    pids = {c.ozon_product_id for c in result}
    assert "P1" in pids and "P2" not in pids, f"评分门槛应拦掉差卖家店铺: {pids}"


@_fission_mock_env({"10001": ["P1"], "10002": ["P2"]})
def test_min_seller_rating_default_none_backward_compatible():
    """缺省 None=不过滤：两家卖家店铺都展开（既有裂变行为零变化）。"""
    seed = _mk_rating_seed("SEED", [
        {"seller_id": "10001", "seller_name": "好卖家"},
        {"seller_id": "10002", "seller_name": "差卖家"},
    ])
    result = ozon_fission.run_fission(
        seed_products=[seed], max_depth=1, max_total_products=20,
        max_sellers_per_product=10, max_products_per_seller=5, time_budget=30)
    pids = {c.ozon_product_id for c in result}
    assert {"P1", "P2"} <= pids, f"缺省不过滤应展开两家店铺: {pids}"


@_fission_mock_env({"10001": ["P1"], "10002": ["P2"]})
def test_min_seller_rating_missing_rating_fails_closed():
    """评分缺失按 0 处理（fail-closed）：4.0 门槛下无评分卖家被拦。"""
    seed = _mk_rating_seed("SEED", [
        {"seller_id": "10001", "seller_name": "有分卖家", "rating": 4.8},
        {"seller_id": "10002", "seller_name": "无分卖家"},   # 无 rating 字段
    ])
    result = ozon_fission.run_fission(
        seed_products=[seed], max_depth=1, max_total_products=20,
        max_sellers_per_product=10, max_products_per_seller=5,
        time_budget=30, min_seller_rating=4.0)
    pids = {c.ozon_product_id for c in result}
    assert "P1" in pids and "P2" not in pids, f"无评分应 fail-closed: {pids}"


@_fission_mock_env({"10001": ["P1"]})
def test_min_seller_rating_filter_before_slice():
    """评级过滤先于 max_sellers 截断：22 个无评级卖家占位时，第 23 位的有评级
    卖家仍能展开（若先截 20 再过滤，会被无评级占坑饿死——webSellerList 实证
    评分稀疏，这是真实会发生的事）。"""
    sellers = [{"seller_id": f"9{i:03d}", "seller_name": f"u{i}"}
               for i in range(22)]
    sellers.append({"seller_id": "10001", "seller_name": "好卖家", "rating": 4.8})
    seed = _mk_rating_seed("SEED", sellers)
    result = ozon_fission.run_fission(
        seed_products=[seed], max_depth=1, max_total_products=20,
        max_sellers_per_product=20, max_products_per_seller=5,
        time_budget=30, min_seller_rating=4.0)
    pids = {c.ozon_product_id for c in result}
    assert "P1" in pids, "评级过滤应在截断前生效，尾部有评级卖家可展开"


# ── ⑤=0 回归 + ⑥ 拓店×resume / checkpoint ───────────────────────────────

def _dt_args(**kw) -> argparse.Namespace:
    base = {"url": PRODUCT_URL, "keyword": "", "target_count": 10,
            "max_scan": 300, "filter_profile": None, "base_filter": "",
            "min_margin": 15.0, "fx_rate": 0.075, "match_limit": None,
            "match_concurrency": 1, "no_match_streak_stop": 5, "store": "",
            "to_box": False, "auto_submit": False, "dry_run": True,
            "resume": False, "no_analytics": False, "export": "",
            "filters": "", "expend_shop": 0, "max_depth": None,
            "allow_depth_3": False, "max_total_products": None,
            "time_budget": None}
    base.update(kw)
    return argparse.Namespace(**base)


def _cand(pid: str, status: str, margin: float = 20.0) -> ProductCandidate:
    c = ProductCandidate(ozon_product_id=pid, ozon_title=f"Товар {pid}",
                         ozon_price=1000.0, ozon_images=["http://img/x.jpg"])
    c.status = status
    c.profit_margin = margin
    c.match_1688_url = f"https://detail.1688.com/offer/{pid}.html" if status == "profitable" else ""
    return c


def _run_full_cli(args, cands, tmp_cache, collect=None):
    """test_discover_task._run_cli 同款 mock；collect=拓店分支返回值（None=走原路径）。"""
    from scripts.lib import chrome_launcher, config_store
    submitted: list = []

    def _fake_submit(envelope, source_batch=None):
        submitted.append(envelope)
        return {"draft_id": f"draft-{len(submitted)}"}

    with mock.patch.object(od, "DISCOVERY_CACHE_DIR", tmp_cache), \
         mock.patch.object(chrome_launcher, "ensure_chrome_cdp", return_value=(True, "ok")), \
         mock.patch.object(od, "collect_and_analyze", return_value=cands) as ca, \
         mock.patch.object(od, "match_selected",
                           side_effect=lambda candidates, cdp, **kw: [
                               setattr(c, "status", "profitable") or
                               setattr(c, "match_1688_url",
                                       f"https://detail.1688.com/offer/{c.ozon_product_id}.html") or
                               setattr(c, "profit_margin", 25.0)
                               for c in candidates if c.status in ("ok", "uncertain")] and candidates), \
         mock.patch.object(config_store, "get_store_profile", return_value={}), \
         mock.patch.object(config_store, "get_setting", return_value=None), \
         mock.patch.object(config_store, "get_store", return_value={"client_id": "1", "api_key": "k"}), \
         mock.patch("scripts.cloud_probe.submit_draft", side_effect=_fake_submit), \
         mock.patch("scripts.cloud_probe.build_envelope_from_discovery",
                    side_effect=lambda c, sc, store_id="": {"token": "t", "envelope": {}}), \
         mock.patch.object(cli, "_collect_expend_shop", return_value=collect) as ce:
        rc = cli.cmd_discover_task(args)
    return rc, {"collect_and_analyze": ca, "collect_expend_shop": ce,
                "submitted": submitted}


def test_expend_shop_zero_regression_uses_original_path(tmp_path):
    """=0（缺省）：走 collect_and_analyze 原路径，拓店分支不触发（现状零变化）。"""
    cands = [_cand("101", "ok"), _cand("102", "ok")]
    rc, calls = _run_full_cli(_dt_args(dry_run=True, expend_shop=0),
                              cands, tmp_path)
    assert rc == 0
    calls["collect_and_analyze"].assert_called_once()
    calls["collect_expend_shop"].assert_not_called()


def test_expend_shop_full_chain_pipeline_unchanged(tmp_path):
    """拓店全链：裂变候选回既有管线（--filters 判定/匹配/入箱/params 落盘）。"""
    fissioned = [_cand("F1", "ok"), _cand("F2", "ok"), _cand("F3", "filtered")]
    rc, calls = _run_full_cli(
        _dt_args(to_box=True, dry_run=False, expend_shop=20), [], tmp_path,
        collect=fissioned)
    assert rc == 0
    calls["collect_expend_shop"].assert_called_once()
    calls["collect_and_analyze"].assert_not_called()
    files = list((tmp_path / "tasks").glob("task_*.json"))
    state = json.loads(files[0].read_text(encoding="utf-8"))
    exp = state["params"]["expend_shop"]
    assert exp["n"] == 20
    assert exp["max_total_products"] == 80            # max(20×4, 60)
    assert exp["max_depth"] == 1
    assert exp["seed_pid"] == "1654983021"
    assert state["summary"]["submitted"] == 2         # F1/F2 入箱（管线不变）


def test_expend_shop_empty_candidates_exits_1(tmp_path):
    """拓店未产出候选（种子失败/裂变为空）→ 退出码 1，不进匹配。"""
    rc, calls = _run_full_cli(
        _dt_args(dry_run=True, expend_shop=10), [], tmp_path, collect=[])
    assert rc == 1
    calls["collect_expend_shop"].assert_called_once()


def test_expend_shop_resume_combo(tmp_path):
    """拓店×--resume：预置同入口任务含已处理 pid → 匹配跳过（既有 resume 逻辑）。"""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True)
    prev = {"task_id": "20260908_000000",
            "entry": {"url": PRODUCT_URL, "keyword": ""},
            "processed": {"F1": {"status": "ok", "draft_id": "draft-old"}},
            "summary": {}}
    (tasks_dir / "task_20260908_000000.json").write_text(
        json.dumps(prev, ensure_ascii=False), encoding="utf-8")
    fissioned = [_cand("F1", "ok"), _cand("F2", "ok")]
    rc, calls = _run_full_cli(
        _dt_args(to_box=True, dry_run=False, resume=True, expend_shop=20),
        [], tmp_path, collect=fissioned)
    assert rc == 0
    # F1 已处理被跳过（不重烧提交），只入箱 F2；resume 的 processed 记账带入新任务
    assert len(calls["submitted"]) == 1
    files = list((tmp_path / "tasks").glob("task_*.json"))
    states = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    newest = max(states, key=lambda s: s["task_id"])
    assert newest["processed"]["F2"]["status"] == "ok"
    assert newest["processed"]["F1"] == {"status": "ok", "draft_id": "draft-old"}


def test_expend_shop_checkpoint_written_with_task_session(tmp_path):
    """checkpoint 断点落盘：fission_state_{session_id=task_id}.json 可定位续跑。"""
    seed = _mk_rating_seed("1654983021", [
        {"seller_id": "10001", "seller_name": "卖家A", "rating": 4.7},
    ])
    ckpt = tmp_path / "fission"
    # 真实 run_fission（全 mock CDP）验证 checkpoint 文件名按 session_id 落盘
    with mock.patch("scripts.lib.cdp_client.CdpConnection") as conn_cls, \
         mock.patch.object(od, "fetch_seller_products",
                           side_effect=lambda **k: ["P1"] if k["seller_id"] == "10001" else []), \
         mock.patch.object(od, "_analyze_product",
                           side_effect=lambda cdp_url, cdp, pid: _mk_ok_seed(pid)), \
         mock.patch.object(ozon_fission, "fetch_seller_products_shallow",
                           return_value=[]):
        conn_cls.return_value = mock.MagicMock()
        ozon_fission.run_fission(
            seed_products=[seed], max_depth=1, max_total_products=20,
            max_sellers_per_product=10, max_products_per_seller=5,
            time_budget=30, session_id="20260909_130000",
            checkpoint_dir=str(ckpt), min_seller_rating=4.0)
    assert (ckpt / "fission_state_20260909_130000.json").exists()


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
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
