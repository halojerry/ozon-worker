"""v0.81.1 在线改图防洗卡（第四 import 出口收口）+ UPDATE 空数组/vat 洗卡修复。

背景（Fix 1/Fix 3，fix/import-exit-guards）：
- image_service.update_product_images 此前裸发 {product_id, offer_id, images}
  三键 import POST——/v3/product/import 是**全量替换语义**（MCP
  ProductAPI_ImportProductsV3：«При обновлении товара передайте в запросе
  всю информацию о нём»），在线改一次图会把整卡特征/价格/尺寸洗空（A6 历史
  事故的第四出口）。现改图前拉现卡全量状态（v4 特征表 + info/list vat/360 图
  + /v5 现价）→ build_image_update_body 全量回显；不可得 → fail-closed 拒绝。
- build_enrich_update_body 此前写死 complex_attributes/images360/pdf_list=[] +
  vat="0"——全量替换下洗掉现卡视频/PDF/360 图/税率。现口径「能回读带回真实值，
  回读不到省略键」。

MCP 契约留证：
- /v4/product/info/attributes（ProductAPI_GetProductAttributesV4）响应含
  complex_attributes/pdf_list/color_image，**不含 vat/images360**；
- /v3/product/info/list（ProductAPI_GetProductInfoList）响应含 vat/images360；
- import 媒体改动词：«Скопируйте данные полей images, images360, color_image»。

运行（纯 mock，无需 PG）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_image_update_echo_v081.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import image_service, product_index_service
from utils.content_enrich import build_enrich_update_body, build_image_update_body

TENANT = "tenant-a"
PRODUCT_ID = "1234567890"
TASK_ID = "task-0000-1111-2222"
CRED_ID = "cred-0000-1111"

# /v4/product/info/attributes 回显的单卡形状（MCP GetProductAttributesV4）
V4_CARD = {
    "id": 1234567890,
    "name": "Деревянные ложки",
    "offer_id": "sku-123",
    "description_category_id": 17027907,
    "type_id": 92359,
    "weight": 500, "weight_unit": "g",
    "depth": 200, "width": 150, "height": 100, "dimension_unit": "mm",
    "primary_image": "https://cos/old-main.jpg",
    "images": [
        {"file_name": "https://cos/old-2.jpg", "index": 1},
        {"file_name": "https://cos/old-main.jpg", "index": 0, "default": True},
    ],
    "attributes": [
        {"id": 85, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
        {"id": 4191, "values": [{"dictionary_value_id": 0, "value": "<p>Аннотация товара.</p>"}]},
        {"id": 11254, "values": [{"dictionary_value_id": 0, "value": '{"content": []}'}]},
        {"id": 23536, "values": [{"dictionary_value_id": 0, "value": "auto"}]},
        {"id": 22604, "values": [{"dictionary_value_id": 0, "value": "TMQ3001"}]},  # 海关码（CUSTOMS_ATTR_IDS）
    ],
    # v4 独有回显（v0.81.1 起原样带回，不再写死 []）
    "complex_attributes": [
        {"attributes": [
            {"id": 21841, "complex_id": 100001,
             "values": [{"dictionaryValueId": 0, "value": "https://video/1.mp4"}]},
        ]},
    ],
    "pdf_list": [{"file_name": "https://cos/manual.pdf", "name": "Инструкция"}],
    "color_image": "https://cos/color.jpg",
}
# /v3/product/info/list 回显（vat/images360 唯一读回通道）
INFO_ITEM = {"id": 1234567890, "vat": "0.1", "images360": ["https://360/a.jpg", "https://360/b.jpg"]}
# /v5/product/info/prices 权威价源
PRICE_ITEM = {"product_id": 1234567890,
              "price": {"price": "254", "old_price": "305", "currency_code": "CNY"}}

NEW_IMAGES = ["https://cos/new-a.jpg", "https://cos/old-main.jpg"]


# ══ build_image_update_body（唯一构造器，纯函数）══════════════


def test_image_body_full_echo_construction():
    """改图 body = 新图 + 现卡特征全量回显 + 扁平 dims + 真实 vat/360 图。"""
    body, reason = build_image_update_body(
        1234567890, V4_CARD, NEW_IMAGES,
        price=254, old_price=305, currency_code="CNY",
        vat="0.1", images360=["https://360/a.jpg"],
    )
    assert reason == ""
    item = body["items"][0]
    assert item["product_id"] == 1234567890 and item["offer_id"] == "sku-123"
    assert item["name"] == "Деревянные ложки"
    assert (item["description_category_id"], item["type_id"]) == (17027907, 92359)
    assert (item["depth"], item["width"], item["height"], item["weight"]) == (200, 150, 100, 500)
    assert item["images"] == NEW_IMAGES                       # 新图生效
    assert item["primary_image"] == "https://cos/old-main.jpg"  # 现卡主图仍在新图集 → 保持
    assert item["price"] == "254" and item["old_price"] == "305"  # 现价回显（字符串）
    assert item["currency_code"] == "CNY"
    assert item["vat"] == "0.1"                               # 真实税率回显（不写死 "0"）
    assert item["images360"] == ["https://360/a.jpg"]
    # 现卡特征全量回显（改图不动卡上内容）
    ids = [a["id"] for a in item["attributes"]]
    assert 85 in ids and 4191 in ids and 11254 in ids         # 特征 + 已有 4191/11254 保全
    assert 23536 not in ids and 22604 not in ids               # Ozon 自动设置/海关码不回发
    assert all(a["id"] for a in item["attributes"])
    # v4 独有回显键原样带回（不再写死 []）
    assert item["complex_attributes"] == V4_CARD["complex_attributes"]
    assert item["pdf_list"] == V4_CARD["pdf_list"]
    assert item["color_image"] == "https://cos/color.jpg"


def test_image_body_omits_unavailable_keys_never_empty_arrays():
    """回读不到的键**省略**——绝不出现 complex_attributes/images360/pdf_list=[] 或 vat 写死。"""
    bare = {k: v for k, v in V4_CARD.items()
            if k not in ("complex_attributes", "pdf_list", "color_image")}
    body, _ = build_image_update_body(
        1, bare, ["https://cos/x.jpg"],
        price=254, old_price=305,
        vat=None, images360=None,
    )
    item = body["items"][0]
    assert "vat" not in item
    assert "complex_attributes" not in item
    assert "images360" not in item
    assert "pdf_list" not in item
    assert "color_image" not in item


def test_image_body_primary_dropped_when_not_in_new_images():
    """现卡主图不在新图集 → 省略 primary_image（import 契约：缺省取 images[0]）。"""
    body, _ = build_image_update_body(
        1, V4_CARD, ["https://cos/new-a.jpg"],
        price=100, old_price=130,
    )
    assert "primary_image" not in body["items"][0]


def test_image_body_abort_paths_fail_closed():
    """保守放弃语义与 enrich 同源：no_card_echo / no_price / zero_dims / no_images。"""
    b, r = build_image_update_body(1, None, ["https://x/1.jpg"], price=10)
    assert b is None and r == "no_card_echo"
    b, r = build_image_update_body(1, V4_CARD, ["https://x/1.jpg"], price=0)
    assert b is None and r == "no_price"
    zero = dict(V4_CARD, height=0)
    b, r = build_image_update_body(1, zero, ["https://x/1.jpg"], price=10)
    assert b is None and r == "zero_dims"
    b, r = build_image_update_body(1, V4_CARD, [], price=10)
    assert b is None and r == "no_images"
    b, r = build_image_update_body("abc", V4_CARD, ["https://x/1.jpg"], price=10)
    assert b is None and r == "bad_product_id"


# ══ build_enrich_update_body：vat/images360 新参（Fix 3）═══════


_ENRICH_BASE = {k: v for k, v in V4_CARD.items()
                if k not in ("complex_attributes", "pdf_list", "color_image")}
_ENRICH_BASE["attributes"] = [
    {"id": 85, "values": [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]},
]
_IMPROVES = [{"id": 4191, "name": "Аннотация"}]


def test_enrich_body_vat_and_images360_echo_when_provided():
    body, _ = build_enrich_update_body(
        1, _ENRICH_BASE, _IMPROVES, {}, {},
        price=254, old_price=305, vat="0.1", images360=["https://360/a.jpg"],
    )
    item = body["items"][0]
    assert item["vat"] == "0.1"
    assert item["images360"] == ["https://360/a.jpg"]
    assert "complex_attributes" not in item
    assert "pdf_list" not in item


def test_enrich_body_vat_omitted_never_hardcoded_zero():
    """缺省（既有调用方零改动）→ vat/360 键省略；v0.81.1 前写死 vat="0" 是洗卡。"""
    body, _ = build_enrich_update_body(
        1, _ENRICH_BASE, _IMPROVES, {}, {},
        price=254, old_price=305,
    )
    item = body["items"][0]
    assert "vat" not in item
    assert "images360" not in item
    assert "complex_attributes" not in item and "pdf_list" not in item


def test_enrich_body_echoes_v4_media_keys_when_present():
    """评分增强/清扫路径：v4 回显里带 complex_attributes/pdf_list/color_image → 带回。"""
    body, _ = build_enrich_update_body(
        1, V4_CARD, _IMPROVES, {}, {},
        price=254, old_price=305,
    )
    item = body["items"][0]
    assert item["complex_attributes"] == V4_CARD["complex_attributes"]
    assert item["pdf_list"] == V4_CARD["pdf_list"]
    assert item["color_image"] == "https://cos/color.jpg"


def test_enrich_body_color_image_from_info_list_array_shape():
    """info/list 的 color_image 是数组形状 → 取首个字符串（import 契约单字符串）。"""
    stored = dict(_ENRICH_BASE, color_image=["https://cos/color.jpg"])
    body, _ = build_enrich_update_body(1, stored, [], {}, {}, price=254, old_price=305)
    assert body["items"][0]["color_image"] == "https://cos/color.jpg"


# ══ update_product_images 全链（mock ozon_post + engine）══════


class FakeRow:
    def __init__(self, row=None):
        self._row = row
        self.rowcount = 1

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self._engine.calls.append((str(stmt), params))
        if self._engine.pending_rows:
            return FakeRow(self._engine.pending_rows.pop(0))
        return FakeRow(None)


class FakeEngine:
    def __init__(self, pending_rows=None):
        self.pending_rows = list(pending_rows or [])
        self.calls = []

    def connect(self):
        return FakeConn(self)

    def begin(self):
        return FakeConn(self)


def _make_engine(monkeypatch, pending_rows=None):
    engine = FakeEngine(pending_rows)
    monkeypatch.setattr(image_service, "get_engine", lambda: engine)
    monkeypatch.setattr(product_index_service, "get_engine", lambda: engine)
    return engine


INDEX_ROW = (PRODUCT_ID, "sku-123", TASK_ID, CRED_ID, None)


def _make_ozon_post(captured, *, v4_card=None, info_item=None, price_items=None,
                    moderate="approved", fail_endpoints=()):
    def _post(client_id, api_key, endpoint, body, **kwargs):
        assert endpoint not in fail_endpoints, f"不应调用 {endpoint}"
        captured.append((endpoint, body))
        if endpoint in fail_endpoints:
            raise RuntimeError(f"ozon down: {endpoint}")
        if endpoint == "/v4/product/info/attributes":
            if v4_card is None:
                return {"result": []}
            return {"result": [dict(v4_card)]}
        if endpoint == "/v3/product/info/list":
            if "product_id" in body:  # 审核状态查询
                return {"result": {"items": [
                    {"id": int(body["product_id"][0]), "statuses": {"moderate_status": moderate}},
                ]}}
            return {"items": [dict(info_item or {})]}
        if endpoint == "/v5/product/info/prices":
            return {"items": [dict(x) for x in (price_items or [])]}
        if endpoint == "/v3/product/import":
            return {"result": {"task_id": 42}}
        raise AssertionError(f"unexpected endpoint: {endpoint}")
    return _post


def test_update_images_full_echo_import_body(monkeypatch):
    """改图全链：import body 是全量回显（含特征/dims/现价），绝非三键裸奔。"""
    _make_engine(monkeypatch, pending_rows=[INDEX_ROW, None])
    captured: list = []
    post = _make_ozon_post(captured, v4_card=V4_CARD, info_item=INFO_ITEM,
                           price_items=[PRICE_ITEM])
    with patch("services.image_service.credential_service.get_decrypted",
               return_value=("4718259", "api-key")), \
         patch("services.image_service.image_quality_evaluator.check_url_alive",
               return_value=True), \
         patch("services.image_service.ozon_post", side_effect=post):
        resp = image_service.update_product_images(TENANT, PRODUCT_ID, NEW_IMAGES)

    assert resp["status"] == "approved"
    endpoints = [ep for ep, _ in captured]
    # 回显三源先于 import（v4 特征表 / info-list vat+360 / v5 现价）
    assert endpoints.index("/v4/product/info/attributes") < endpoints.index("/v3/product/import")
    assert endpoints.index("/v5/product/info/prices") < endpoints.index("/v3/product/import")
    import_body = next(b for ep, b in captured if ep == "/v3/product/import")
    item = import_body["items"][0]
    assert set(item.keys()) >= {"product_id", "offer_id", "name", "attributes", "images",
                                "depth", "width", "height", "weight", "price", "old_price"}
    assert item["images"] == NEW_IMAGES
    assert item["vat"] == "0.1" and item["images360"] == ["https://360/a.jpg", "https://360/b.jpg"]
    ids = [a["id"] for a in item["attributes"]]
    assert 85 in ids and 4191 in ids  # 现卡特征保全（历史版本此场景被洗空）
    assert len(ids) >= 3


def test_update_images_v4_failure_fails_closed(monkeypatch):
    """v4 回显不可得 → 502 拒绝改图，import 绝不发出（fail-closed 红线）。"""
    _make_engine(monkeypatch, pending_rows=[INDEX_ROW, None])
    captured: list = []
    post = _make_ozon_post(captured, v4_card=None, info_item=INFO_ITEM,
                           price_items=[PRICE_ITEM])
    with patch("services.image_service.credential_service.get_decrypted",
               return_value=("4718259", "api-key")), \
         patch("services.image_service.image_quality_evaluator.check_url_alive",
               return_value=True), \
         patch("services.image_service.ozon_post", side_effect=post):
        with pytest.raises(HTTPException) as ei:
            image_service.update_product_images(TENANT, PRODUCT_ID, NEW_IMAGES)
    assert ei.value.status_code == 502
    assert "/v3/product/import" not in [ep for ep, _ in captured]


def test_update_images_v4_exception_fails_closed(monkeypatch):
    """v4 调用抛异常（网络/鉴权）→ 502，不裸发。"""
    _make_engine(monkeypatch, pending_rows=[INDEX_ROW, None])
    captured: list = []

    def _post(client_id, api_key, endpoint, body, **kwargs):
        captured.append(endpoint)
        if endpoint == "/v4/product/info/attributes":
            raise RuntimeError("ozon down")
        raise AssertionError(f"unexpected {endpoint}")

    with patch("services.image_service.credential_service.get_decrypted",
               return_value=("4718259", "api-key")), \
         patch("services.image_service.image_quality_evaluator.check_url_alive",
               return_value=True), \
         patch("services.image_service.ozon_post", side_effect=_post):
        with pytest.raises(HTTPException) as ei:
            image_service.update_product_images(TENANT, PRODUCT_ID, NEW_IMAGES)
    assert ei.value.status_code == 502
    assert captured == ["/v4/product/info/attributes"]


def test_update_images_no_price_fails_closed(monkeypatch):
    """v5 与 info/list 都拿不到价 → 502 拒绝（无价 item 必被 Ozon 拒且洗价格）。"""
    _make_engine(monkeypatch, pending_rows=[INDEX_ROW, None])
    captured: list = []
    post = _make_ozon_post(captured, v4_card=V4_CARD, info_item={},
                           price_items=[])
    with patch("services.image_service.credential_service.get_decrypted",
               return_value=("4718259", "api-key")), \
         patch("services.image_service.image_quality_evaluator.check_url_alive",
               return_value=True), \
         patch("services.image_service.ozon_post", side_effect=post):
        with pytest.raises(HTTPException) as ei:
            image_service.update_product_images(TENANT, PRODUCT_ID, NEW_IMAGES)
    assert ei.value.status_code == 502
    assert "/v3/product/import" not in [ep for ep, _ in captured]


def test_update_images_zero_dims_refused(monkeypatch):
    """现卡维度缺失 → 422 保守放弃（构造器拒绝语义透传），import 不发。"""
    _make_engine(monkeypatch, pending_rows=[INDEX_ROW, None])
    zero = dict(V4_CARD, weight=0)
    captured: list = []
    post = _make_ozon_post(captured, v4_card=zero, info_item=INFO_ITEM,
                           price_items=[PRICE_ITEM])
    with patch("services.image_service.credential_service.get_decrypted",
               return_value=("4718259", "api-key")), \
         patch("services.image_service.image_quality_evaluator.check_url_alive",
               return_value=True), \
         patch("services.image_service.ozon_post", side_effect=post):
        with pytest.raises(HTTPException) as ei:
            image_service.update_product_images(TENANT, PRODUCT_ID, NEW_IMAGES)
    assert ei.value.status_code == 422
    assert "zero_dims" in ei.value.detail
    assert "/v3/product/import" not in [ep for ep, _ in captured]


def test_update_images_price_fallback_from_info_list(monkeypatch):
    """v5 拿不到价但 info/list 带（swagger 有 price 字段）→ 兜底可用，不拒绝。"""
    _make_engine(monkeypatch, pending_rows=[INDEX_ROW, None])
    captured: list = []
    post = _make_ozon_post(captured, v4_card=V4_CARD,
                           info_item={"id": 1234567890, "vat": "0",
                                      "price": "254", "old_price": "305",
                                      "currency_code": "CNY"},
                           price_items=[])
    with patch("services.image_service.credential_service.get_decrypted",
               return_value=("4718259", "api-key")), \
         patch("services.image_service.image_quality_evaluator.check_url_alive",
               return_value=True), \
         patch("services.image_service.ozon_post", side_effect=post):
        resp = image_service.update_product_images(TENANT, PRODUCT_ID, NEW_IMAGES)
    assert resp["status"] == "approved"
    item = next(b for ep, b in captured if ep == "/v3/product/import")["items"][0]
    assert item["price"] == "254" and item["old_price"] == "305"


def test_update_images_info_list_failure_omits_keys(monkeypatch):
    """info/list 挂（vat/360 取不到）→ 键省略但不阻断改图（可选回显语义）。"""
    _make_engine(monkeypatch, pending_rows=[INDEX_ROW, None])
    captured: list = []

    def _post(client_id, api_key, endpoint, body, **kwargs):
        captured.append((endpoint, body))
        if endpoint == "/v4/product/info/attributes":
            return {"result": [dict(V4_CARD)]}
        if endpoint == "/v3/product/info/list" and "offer_id" in body:
            raise RuntimeError("info/list down")  # vat/360 源挂
        if endpoint == "/v5/product/info/prices":
            return {"items": [dict(PRICE_ITEM)]}
        if endpoint == "/v3/product/info/list":
            return {"result": {"items": [
                {"id": 1234567890, "statuses": {"moderate_status": "approved"}}]}}
        if endpoint == "/v3/product/import":
            return {"result": {"task_id": 42}}
        raise AssertionError(f"unexpected {endpoint}")

    with patch("services.image_service.credential_service.get_decrypted",
               return_value=("4718259", "api-key")), \
         patch("services.image_service.image_quality_evaluator.check_url_alive",
               return_value=True), \
         patch("services.image_service.ozon_post", side_effect=_post):
        resp = image_service.update_product_images(TENANT, PRODUCT_ID, NEW_IMAGES)
    assert resp["status"] == "approved"
    item = next(b for ep, b in captured if ep == "/v3/product/import")["items"][0]
    assert "vat" not in item and "images360" not in item
    assert item["attributes"]  # 特征回显不受 info/list 故障影响
