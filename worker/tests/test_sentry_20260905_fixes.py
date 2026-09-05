# -*- coding: utf-8 -*-
"""Sentry 2026-09-05 分析修复回归（v0.63.2 批次）。

覆盖四组修复：
1. P1b 数值规格字典属性（轮胎 7387 截面宽度 / 7389 直径英寸）—— 字典主体纯数字时
   从 1688 属性值/标题提取数字精确唯一命中（Sentry POUDING_OZON-42/E1/E2/E3）。
2. P1b 证书编号类必填属性（12882 等）retry 提前终态，不烧满 3 轮 retry（POUDING_OZON-42）。
3. P2 9048(Название модели) 移出本地纯拉丁检测 —— v0.60 防并卡前缀 `item_id~sha1`
   本就是拉丁+数字，本地误报烧 retry（POUDING_OZON-C2）。
4. P1a category_tree_nodes 全量同步 advisory lock + 去重 + 分块 bulk upsert
   （死锁 POUDING_OZON-E6/E7/E8，需本地 PG，不可达时 skip）。
5. P1b 汽车/摩托消歧组合词归一（「汽摩配件>汽车轮胎」不再因字面双信号放弃判别）。

运行: cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_sentry_20260905_fixes.py -q
"""
import inspect
import os
import re
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.attr_defaults import (  # noqa: E402
    find_dict_value_id,
    is_cert_number_attr,
    resolve_missing_mandatory_dict_attr,
    resolve_numeric_dict_default,
)

# 真实轮胎宽度字典档位形态（Ozon 7387: "135"~"355" 纯数字字符串）
WIDTH_DICT = [{"id": 9000 + int(w), "value": w} for w in
              ("135", "155", "165", "175", "185", "195", "205", "215", "225", "235", "245", "255")]
# 直径档位（7389: "12"~"24"）
DIAM_DICT = [{"id": 8000 + int(d), "value": d} for d in
             ("12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22")]
# 文本字典（颜色），用于门槛验证
TEXT_DICT = [{"id": 7000 + i, "value": v} for i, v in
             enumerate(("черный", "белый", "серый", "синий", "красный", "зеленый"))]


# ─────────────────────── 1. 数值规格字典属性 ───────────────────────

def test_numeric_width_from_zh_attr():
    """截面宽度：1688 值 "225mm" → 精确命中 225 档位。"""
    res = resolve_numeric_dict_default(
        "Ширина профиля, мм",
        draft_attrs={"截面宽度": "225mm", "直径": "17英寸"},
        title_cn="汽车轮胎 225/45R17",
        dict_vals=WIDTH_DICT,
    )
    assert res == (9000 + 225, "225")


def test_numeric_diameter_from_zh_attr():
    """直径英寸：1688 值 "17英寸" → 精确命中 17 档位（同款 225/45R17 数据）。"""
    res = resolve_numeric_dict_default(
        "Диаметр, дюймы",
        draft_attrs={"截面宽度": "225mm", "直径(英寸)": "17英寸"},
        title_cn="汽车轮胎 225/45R17",
        dict_vals=DIAM_DICT,
    )
    assert res == (8000 + 17, "17")


def test_numeric_router_wiring_both_attrs():
    """路由器接线：225/45R17 单条数据分别喂宽度/直径两个属性，各自唯一命中。"""
    draft_attrs = {"截面宽度": "225mm", "直径": "17英寸"}
    width = resolve_missing_mandatory_dict_attr(
        7387, "Ширина профиля, мм",
        title_cn="汽车轮胎 225/45R17", dict_vals=WIDTH_DICT, draft_attrs=draft_attrs,
    )
    diam = resolve_missing_mandatory_dict_attr(
        7389, "Диаметр, дюймы",
        title_cn="汽车轮胎 225/45R17", dict_vals=DIAM_DICT, draft_attrs=draft_attrs,
    )
    assert width == (9000 + 225, "225")
    assert diam == (8000 + 17, "17")


def test_numeric_title_fallback_unique():
    """1688 属性缺失时，标题数字精确唯一命中也可填。"""
    res = resolve_numeric_dict_default(
        "Диаметр, дюймы",
        draft_attrs={"品牌": "某牌"},
        title_cn="摩托车轮胎 90/90-14",
        dict_vals=DIAM_DICT,
    )
    assert res == (8000 + 14, "14")


def test_numeric_ambiguous_abstains():
    """多档位命中（235 与 225 都在源数据里且无定向属性）→ 宁缺毋滥。"""
    res = resolve_numeric_dict_default(
        "Ширина профиля, мм",
        draft_attrs={"适用轮胎": "225 或 235"},
        title_cn="",
        dict_vals=WIDTH_DICT,
    )
    assert res is None


def test_numeric_text_dict_gated():
    """文本字典（颜色等）不启用数值分支。"""
    res = resolve_numeric_dict_default(
        "Ширина профиля, мм",
        draft_attrs={"截面宽度": "225mm"},
        title_cn="",
        dict_vals=TEXT_DICT,
    )
    assert res is None


