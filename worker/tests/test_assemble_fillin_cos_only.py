# -*- coding: utf-8 -*-
"""assemble 无图补位只吃本方 COS 托管图（fix/image-ref-cos-whitelist-v1 批2，TDD）。

事故（2026-09-16 原图上卡）：`_validate_and_enrich_items` 在 item 无图时用**未过滤**
的 `draft.images` 补位——镜像未跑/失败时 draft.images 仍是 1688 裸 alicdn 原图，
外链原样进 payload（Ozon 抓不到外链），且与 prepare「不使用 alicdn 原图」纪律矛盾。
收口：补位子集只保留 `is_cos_url` 成立的本方 COS 托管图；全外链 → 诚实不补
（走既有 IMAGE_ERROR 语义）+ warning 带来源 key 前缀；primary_image 同口径。

运行:
    cd worker && PYTHONPATH=src /Volumes/os/dev/ozon-worker/skill/.venv314/bin/python -m pytest tests/test_assemble_fillin_cos_only.py -q
mock-only，无需 PG/GPU（最小 item 无属性，避开字典与 /values/search 网络路径）。
"""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault(
    "APP_WORKSPACE_PATH",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
)

from graphs.nodes.assemble_ozon_product_node import (
    _assemble_follow_sell,
    _build_items_deterministically,
    _validate_and_enrich_items,
)

_COS = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/draft-images/{}.jpg"
_ALICDN = "https://cbu01.alicdn.com/img/ibank/{}.jpg"


def _run(images, items=None):
    """直接调 _validate_and_enrich_items（最小参数：无属性 schema/无类目路径，
    属性校验/8229 填充/必填补全全部零触发，只观察图片补位行为）。"""
    if items is None:
        items = [{"offer_id": "off1", "name": "тестовый товар", "attributes": []}]
    return _validate_and_enrich_items(
        items=items,
        attr_list=[],
        dict_lookup={},
        images=images,
        ozon_client_id="test_client",
        ozon_api_key="test_key",
        description_category_id=17028747,
        type_id=99385,
        weight_grams=200,
        dimensions={"length": 100, "width": 100, "height": 50},
        ru_category_path="",  # 不触发 8229 /values/search 网络路径
    )[0]


# ------------------------------------------------------------
# 用例 9：item 无图 + draft.images 全 COS → 补位成功，primary=首张 COS
# ------------------------------------------------------------
def test_fillin_cos_images_backfilled(caplog):
    cos_imgs = [_COS.format(i) for i in range(5)]
    item = _run(cos_imgs)
    assert item["images"] == cos_imgs, f"应补位全部 5 张 COS 镜像，实际: {item['images']}"
    assert item["primary_image"] == cos_imgs[0], "primary_image 应取首张 COS"


# ------------------------------------------------------------
# 用例 10：item 无图 + draft.images 全裸 alicdn → 诚实不补（images 空）
# ------------------------------------------------------------
def test_fillin_bare_alicdn_not_backfilled(caplog):
    with caplog.at_level(logging.WARNING):
        item = _run([_ALICDN.format(i) for i in range(5)])
    assert not item.get("images"), f"裸外链不得补位，实际: {item.get('images')}"
    assert not item.get("primary_image"), "全外链时 primary_image 也不得补位"
    warn_texts = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("不补位" in t for t in warn_texts), f"应 log warning 带不补位原因，实际: {warn_texts}"
    assert any("来源 key 前缀" in t for t in warn_texts), "warning 应带来源 key 前缀便于取证"


# ------------------------------------------------------------
# 用例 11：混合（COS + 裸 alicdn）→ 只补 COS 子集，顺序保持
# ------------------------------------------------------------
def test_fillin_mixed_keeps_only_cos_in_order():
    cos1, cos2 = _COS.format("a"), _COS.format("b")
    item = _run([cos1, _ALICDN.format("x"), cos2, _ALICDN.format("y")])
    assert item["images"] == [cos1, cos2], f"只补两张 COS 且顺序保持，实际: {item['images']}"
    assert item["primary_image"] == cos1, "primary_image 应取 COS 子集首张"


# ------------------------------------------------------------
# 回归：item 已有图 → 补位逻辑完全不触发（现状锁定）
# ------------------------------------------------------------
def test_existing_images_untouched():
    own = [_COS.format("own")]
    item = {
        "offer_id": "off1",
        "name": "тестовый товар",
        "images": list(own),
        "primary_image": own[0],
        "attributes": [],
    }
    mixed = [_COS.format("draft"), _ALICDN.format("x")]
    result = _run(mixed, items=[item])
    assert result["images"] == own, "已有图不得被补位覆盖"
    assert result["primary_image"] == own[0], "已有 primary_image 不得被覆盖"


