#!/usr/bin/env python3
"""v0.69 Wave 1 三任务回归（T0.1a / T0.1b / T0.5）。

T0.1a — graph 管线 search_kw 类目自校验：1688 汽油桶商品曾被本地文本猜测写成
Труба металлическая（金属管，dc=200001728/tp=970693823），与
source_category="包装 > 金属包装容器 > 金属桶" 完全矛盾，毒类目进信封干扰
worker 仲裁。修复：猜中类目与「商品标题 ∪ source_category 末段」做 gram
overlap 自校验（`_category_guess_consistent` 纯函数），不一致 → 不写
draft.ozon_category，留空让 worker 全链匹配。

T0.1b — manual 类目直传通道：CLI `--category-id/--type-id` 直传信封
（source="manual"），覆盖任何 search_kw 猜测并跳过自校验（人工指定天然可信；
worker 侧并行批次将 manual 加进权威白名单，skill 侧只管产出）。

T0.5 — CLI 输出与 argv：
  ①win32 重定向流（`> out.json` 得 0 字节）→ main() 入口 stdout/stderr
    reconfigure(encoding="utf-8", errors="replace")；
  ②argv 切分取证：仓库内全链路 argv 以 list 传递（runtime_probe.py:161
    os.execve(python_cmd, full_argv_list, env)，无 join+split）。本测试锁定
    argparse 解析带空格的西里尔参数完整到达 args.category_query——结论：
    仓库代码无切分 bug，历史空格丢失问题在外部启动器（打包层 argv 拼接）。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_manual_category_v069.py -q
"""
from __future__ import annotations

import io
import sys
import unittest.mock as mock
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import cli, cloud_probe  # noqa: E402

ITEM_ID = "980815374096"
DETAIL_URL = f"https://detail.1688.com/offer/{ITEM_ID}.html"
ALICDN_IMG = "https://cbu01.alicdn.com/img/ibank/2024/O/123/456.jpg"

# 生产实证锚点：1688 汽油桶商品
CANISTER_TITLE = "加厚汽油桶铁桶手提 20L Канистра"
CANISTER_SRC_PATH = "包装 > 金属包装容器 > 金属桶"
# Ozon ZH_HANS 树错配返回（生产 reverse-lookup = Труба металлическая 金属管）
METAL_PIPE_CAT = {
    "description_category_id": "200001728",
    "type_id": "970693823",
    "type_name": "金属管",
    "category_name": "金属材料",
    "score": 1.0,
}
METAL_BARREL_CAT = {
    "description_category_id": "200001728",
    "type_id": "970693823",
    "type_name": "金属桶",
    "category_name": "金属包装容器",
    "score": 1.0,
}


# ── T0.1a: _category_guess_consistent 纯函数 ────────────────────────────


