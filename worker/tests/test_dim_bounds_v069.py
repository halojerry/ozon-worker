"""v0.69 Wave 3 回归：尺寸契约边界 clamp + repair 体积重反推 + validate 预检一次列全。

背景（店铺健康扫描：尺寸重量类错误 17 例）：信封 330×430×100mm，Ozon 类目约束
长42–400 / 宽25–400 / 高5–200 → 宽 430 超上限被 INCORRECT_DIMENSION 拒；
retry 映射 repair_prepare 原样重跑同尺寸 → 重试耗尽 failed。

三道防线（本文件全锁定）：
A. normalizer 源头 clamp（OZON_DIM_BOUNDS_MM 唯一事实源，越界取边界值并留痕
   marks["dimensions_clamped"]）；
B. repair_dimensions 仅在修复路径做体积重反推（密度<50 kg/m³ → 体积×300 kg/m³，
   夹 [10, 50000]g）；主链路密度异常仍只标疑不改写（v0.37 轻物保护回归）；
C. ozon_validate 防御第二道：尺寸越界 + 数值坏值 + 必填缺失同批一次列全。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_dim_bounds_v069.py -v
⚠️ 纯 mock（validate/repair 节点不调外部 API），无需 PG/GPU。
"""
import os
import sys

import pytest

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.weight_dimension_normalizer import (
    DEFAULT_DIMS_MM,
    OZON_DIM_BOUNDS_MM,
    normalize_weight_dimensions,
)


@pytest.fixture(autouse=True)
def _img_probe_ok(monkeypatch):
    """v0.69 Wave4 起 validate 图片可达性错误真实生效（extend 收集缺陷修复）——
    测试必须确定性控制 HTTP 探测结果，不得依赖本机网络/代理行为。"""
    class _R:
        status_code = 200

    monkeypatch.setattr("requests.head", lambda *a, **k: _R(), raising=False)


# ─────────────────────── 任务 A：normalizer 源头 clamp ───────────────────────

def test_bounds_constant_shape():
    """OZON_DIM_BOUNDS_MM 唯一事实源：三键 + (下限, 上限) 二元组，validate/retry 共用。"""
    assert OZON_DIM_BOUNDS_MM == {
        "length": (42, 400),
        "width": (25, 400),
        "height": (5, 200),
    }


def test_width_430_clamped_to_400_with_marks():
    """①锚点案例：宽 430 超上限 → clamp 400 + marks["dimensions_clamped"] 记 from/to。"""
    w, d, marks = normalize_weight_dimensions(
        950, {"length": 330, "width": 430, "height": 100}, None
    )
    assert d["width"] == 400, f"宽 430 应 clamp 到 400，实际 {d['width']}"
    assert d["length"] == 330 and d["height"] == 100, "合法维不得误改"
    assert w == 950, "clamp 只动尺寸不动重量"
    assert marks["dimensions_clamped"] == {"width": {"from": 430, "to": 400}}
    assert any("dim_width_clamped" in r for r in marks["reasons"])
    assert marks["dimensions_suspected"] is False, "密度 72 kg/m³ 正常，不得误标疑"


def test_legal_dims_untouched():
    """②合法 300/200/50 → 原样 + dimensions_clamped 空。"""
    w, d, marks = normalize_weight_dimensions(
        500, {"length": 300, "width": 200, "height": 50}, None
    )
    assert d == {"length": 300, "width": 200, "height": 50}
    assert w == 500
    assert marks.get("dimensions_clamped", {}) == {}


def test_length_30_clamped_to_42_and_width_low_bound():
    """③长 30 低于下限 → clamp 42；宽 10 低于下限 → clamp 25（双向边界）。"""
    w, d, marks = normalize_weight_dimensions(
        100, {"length": 30, "width": 10, "height": 50}, None
    )
    assert d["length"] == 42
    assert d["width"] == 25
    assert d["height"] == 50
    assert marks["dimensions_clamped"] == {
        "length": {"from": 30, "to": 42},
        "width": {"from": 10, "to": 25},
    }


def test_default_fallback_dims_not_clamped():
    """④缺失兜底默认值 300/200/50 本就合法 → 不被误改（走同一 clamp 路径但零命中）。"""
    w, d, marks = normalize_weight_dimensions(300, {"length": 0, "width": 0, "height": 0}, None)
    assert (d["length"], d["width"], d["height"]) == DEFAULT_DIMS_MM
    assert marks.get("dimensions_clamped", {}) == {}
    assert not any("clamped" in r for r in marks["reasons"])


