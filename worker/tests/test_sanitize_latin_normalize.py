"""R5 (v0.62): 描述拉丁误报 — 尺寸乘号归一化 + 单字母拉丁清理。

覆盖：
- _sanitize_description：10x10x5 → 10×10×5（单拉丁 x 归一化，Ozon 不再拒）
- 30×40 см 保持（已有乘号不受影响）
- 残留单拉丁 x 清理（非尺寸上下文）
- XL/USB 等 2+ 连续拉丁移除（不回归）
- 西里尔词不误伤（保护词边界）
- 富文本 _sanitize_rich_description：HTML 结构保留 + 正文同规则
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from graphs.nodes.prepare_ozon_upload_node import (
    _sanitize_description,
    _sanitize_rich_description,
)


# ═══ 尺寸乘号归一化 ═══

def test_dimensions_x_normalized():
    """10x10x5 → 10×10×5：单拉丁 x 归一化为乘号，描述不再含拉丁。"""
    out = _sanitize_description("Размер 10x10x5 см")
    assert "10×10×5" in out
    assert "x" not in out.lower() or "×" in out


def test_dimensions_capital_x_normalized():
    out = _sanitize_description("Габариты 30X40X50 мм")
    assert "30×40×50" in out


def test_existing_multiply_sign_kept():
    out = _sanitize_description("30×40 см")
    assert "30×40 см" in out


def test_no_latin_letters_left_in_dimensions():
    out = _sanitize_description("10x10x5 см, вес 200г")
    import re
    assert not re.search(r"[a-zA-Z]", out), f"描述仍含拉丁: {out}"


# ═══ 残留单字母清理 ═══

def test_single_letter_x_cleaned():
    """非尺寸上下文的单拉丁 x（如混入文本的 'x'）被清理。"""
    out = _sanitize_description("цвет x белый")
    assert "x" not in out.lower()


def test_multi_letter_latin_removed_no_regression():
    """XL/USB 等 2+ 连续拉丁移除（v0.29.2 行为不回归）。"""
    out = _sanitize_description("Размер XL USB порт")
    assert "XL" not in out
    assert "USB" not in out


def test_cyrillic_words_not_damaged():
    """西里尔词不受单字母清理影响。"""
    out = _sanitize_description("Ручной опрыскиватель садовый")
    assert "Ручной" in out
    assert "опрыскиватель" in out
    assert "садовый" in out


# ═══ 富文本 ═══

def test_rich_description_html_structure_preserved():
    """富文本 HTML 结构保留 + 尺寸归一化 + 正文拉丁清理。"""
    html = "<ul><li>Размер 10x10x5 см</li><li>Материал ABS</li></ul>"
    out = _sanitize_rich_description(html)
    assert "<ul><li>" in out
    assert "</li></ul>" in out
    assert "10×10×5" in out
    assert "ABS" not in out
    assert "Материал" in out


def test_rich_description_single_latin_cleaned():
    out = _sanitize_rich_description("<p>цвет x белый</p>")
    assert "x" not in out.lower()
    assert "<p>" in out