def test_numeric_small_dict_gated():
    """数字档位 <3 的小字典不启用（唯一值场景由其他分支负责）。"""
    res = resolve_numeric_dict_default(
        "Диаметр, дюймы",
        draft_attrs={"直径": "17英寸"},
        title_cn="",
        dict_vals=[{"id": 1, "value": "17"}],
    )
    assert res is None


def test_router_backward_compatible_without_draft_attrs():
    """新增 draft_attrs 是可选参数：不传时既有语义分支行为不变（品牌默认）。"""
    brand_dict = [{"id": 126745801, "value": "Нет бренда"}]
    res = resolve_missing_mandatory_dict_attr(85, "Производитель", dict_vals=brand_dict)
    assert res == (126745801, "Нет бренда")


# ─────────────────────── 2. 证书编号类前置终态 ───────────────────────

def _make_retry_state(**overrides):
    from graphs.validation_retry_loop import ValidationRetryLoopState
    base = dict(
        error_code="error_attribute_values_empty",
        attribute_id=12882,
        retry_count=1,
        max_retries=3,
        final_attributes=[],
        attributes_schema=[{"id": 12882, "name": "Номер сертификата",
                            "dictionary_id": 12345, "type": "Dictionary"}],
        draft={"title": "Автошина 225/45R17", "attributes": {"截面宽度": "225mm"}},
        ozon_payload={"items": []},
        product_name="Автошина летняя 225/45R17",
    )
    base.update(overrides)
    return ValidationRetryLoopState(**base)


def test_is_cert_number_attr():
    assert is_cert_number_attr(12882, "")
    assert is_cert_number_attr(0, "Номер сертификата")
    assert is_cert_number_attr(0, "证书编号")
    assert not is_cert_number_attr(7387, "Ширина профиля, мм")


def test_cert_attr_fails_fast_without_source():
    """无证书来源 → 强制收敛退出循环（retry_count==max_retries）+ 可行动错误信息。"""
    from graphs.validation_retry_loop import error_repair_llm_node
    state = _make_retry_state()
    out = error_repair_llm_node(state)
    assert out.retry_count == 3, "应强制收敛（should_continue 走 exit），不再烧满 3 轮 retry"
    assert "证书编号" in out.error_message and "12882" in out.error_message
    assert all(not (isinstance(a, dict) and a.get("id") == 12882) for a in out.final_attributes), \
        "不得伪造证书编号写入属性"


def test_cert_attr_fills_when_envelope_has_cert_dict_hit():
    """信封真的带证书编号且字典精确命中 → 正常补齐，不强制收敛。"""
    from graphs.validation_retry_loop import error_repair_llm_node
    cert = "RU Д-12345-2026"
    state = _make_retry_state(
        draft={"title": "x", "attributes": {"证书编号": cert}},
        dictionary_values={"12882": [{"id": 555001, "value": cert}]},
    )
    out = error_repair_llm_node(state)
    hit = [a for a in out.final_attributes if isinstance(a, dict) and a.get("id") == 12882]
    assert hit and hit[0]["dictionary_value_id"] == 555001
    assert out.retry_count == 1, "命中补齐不应强制收敛"


def test_cert_attr_no_false_positive_on_width():
    """数值规格属性（7387）不触发证书前置终态（走正常修复链路）。"""
    from graphs.validation_retry_loop import error_repair_llm_node
    state = _make_retry_state(
        attribute_id=7387,
        attributes_schema=[{"id": 7387, "name": "Ширина профиля, мм",
                            "dictionary_id": 999, "type": "Dictionary"}],
        dictionary_values={"7387": WIDTH_DICT},
    )
    out = error_repair_llm_node(state)
    # 数值分支应命中 225 并写回 final_attributes（而非强制收敛）
    assert out.retry_count == 1
    hit = [a for a in out.final_attributes if isinstance(a, dict) and a.get("id") == 7387]
    assert hit and hit[0]["dictionary_value_id"] == 9000 + 225


# ─────────────────────── 3. 9048 纯拉丁误报豁免 ───────────────────────

def test_9048_removed_from_local_latin_check():
    """9048 不在本地纯拉丁检测名单（v0.60 防并卡前缀 `item_id~sha1` 是合法拉丁）。"""
    from graphs.nodes import ozon_validate_node as m
    src = inspect.getsource(m.ozon_validate_node)
    assert "in (4191, 9048, 4180)" not in src, "9048 必须移出纯拉丁检测（误报 C2）"
    assert "in (4191, 4180)" in src, "4191/4180 仍保留检测"


def test_9048_prefix_value_semantics():
    """v0.60 前缀值 `item_id~sha1` 形态纯拉丁+数字 → 不应被视为错误。"""
    val = "779015593932~1b2acd9b"
    latin_re = re.compile(r"[a-zA-Z]")
    cyr_re = re.compile(r"[а-яА-ЯёЁ]")
    assert latin_re.search(val) and not cyr_re.search(val), "样例值应是纯拉丁（即 C2 误报形态）"


# ─────────────────────── 4. 汽车/摩托消歧组合词归一 ───────────────────────

