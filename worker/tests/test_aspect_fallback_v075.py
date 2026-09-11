"""v0.75 收口批 · is_aspect 名称关键词兜底收窄（审计 A4 F-P1-2，TDD）。

收窄后分支序（utils/attribute_utils.is_aspect_attr）：
  ① ASPECT_ATTR_ID_OVERRIDES 命中 → True（优先于一切，含 schema 显式 False）；
  ② schema 行命中且显式含 is_aspect 键 → bool(值)——显式 False 绝对尊重，不落关键词兜底；
  ③ schema 行命中但缺 is_aspect 键 → 名称关键词兜底 且 dictionary_id>0——自由文本
    属性（如 «вид деятельности»，dictionary_id=0）不误伤跳过填充；
  ④ 无 schema 行 → 名称关键词兜底（revalidate 安全优先，现状保持）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_aspect_fallback_v075.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import utils.attribute_utils as attribute_utils  # noqa: E402
from utils.attribute_utils import ASPECT_ATTR_ID_OVERRIDES, is_aspect_attr  # noqa: E402


# ═══════════ ② schema 显式键 ═══════════

def test_explicit_false_wins():
    """schema 行显式 is_aspect=False + 名称含 «Цвет» → False（显式 false 尊重，不落关键词）。"""
    entries = [{"id": 1234, "is_aspect": False, "dictionary_id": 1}]
    assert is_aspect_attr(1234, "Цвет товара", entries) is False


def test_explicit_true():
    """schema 行显式 is_aspect=True → True（名称无关，无关键词也 True）。"""
    entries = [{"id": 1234, "is_aspect": True, "dictionary_id": 1960}]
    assert is_aspect_attr(1234, "Страна производства", entries) is True


# ═══════════ ③ schema 缺键：字典形态 + 关键词 ═══════════

def test_missing_key_requires_dictionary():
    """schema 行缺 is_aspect 键 + 名称含关键词：
    dictionary_id=0 → False（自由文本不误伤）；dictionary_id=1960 → True（字典形态）。"""
    free_text = [{"id": 1234, "dictionary_id": 0}]
    assert is_aspect_attr(1234, "Вид деятельности", free_text) is False

    dict_form = [{"id": 1234, "dictionary_id": 1960}]
    assert is_aspect_attr(1234, "Тип товара", dict_form) is True


def test_missing_key_no_keyword_name_stays_false():
    """schema 行缺键 + 字典形态 + 名称不含关键词 → False（关键词仍是必要条件）。"""
    entries = [{"id": 1234, "dictionary_id": 1960}]
    assert is_aspect_attr(1234, "Страна производства", entries) is False


# ═══════════ ④ 无 schema 行：关键词兜底（现状保持） ═══════════

def test_no_schema_falls_back():
    """schema_entries=None/空 + 名称含关键词 → True（revalidate 安全优先，现状保持）。"""
    assert is_aspect_attr(1234, "Цвет товара", None) is True
    assert is_aspect_attr(1234, "Цвет товара", []) is True
    assert is_aspect_attr(1234, "Материал", None) is False


def test_no_schema_id_only_attr_without_keyword():
    """无 schema 行 + 无名称（retry 调用方传空名）→ False（关键词兜底无输入不触发）。"""
    assert is_aspect_attr(1234, "", None) is False


# ═══════════ ① ID override 优先于一切 ═══════════

def test_id_override_tuple_is_placeholder():
    """ASPECT_ATTR_ID_OVERRIDES 当前为空元组（语义占位，实测确认后逐步补充）。"""
    assert isinstance(ASPECT_ATTR_ID_OVERRIDES, tuple)
    assert ASPECT_ATTR_ID_OVERRIDES == ()


def test_id_override_beats_everything(monkeypatch):
    """override 命中优先于一切：schema 显式 False / 非关键词名也判 True（临时 id 注入）。"""
    monkeypatch.setattr(attribute_utils, "ASPECT_ATTR_ID_OVERRIDES", (9999,))
    entries = [{"id": 9999, "is_aspect": False}]
    assert is_aspect_attr(9999, "Материал", entries) is True
    # override 只对命中 id 生效，其余属性照常走 ②③④
    assert is_aspect_attr(1234, "Материал", [{"id": 1234, "is_aspect": False}]) is False


def test_explicit_type_trumps_non_bool_value():
    """显式键存在时 bool() 归一（is_aspect=0 → False；"yes" → True）——不再看名称。"""
    assert is_aspect_attr(1, "Цвет", [{"id": 1, "is_aspect": 0}]) is False
    assert is_aspect_attr(2, "Материал", [{"id": 2, "is_aspect": 1}]) is True