# ============================================================
# 批2补强（controller 扩围）：builder 路径同闸
# `_build_items_deterministically` 是 assemble 主流程 items 的唯一生产者，
# 直填 draft.images 会绕过补位闸直达 payload——同口径收口。
# ============================================================
def _run_builder(images):
    draft = {"item_id": "b001", "title": "тестовый товар", "attributes": {}}
    return _build_items_deterministically(
        draft=draft,
        description_category_id=17028747,
        type_id=99385,
        attr_list=[],
        dict_lookup={},
        images=images,
        ozon_client_id="test_client",
        ozon_api_key="test_key",
        weight_grams=200,
        dimensions={"length": 100, "width": 100, "height": 50},
        price_rub="1990",
        old_price_rub="2390",
        currency_code="RUB",
        token="sk-test",
    )[0]


def test_builder_bare_alicdn_images_empty_and_primary_blank(caplog):
    """builder + 全裸 alicdn → item.images 空 + primary 空串（不再进 payload）。"""
    with caplog.at_level(logging.WARNING):
        item = _run_builder([_ALICDN.format(i) for i in range(3)])
    assert item["images"] == [], f"builder 裸外链不得进 items，实际: {item['images']}"
    assert item["primary_image"] == "", "全外链时 primary_image 应为空串"
    warn_texts = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("不进 items" in t for t in warn_texts), f"应 warning 带剔除原因，实际: {warn_texts}"
    assert any("来源 key 前缀" in t for t in warn_texts), "warning 应带来源 key 前缀便于取证"


def test_builder_cos_mirrors_filled():
    """builder + COS 镜像 → items 全量补位，primary=首张。"""
    cos_imgs = [_COS.format(i) for i in range(3)]
    item = _run_builder(cos_imgs)
    assert item["images"] == cos_imgs, f"COS 镜像应全量进 items，实际: {item['images']}"
    assert item["primary_image"] == cos_imgs[0]


def test_builder_mixed_keeps_only_cos_in_order(caplog):
    """builder + 混合 → 只进 COS 子集（顺序保持），被剔除的外链有 warning。"""
    cos1, cos2 = _COS.format("a"), _COS.format("b")
    with caplog.at_level(logging.WARNING):
        item = _run_builder([cos1, _ALICDN.format("x"), cos2])
    assert item["images"] == [cos1, cos2], f"只进 COS 子集且顺序保持，实际: {item['images']}"
    assert item["primary_image"] == cos1
    warn_texts = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("不进 items" in t for t in warn_texts), "混合剔除时也应 warning（内容被丢弃）"


# ------------------------------------------------------------
# 回归：跟卖路径不受本闸影响（images=[] 是 v0.40.1 有意语义，禁触碰）
# ------------------------------------------------------------
class _FakeFollowState:
    def __init__(self):
        self.envelope = {"draft": {}, "extensions": {"follow_sell": True}}
        self.ozon_client_id = "123"
        self.ozon_api_key = "key"
        self.currency_code = "CNY"
        self.token = "sk-test"
        self.product_id = "999888777"   # UPDATE 模式
        self.description_category_id = ""
        self.type_id = ""               # type_id=0 → 跳过 schema 拉取（避免 DB/HTTP）
        self.final_attributes = []
        self.attributes_schema = []


class _FakeFollowProgress:
    def log_node_action(self, *a, **k):
        pass

    def log_node_start(self, *a, **k):
        pass


def test_follow_sell_path_images_still_deliberately_empty():
    """跟卖组装 images=[] 是有意语义（竞品参考图禁上卡），本闸不得改变它。"""
    out = _assemble_follow_sell(
        _FakeFollowState(),
        {
            "ozon_product_id": "3852000144",
            "item_id": "980815374096",
            "title": "тестовый товар",
            "weight": 500,
            "dimensions": {"length": 200, "width": 150, "height": 100},
        },
        "тестовый товар",
        [_ALICDN.format("follow")],
        {"price": "2500", "old_price": "3000"},
        _FakeFollowProgress(),
    )
    assert out["ozon_payload"]["items"][0]["images"] == [], "跟卖 images=[] 语义必须保持"
