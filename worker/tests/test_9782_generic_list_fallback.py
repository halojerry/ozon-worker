"""v0.32 T3 补漏回归: 必填字典属性通用 list 兜底。

根因: _fill_missing_required_dict_attrs 的 list_dictionary_values 全量拉取
只嵌在「竞品 ozon_attributes 分支」(if _ozon_val:) 内, 9782(危险等级)/
4295(尺码) 等无竞品值、无缓存、search 词为空的必填字典属性永远无法填充
→ Ozon 报 error_attribute_values_empty(attr=9782)。

修复: ③ search 循环之后增加通用 list 兜底, 对任何 unresolved 必填字典
属性执行 list_dictionary_values → resolve_missing_mandatory_dict_attr。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class _FakeState:
    """最小 GlobalState 替身（prepare 兜底只读 ozon_client_id/api_key/dc/tp）。"""

    def __init__(self, ozon_client_id="cid", ozon_api_key="akey",
                 description_category_id="17028747", type_id="99385"):
        self.ozon_client_id = ozon_client_id
        self.ozon_api_key = ozon_api_key
        self.description_category_id = description_category_id
        self.type_id = type_id
        self.dictionary_values = {}  # follow 链路为空
        self.token = "test-token"


SAFE_DICT_VALUES = [
    {"id": 970593901, "value": "Класс 1. Взрывчатые материалы"},
    {"id": 970593902, "value": "Класс 2. Газы"},
    {"id": 970661099, "value": "Не опасен"},
]

SCHEMA = [
    {
        "id": 9782, "name": "Класс опасности товара",
        "is_required": True, "dictionary_id": 26026952,
    },
    {
        "id": 8229, "name": "Тип", "is_required": True, "dictionary_id": 1960,
    },
]


@pytest.fixture(autouse=True)
def _mock_ozon_dict(monkeypatch):
    """mock list_dictionary_values 返回 9782 安全值; search 返回空(模拟 follow 无 search 词)。"""
    import utils.ozon_dict_values as odv

    def _fake_list(client_id, api_key, attribute_id, dc, tp, language="RU", limit=200):
        if int(attribute_id) == 9782:
            return list(SAFE_DICT_VALUES)
        return []

    def _fake_search(client_id, api_key, attribute_id, dc, tp, term, language="RU", limit=20):
        return []

    monkeypatch.setattr(odv, "list_dictionary_values", _fake_list)
    monkeypatch.setattr(odv, "search_dictionary_values", _fake_search)


def _run_fill(items, schema=None):
    """调用 _fill_missing_required_dict_attrs。"""
    from graphs.nodes.prepare_ozon_upload_node import _fill_missing_required_dict_attrs

    draft = {"title": "Палочки от комаров", "attributes": {}}
    return _fill_missing_required_dict_attrs(items, schema or SCHEMA, draft, _FakeState())


def test_9782_filled_via_generic_list_fallback():
    """9782 无竞品值/无缓存/search 词空 → 通用 list 兜底填 Не опасен(970661099)。"""
    items = [{"id": "offer-1", "attributes": [
        {"id": 85, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
    ]}]
    out = _run_fill(items)
    attrs = {int(a["id"]): a for a in out[0]["attributes"]}
    assert 9782 in attrs, "9782 必须被补齐"
    v = attrs[9782]["values"][0]
    assert v["dictionary_value_id"] == 970661099, f"应为 Не опасен 970661099, got {v}"
    assert v["value"] == "Не опасен"


def test_9782_preserved_when_already_present():
    """9782 已存在(含首填值) → 不被覆盖/清除。"""
    items = [{"id": "offer-1", "attributes": [
        {"id": 9782, "values": [{"dictionary_value_id": 970661099, "value": "Не опасен"}]},
    ]}]
    out = _run_fill(items)
    attrs = {int(a["id"]): a for a in out[0]["attributes"]}
    v = attrs[9782]["values"][0]
    assert v["dictionary_value_id"] == 970661099


def test_8229_untouched_when_no_list_values():
    """8229 list 为空 → 不盲填(保持缺失, 不注入错误值)。"""
    items = [{"id": "offer-1", "attributes": []}]
    out = _run_fill(items)
    attrs = {int(a["id"]): a for a in out[0]["attributes"]}
    assert 8229 not in attrs, "8229 无字典值时应保持缺失(不盲补)"


def test_hazard_never_first_value():
    """9782 兜底只允许安全默认, 绝不取列表首值(爆炸物)。"""
    from utils.attribute_utils import get_safe_hazard_default

    r = get_safe_hazard_default(SAFE_DICT_VALUES)
    assert r == (970661099, "Не опасен")
    assert r[0] != SAFE_DICT_VALUES[0]["id"], "绝不盲取首值"
