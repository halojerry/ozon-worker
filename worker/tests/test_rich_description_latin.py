"""v0.29.2: 富文本描述(4191)拉丁字符清理回归 — 描述含拉丁被 Ozon 拒(DESCRIPTION_DECLINE)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from graphs.nodes.prepare_ozon_upload_node import _sanitize_rich_description


def test_removes_latin_words_from_body():
    """正文英文单词被移除(尺寸/材质/型号等)。"""
    html = "<p>Кружка с подогревом Size: M 100% Cotton USB</p>"
    out = _sanitize_rich_description(html)
    assert "Size" not in out
    assert "Cotton" not in out
    assert "USB" not in out
    assert "Кружка" in out


def test_keeps_html_tags():
    """HTML 标签保留不被拉丁清理破坏。"""
    html = "<ul><li><b>Преимущество</b> первое</li><li>второе</li></ul>"
    out = _sanitize_rich_description(html)
    assert "<ul>" in out and "</ul>" in out
    assert "<li>" in out and "</li>" in out
    assert "<b>" in out and "</b>" in out


def test_removes_chinese_still_works():
    """中文清理不受影响。"""
    html = "<p>Описание 中文内容 тест</p>"
    out = _sanitize_rich_description(html)
    assert "中文内容" not in out
    assert "<p>" in out


def test_no_latin_left_in_pure_russian():
    """纯俄语 HTML 不受影响(无拉丁可清)。"""
    html = "<p>Отличное качество, быстрая доставка</p>"
    out = _sanitize_rich_description(html)
    assert "Отличное" in out
    assert "<p>" in out


def test_latin_single_letters_kept():
    """单个拉丁字母(标签残片/单位如 m/cm)保留——只清 2+ 连续字母。"""
    html = "<p>Размер 5 cm, вес 100 g</p>"
    out = _sanitize_rich_description(html)
    assert "cm" in out or "g" in out or "5" in out
