"""batch_test URL 解析 + worker-url 优先级 + 凭证回退回归（v0.31.1 T4）。

修复点:
1. parse_urls_file 正则 offer/(\\d+) 不认 m 站 detail.m.1688.com/page/index.html?offerId=xxx
   → 兼容 offerId= query 参数
2. --worker-url 默认读 MXOU_API_BASE 改为 WORKER_URL 优先
3. 未传凭证（--client-id/--api-key 且 env 空）→ 复用 get_ozon_credentials(store_id)
"""
from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import batch_test


# ── 1. parse_urls_file: m 站 offerId 兼容 ──

def _write_urls(lines: list[str]) -> str:
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="urls_", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def test_parse_mobile_1688_offerId_query():
    """m 站 detail.m.1688.com/page/index.html?offerId=xxx → 提取 offerId。"""
    path = _write_urls(["https://detail.m.1688.com/page/index.html?offerId=1234567890"])
    try:
        items = batch_test.parse_urls_file(path)
    finally:
        os.unlink(path)
    assert len(items) == 1, f"应解析出 1 条，实际 {items}"
    assert items[0]["type"] == "1688"
    assert items[0]["id"] == "1234567890", items[0]


def test_parse_mobile_offerId_with_extra_params():
    """offerId 与其它 query 参数混排也能提取。"""
    path = _write_urls(
        ["https://detail.m.1688.com/page/index.html?from=detail&offerId=9988776655&scene=1"]
    )
    try:
        items = batch_test.parse_urls_file(path)
    finally:
        os.unlink(path)
    assert items and items[0]["id"] == "9988776655", items


def test_parse_desktop_offer_path_still_works():
    """PC 站 offer/(\\d+) 路径仍解析（回归）。"""
    path = _write_urls(["https://detail.1688.com/offer/980815374096.html"])
    try:
        items = batch_test.parse_urls_file(path)
    finally:
        os.unlink(path)
    assert items and items[0]["type"] == "1688"
    assert items[0]["id"] == "980815374096", items


def test_parse_mixed_desktop_mobile_dedup():
    """PC + m 站同一 offerId → 去重只留一条。"""
    path = _write_urls([
        "https://detail.1688.com/offer/5555555555.html",
        "https://detail.m.1688.com/page/index.html?offerId=5555555555",
        "https://www.ozon.ru/product/slug-123456789/",
    ])
    try:
        items = batch_test.parse_urls_file(path)
    finally:
        os.unlink(path)
    ids = {i["id"] for i in items}
    assert ids == {"5555555555", "123456789"}, ids
    assert sum(1 for i in items if i["type"] == "1688") == 1, "同 offerId 应去重"


# ── 1b. Ozon 纯数字 product_id 解析（v0.35.x）──

def test_parse_ozon_bare_numeric_product_id():
    """纯数字 Ozon 链接 /product/4767514314 → 解析出 4767514314。"""
    path = _write_urls(["https://www.ozon.ru/product/4767514314"])
    try:
        items = batch_test.parse_urls_file(path)
    finally:
        os.unlink(path)
    assert len(items) == 1, f"应解析出 1 条，实际 {items}"
    assert items[0]["type"] == "ozon"
    assert items[0]["id"] == "4767514314", items[0]


def test_parse_ozon_slug_form_still_works():
    """slug 形式 /product/my-product-4767514314 仍解析（回归）。"""
    path = _write_urls(["https://www.ozon.ru/product/my-product-4767514314"])
    try:
        items = batch_test.parse_urls_file(path)
    finally:
        os.unlink(path)
    assert len(items) == 1
    assert items[0]["type"] == "ozon"
    assert items[0]["id"] == "4767514314", items[0]


def test_parse_ozon_mixed_forms_both_parse_and_dedup():
    """纯数字 + slug 混合：两种形式都解析，同 product_id 去重只留一条。"""
    path = _write_urls([
        "https://www.ozon.ru/product/1234567890",          # 纯数字
        "https://www.ozon.ru/product/slug-1234567890",     # 同 ID slug 形式
        "https://www.ozon.ru/product/other-0987654321",    # 独立 slug 产品
    ])
    try:
        items = batch_test.parse_urls_file(path)
    finally:
        os.unlink(path)
    ids = {i["id"] for i in items}
    assert ids == {"1234567890", "0987654321"}, ids
    assert sum(1 for i in items if i["id"] == "1234567890") == 1, \
        "纯数字 + slug 同 ID 应去重"


# ── 1c. Ozon URL 边界形态（v0.35.x 回归）──

