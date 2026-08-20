"""标题公式三处接线测试（TDD）— 三份拷贝统一到共享模块 + traffic_keywords 注入。

覆盖：
- prepare `_translate_to_russian_llm` 标题分支（zh/en）用共享公式 + 注入流量词
- prepare 兜底生成（L1745）用共享公式（中文公式）
- extensions.traffic_keywords 读取 + parse 过滤（`_extract_traffic_keywords`）
- 全节点接线：envelope extensions → 标题翻译调用收到过滤后的流量词
- ai_field_service `_build_prompt` 标题分支用共享公式
全部纯 mock，不连 PG、不发真实 LLM 请求。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("APP_WORKSPACE_PATH", os.path.join(os.path.dirname(__file__), ".."))


# ── prepare 主路径 `_translate_to_russian_llm` 标题分支 ──


def test_translate_title_zh_prompt_embeds_traffic_keywords():
    """zh 标题分支：traffic_keywords → system_prompt 含「流量词建议」行 + 具体西里尔词。"""
    from graphs.nodes.prepare_ozon_upload_node import _translate_to_russian_llm
    from unittest.mock import patch

    captured: dict = {}

    def fake_chat(**kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt", "")
        return "Подставка для растений, декоративная лягушка, для дома"

    with patch("graphs.nodes.prepare_ozon_upload_node.call_mxou_chat_api", side_effect=fake_chat):
        result = _translate_to_russian_llm(
            "青蛙植物架",
            "tok",
            source_lang="zh",
            text_type="title",
            traffic_keywords=["игрушка", "музыкальная"],
        )
    assert result  # 返回俄语标题
    assert "核心词" in captured["system_prompt"]  # 共享公式结构（核心词+属性+场景）
    assert "流量词建议" in captured["system_prompt"]  # 注入行
    assert "игрушка" in captured["system_prompt"]
    assert "музыкальная" in captured["system_prompt"]


def test_translate_title_zh_prompt_without_traffic_no_line():
    """traffic_keywords=None → prompt 无「流量词建议」行（向后兼容）。"""
    from graphs.nodes.prepare_ozon_upload_node import _translate_to_russian_llm
    from unittest.mock import patch

    captured: dict = {}

    def fake_chat(**kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt", "")
        return "Подставка для растений"

    with patch("graphs.nodes.prepare_ozon_upload_node.call_mxou_chat_api", side_effect=fake_chat):
        _translate_to_russian_llm("中文标题", "tok", source_lang="zh", text_type="title")

    assert "核心词" in captured["system_prompt"]
    assert "流量词建议" not in captured["system_prompt"]


def test_translate_title_en_prompt_uses_shared_formula():
    """en 标题分支：共享英文公式（Core keyword）+ en 流量词行。"""
    from graphs.nodes.prepare_ozon_upload_node import _translate_to_russian_llm
    from unittest.mock import patch

    captured: dict = {}

    def fake_chat(**kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt", "")
        return "Подставка для растений"

    with patch("graphs.nodes.prepare_ozon_upload_node.call_mxou_chat_api", side_effect=fake_chat):
        _translate_to_russian_llm(
            "Frog Plant Stand",
            "tok",
            source_lang="en",
            text_type="title",
            traffic_keywords=["игрушка"],
        )

    assert "Core keyword" in captured["system_prompt"]
    assert "Traffic keyword" in captured["system_prompt"]
    assert "игрушка" in captured["system_prompt"]


# ── extensions 读取 + parse 过滤（_extract_traffic_keywords）──


def test_extract_traffic_keywords_filters_mixed_and_caps_at_three():
    """extensions.traffic_keywords 混合中/拉丁/超长词 → 只留纯西里尔 ≤3 个。"""
    from graphs.nodes.prepare_ozon_upload_node import _extract_traffic_keywords

    result = _extract_traffic_keywords({
        "traffic_keywords": [
            "игрушка",
            "musical",                       # 拉丁 → 丢弃
            "玩具",                           # 中文 → 丢弃
            "оченьдлинноеключевоесловопревышающеелимит",  # 超长 → 丢弃
            "музыкальная",
            "подарок",                       # 第 3 个合法词
            "ещеодно",                       # 第 4 个 → 截断
        ],
    })
    assert result == ["игрушка", "музыкальная", "подарок"]
    assert len(result) <= 3


def test_extract_traffic_keywords_empty_or_missing():
    """无 traffic_keywords / extensions None / 空 dict → []（向后兼容）。"""
    from graphs.nodes.prepare_ozon_upload_node import _extract_traffic_keywords

    assert _extract_traffic_keywords(None) == []
    assert _extract_traffic_keywords({}) == []
    assert _extract_traffic_keywords({"traffic_keywords": []}) == []
    assert _extract_traffic_keywords("not-a-dict") == []


# ── 全节点接线：envelope extensions → 标题翻译调用 ──


def _make_state(extensions=None):
    from graphs.state import PrepareOzonUploadInput
    return PrepareOzonUploadInput(
        draft={
            "title": "青蛙植物架",
            "description": "青蛙造型装饰植物架",
            "attributes": {"材质": "塑料", "颜色": "绿色"},
            "images": ["https://img.test/1.jpg"],
            "weight": 300,
            "dimensions": {"length": 100, "width": 100, "height": 150},
            "purchase_cost": "10",
            "purchase_url": "http://1688.test/item",
            "sku_id": "sku_test",
        },
        source={"purchase_url": "http://1688.test/item", "purchase_cost": "10"},
        extensions=extensions,
        pricing_info={"final_price": "1290", "selling_price": "1290", "variant_prices": []},
        description_category_id="17028830",
        type_id="971206780",
        final_attributes=[
            {"attribute_id": 85, "value": "Нет бренда", "dictionary_value_id": 126745801, "source": "llm"},
        ],
        attributes_schema=[{"id": 85, "name": "Бренд", "dictionary_id": 11, "is_required": True}],
        dictionary_values={},
        token="sk-test",
        original_images=["https://img.test/1.jpg"],
    )


def test_prepare_node_passes_traffic_keywords_to_title_translation():
    """envelope extensions 带 traffic_keywords → zh 标题翻译收到过滤后的流量词。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    from unittest.mock import patch

    title_calls: list = []

    def fake_translate(text, token, source_lang="auto", text_type="description", **kwargs):
        if text_type == "title":
            title_calls.append({
                "source_lang": source_lang,
                "traffic_keywords": kwargs.get("traffic_keywords"),
            })
        return "Подставка для растений, декоративная лягушка, для дома"

    state = _make_state(extensions={"traffic_keywords": ["игрушка", "musical", "玩具", "музыкальная"]})
    with patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
               side_effect=fake_translate), \
         patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title",
               return_value="Товар для дома"), \
         patch("graphs.nodes.prepare_ozon_upload_node.sanitize_title",
               side_effect=lambda t, **kw: t), \
         patch("graphs.nodes.prepare_ozon_upload_node._generate_rich_description", return_value=""):
        prepare_ozon_upload_node(state, None, None)

    zh_call = next((c for c in title_calls if c["source_lang"] == "zh"), None)
    assert zh_call is not None, "zh 标题翻译应被调用"
    assert zh_call["traffic_keywords"] == ["игрушка", "музыкальная"], (
        f"流量词应过滤后传入: {zh_call['traffic_keywords']}"
    )