class TestCategoryGuessConsistent:
    """自校验纯函数（汽油桶案例为锚点）。"""

    def test_gasoline_canister_anchor_rejects_metal_pipe(self):
        """锚点：猜中「金属管」+ source「包装>金属包装容器>金属桶」+ 汽油桶标题 → False。

        金属管 vs 金属桶一字之差共享 bigram「金属」→ 普通 overlap 判不断，
        靠尾字（语义中心）核对拦截：管 不在标题 ∪ source_category 任何位置。
        """
        assert cloud_probe._category_guess_consistent(
            "金属管", CANISTER_TITLE, CANISTER_SRC_PATH) is False

    def test_gasoline_canister_consistent_barrel_kept(self):
        """一致类目：猜中「金属桶」（与 source 末段一致）→ True。"""
        assert cloud_probe._category_guess_consistent(
            "金属桶", CANISTER_TITLE, CANISTER_SRC_PATH) is True

    def test_cyrillic_pipe_vs_chinese_evidence(self):
        """跨语言毒猜：猜中「Труба металлическая」对中文证据零字符交集 → False。"""
        assert cloud_probe._category_guess_consistent(
            "Труба металлическая", CANISTER_TITLE, CANISTER_SRC_PATH) is False

    def test_cyrillic_consistent_guess(self):
        """跨语言一致：猜中「Канистра」且标题含 Канистра → True。"""
        assert cloud_probe._category_guess_consistent(
            "Канистра", CANISTER_TITLE, CANISTER_SRC_PATH) is True

    def test_title_only_head_noun_mismatch(self):
        """无 source_category 时退化为标题核对：化妆刷 vs 宠物食品标题 → False。"""
        assert cloud_probe._category_guess_consistent(
            "化妆刷", "宠物零食鸡肉干", "") is False

    def test_title_only_consistent(self):
        """无 source_category：猜中「鸡肉干」命中标题词 → True。"""
        assert cloud_probe._category_guess_consistent(
            "鸡肉干", "宠物零食鸡肉干 100g", "") is True

    def test_no_evidence_returns_true(self):
        """标题与 source_category 全空 → 无法判定，不拦（True）。"""
        assert cloud_probe._category_guess_consistent("任意类目", "", "") is True

    def test_empty_guess_returns_true(self):
        """猜中类目名缺失 → 无法判定，不拦（True，保持现行为写 ID）。"""
        assert cloud_probe._category_guess_consistent("", CANISTER_TITLE, CANISTER_SRC_PATH) is True

    def test_none_inputs_do_not_crash(self):
        """None 输入不炸（graph 管线字段可缺失）。"""
        assert cloud_probe._category_guess_consistent(None, None, None) is True


# ── T0.1a: build_graph_envelope 集成（search_kw 拒写/照写） ─────────────


def _api_data() -> dict:
    """1688 API 数据：带包装>金属包装容器>金属桶 类目面包屑。"""
    return {
        "title": CANISTER_TITLE,
        "price": "12.50",
        "categories": [
            {"name": "包装", "id": "1"},
            {"name": "金属包装容器", "id": "2"},
            {"name": "金属桶", "id": "3"},
        ],
    }


def _enriched() -> dict:
    """CDP enrich 返回（api_only 降级形态，data 含 API 数据）。"""
    return {
        "ok": False,
        "degraded": True,
        "degraded_reason": "浏览器探测失败: mock api_only",
        "user_action": None,
        "source": "api_only",
        "data": {
            "title": CANISTER_TITLE,
            "price": "12.50",
            "brand": "",
            "seller": "某五金厂",
            "images": [ALICDN_IMG],
            "weight_grams": 1500,
            "packaging_rows": [],
            "shipping": {},
            "description": "",
            "sku_details": [],
            "attributes": [],
            "option_groups": [],
            "category_id": "",
        },
    }


def _build(api_data: dict | None = None, **kw) -> dict:
    """调 build_graph_envelope（poll_category=True，mock 全部外部依赖）。"""
    with mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch("scripts.lib.ak_1688_client.get_product_details",
                    return_value={ITEM_ID: api_data or {}}), \
         mock.patch("scripts.lib.ak_1688_client.enrich_product_with_cdp",
                    return_value=_enriched()), \
         mock.patch.object(cloud_probe, "_get_ozon_credentials",
                           return_value={"client_id": "123", "api_key": "key"}), \
         mock.patch.object(cloud_probe, "_get_mxou_token", return_value="sk-test"), \
         mock.patch("scripts.lib.ozon_api.search_categories",
                    return_value=[kw.pop("search_result", METAL_PIPE_CAT)]):
        return cloud_probe.build_graph_envelope(
            item_id=ITEM_ID,
            detail_url=DETAIL_URL,
            poll_category=True,
            **kw,
        )