def test_competitor_backfill_dims_clamped():
    """A.4 竞品回填值走同一 clamp 路径：竞品 450×500×300 → 400/400/200 三维全夹。"""
    w, d, marks = normalize_weight_dimensions(
        0, {"length": 0, "width": 0, "height": 0},
        {"competitor_weight_g": 2000,
         "competitor_dimensions_mm": {"length": 450, "width": 500, "height": 300}},
    )
    assert d == {"length": 400, "width": 400, "height": 200}
    assert marks["dimensions_clamped"] == {
        "length": {"from": 450, "to": 400},
        "width": {"from": 500, "to": 400},
        "height": {"from": 300, "to": 200},
    }


def test_main_path_low_density_marked_not_rewritten():
    """⑧主链路密度异常仍只标疑不改写重量（v0.37 轻物保护回归）：
    40g / 500×400×300 → 先 clamp 到 400×400×200，密度 1.25 kg/m³ < 1.293 →
    dimensions_suspected=True，但重量保持 40g（绝不改写/不 ×1000）。"""
    w, d, marks = normalize_weight_dimensions(
        40, {"length": 500, "width": 400, "height": 300}, None
    )
    assert w == 40, "密度异常只标疑，重量绝不改写"
    assert d == {"length": 400, "width": 400, "height": 200}
    assert marks["dimensions_suspected"] is True
    assert any("density_too_low" in r for r in marks["reasons"])
    assert marks["dimensions_clamped"]["length"] == {"from": 500, "to": 400}


# ─────────────────────── 任务 C：validate 预检一次列全 ───────────────────────

from graphs.state import OzonValidateInput
from graphs.nodes.ozon_validate_node import ozon_validate_node


def _run_validate(items, attributes_schema=None):
    state = OzonValidateInput(
        ozon_payload={"items": items},
        ozon_client_id="c",
        ozon_api_key="k",
        attributes_schema=attributes_schema or [],
    )
    runtime = type("R", (), {"context": None})()
    return ozon_validate_node(state, {}, runtime)


def _item(**over):
    # name 与 dc/tp 的 RU 类目路径（…Трещотка）保持词面一致——v0.69 Wave4 T2.1
    # 标题-类目一致性预检（只拦零交集）上线后，通用假名「Тест товар」会被误拦，
    # 本文件测的是尺寸/数值/必填，不是标题语义，fixture 对齐类目保住原意。
    base = {
        "name": "Трещотка набор", "offer_id": "sku1", "price": "1990",
        "old_price": "2390", "vat": "0", "weight": 950, "weight_unit": "g",
        "depth": 330, "width": 400, "height": 100, "dimension_unit": "mm",
        "images": ["https://example.com/img.jpg"],
        "primary_image": "https://example.com/img.jpg",
        "description_category_id": 17028653, "type_id": 92147,
        "attributes": [],
    }
    base.update(over)
    return base


def test_validate_dim_out_of_bounds_reported():
    """⑤越界 payload → errors 含尺寸明细（维度名+实测+边界），is_valid=False。"""
    out = _run_validate([_item(width=430)])
    dim_errs = [e for e in out.validation_errors if "超出" in e and "width" in e]
    assert dim_errs, f"宽 430 越界必须进 validation_errors，实际: {out.validation_errors}"
    msg = dim_errs[0]
    assert "430" in msg, f"错误需含实测值: {msg}"
    assert "400" in msg and "25" in msg, f"错误需含边界: {msg}"
    assert out.is_valid is False, "尺寸越界必须阻断"


def test_validate_dim_and_numeric_bad_both_listed():
    """⑥尺寸越界 + 数值坏值同时存在 → 两条都列出（一次列全，不 fail-first）。"""
    schema = [{"id": 8962, "name": "Единиц в одном товаре", "type": "Integer",
               "dictionary_id": 0, "is_required": False}]
    item = _item(width=430, attributes=[{"id": 8962, "values": [{"value": "без числа"}]}])
    out = _run_validate([item], attributes_schema=schema)
    errs = out.validation_errors
    assert any("超出" in e and "width" in e for e in errs), f"尺寸错误缺失: {errs}"
    assert any("无法解析" in e and "8962" in e for e in errs), f"数值坏值错误缺失: {errs}"
    assert out.is_valid is False


def test_validate_numeric_ok_value_not_flagged():
    """数值属性带单位（'30包'）可解析 → validate 不报错（清洗是 prepare 的职责，
    validate 只拦完全无法解析的坏值）。"""
    schema = [{"id": 8962, "name": "Единиц в одном товаре", "type": "Integer",
               "dictionary_id": 0, "is_required": False}]
    item = _item(attributes=[{"id": 8962, "values": [{"value": "30包"}]}])
    out = _run_validate([item], attributes_schema=schema)
    assert not any("8962" in e for e in out.validation_errors), \
        f"'30包' 可解析不应报错，实际: {out.validation_errors}"
    assert out.is_valid is True


