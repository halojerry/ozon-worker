"""hashtag 23171 生成单测 — traffic_keywords 流量关键词接入（TDD）。

覆盖：流量词优先 / 无流量词回退字典 / 无西里尔 #товар 兜底 / 中文+拉丁流量词过滤 /
品牌词过滤。纯函数测试（`_generate_hashtags`），不连 PG、不发 LLM。

背景：hashtag 是 Ozon 搜索流量载体。原先 `_generate_hashtags` 只吃 `item.name`
（原始中文标题），中文标题时西里尔提取必然为空 → 只产出 `#товар`，tag 质量差。
本任务接入 `extensions.traffic_keywords`（俄语流量词，what-to-sell all-queries 来源）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.assemble_ozon_product_node import _generate_hashtags


def test_traffic_keywords_priority_non_garden():
    """非园艺产品 + 俄语流量词 → hashtag 用流量词，不出现 #товаr 兜底。"""
    tags = _generate_hashtags("儿童音乐玩具", traffic_keywords=["игрушка", "музыкальная"])
    assert "#игрушка" in tags
    assert "#музыкальная" in tags
    assert "#товар" not in tags


def test_no_traffic_keywords_fallback_dict():
    """无 traffic_keywords → 原兜底：园艺词字典命中（секатор → 字典词）。"""
    tags = _generate_hashtags("Секатор садовый")
    assert "#секатор" in tags
    assert "#товар" not in tags


def test_no_traffic_keywords_fallback_tovar():
    """无 traffic_keywords 且标题无西里尔词 → #товар 兜底。"""
    tags = _generate_hashtags("儿童音乐玩具")
    assert tags == "#товар"


def test_chinese_latin_traffic_keywords_filtered():
    """traffic_keywords 含中文/拉丁 → 被过滤，仅保留纯西里尔词。"""
    tags = _generate_hashtags("玩具", traffic_keywords=["игрушка", "玩具", "musical"])
    assert "#игрушка" in tags
    assert "#玩具" not in tags
    assert "#musical" not in tags


def test_brand_word_filtered():
    """品牌词（amazon）从流量词中被剔除。"""
    tags = _generate_hashtags("товар", traffic_keywords=["amazon", "игрушка"])
    assert "#amazon" not in tags
    assert "#игрушка" in tags


def test_traffic_keywords_up_to_five():
    """流量词超长/超量时截断到 5 个内，且均为 # 前缀。"""
    tags = _generate_hashtags(
        "товар",
        traffic_keywords=["один", "два", "три", "четыре", "пять", "шесть"],
    )
    n_tags = len(tags.split())
    assert 0 < n_tags <= 5
    assert all(t.startswith("#") for t in tags.split())
