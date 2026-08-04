"""api 兜底信封（无 1688 货源字段）应能通过入队校验（P1 回归）。"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from main import _validate_draft_required_fields

def test_api_follow_envelope_skips_1688_fields():
    draft = {
        "item_id": "3852000144", "title": "Тест", "currency": "CNY",
        "images": ["https://cdn.ozon.ru/1.jpg"],
        "ozon_product_id": "3852000144",
        # 无 weight/dimensions/purchase_cost/purchase_url（api 模式不需要）
    }
    extensions = {"follow_sell": True, "follow_type": "api"}
    err = _validate_draft_required_fields(draft, extensions)
    assert err is None, f"api 信封不应被拒: {err}"

def test_hand_envelope_still_requires_1688_fields():
    draft = {"item_id": "1", "title": "T", "currency": "CNY", "images": []}
    extensions = {"follow_sell": True, "follow_type": "hand"}
    err = _validate_draft_required_fields(draft, extensions)
    assert err is not None and "weight" in err


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