class TestGraphEnvelopeCategoryGate:
    """graph 管线 search_kw 写入点自校验。"""

    def test_inconsistent_guess_not_written(self):
        """①汽油桶锚点：search_kw 猜成金属管 → draft 不带 ozon_category。"""
        graph = _build(api_data=_api_data())
        draft = graph["envelope"]["draft"]
        assert "ozon_category" not in draft, (
            f"不一致猜测应拒写，实际 draft.ozon_category={draft.get('ozon_category')}")

    def test_consistent_guess_written_with_source(self):
        """②一致类目：猜成金属桶 → 照常写入且 source=search_kw。"""
        graph = _build(api_data=_api_data(), search_result=METAL_BARREL_CAT)
        draft = graph["envelope"]["draft"]
        ozc = draft.get("ozon_category")
        assert ozc, "一致猜测应照常写入"
        assert ozc["description_category_id"] == "200001728"
        assert ozc["type_id"] == "970693823"
        assert ozc["source"] == "search_kw"
        assert ozc["namespace"] == "seller"

    def test_manual_overrides_search_kw(self):
        """③manual 直传：category_id/type_id 存在时覆盖猜测（甚至不触发错猜）。"""
        graph = _build(
            api_data=_api_data(),
            category_id="71890001",
            type_id="970693823",
        )
        draft = graph["envelope"]["draft"]
        ozc = draft.get("ozon_category")
        assert ozc, "manual 直传必须写入"
        assert ozc == {
            "description_category_id": "71890001",
            "type_id": "970693823",
            "source": "manual",
            "namespace": "seller",
        }

    def test_manual_bypasses_guess_and_search(self):
        """manual 存在时不调 search_categories（自校验天然跳过）。"""
        with mock.patch("scripts.lib.ozon_api.search_categories") as _search:
            graph = _build(api_data=_api_data(), category_id="71890001", type_id="970693823")
            _search.assert_not_called()
        assert graph["envelope"]["draft"]["ozon_category"]["source"] == "manual"


# ── T0.1b: CLI 传参链 + summary ────────────────────────────────────────


class TestCliManualCategoryArgs:
    """--category-id/--type-id 解析与 summary 输出。"""

    def test_parser_accepts_category_id_type_id(self):
        """graph 子解析器新增 --category-id/--type-id（字符串，默认空）。"""
        parser = cli.build_arg_parser()
        args = parser.parse_args([
            "graph", "--item-id", "123", "--category-id", "71890001",
            "--type-id", "970693823",
        ])
        assert args.category_id == "71890001"
        assert args.type_id == "970693823"

    def test_parser_defaults_empty(self):
        """不传时默认空字符串（自动匹配路径零影响）。"""
        args = cli.build_arg_parser().parse_args(["graph", "--item-id", "123"])
        assert args.category_id == ""
        assert args.type_id == ""

    def test_category_query_help_corrected(self):
        """--category-query help 不再误导为「Ozon 类目关键词（俄语）」。"""
        parser = cli.build_arg_parser()
        graph_action = next(
            a for a in parser._subparsers._group_actions  # noqa: SLF001
            if a.dest == "command")
        graph_parser = graph_action.choices["graph"]
        help_text = next(
            a.help for a in graph_parser._actions if a.dest == "category_query")
        assert "俄语" not in help_text
        assert "--category-id" in help_text

    def test_summary_includes_ozon_category_with_source(self):
        """④summary 摘要带 ozon_category 三键（description_category_id/type_id/source）。

        cmd_graph 内部 `from scripts.cloud_probe import build_graph_envelope_with_retry`
        为函数内局部导入 → 必须 patch 源模块属性；summary 经 `_out` 载荷捕获。
        """
        payloads: list[dict] = []

        def _fake_graph(**kw):
            assert kw.get("category_id") == "71890001"
            assert kw.get("type_id") == "970693823"
            # 模拟 build_graph_envelope_with_retry 返回（manual 直传）
            return {
                "token": "t", "ozon_client_id": "c", "ozon_api_key": "k",
                "envelope": {"draft": {
                    "item_id": "123", "title": "x",
                    "purchase_cost": 1.0, "weight": 0,  # weight=0 跳过预估价网络查询
                    "dimensions": {"length": 0, "width": 0, "height": 0},
                    "ozon_category": {
                        "description_category_id": "71890001",
                        "type_id": "970693823",
                        "source": "manual",
                        "namespace": "seller",
                    },
                }, "source": {}, "extensions": {}},
            }

        fake_args = cli.build_arg_parser().parse_args([
            "graph", "--item-id", "123", "--no-submit",
            "--category-id", "71890001", "--type-id", "970693823",
        ])
        with mock.patch("scripts.lib.config_store.preflight_check", return_value=[]), \
             mock.patch("scripts.cloud_probe.build_graph_envelope_with_retry", _fake_graph), \
             mock.patch.object(cli, "_out", lambda payload: payloads.append(payload)):
            rc = cli.cmd_graph(fake_args)
        assert rc == 0
        summary = payloads[0]["summary"]
        assert summary["ozon_category"] == {
            "description_category_id": "71890001",
            "type_id": "970693823",
            "source": "manual",
        }
        # namespace 非契约三键，不进 summary
        assert "namespace" not in summary["ozon_category"]


