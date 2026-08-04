"""必填字典属性默认值解析单测（v0.24 F1b）— 字典值语义解析 + 尺码表真实映射。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["APP_WORKSPACE_PATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

from utils.attr_defaults import (
    find_dict_value_id, resolve_brand_default, resolve_gender_default,
    resolve_size_default, resolve_merge_card_default,
    resolve_missing_mandatory_dict_attr,
)


def test_find_dict_value_id_exact():
    vals = [{"id": 126745801, "value": "Нет бренда"}, {"id": 94435, "value": "Спортивная бутылка"}]
    assert find_dict_value_id(vals, "Нет бренда") == (126745801, "Нет бренда")
    assert find_dict_value_id(vals, "нет  бренда") == (126745801, "Нет бренда")
    assert find_dict_value_id(vals, "不存在") is None


def test_resolve_brand_default():
    vals = [{"id": 126745801, "value": "Нет бренда"}, {"id": 11, "value": "Samsung"}]
    assert resolve_brand_default(vals) == (126745801, "Нет бренда")


def test_resolve_gender_default():
    vals = [{"id": 1, "value": "Мужской"}, {"id": 2, "value": "Женский"}, {"id": 3, "value": "Унисекс"}]
    assert resolve_gender_default("女袜", vals) == (2, "Женский")
    assert resolve_gender_default("男女通用", vals) == (3, "Унисекс")
    assert resolve_gender_default("无性别词", vals) is None


def test_resolve_size_default_with_real_tables():
    # 男性表：48 = M；女性表：42 = S；鞋子：38(1688) → 37(RU)
    male_vals = [{"id": 10, "value": "48"}, {"id": 11, "value": "50"}]
    female_vals = [{"id": 20, "value": "42"}, {"id": 21, "value": "44"}]
    shoe_vals = [{"id": 30, "value": "37"}, {"id": 31, "value": "38"}]
    assert resolve_size_default("M", "Рубашка мужская", male_vals) == (10, "48")
    assert resolve_size_default("S", "Платье женское", female_vals) == (20, "42")
    assert resolve_size_default("38", "Кроссовки", shoe_vals) == (30, "37")


def test_resolve_merge_card_default():
    vals = [{"id": 501, "value": "Да"}, {"id": 502, "value": "Нет"}]
    assert resolve_merge_card_default(vals) == (502, "Нет")


def test_resolve_missing_mandatory_dict_attr_dispatch():
    brand_vals = [{"id": 126745801, "value": "Нет бренда"}]
    gender_vals = [{"id": 2, "value": "Женский"}]
    assert resolve_missing_mandatory_dict_attr(
        31, "Бренд одежды", title_cn="女袜", dict_vals=brand_vals
    ) == (126745801, "Нет бренда")
    assert resolve_missing_mandatory_dict_attr(
        9163, "Пол", title_cn="女袜", dict_vals=gender_vals
    ) == (2, "Женский")
    assert resolve_missing_mandatory_dict_attr(99999, "未知属性", dict_vals=[]) is None


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