def test_prepare_node_no_traffic_keywords_backward_compat():
    """extensions 无 traffic_keywords → 标题翻译调用不带 traffic_keywords kwarg（兼容既有 mock）。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    from unittest.mock import patch

    title_calls: list = []

    def fake_translate(text, token, source_lang="auto", text_type="description"):
        # 故意不带 **kwargs：若节点无条件传 traffic_keywords kwarg 会 TypeError
        if text_type == "title":
            title_calls.append(source_lang)
        return "Подставка для растений, декоративная лягушка, для дома"

    state = _make_state(extensions=None)
    with patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
               side_effect=fake_translate), \
         patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title",
               return_value="Товар для дома"), \
         patch("graphs.nodes.prepare_ozon_upload_node.sanitize_title",
               side_effect=lambda t, **kw: t), \
         patch("graphs.nodes.prepare_ozon_upload_node._generate_rich_description", return_value=""):
        prepare_ozon_upload_node(state, None, None)

    assert "zh" in title_calls, "节点应正常工作且不因新 kwarg 破坏既有调用"


# ── 兜底生成（_attr_keywords_cn 路径）用共享公式 ──


def test_fallback_generation_uses_shared_zh_formula():
    """兜底生成 system_prompt → 共享中文公式（核心词+属性+场景），非内联旧文案。"""
    from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node
    from unittest.mock import patch

    captured: dict = {}

    def fake_translate(text, token, source_lang="auto", text_type="description"):
        # 返回纯拉丁 → 触发 sanitize_title 后不合格 → 走公式兜底生成
        return "LatinOnlyTitle"

    def fake_chat(**kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt", "")
        return "Садовые грабли, пластиковые, для уборки листвы"

    state = _make_state(extensions=None)
    with patch("graphs.nodes.prepare_ozon_upload_node._translate_to_russian_llm",
               side_effect=fake_translate), \
         patch("graphs.nodes.prepare_ozon_upload_node._get_category_fallback_title",
               return_value="Товар для дома"), \
         patch("graphs.nodes.prepare_ozon_upload_node.sanitize_title",
               side_effect=lambda t, **kw: t), \
         patch("graphs.nodes.prepare_ozon_upload_node._generate_rich_description", return_value=""):
        # 兜底内部是局部 `from utils.mxou_api import call_mxou_chat_api`，需 patch 源头
        with patch("utils.mxou_api.call_mxou_chat_api", side_effect=fake_chat):
            prepare_ozon_upload_node(state, None, None)

    # 共享模块 zh 公式的精确结构行（旧内联兜底文案是「用「核心词+属性+场景」公式生成」）
    assert "标题公式：[核心词], [属性], [场景]" in captured["system_prompt"], (
        "兜底生成必须用共享中文公式（含精确公式行）"
    )


def test_translate_title_internal_fallback_uses_shared_formula():
    """_translate_to_russian_llm 内部最终 fallback（简化重试失败后）→ 共享 zh 公式 + 流量词。"""
    from graphs.nodes.prepare_ozon_upload_node import _translate_to_russian_llm
    from unittest.mock import patch

    prompts: list = []

    def fake_chat(**kwargs):
        prompts.append(kwargs.get("system_prompt", ""))
        return ""  # 简化重试失败 → 走公式生成；公式生成也返回空 → 回退原文

    with patch("graphs.nodes.prepare_ozon_upload_node.call_mxou_chat_api", side_effect=fake_chat):
        result = _translate_to_russian_llm(
            "青蛙植物架", "tok", source_lang="zh", text_type="title",
            traffic_keywords=["игрушка"],
        )
    assert result == "青蛙植物架"  # 全部失败 → 回退原文
    assert len(prompts) >= 2  # 简化重试 + 公式生成
    gen_prompt = prompts[-1]
    assert "标题公式：[核心词], [属性], [场景]" in gen_prompt, "内部 fallback 必须用共享公式"
    assert "игрушка" in gen_prompt  # 流量词同样注入


# ── ai_field_service 标题分支 ──


def test_ai_field_service_title_prompt_uses_shared_formula():
    """_build_prompt('title') → system_prompt 含共享公式「核心词」结构；无流量词无注入行。"""
    from services.ai_field_service import _build_prompt

    sys_prompt, user_prompt = _build_prompt("title", "青蛙植物架")
    assert "核心词" in sys_prompt
    assert "流量词建议" not in sys_prompt


def test_ai_field_service_title_prompt_with_traffic_keywords():
    """_build_prompt('title', traffic_keywords=...) → 注入行只含 parse 过滤后的西里尔词。"""
    from services.ai_field_service import _build_prompt

    sys_prompt, _ = _build_prompt("title", "青蛙植物架", traffic_keywords=["игрушка", "musical", "玩具"])
    assert "核心词" in sys_prompt
    assert "流量词建议" in sys_prompt
    assert "игрушка" in sys_prompt
    assert "musical" not in sys_prompt  # 拉丁被 parse 过滤
    assert "玩具" not in sys_prompt  # 中文被 parse 过滤


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