def test_parse_ozon_trailing_slash():
    """尾部斜杠 /product/4767514314/ → 解析出。"""
    path = _write_urls(["https://www.ozon.ru/product/4767514314/"])
    try:
        items = batch_test.parse_urls_file(path)
    finally:
        os.unlink(path)
    assert len(items) == 1
    assert items[0]["id"] == "4767514314", items


def test_parse_ozon_query_params():
    """查询参数 /product/4767514314?utm_source=x → 解析出。"""
    path = _write_urls(["https://www.ozon.ru/product/4767514314?utm_source=x&ref=y"])
    try:
        items = batch_test.parse_urls_file(path)
    finally:
        os.unlink(path)
    assert len(items) == 1
    assert items[0]["id"] == "4767514314", items


def test_parse_ozon_uppercase_domain():
    """大写域名 https://www.OZON.RU/product/4767514314 → 解析出（域名检查不区分大小写）。"""
    path = _write_urls(["https://www.OZON.RU/product/4767514314"])
    try:
        items = batch_test.parse_urls_file(path)
    finally:
        os.unlink(path)
    assert len(items) == 1, f"大写域名应解析，实际 {items}"
    assert items[0]["type"] == "ozon"
    assert items[0]["id"] == "4767514314", items


def test_parse_ozon_no_www():
    """无 www https://ozon.ru/product/4767514314 → 解析出。"""
    path = _write_urls(["https://ozon.ru/product/4767514314"])
    try:
        items = batch_test.parse_urls_file(path)
    finally:
        os.unlink(path)
    assert len(items) == 1
    assert items[0]["id"] == "4767514314", items


def test_parse_uppercase_1688_offer():
    """大写 1688 域名 detail.1688.COM → 解析出（域名检查不区分大小写）。"""
    path = _write_urls(["https://detail.1688.COM/offer/980815374096.html"])
    try:
        items = batch_test.parse_urls_file(path)
    finally:
        os.unlink(path)
    assert len(items) == 1, f"大写 1688 域名应解析，实际 {items}"
    assert items[0]["type"] == "1688"
    assert items[0]["id"] == "980815374096", items


# ── 2. worker-url 优先级: WORKER_URL > MXOU_API_BASE ──

def test_default_worker_url_prefers_worker_url():
    with mock.patch.dict(os.environ, {}, clear=True):
        assert batch_test._default_worker_url() == "https://worker.mxou.cn"
    with mock.patch.dict(os.environ, {"MXOU_API_BASE": "http://cloud.example"}, clear=True):
        assert batch_test._default_worker_url() == "http://cloud.example"
    with mock.patch.dict(os.environ, {"WORKER_URL": "http://localhost:8080"}, clear=True):
        assert batch_test._default_worker_url() == "http://localhost:8080"
    with mock.patch.dict(os.environ, {
        "WORKER_URL": "http://localhost:8080",
        "MXOU_API_BASE": "http://cloud.example",
    }, clear=True):
        assert batch_test._default_worker_url() == "http://localhost:8080", \
            "WORKER_URL 应优先于 MXOU_API_BASE"


# ── 3. 凭证回退: get_ozon_credentials(store_id) ──

def test_resolve_credentials_falls_back_to_store():
    """--client-id/--api-key 与 env 都空 → 复用 get_ozon_credentials(store_id)。"""
    with mock.patch("scripts.lib.config_store.get_ozon_credentials",
                    return_value={"client_id": "4718259", "api_key": "sk-abc"}) as goc:
        cid, akey = batch_test._resolve_credentials(
            client_id="", api_key="", store_id="main")
    goc.assert_called_once_with("main")
    assert cid == "4718259", cid
    assert akey == "sk-abc", akey


def test_resolve_credentials_explicit_wins():
    """显式 --client-id/--api-key 优先，不再查 store。"""
    with mock.patch("scripts.lib.config_store.get_ozon_credentials") as goc:
        cid, akey = batch_test._resolve_credentials(
            client_id="5371047", api_key="411afbd4-...", store_id="main")
    goc.assert_not_called()
    assert cid == "5371047"
    assert akey == "411afbd4-..."


def test_resolve_credentials_store_missing_returns_empty():
    """store 无凭证 → 返回空串（调用方据此报错）。"""
    with mock.patch("scripts.lib.config_store.get_ozon_credentials", return_value=None):
        cid, akey = batch_test._resolve_credentials(
            client_id="", api_key="", store_id="missing")
    assert cid == "" and akey == ""


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
