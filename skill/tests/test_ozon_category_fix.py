#!/usr/bin/env python3
"""v0.19.1 类目修复单测：面包屑必须挑「真类目」而不是品牌页。

背景：甩脂机信封曾把品牌 Luxhommè 当类目（dc=101029485 是品牌页），
棘轮扳手则因面包屑 widget 缺失完全没写 ozon_category。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib.ozon_scraper import (  # noqa: E402
    _category_path_from_crumbs,
    _pick_category_from_crumbs,
)


def _crumb(text: str, link: str, crumb_type: str = "CRUMB_TYPE_FULL_LINK") -> dict:
    import re
    m = re.search(r"-(\d+)/?$", link)
    return {"text": text, "link": link, "category_id": m.group(1) if m else "", "crumbType": crumb_type}


def test_vibro_brand_crumb_not_selected():
    """甩脂机案例：品牌 Luxhommè 在最后，必须选上一个 /category/ 的 Мини-тренажеры。"""
    crumbs = [
        _crumb("Спорт и отдых", "/category/sport-9700/"),
        _crumb("Тренажеры", "/category/trenazhery-101028000/"),
        _crumb("Мини-тренажеры", "/category/mini-trenazhery-101029485/"),
        _crumb("Luxhommè", "/brand/luxhomme-100081165/"),
    ]
    best = _pick_category_from_crumbs(crumbs)
    assert best is not None
    assert best["category_id"] == "101029485"  # Мини-тренажеры，而非品牌 100081165
    assert best["text"] == "Мини-тренажеры"


def test_wrench_real_category_selected():
    """棘轮扳手案例：真实类目 Гаечные ключи(9938) 被选中，品牌 Дело Техники 被排除。"""
    crumbs = [
        _crumb("Строительство и ремонт", "/category/stroitelstvo-9700/"),
        _crumb("Инструменты", "/category/instrumenty-9856/"),
        _crumb("Ключи и отвертки", "/category/klyuchi-31813/"),
        _crumb("Гаечные ключи", "/category/gaechnye-klyuchi-9938/"),
        _crumb("Дело Техники", "/brand/delo-tehniki-100081165/"),
    ]
    best = _pick_category_from_crumbs(crumbs)
    assert best is not None
    assert best["category_id"] == "9938"
    assert best["text"] == "Гаечные ключи"


def test_only_category_crumbs():
    crumbs = [
        _crumb("Аптека", "/category/apteka-6287/"),
        _crumb("Ортопедия", "/category/ortopediya-6287/"),
    ]
    best = _pick_category_from_crumbs(crumbs)
    assert best["category_id"] == "6287"


def test_no_category_link_falls_back_to_crumb_type():
    """无 /category/ 链接时兼容旧逻辑（crumbType=CRUMB_TYPE_FULL_*）。"""
    crumbs = [
        {"text": "А", "link": "", "category_id": "11", "crumbType": "CRUMB_TYPE_FULL_LINK"},
        {"text": "Б", "link": "", "category_id": "22", "crumbType": "CRUMB_TYPE_OTHER"},
    ]
    best = _pick_category_from_crumbs(crumbs)
    assert best["category_id"] == "11"  # 只认 CRUMB_TYPE_FULL，OTHER 被过滤


def test_empty_returns_none():
    assert _pick_category_from_crumbs([]) is None
    assert _pick_category_from_crumbs([
        {"text": "X", "link": "/brand/x-1/", "category_id": "1", "crumbType": "CRUMB_TYPE_FULL_LINK"},
    ]) is None  # 只有品牌页 → None（不会把品牌当类目）


def test_category_path_excludes_brand():
    """v0.20 A：category_path 只含类目 crumb，品牌段（Luxhommè）不得进入。"""
    crumbs = [
        _crumb("Спорт и отдых", "/category/sport-9700/"),
        _crumb("Тренажеры", "/category/trenazhery-101028000/"),
        _crumb("Мини-тренажеры", "/category/mini-trenazhery-101029485/"),
        _crumb("Luxhommè", "/brand/luxhomme-100081165/"),
    ]
    path = _category_path_from_crumbs(crumbs)
    assert "Luxhommè" not in path
    assert path.endswith("Мини-тренажеры")


def test_category_path_fallback_full_text():
    """无 /category/ 链接时退回全部文本（旧数据兼容）。"""
    crumbs = [
        {"text": "A", "link": "", "category_id": "", "crumbType": ""},
        {"text": "B", "link": "", "category_id": "", "crumbType": ""},
    ]
    assert _category_path_from_crumbs(crumbs) == "A > B"


def _main() -> int:
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"✅ {name}")
            except AssertionError as exc:
                failed += 1
                print(f"❌ {name}: {exc}")
            except Exception as exc:
                failed += 1
                print(f"❌ {name}: {type(exc).__name__}: {exc}")
    total = sum(1 for n in globals() if n.startswith("test_") and callable(globals()[n]))
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
