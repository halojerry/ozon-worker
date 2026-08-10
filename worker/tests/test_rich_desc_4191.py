# -*- coding: utf-8 -*-
"""
C8 富文本属性 4191 (Описание) 缺失修复回归测试 — 锁定真实根因

真实根因（调查结论）：
1. 每属性翻译分支（prepare_ozon_upload_node :2002 无西里尔 / :2018 含中文）会把 4191 的
   HTML 值（<p>/<b>/<ul>/<li>）当普通文本交给 LLM → 标签被翻译成词 → HTML 结构破坏
   （等价于 4191 无效，Ozon 拒）。批量翻译（:1913）与 assemble（:1808）已排除 4191，
   每属性翻译是漏网点。
2. rich_desc 兜底守卫 `if not rich_desc and title_ru:` 语义错误——兜底追加不应依赖
   title_ru 非空；title_ru 为空（LLM 空输出/无 token）时 4191 静默不追加。

覆盖：
(3) title_ru="" 时 4191 仍被追加最小 HTML（修复前: 守卫拦截 → 不追加 → FAIL）
(4) 4191 HTML 含中文 / 无西里尔 → 不走每属性 LLM 翻译，标签结构保持（修复前: 翻译破坏 → FAIL）
(5) _sanitize_rich_description 不破坏合法 HTML（guard 用例）
+ 非 HTML 的 4191 纯拉丁值仍走翻译（防修复过度，保持 v0.16 行为）

运行：cd worker && PYTHONPATH=src python3 -m pytest tests/test_rich_desc_4191.py -v
"""
import os
import sys
import types
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import PrepareOzonUploadInput
from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node


def _draft():
    return {
        "item_id": "test4191",
        "title": "Игрушка для кошек",  # 俄语标题，避免标题翻译分支（翻译/生图已 mock）
        "images": ["http://img.test/1.jpg"],
        "weight": 300,
        "dimensions": {"length": 100, "width": 100, "height": 50},
        "attributes": {"材质": "ABS"},
        "sku_id": "test4191",
        "price": "1290",
        "original_price": "1490",
    }


def _make_state(final_attributes, schema):
    return PrepareOzonUploadInput(
        draft=_draft(),
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10.5"},
        pricing_info={"final_price": "1290", "selling_price": "1290", "variant_prices": []},
        description_category_id="17028830",
        type_id="971206780",
        final_attributes=final_attributes,
        attributes_schema=schema,
        dictionary_values={},
        token="sk-test",
        original_images=_draft()["images"],
    )


def _payload_attr_map(output):
    """从 prepare 输出提取 payload 属性 {id → values[0].value}"""
    attrs = []
    for item in (output.ozon_payload or {}).get("items", []):
        attrs.extend(item.get("attributes", []))
    return {int(a["id"]): a for a in attrs if isinstance(a, dict) and a.get("id")}


def _base_patches():
    """统一 mock：LLM 富文本生成失败 + 标题兜底 + mxou chat（防网络）"""
    return [
        patch("graphs.nodes.prepare_ozon_upload_node._generate_rich_description", return_value=""),
        patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title", return_value="Товар для дома"),
        patch("utils.mxou_api.call_mxou_chat_api", return_value=""),
    ]


@contextmanager
def _patches(*extra):
    """进入 _base_patches + 额外 patch 的上下文"""
    with ExitStack() as st:
        for p in _base_patches() + list(extra):
            st.enter_context(p)
        yield


# ── (3) title_ru 空 → 4191 仍追加 ──
def test_rich_desc_4191_appended_when_title_ru_empty():
    """C8 根因2: title_ru 为空（LLM 空输出/无 token 的已知坑 + 标题兜底失败）→
    4191 仍追加最小 HTML。
    修复前: 守卫 `if not rich_desc and title_ru:` 在 title_ru 空时静默不触发兜底
    → 4191 缺失 → 本用例 FAIL（helper 不存在）。"""
    import graphs.nodes.prepare_ozon_upload_node as m

    ensure_fn = getattr(m, "_ensure_rich_description_attr", None)
    assert ensure_fn is not None, (
        "C8 未修复: 缺少 _ensure_rich_description_attr（title_ru 空时 4191 不追加）"
    )

    state = types.SimpleNamespace(description_category_id="17028830", type_id="971206780")
    with patch.object(m, "_get_category_fallback_title", return_value="Товар для дома"):
        final_attrs, rich_desc = ensure_fn([], "", "", {}, "", state)

    assert rich_desc, "title_ru 空时兜底富文本不应为空"
    ids = {int(fa.get("attribute_id", 0)) for fa in final_attrs if fa}
    assert 4191 in ids, "title_ru 空时 4191 仍应被追加"
    val = next(fa["value"] for fa in final_attrs if int(fa.get("attribute_id", 0)) == 4191)
    assert "<p>" in val, f"4191 追加值应为 HTML: {val[:60]}"
    assert len(val) > 50, f"4191 追加值应 ≥50 字符: {len(val)}"