from graphs.nodes.assemble_ozon_product_node import _apply_vehicle_disambiguation  # noqa: E402

_COMBO_CANDIDATES = [
    {"description_category_id": 200001531, "type_id": 971447047,
     "node_name": "摩托车轮毂", "full_path": "汽车用品 > 摩托车零件 > 摩托车轮毂"},
    {"description_category_id": 17028758, "type_id": 970619447,
     "node_name": "车轮总成", "full_path": "汽车用品 > 轮辋 > 车轮总成"},
]


def test_combo_prefix_qimoche_car_signal_judged():
    """「汽摩配件+汽车轮胎」：剥除组合词后汽车信号生效 → 剔除摩托子树。"""
    signal = "汽摩配件 汽车轮胎 225/45R17 适配奥迪A4"
    res = _apply_vehicle_disambiguation(_COMBO_CANDIDATES, signal)
    assert [c["node_name"] for c in res] == ["车轮总成"]


def test_combo_prefix_qiche_moto_signal_still_neutral():
    """「汽车摩托」组合词出现在真双域商品（另有独立摩托词）→ 剥除后仍双信号 → 不判别。"""
    signal = "汽摩配件 通用轮胎 适用于汽车摩托车"
    res = _apply_vehicle_disambiguation(_COMBO_CANDIDATES, signal)
    assert len(res) == 2, "真双信号不得判别（避免误伤）"


# ─────────────────────── 5. P1a 类目树同步（需本地 PG） ───────────────────────

_PG_URL = os.getenv("PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon")
_TEST_LANG = f"ZZ_TEST_{int(time.time())}"


def _pg_reachable() -> bool:
    try:
        from sqlalchemy import create_engine, text
        eng = create_engine(_PG_URL)
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


def _small_tree(dc_base: int):
    """构造小类目树：1 个一级类目 + 2 个叶子 type。"""
    return {"result": [
        {
            "description_category_id": dc_base,
            "category_name": "测试一级类目",
            "disabled": False,
            "children": [
                {"description_category_id": 0, "type_name": "测试类型A", "type_id": dc_base + 1, "children": []},
                {"description_category_id": 0, "type_name": "测试类型B", "type_id": dc_base + 2, "children": []},
            ],
        }
    ]}


def _cleanup_test_rows():
    from sqlalchemy import create_engine, text
    eng = create_engine(_PG_URL)
    with eng.begin() as c:
        c.execute(text("DELETE FROM category_tree_nodes WHERE language = :l"), {"l": _TEST_LANG})
    eng.dispose()


@pytest.mark.skipif(not _pg_reachable(), reason="本地 PG 不可达")
def test_sync_basic_and_skip_if_nonempty():
    """基础同步 + skip_if_nonempty 双检：表非空时判空同步路径直接放弃。"""
    from utils.ozon_category_query import OzonCategoryQuery
    try:
        q = OzonCategoryQuery()
        dc_base = 990000000 + int(time.time()) % 100000
        n = q.sync_category_tree_nodes(_small_tree(dc_base), language=_TEST_LANG)
        assert n == 3, "1 类目 + 2 类型 = 3 行"

        # 再同步一次（全量 upsert 幂等）
        n2 = q.sync_category_tree_nodes(_small_tree(dc_base), language=_TEST_LANG)
        assert n2 == 3

        # 判空同步路径：表已有数据 → skip 返回 0
        n3 = q.sync_category_tree_nodes(_small_tree(dc_base), language=_TEST_LANG, skip_if_nonempty=True)
        assert n3 == 0
    finally:
        _cleanup_test_rows()


@pytest.mark.skipif(not _pg_reachable(), reason="本地 PG 不可达")
def test_sync_duplicate_keys_and_concurrent_no_deadlock():
    """重复唯一键不炸（去重后写）+ 双线程并发同步被 advisory lock 串行化不死锁。"""
    from utils.ozon_category_query import OzonCategoryQuery
    try:
        q = OzonCategoryQuery()
        dc_base = 991000000 + int(time.time()) % 100000
        tree = _small_tree(dc_base)
        # 构造重复键：同一 (dc, type_id) 出现两次（旧逐行实现容忍、bulk 会报
        # cannot affect row a second time，必须先去重）
        dup = {"result": list(tree["result"]) + [{
            "description_category_id": dc_base,
            "category_name": "测试一级类目",
            "disabled": False,
            "children": [
                {"description_category_id": 0, "type_name": "测试类型A重", "type_id": dc_base + 1, "children": []},
            ],
        }]}
        n = q.sync_category_tree_nodes(dup, language=_TEST_LANG)
        assert n == 3, "重复键去重后仍 3 行"

        # 并发同步：两个线程同时全量同步，advisory lock 串行化 → 都成功、无死锁
        results = []
        errors = []

        def _worker():
            try:
                qq = OzonCategoryQuery()
                results.append(qq.sync_category_tree_nodes(tree, language=_TEST_LANG))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=_worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors, f"并发同步不应报错（死锁会以异常出现）: {errors}"
        assert results == [3, 3]
    finally:
        _cleanup_test_rows()
