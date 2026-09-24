# -*- coding: utf-8 -*-
"""feat/competitor-fullattrs-v1 (A7) 回归：箱规渗透源头修 + 竞品特征进 A5 证据。

A7c：quantity 组 zh_exclude_keywords——1688「箱装数量/起批量」类批发键不再
    命中数量属性（gate 实证 8513/11650/23249=500/48 箱规渗透上卡）。
A7b：draft.ozon_attributes（竞品页特征表）进 A5 prompt 证据段——LLM 负责键名
    语义映射（EN/RU→schema 中文名），值仍过确定性字典验证。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.attr_fill_extras import build_llm_schema_prompt  # noqa: E402
from utils.attribute_utils import match_attr_name_synonym  # noqa: E402
from utils.attr_synonyms import load_attr_synonyms  # noqa: E402

_SCHEMA = [
    {"id": 4384, "name": "配套", "description": "部件", "type": "String",
     "dictionary_id": 0, "is_collection": False},
    {"id": 6829, "name": "炊具的特点", "description": "", "type": "String",
     "dictionary_id": 412, "is_collection": True},
]
_ITEMS = [{"attributes": [], "depth": 140, "width": 140, "height": 140}]


# ── A7c 箱规渗透源头修 ────────────────────────────────────────

def test_box_moq_keys_excluded_from_quantity_group():
    # 自隔离：同进程先跑的测试（如 v084 fixture）可能把 attr_synonyms 模块级
    # 缓存指向 mock 的 APP_WORKSPACE_PATH——重置后强制按当前 cwd 重读真配置。
    import utils.attr_synonyms as _asyn
    _asyn._CACHE, _asyn._CACHE_KEY = None, None
    syn = load_attr_synonyms()
    assert "箱装" in (syn["quantity"].get("zh_exclude_keywords") or []), "quantity 组缺排除键"
    assert match_attr_name_synonym("每包数量,pcs", ["箱装数量"], syn) is None
    assert match_attr_name_synonym("每包数量,pcs", ["起批量"], syn) is None
    assert match_attr_name_synonym("每包数量,pcs", ["装箱数量"], syn) is None
    assert match_attr_name_synonym("每包数量,pcs", ["件数"], syn) == "件数"
    # 排除键只作用于声明组：材质组不受影响
    assert match_attr_name_synonym("主要容器材料", ["材质"], syn) == "材质"


def test_fill_optional_skips_box_moq(monkeypatch):
    from graphs.nodes.prepare_ozon_upload_node import _fill_optional_dict_attrs
    import utils.ozon_dict_values as m
    monkeypatch.setattr(
        m, "search_dictionary_values",
        lambda *a, **k: [{"id": 9, "value": "500"}], raising=True)
    from types import SimpleNamespace
    st = SimpleNamespace(
        description_category_id="17027933", type_id="93712",
        ozon_client_id="1", ozon_api_key="k", user_id="t", token="",
        task_id="t7", dictionary_values={})
    schema = [{"id": 8513, "name": "每包数量,pcs", "dictionary_id": 123,
               "is_required": False, "is_collection": False, "max_value_count": 1}]
    draft = {"title": "保鲜盒", "attributes": {"箱装数量": "500"}}
    out = _fill_optional_dict_attrs([{"attributes": []}], schema, draft, st)
    assert not any(a["id"] == 8513 for a in out[0]["attributes"]), "箱装数量不应写每包数量"


# ── A7b 竞品特征进 A5 证据 ────────────────────────────────────

def test_prompt_includes_competitor_attributes():
    draft = {
        "title": "массажер для лица",
        "attributes": {"材质": "塑料"},
        "ozon_attributes": {"Type of massage": "Wellness", "Massage zones": "Face",
                            "Бренд": "SomeBrand"},
    }
    prompt, todo = build_llm_schema_prompt(_ITEMS, _SCHEMA, draft)
    assert prompt and todo
    assert "Type of massage=Wellness" in prompt
    assert "Massage zones=Face" in prompt
    assert "竞品特征" in prompt


def test_prompt_without_competitor_attributes_unaffected():
    prompt, todo = build_llm_schema_prompt(_ITEMS, _SCHEMA, {"title": "保鲜盒"})
    assert prompt and "竞品特征" in prompt