# ── (4) 每属性翻译不破坏 4191 HTML ──
def test_rich_desc_4191_html_preserved_with_chinese():
    """C8 根因1a: 4191 HTML 值含中文 → 不走每属性翻译（:2018 has_chinese 分支），
    标签结构保持 + 中文残留仅剥离。
    修复前: 翻译分支把 <b>/<ul>/<li> 当文本翻译 → 结构破坏 → 本用例 FAIL。"""
    html_4191 = (
        "<p>Описание товара.</p><b>Характеристики:</b><ul>"
        "<li>Материал: ABS塑料</li><li>Вес: 300 г</li></ul>"
    )
    schema = [{"id": 4191, "name": "Описание", "dictionary_id": 0, "is_required": True},
              {"id": 85, "name": "Бренд", "dictionary_id": 11, "is_required": True}]
    final_attributes = [
        {"attribute_id": 4191, "value": html_4191, "dictionary_value_id": 0, "source": "llm"},
        {"attribute_id": 85, "value": "Нет бренда", "dictionary_value_id": 126745801, "source": "llm"},
    ]
    state = _make_state(final_attributes, schema)

    def fake_translate(text, token, source_lang="auto", text_type="description"):
        # 模拟真实 LLM 对 HTML 的行为：标签被翻译成词 → 结构破坏
        return "bold характеристики list материал ABS вес грамм"

    with _patches(patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
                        side_effect=fake_translate)):
        output = prepare_ozon_upload_node(state, None, None)

    attr_map = _payload_attr_map(output)
    assert 4191 in attr_map, "4191 不应被跳过"
    val = attr_map[4191]["values"][0]["value"]
    assert "<p>" in val and "<b>" in val and "<ul>" in val and "<li>" in val, (
        f"4191 HTML 标签结构被破坏: {val[:120]}"
    )
    assert "塑料" not in val, f"4191 中文残留应被剥离: {val[:120]}"


def test_rich_desc_4191_html_preserved_without_cyrillic():
    """C8 根因1b: 4191 HTML 值无西里尔（纯拉丁被净化剥成标签+数字）→
    不走每属性翻译（:2002 _russian_required_attrs 无西里尔分支），标签结构保持。
    修复前: 翻译破坏 → 本用例 FAIL。"""
    html_4191 = "<p>USB Fan 12000RPM</p><ul><li>Battery 5000mAh</li></ul>"
    schema = [{"id": 4191, "name": "Описание", "dictionary_id": 0, "is_required": True},
              {"id": 85, "name": "Бренд", "dictionary_id": 11, "is_required": True}]
    final_attributes = [
        {"attribute_id": 4191, "value": html_4191, "dictionary_value_id": 0, "source": "llm"},
        {"attribute_id": 85, "value": "Нет бренда", "dictionary_value_id": 126745801, "source": "llm"},
    ]
    state = _make_state(final_attributes, schema)

    def fake_translate(text, token, source_lang="auto", text_type="description"):
        return "жирный характеристики список батарея"

    with _patches(patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
                        side_effect=fake_translate)):
        output = prepare_ozon_upload_node(state, None, None)

    attr_map = _payload_attr_map(output)
    assert 4191 in attr_map, "4191 不应被跳过"
    val = attr_map[4191]["values"][0]["value"]
    assert "<p>" in val and "<ul>" in val and "<li>" in val, (
        f"4191 HTML 标签结构被破坏: {val[:120]}"
    )


# ── 防过度修复: 非 HTML 的 4191 纯拉丁值仍走翻译（v0.16 行为保持）──
def test_rich_desc_4191_plain_latin_still_translated():
    """非 HTML 的 4191 纯拉丁文本 → 仍走每属性翻译（标签识别不误伤普通文本）"""
    schema = [{"id": 4191, "name": "Описание", "dictionary_id": 0, "is_required": True},
              {"id": 85, "name": "Бренд", "dictionary_id": 11, "is_required": True}]
    final_attributes = [
        {"attribute_id": 4191, "value": "Pet toy for cat", "dictionary_value_id": 0, "source": "llm"},
        {"attribute_id": 85, "value": "Нет бренда", "dictionary_value_id": 126745801, "source": "llm"},
    ]
    state = _make_state(final_attributes, schema)

    with _patches(patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
                        return_value="Игрушка для кошек")):
        output = prepare_ozon_upload_node(state, None, None)

    attr_map = _payload_attr_map(output)
    assert 4191 in attr_map, "4191 翻译成功应保留"
    assert attr_map[4191]["values"][0]["value"] == "Игрушка для кошек"


# ── (5) _sanitize_rich_description guard ──
def test_sanitize_rich_description_preserves_html():
    """(5) guard: _sanitize_rich_description 清理中文/拉丁正文时保留合法 HTML 标签"""
    from graphs.nodes.prepare_ozon_upload_node import _sanitize_rich_description

    html = ("<p>Краткое описание товара.</p><b>Характеристики:</b>"
            "<ul><li>Материал: металл</li><li>Вес: 300 г</li></ul>")
    out = _sanitize_rich_description(html)
    assert "<p>" in out and "<b>" in out and "<ul>" in out and "<li>" in out
    assert "металл" in out

    mixed = "<p>材质: ABS塑料</p><ul><li>Бренд: Нет</li></ul>"
    out2 = _sanitize_rich_description(mixed)
    assert "<p>" in out2 and "<ul>" in out2 and "<li>" in out2
    assert "材质" not in out2 and "塑料" not in out2, "中文正文应被清理"
    assert "Бренд" in out2, "西里尔正文应保留"


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