def test_validate_required_attr_missing_listed():
    """C.4 必填对照：schema is_required 属性缺失 → 列 attr_id+name，同批返回。"""
    schema = [{"id": 5911, "name": "Назначение", "type": "String",
               "dictionary_id": 0, "is_required": True}]
    out = _run_validate([_item(width=430)], attributes_schema=schema)
    errs = out.validation_errors
    assert any("必填属性缺失" in e and "5911" in e and "Назначение" in e for e in errs), \
        f"必填缺失需列 attr_id+name: {errs}"
    assert any("超出" in e and "width" in e for e in errs), "与尺寸错误同批列出"
    assert out.is_valid is False


def test_validate_required_attr_present_ok():
    """必填属性在 payload 中 → 不报缺失。"""
    schema = [{"id": 5911, "name": "Назначение", "type": "String",
               "dictionary_id": 0, "is_required": True}]
    item = _item(attributes=[{"id": 5911, "values": [{"value": "для дома"}]}])
    out = _run_validate([item], attributes_schema=schema)
    assert not any("必填属性缺失" in e for e in out.validation_errors)
    assert out.is_valid is True


# ─────────────────────── 任务 B：repair 路径体积重反推 ───────────────────────

from graphs.validation_retry_loop import (
    MIN_PHYSICAL_DENSITY_KG_M3,
    VOLUME_WEIGHT_DENSITY_KG_M3,
    ValidationRetryLoopState,
    repair_dimensions_node,
)


def _repair(weight, depth, width, height):
    state = ValidationRetryLoopState(
        ozon_payload={"items": [{
            "name": "Товар", "weight": str(weight), "weight_unit": "g",
            "depth": str(depth), "width": str(width), "height": str(height),
            "dimension_unit": "mm",
            "offer_id": "x", "price": "10", "old_price": "12", "currency_code": "CNY",
        }]},
        error_code="ML_INCORRECT_VOLUME_WEIGHT",
        error_message="ML_INCORRECT_VOLUME_WEIGHT",
        retry_count=1,
    )
    out = repair_dimensions_node(state)
    item = out.ozon_payload["items"][0]
    return item, out


def test_repair_volume_weight_inferred_below_physical_density():
    """⑦体积重反推锚点：300g / 330×430×100mm（14190cm³）→ 密度 21 kg/m³ < 50 →
    按 300 kg/m³ 反推 14190×0.3=4257g（夹 [10,50000] 内不截断）+ repair_marks 留痕。
    对照：950g 同尺寸 = 67 kg/m³ > 50 → 保持真实重量不反推（阈值不误伤）。"""
    assert MIN_PHYSICAL_DENSITY_KG_M3 == 50
    assert VOLUME_WEIGHT_DENSITY_KG_M3 == 300

    item, out = _repair(300, 330, 430, 100)
    assert item["weight"] == "4257", f"应反推为 4257g，实际 {item['weight']}"
    inferred = (out.repair_marks or {}).get("weight_inferred", [])
    assert inferred and inferred[0]["from_g"] == 300 and inferred[0]["to_g"] == 4257

    item2, out2 = _repair(950, 330, 430, 100)
    assert item2["weight"] == "950", "密度 67 kg/m³ > 50 不得反推（保持真实重量）"
    assert not (out2.repair_marks or {}).get("weight_inferred")


def test_repair_volume_weight_clamped_to_bounds():
    """反推结果夹到 [10, 50000]g：0.2m³×300=60000g → 夹 50000；体积过小 → 夹下限 10。"""
    item, out = _repair(100, 1000, 1000, 200)  # 0.2 m³, 密度 0.5 < 50 → 反推 60000 → 50000
    assert item["weight"] == "50000"
    assert (out.repair_marks or {})["weight_inferred"][0]["to_g"] == 50000

    item3, out3 = _repair(1, 100, 40, 10)  # 40000mm³=0.00004m³, 密度 25 < 50 → 反推 12g
    assert item3["weight"] == "12", f"小体积反推 12g 不触下限，实际 {item3['weight']}"


def test_repair_normal_density_weight_untouched():
    """repair 路径密度正常/偏高（含 v0.37 wave2 锚点 387g/115×32×115=914 kg/m³）→
    重量与尺寸一律保留。"""
    for weight, dd, ww, hh in [(387, 115, 32, 115), (250, 100, 80, 60), (1200, 200, 150, 100)]:
        item, out = _repair(weight, dd, ww, hh)
        assert int(item["weight"]) == weight
        assert (int(item["depth"]), int(item["width"]), int(item["height"])) == (dd, ww, hh)
        assert not (out.repair_marks or {}).get("weight_inferred")


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
