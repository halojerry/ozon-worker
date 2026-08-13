"""v0.40: Ozon 富内容（Rich Content 11254）JSON 构建单测。

锁定：
- JSON 结构 {"content":[{widgetName:raShowcase,type:chess,blocks}],"version":0.3}
- chess 最低 2 blocks（图片不足 2 张 → 返回空串）
- img/title/text 字段结构（src/srcMobile/width/height 等）
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from graphs.nodes.prepare_ozon_upload_node import _build_rich_content_json  # noqa: E402


def test_build_rich_content_basic():
    """2 图 + 标题 + 卖点 → 合法 chess JSON。"""
    out = _build_rich_content_json(
        ["https://img1.jpg", "https://img2.jpg"],
        "Умный датчик дыма",
        ["Обнаруживает дым за секунды", "Работает с Алисой"],
    )
    d = json.loads(out)
    assert d["version"] == 0.3
    content = d["content"]
    assert len(content) == 1
    widget = content[0]
    assert widget["widgetName"] == "raShowcase"
    assert widget["type"] == "chess"
    blocks = widget["blocks"]
    assert len(blocks) == 2
    b0 = blocks[0]
    assert b0["img"]["src"] == "https://img1.jpg"
    assert b0["img"]["width"] == 708
    assert b0["title"]["content"] == ["Умный датчик дыма"]
    assert b0["text"]["content"] == ["Обнаруживает дым за секунды"]


def test_build_rich_content_max_6_blocks():
    """最多 6 个 blocks（Ozon chess 上限）。"""
    out = _build_rich_content_json(
        [f"https://img{i}.jpg" for i in range(10)],
        "Товар",
        [f"Продающий текст {i}" for i in range(10)],
    )
    d = json.loads(out)
    assert len(d["content"][0]["blocks"]) == 6


def test_build_rich_content_reverse_alternates():
    """reverse 交错布局（偶数 0=False，奇数 1=True）。"""
    out = _build_rich_content_json(
        ["https://a.jpg", "https://b.jpg", "https://c.jpg", "https://d.jpg"],
        "Товар",
        ["t1", "t2", "t3", "t4"],
    )
    d = json.loads(out)
    blocks = d["content"][0]["blocks"]
    assert blocks[0]["reverse"] is False
    assert blocks[1]["reverse"] is True
    assert blocks[2]["reverse"] is False
    assert blocks[3]["reverse"] is True


def test_build_rich_content_insufficient_images():
    """图片不足 2 张 → 返回空串（chess 最低 2 blocks）。"""
    assert _build_rich_content_json([], "Товар", []) == ""
    assert _build_rich_content_json(["https://only1.jpg"], "Товар", []) == ""
    assert _build_rich_content_json([None, ""], "Товар", []) == ""


def test_build_rich_content_default_text():
    """卖点文字不足 → 用默认文案补齐（不空 block）。"""
    out = _build_rich_content_json(
        ["https://a.jpg", "https://b.jpg", "https://c.jpg"],
        "Товар",
        ["только один текст"],
    )
    d = json.loads(out)
    blocks = d["content"][0]["blocks"]
    assert len(blocks) == 3
    assert blocks[0]["text"]["content"] == ["только один текст"]
    assert blocks[1]["text"]["content"] and blocks[2]["text"]["content"]
