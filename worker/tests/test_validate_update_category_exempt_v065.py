# -*- coding: utf-8 -*-
"""v0.65 C3(N6): 跟卖 UPDATE 省略类目 vs validate 类目必填打架修复测试。

背景：follow api(import-by-sku) 成功 + 类目缺失 → 设计上省略 dc/tp（走 UPDATE
更新已有卡），但 ozon_validate 无条件报「类目缺失」→ critical → 进 retry 对
一张本不需类目的卡盲修烧 3 轮。

修复：item 带 product_id（UPDATE 模式）→ 豁免类目必填校验（Ozon 按 product_id
更新已有卡，无需类目）；CREATE item（无 product_id）缺类目仍报错。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_validate_update_category_exempt_v065.py -v
⚠️ 纯 mock（node 不调外部 API，走本地校验），无需 PG/GPU。
"""
import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import OzonValidateInput  # noqa: E402
from graphs.nodes.ozon_validate_node import ozon_validate_node  # noqa: E402


def _run(items):
    """构造完整 payload item（含 name/offer_id/price 等必需字段），跑 validate。"""
    state = OzonValidateInput(
        ozon_payload={"items": items},
        ozon_client_id="c",
        ozon_api_key="k",
    )
    runtime = type("R", (), {"context": None})()
    return ozon_validate_node(state, {}, runtime)


def _item(**over):
    base = {
        "name": "Тест товар", "offer_id": "sku1", "price": "1990",
        "old_price": "2390", "vat": "0", "weight": 300, "weight_unit": "g",
        "depth": 100, "width": 100, "height": 50, "dimension_unit": "mm",
        "images": ["https://example.com/img.jpg"],
        "primary_image": "https://example.com/img.jpg",
        # 无 description_category_id / type_id（缺类目）
    }
    base.update(over)
    return base


def test_update_item_missing_category_exempt():
    """带 product_id（UPDATE 模式）item 缺类目 → 不报类目缺失、is_valid。"""
    out = _run([_item(product_id=12345)])
    assert not any("类目" in e for e in out.validation_errors), \
        f"UPDATE item 缺类目不应报类目缺失，实际: {out.validation_errors}"
    assert out.is_valid is True, "UPDATE item 缺类目应放行"


def test_create_item_missing_category_still_errors():
    """CREATE item（无 product_id）缺类目 → 仍报类目缺失、is_valid=False。"""
    out = _run([_item()])
    assert any("类目" in e for e in out.validation_errors), \
        f"CREATE item 缺类目应报错，实际: {out.validation_errors}"
    assert out.is_valid is False, "CREATE item 缺类目应阻断"


def test_create_item_with_category_valid():
    """CREATE item 带类目 → 通过（不误报类目缺失）。"""
    out = _run([_item(description_category_id=17028653, type_id=92147)])
    assert not any("类目" in e for e in out.validation_errors), \
        f"带类目不应报类目缺失，实际: {out.validation_errors}"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