# ── T0.5: win32 stdout 编码 + argv 切分取证 ─────────────────────────────


class TestCliOutputAndArgv:
    """main() 入口 win32 reconfigure + argparse 带空格参数。"""

    def test_argparse_keeps_spaces_in_cyrillic_query(self):
        """⑤argv 切分取证：带空格西里尔参数完整到达 args.category_query。

        结论（写死在此）：仓库内全链路 argv 以 list 传递——runtime_probe.py:161
        os.execve(python_cmd, full_argv_list, env) 无 join+split；argparse 对
        list 元素原样保留。历史「带空格参数被切」的根因在仓库外（打包层/外部
        启动器 argv 拼接成字符串再 split），不在本仓库代码。
        """
        parser = cli.build_arg_parser()
        args = parser.parse_args(
            ["graph", "--category-query", "Канистра для ГСМ", "--no-submit"])
        assert args.category_query == "Канистра для ГСМ"
        assert args.no_submit is True

    def test_main_win32_reconfigures_stdout_stderr(self):
        """⑥win32：main() 入口对 stdout/stderr reconfigure utf-8 + replace。

        生产实证：Windows `cli.py graph ... > out.json` 重定向流走 ANSI 代码页，
        输出含 emoji/非 ASCII 抛 UnicodeEncodeError → 文件 0 字节。
        """
        recorded: list[dict] = []

        class _FakeStream:
            def reconfigure(self, **kw):
                recorded.append(kw)

            def write(self, *_a, **_k):
                return 0

            def flush(self):
                pass

        out, err = _FakeStream(), _FakeStream()
        with mock.patch("sys.platform", "win32"), \
             mock.patch.object(sys, "stdout", out), \
             mock.patch.object(sys, "stderr", err), \
             mock.patch.object(sys, "argv", ["cli.py"]):
            rc = cli.main()
        assert rc == 0
        assert len(recorded) == 2, f"stdout+stderr 各 reconfigure 一次，实际 {len(recorded)}"
        for kw in recorded:
            assert kw.get("encoding") == "utf-8"
            assert kw.get("errors") == "replace"

    def test_main_win32_reconfigure_failure_swallowed(self):
        """reconfigure 不可用（如 StringIO 无该方法）→ 静默吞掉，不炸入口。"""
        with mock.patch("sys.platform", "win32"), \
             mock.patch.object(sys, "stdout", io.StringIO()), \
             mock.patch.object(sys, "stderr", io.StringIO()), \
             mock.patch.object(sys, "argv", ["cli.py"]):
            rc = cli.main()
        assert rc == 0

    def test_main_non_win32_does_not_reconfigure(self):
        """非 win32 平台不做 reconfigure（macOS/Linux 行为零变化）。"""
        recorded: list[dict] = []

        class _FakeStream:
            def reconfigure(self, **kw):
                recorded.append(kw)

            def write(self, *_a, **_k):
                return 0

            def flush(self):
                pass

        with mock.patch("sys.platform", "darwin"), \
             mock.patch.object(sys, "stdout", _FakeStream()), \
             mock.patch.object(sys, "stderr", _FakeStream()), \
             mock.patch.object(sys, "argv", ["cli.py"]):
            rc = cli.main()
        assert rc == 0
        assert recorded == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
