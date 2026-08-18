"""P2b/T4b.1: 榜单浏览服务测试（ozon_bestsellers 读取 + 全局共享 + 筛选/排序）。

验收门：全局共享（A token 可见 B 采集的数据，含贡献者列）、类目筛选、
排序字段白名单、分页。
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services.analytics_service import list_bestsellers

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)
TOKEN_A = f"tok-a-{uuid.uuid4().hex[:8]}"
TOKEN_B = f"tok-b-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def _pg():
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过榜单测试")


@pytest.fixture(autouse=True)
def _seed(_pg):
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM ozon_bestsellers WHERE contributed_by_token_id IN (:a, :b)"
        ), {"a": TOKEN_A, "b": TOKEN_B})
        # A 的 3 条
        conn.execute(text(
            "INSERT INTO ozon_bestsellers (sku_or_id, brand, category_path, ordering_amount, ordering_count, avg_price_rub, contributed_by_token_id, source) VALUES "
            "(:s1, '品牌A', '宠物用品', 1000, 50, 200, :a, 'skill'), "
            "(:s2, '品牌B', '家居', 2000, 20, 500, :a, 'skill'), "
            "(:s3, '品牌C', '宠物用品', 500, 80, 100, :a, 'skill')"
        ), {"s1": "sku-1", "s2": "sku-2", "s3": "sku-3", "a": TOKEN_A})
        # B 的 1 条
        conn.execute(text(
            "INSERT INTO ozon_bestsellers (sku_or_id, brand, category_path, ordering_amount, ordering_count, avg_price_rub, contributed_by_token_id, source) VALUES "
            "(:s, '品牌X', '宠物用品', 9999, 99, 999, :b, 'skill')"
        ), {"s": "sku-b", "b": TOKEN_B})
    eng.dispose()
    yield


@pytest.fixture(autouse=True)
def _cleanup(_pg):
    yield
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM ozon_bestsellers WHERE contributed_by_token_id IN (:a, :b)"
        ), {"a": TOKEN_A, "b": TOKEN_B})
    eng.dispose()


def test_list_global_sharing():
    """T4b.1：A token 看到全部 4 条（含 B 的），每条带贡献者列。"""
    result = list_bestsellers(TOKEN_A)
    assert result["total"] == 4
    skus = {i["sku_or_id"] for i in result["items"]}
    assert skus == {"sku-1", "sku-2", "sku-3", "sku-b"}
    by_sku = {i["sku_or_id"]: i for i in result["items"]}
    assert by_sku["sku-b"]["contributed_by_token_id"] == TOKEN_B  # 贡献者标注保留
    assert by_sku["sku-1"]["contributed_by_token_id"] == TOKEN_A


def test_list_category_filter():
    result = list_bestsellers(TOKEN_A, category="宠物")
    assert result["total"] == 3  # A 2 条宠物 + B 1 条宠物（全局共享）
    assert all("宠物" in i["category_path"] for i in result["items"])


def test_list_order_by_amount_desc():
    result = list_bestsellers(TOKEN_A, order_by="ordering_amount")
    amounts = [i["ordering_amount"] for i in result["items"]]
    assert amounts == sorted(amounts, reverse=True)  # 9999, 2000, 1000, 500
    assert result["items"][0]["sku_or_id"] == "sku-b"  # B 的数据参与全局排序


def test_list_order_by_count():
    result = list_bestsellers(TOKEN_A, order_by="ordering_count")
    counts = [i["ordering_count"] for i in result["items"]]
    assert counts == sorted(counts, reverse=True)  # 99, 80, 50, 20
    assert result["items"][0]["sku_or_id"] == "sku-b"


def test_list_invalid_order_falls_back():
    result = list_bestsellers(TOKEN_A, order_by="hack")
    assert result["total"] == 4  # 白名单兜底，不报错


def test_list_pagination():
    result = list_bestsellers(TOKEN_A, limit=2, offset=0)
    assert len(result["items"]) == 2
    assert result["total"] == 4
    result2 = list_bestsellers(TOKEN_A, limit=2, offset=2)
    assert len(result2["items"]) == 2
