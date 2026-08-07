"""v0.29.2: ozon_validate_node 描述拉丁检测 — 规格表 HTML 标签不误报回归。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _check_description(description: str) -> list:
    """复用 validate 节点的检测逻辑: 剔 HTML 标签后查拉丁/中文。返回错误列表。"""
    import re
    errors = []
    _latin_re = re.compile(r'[a-zA-Z]')
    _chinese_re = re.compile(r'[\u4e00-\u9fff]')
    if description:
        _desc_text = re.sub(r'<[^>]+>', ' ', description)
        if _latin_re.search(_desc_text):
            _latin_fragments = re.findall(r'[a-zA-Z]{2,}', _desc_text)
            errors.append(f"含拉丁: {', '.join(_latin_fragments[:3])}")
        if _chinese_re.search(_desc_text):
            errors.append("含中文")
    return errors


def test_spec_table_html_not_flagged():
    """规格表 HTML 标签(table/ozon-spec/caption)不触发拉丁误报。"""
    desc = ('Вентилятор настольный. '
            '<table class="ozon-spec"><caption>Характеристики</caption>'
            '<tr><td>Цвет</td><td>черный</td></tr></table>')
    assert _check_description(desc) == []


def test_pure_russian_body_ok():
    """纯俄语正文 + 规格表 → 无错误。"""
    desc = '<p>Отличный вентилятор для дома</p><table class="ozon-spec"><tr><td>Вес</td><td>100 г</td></tr></table>'
    assert _check_description(desc) == []


def test_real_latin_in_body_flagged():
    """正文真含拉丁(USB/Size)→ 报错(HTML 标签仍不误报)。"""
    desc = 'Вентилятор USB <table class="ozon-spec"><tr><td>x</td><td>1</td></tr></table>'
    errors = _check_description(desc)
    assert len(errors) == 1 and "USB" in errors[0]


def test_chinese_in_body_flagged():
    """正文真含中文 → 报错。"""
    desc = 'Вентилятор 桌面风扇'
    errors = _check_description(desc)
    assert any("中文" in e for e in errors)


def test_sentry_issue_repro():
    """Sentry POUDING_OZON-60 原始消息复现: 规格表 + 标题拼接 → 不误报。"""
    desc = ('вентилятор ， ， ，, черный, серебристый '
            '<table class="ozon-spec"><caption>Характеристики</caption>'
            '<tr><td>Цвет</td><td>черный</td></tr></table>')
    assert _check_description(desc) == []
