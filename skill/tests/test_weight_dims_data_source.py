#!/usr/bin/env python3
"""尺寸重量数据源回归锁定（任务 3.3）。

锁定事实：
- what_to_sell `data/v3` 响应不含 attributes 字段 → 重量(4497)/尺寸(9454/9455/9456)
  不能从 what_to_sell 拿
- 正确数据源：Ozon 商品页直采 `extract_weight_dims_from_attrs`（ISSUE-009 已修）
- 竞品（毛子/上品帮）做法一致：1688 只抓重量，尺寸用 Ozon 竞品兜底

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_weight_dims_data_source.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ozon_scraper import extract_weight_dims_from_attrs, parse_ru_dims


def test_extract_weight_dims_from_product_page():
    """Ozon 商品页 attributes 三键（Длина/Ширина/Высота, см）→ 尺寸 mm。"""
    attrs = {"Длина, см": "25", "Ширина, см": "7", "Высота, см": "10"}
    weight_g, dimensions = extract_weight_dims_from_attrs(attrs)
    assert weight_g is None
    assert dimensions == {"length": 250, "width": 70, "height": 100}


def test_extract_weight_dims_merged_keys():
    """合并键形态：重量（键名带单位 г）+ 尺寸（Размеры, мм 星号分隔）。"""
    attrs = {"Вес товара, г": "270", "Размеры, мм": "190*64*230"}
    weight_g, dimensions = extract_weight_dims_from_attrs(attrs)
    assert weight_g == 270
    assert dimensions == {"length": 190, "width": 64, "height": 230}


def test_no_fabrication_when_missing():
    """空 dict / 无尺寸重量字段 → (None, None)，不臆造。"""
    assert extract_weight_dims_from_attrs({}) == (None, None)
    assert extract_weight_dims_from_attrs({"Цвет": "белый"}) == (None, None)


def test_parse_ru_dims_star_separator():
    """`*` 分隔符（Ozon 'Размеры, мм' 形态）已支持。"""
    assert parse_ru_dims("190*64*230") == {"length": 190, "width": 64, "height": 230}


def test_parse_ru_dims_cm_multiplier():
    """cm 单位 ×10 转 mm；三边不齐全返回 None（不瞎估）。"""
    assert parse_ru_dims("20x15x5 см") == {"length": 200, "width": 150, "height": 50}
    assert parse_ru_dims("190*64") is None