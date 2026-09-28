#!/usr/bin/env python3
"""v0.83.1 N2（fix/rich-content-format-v083）：validate 级类目重配「泛词」误命中修复。

gate v083 重跑实锤（REPORT-v083-rerun.md N2）：
  - unit3 手持风扇 RU 标题 «…портативный вентилятор…» × 候选
    «Коагулометр портативный»（凝血分析仪）— 共用**单个泛形容词** портативный
    → 旧强匹配判据（单个公共西里尔词）判为强匹配 → 类目被改写成凝血仪子树。
  - unit2 钥匙盒 «камень-тайник … декоративный …» × «Декоративный камень для
    отделки» → 被改写成建筑装饰石材。

修法：`_strong_category_match` 剔除泛词后要求 **≥2 个非泛词共享**，或
**1 个非泛词 + 候选与当前类目同顶层大类**（复用 utils.category_consistency_lexicon
的 generic_words 泛词表）。

契约：
  N2-a 单词（泛形容词）共享不再换类目（风扇×凝血仪、钥匙盒×装饰石材）。
  N2-b 双非泛词共享仍可换类目（держатель + душа）。
  N2-c 单非泛词 + 同顶层大类 → 可换；跨大类 → 不换。
  N2-d 泛词表加载失败/缺失 → 退化为旧行为（不误收紧），加载器本身宁严勿松。
  N2-e 端到端：ozon_validate_node 不再把风扇改写到凝血仪（维持入箱拦截）。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_recategorize_generic_word_v0831.py -q
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402

from graphs.state import OzonValidateInput  # noqa: E402
from graphs.nodes import ozon_validate_node as ovn  # noqa: E402
from graphs.nodes.ozon_validate_node import (  # noqa: E402
    _strong_category_match,
    _top_category_segment,
    ozon_validate_node,
)
from utils.category_consistency_lexicon import (  # noqa: E402
    is_generic_word,
    non_generic_words,
)

# ── gate 实况数据 ──
# unit3：手持风扇（当前类目 RU 路径与标题零交集，才会触发零交集闸 → 重配）
_FAN_TITLE = "Портативный вентилятор настольный с подсветкой"
_FAN_CAND = {"node_name": "Коагулометр портативный",
             "full_path": "Медицина > Приборы > Коагулометр"}
_FAN_CURRENT_PATH = "Дом > Климат > Осушители воздуха"

# unit2：钥匙盒（当前类目 Дом/Хранение）
_KEYBOX_TITLE = "Камень-тайник декоративный для ключей"
_KEYBOX_CAND = {"node_name": "Декоративный камень для отделки",
                "full_path": "Стройматериалы > Отделка > Камень"}
_KEYBOX_CURRENT_PATH = "Дом > Хранение > Ключницы"


# ═══════════════ N2-d：泛词表加载器 ═══════════════

class TestGenericLexicon:
    def test_gate_words_are_generic(self):
        assert is_generic_word("портативный") is True
        assert is_generic_word("декоративный") is True
        # 词形变体（词根前缀命中）
        assert is_generic_word("портативная") is True
        assert is_generic_word("Портативные") is True
        assert is_generic_word("универсальный") is True

    def test_category_words_are_not_generic(self):
        """品类词/材质词严禁入泛词表（红线）。"""
        for w in ("вентилятор", "коагулометр", "камень", "ключница",
                  "держатель", "душа", "ложка", "блузка"):
            assert is_generic_word(w) is False, f"{w} 不应是泛词"

    def test_short_tokens_never_generic(self):
        for w in ("", "для", "и", "к"):
            assert is_generic_word(w) is False

    def test_non_generic_words_filters(self):
        got = non_generic_words({"портативный", "вентилятор", "декоративный"})
        assert got == {"вентилятор"}

    def test_missing_config_degrades_to_empty(self, monkeypatch):
        """配置缺失/损坏 → 空泛词表 → is_generic_word 恒 False（不误收紧）。"""
        import utils.category_consistency_lexicon as lex

        monkeypatch.setattr(lex, "_LEXICON_PATH", "/nonexistent/lex.json")
        lex._load_generic_words.cache_clear()
        try:
            assert is_generic_word("портативный") is False
        finally:
            lex._load_generic_words.cache_clear()


# ═══════════════ N2-a：单词泛形容词共享不再换类目 ═══════════════

class TestSingleGenericWordRejected:
    def test_fan_portable_not_strong_match(self):
        """«портативный вентилятор» × «Коагулометр портативный» → 非强匹配。"""
        assert _strong_category_match(
            _FAN_TITLE, _FAN_CAND, _FAN_CURRENT_PATH) is False

    def test_keybox_decorative_not_strong_match(self):
        """«декоративный камень» × «Декоративный камень для отделки» →
        唯一非泛词 камень 但跨大类 → 非强匹配。"""
        assert _strong_category_match(
            _KEYBOX_TITLE, _KEYBOX_CAND, _KEYBOX_CURRENT_PATH) is False

    def test_only_generic_words_never_strong(self):
        """标题与候选只剩泛词共享 → 恒 False（即便同顶层大类也不放行）。"""
        assert _strong_category_match(
            "Портативный универсальный",
            {"node_name": "Универсальный портативный",
             "full_path": "Дом > Приборы > Прочее"},
            "Дом > Приборы > Прочее",
        ) is False


# ═══════════════ N2-b / N2-c：正例仍可换 ═══════════════

class TestStrongMatchPositive:
    def test_two_non_generic_words_pass(self):
        """держатель + душа（2 个非泛词）→ 强匹配（6022b0c9 救场路径不回归）。"""
        assert _strong_category_match(
            "Держатель для душа угловой",
            {"node_name": "Держатель для душа",
             "full_path": "Стройматериалы > Сантехника > Держатель для душа"},
            "Дом > Мебель > Полка",
        ) is True

    def test_one_non_generic_plus_same_top_category_pass(self):
        """1 个非泛词 держатель + 同顶层大类（Дом）→ 可换（同大类横向改配）。"""
        assert _strong_category_match(
            "Держатель для душа угловой",
            {"node_name": "Держатель полки", "full_path": "Дом > Сантехника > Держатель"},
            "Дом > Мебель > Полка",
        ) is True

    def test_one_non_generic_cross_top_category_rejected(self):
        """1 个非泛词但跨顶层大类 → 拒绝（防跨大类跳转）。"""
        assert _strong_category_match(
            "Держатель для душа угловой",
            {"node_name": "Держатель полки",
             "full_path": "Стройматериалы > Сантехника > Держатель"},
            "Дом > Мебель > Полка",
        ) is False

    def test_top_category_segment(self):
        assert _top_category_segment("Дом > Климат > Вентиляторы") == "дом"
        assert _top_category_segment("") == ""
        assert _top_category_segment("Дом") == "дом"


# ═══════════════ N2-e：端到端 ═══════════════

class _Resp:
    def __init__(self, status=200):
        self.status_code = status
        self.headers = {}
        self.is_redirect = False
        self.is_permanent_redirect = False


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, port=None, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port or 0))])
    monkeypatch.setattr("requests.request", lambda *a, **k: _Resp(200))


def _run(items):
    state = OzonValidateInput(
        ozon_payload={"items": items},
        ozon_client_id="c",
        ozon_api_key="k",
        attributes_schema=[],
    )
    runtime = type("R", (), {"context": None})()
    return ozon_validate_node(state, {}, runtime)


def _fan_item(**over):
    base = {
        "name": _FAN_TITLE, "offer_id": "sku-fan", "price": "1990",
        "old_price": "2390", "vat": "0", "weight": 300, "weight_unit": "g",
        "depth": 100, "width": 100, "height": 50, "dimension_unit": "mm",
        "images": ["https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/x.jpg"],
        "primary_image": "https://example.com/img.jpg",
        # 当前类目与 RU 路径零交集（触发零交集闸 → 才可能重配）
        "description_category_id": 17028743, "type_id": 148495146,
        "attributes": [],
    }
    base.update(over)
    return base


def test_e2e_fan_not_rewritten_to_coagulometer(monkeypatch):
    """端到端：风扇类目零交集 + 搜索命中凝血仪（仅共享泛词 портативный）→
    不再改写 dc/tp，维持 mismatch 拦截。"""
    monkeypatch.setattr(ovn, "_fetch_ru_category_path",
                        lambda dc, tp: _FAN_CURRENT_PATH
                        if (dc, tp) == (17028743, 148495146)
                        else "Медицина > Приборы > Коагулометр",
                        raising=False)
    with patch("utils.ozon_category_query.OzonCategoryQuery.search_nodes",
               return_value=[{"description_category_id": 52620255, "type_id": 971167360,
                              "node_name": "Коагулометр портативный",
                              "full_path": "Медицина > Приборы > Коагулометр",
                              "similarity": 0.9}]):
        out = _run([_fan_item()])
    item = out.ozon_payload["items"][0]
    assert item["description_category_id"] == 17028743, "风扇类目不得被改写到凝血仪"
    assert item["type_id"] == 148495146
    assert any("标题与类目不一致" in e for e in out.validation_errors), out.validation_errors

def test_e2e_legit_rescue_still_rewrites(monkeypatch):
    """端到端正例：держатель+душа（2 非泛词）仍可救场改写（不回归 6022b0c9）。"""
    _DC, _TP = 17028001, 91001
    monkeypatch.setattr(ovn, "_fetch_ru_category_path",
                        lambda dc, tp: (
                            "Дом > Мебель > Полка" if (dc, tp) == (_DC, _TP)
                            else "Стройматериалы > Сантехника > Держатель для душа"),
                        raising=False)
    item = _fan_item(name="Держатель для душа угловой",
                     description_category_id=_DC, type_id=_TP)
    with patch("utils.ozon_category_query.OzonCategoryQuery.search_nodes",
               return_value=[{"description_category_id": 999, "type_id": 888,
                              "node_name": "Держатель для душа",
                              "full_path": "Стройматериалы > Сантехника > Держатель для душа",
                              "similarity": 0.567}]):
        out = _run([item])
    rewritten = out.ozon_payload["items"][0]
    assert rewritten["description_category_id"] == 999
    assert rewritten["type_id"] == 888
    assert not any("标题与类目不一致" in e for e in out.validation_errors)


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
