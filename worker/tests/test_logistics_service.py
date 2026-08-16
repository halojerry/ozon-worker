"""C2: 物流费率管理测试（logistics_rates 表 CRUD + CSV 导入/upsert）。

验收门：
1. list_rates 返回 total + items（分页 + 固定排序）
2. update_rate 改字段成功；id 不存在 → None
3. update_rate 校验：weight_min>weight_max / vol_weight_divisor<0 → ValueError
4. import_rates_csv 插入新行（imported 计数）
5. import_rates_csv 按 (scoring_group,service_level,tpl_provider,weight_min,weight_max) upsert —— 重复导入 → updated，无重复行
6. import_rates_csv 坏行跳过 + error 记录，其余行仍导入
7. import_rates_csv UTF-8 BOM 兼容
"""
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import logistics_service

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)

TPL = "test-tpl"  # 测试标记：清理 + 查询都只命中本测试数据


@pytest.fixture(scope="module")
def _pg():
    """PG 可用性守卫：不可达 → 整模块跳过。"""
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PG 不可用（{exc}），跳过物流费率测试")


@pytest.fixture(autouse=True)
def _env(_pg, monkeypatch):
    monkeypatch.setenv("PGDATABASE_URL", DB_URL)
    # 幂等建表（create_all 内部 IF NOT EXISTS）
    from storage.database.shared.model import Base
    Base.metadata.create_all(create_engine(DB_URL))
    yield
    _cleanup_rows()


def _cleanup_rows():
    """清理测试行（tpl_provider='test-tpl'）。"""
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM logistics_rates WHERE tpl_provider=:t"), {"t": TPL})
    eng.dispose()


def _seed_row(conn, scoring_group, service_level, weight_min, weight_max,
              base_cost=5.0, per_gram_rate=0.01, sum_limit_cm=100,
              longest_limit_cm=60, charge_type="实际重量"):
    conn.execute(text(
        "INSERT INTO logistics_rates (scoring_group, service_level, tpl_provider, delivery_method, "
        "base_cost, per_gram_rate, weight_min, weight_max, sum_limit_cm, longest_limit_cm, "
        "charge_type, vol_weight_divisor) "
        "VALUES (:sg, :sl, :tpl, NULL, :bc, :pgr, :wmin, :wmax, :sum, :long, :ct, 0)"
    ), {
        "sg": scoring_group, "sl": service_level, "tpl": TPL,
        "bc": base_cost, "pgr": per_gram_rate,
        "wmin": weight_min, "wmax": weight_max,
        "sum": sum_limit_cm, "long": longest_limit_cm, "ct": charge_type,
    })


def _seed(rows: list[tuple]) -> None:
    """批量种子：rows 为 (scoring_group, service_level, weight_min, weight_max) 元组。"""
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        for r in rows:
            _seed_row(conn, *r)
    eng.dispose()


def _count_rows() -> int:
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM logistics_rates WHERE tpl_provider=:t"), {"t": TPL}).scalar()
    eng.dispose()
    return int(n or 0)


# ============================================================
# 1. list_rates
# ============================================================

def test_list_rates_returns_total_and_items(_pg):
    """种子 2 行 → total + items 都返回，排序固定（scoring_group, service_level, weight_min）。"""
    _seed([
        ("Small", "Standard", 501, 2000),
        ("Extra Small", "Standard", 0, 500),
    ])
    result = logistics_service.list_rates(limit=500)
    assert result["total"] >= 2
    groups = [i["scoring_group"] for i in result["items"] if i["tpl_provider"] == TPL]
    assert groups == ["Extra Small", "Small"]  # 按 scoring_group 字典序
    assert all(i["per_gram_rate"] is not None for i in result["items"])


def test_list_rates_limit_offset(_pg):
    """limit=1 → 只返回 1 条；offset 生效。"""
    _seed([("Small", "Standard", 501, 2000), ("Extra Small", "Standard", 0, 500)])
    result = logistics_service.list_rates(limit=1, offset=0)
    assert len(result["items"]) == 1
    assert result["total"] >= 2


# ============================================================
# 2. update_rate
# ============================================================

def _pick_first_id() -> int:
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        rid = conn.execute(text(
            "SELECT id FROM logistics_rates WHERE tpl_provider=:t "
            "ORDER BY scoring_group LIMIT 1"), {"t": TPL}).scalar()
    eng.dispose()
    assert rid is not None
    return int(rid)


def test_update_rate_changes_fields(_pg):
    """更新 base_cost/per_gram_rate → 行内字段变化，返回更新后行。"""
    _seed([("Extra Small", "Standard", 0, 500)])
    rid = _pick_first_id()
    updated = logistics_service.update_rate(rid, {
        "scoring_group": "Extra Small",
        "service_level": "Standard",
        "tpl_provider": TPL,
        "base_cost": 9.9,
        "per_gram_rate": 0.02,
        "weight_min": 0,
        "weight_max": 500,
        "sum_limit_cm": 100,
        "longest_limit_cm": 60,
        "charge_type": "实际重量",
        "vol_weight_divisor": 0,
    })
    assert updated is not None
    assert updated["base_cost"] == 9.9
    assert updated["per_gram_rate"] == 0.02


def test_update_rate_missing_id_returns_none(_pg):
    """id 不存在 → None（404 语义）。"""
    assert logistics_service.update_rate(999999999, {
        "scoring_group": "Extra Small", "service_level": "Standard",
        "tpl_provider": TPL, "base_cost": 1.0, "per_gram_rate": 0.01,
        "weight_min": 0, "weight_max": 100,
        "sum_limit_cm": 100, "longest_limit_cm": 60, "charge_type": "x",
    }) is None


# ============================================================
# 3. update_rate 校验
# ============================================================

def test_update_rate_weight_range_invalid(_pg):
    """weight_min > weight_max → ValueError。"""
    _seed([("Extra Small", "Standard", 0, 500)])
    rid = _pick_first_id()
    with pytest.raises(ValueError):
        logistics_service.update_rate(rid, {
            "scoring_group": "Extra Small", "service_level": "Standard",
            "tpl_provider": TPL, "base_cost": 1.0, "per_gram_rate": 0.01,
            "weight_min": 600, "weight_max": 500,
            "sum_limit_cm": 100, "longest_limit_cm": 60, "charge_type": "x",
        })


def test_update_rate_vol_divisor_negative(_pg):
    """vol_weight_divisor < 0 → ValueError。"""
    _seed([("Extra Small", "Standard", 0, 500)])
    rid = _pick_first_id()
    with pytest.raises(ValueError):
        logistics_service.update_rate(rid, {
            "scoring_group": "Extra Small", "service_level": "Standard",
            "tpl_provider": TPL, "base_cost": 1.0, "per_gram_rate": 0.01,
            "weight_min": 0, "weight_max": 500,
            "sum_limit_cm": 100, "longest_limit_cm": 60, "charge_type": "x",
            "vol_weight_divisor": -1,
        })


def test_update_rate_required_empty(_pg):
    """必填字段空 → ValueError。"""
    _seed([("Extra Small", "Standard", 0, 500)])
    rid = _pick_first_id()
    with pytest.raises(ValueError):
        logistics_service.update_rate(rid, {
            "scoring_group": "", "service_level": "Standard",
            "tpl_provider": TPL, "base_cost": 1.0, "per_gram_rate": 0.01,
            "weight_min": 0, "weight_max": 500,
            "sum_limit_cm": 100, "longest_limit_cm": 60, "charge_type": "x",
        })


# ============================================================
# 4-7. import_rates_csv
# ============================================================

def _base_csv(extra_rows: str = "") -> str:
    return (
        "scoring_group,service_level,tpl_provider,weight_min,weight_max,base_cost,"
        "per_gram_rate,sum_limit_cm,longest_limit_cm,charge_type,vol_weight_divisor,delivery_method\n"
        "Extra Small,Standard,test-tpl,0,500,5.0,0.01,100,60,实际重量,0,\n"
        "Small,Standard,test-tpl,501,2000,8.0,0.008,150,80,实际重量,0,快递\n"
        + extra_rows
    )


def test_import_csv_inserts_new_rows(_pg):
    """无匹配键 → 全部 INSERT，imported=2。"""
    result = logistics_service.import_rates_csv(_base_csv())
    assert result["imported"] == 2
    assert result["updated"] == 0
    assert result["errors"] == []
    assert _count_rows() == 2
    assert logistics_service.list_rates()["total"] >= 2


def test_import_csv_upserts_same_key(_pg):
    """同一键（scoring_group,service_level,tpl_provider,weight_min,weight_max）重复导入 → updated，不产生重复行。"""
    result1 = logistics_service.import_rates_csv(_base_csv())
    assert result1["imported"] == 2
    # 第二次：base_cost 变化 → 同一键 UPDATE
    result2 = logistics_service.import_rates_csv(_base_csv())
    assert result2["imported"] == 0
    assert result2["updated"] == 2
    assert _count_rows() == 2  # 无重复行

    # 验证 upsert 确实改了值（base_cost 5.0 → 7.7）
    result3 = logistics_service.import_rates_csv(_base_csv().replace("5.0", "7.7"))
    assert result3["updated"] == 2
    eng = create_engine(DB_URL)
    with eng.connect() as conn:
        vals = conn.execute(text(
            "SELECT base_cost FROM logistics_rates WHERE tpl_provider=:t "
            "AND weight_min=0 ORDER BY base_cost"), {"t": TPL}).fetchall()
    eng.dispose()
    assert any(float(v[0]) == 7.7 for v in vals)


def test_import_csv_bad_row_skipped(_pg):
    """坏行（缺 weight_max）跳过 + error 记录，其余行仍导入。"""
    csv_text = (
        "scoring_group,service_level,tpl_provider,weight_min,weight_max,base_cost,"
        "per_gram_rate,sum_limit_cm,longest_limit_cm,charge_type\n"
        "Extra Small,Standard,test-tpl,0,500,5.0,0.01,100,60,实际重量\n"
        "Small,Standard,test-tpl,501,,8.0,0.008,150,80,实际重量\n"
        "Budget,Standard,test-tpl,2001,5000,12.0,0.006,200,100,实际重量\n"
    )
    result = logistics_service.import_rates_csv(csv_text)
    assert result["imported"] == 2
    assert len(result["errors"]) == 1
    assert result["errors"][0]["row"] == 3
    assert "weight_max" in result["errors"][0]["error"]
    # 坏行未落库：只有 2 条
    assert _count_rows() == 2


def test_import_csv_utf8_bom_handled(_pg):
    """UTF-8 BOM（\\ufeff）开头 → 正常解析导入。"""
    csv_text = "\ufeff" + _base_csv()
    result = logistics_service.import_rates_csv(csv_text)
    assert result["imported"] == 2
    assert result["errors"] == []


def test_import_csv_missing_required_column_skipped(_pg):
    """必填字段（tpl_provider）为空 → 跳过 + error。"""
    csv_text = (
        "scoring_group,service_level,tpl_provider,weight_min,weight_max,base_cost,"
        "per_gram_rate,sum_limit_cm,longest_limit_cm,charge_type\n"
        "Extra Small,Standard,test-tpl,0,500,5.0,0.01,100,60,实际重量\n"
        "Small,Standard,,501,2000,8.0,0.008,150,80,实际重量\n"
    )
    result = logistics_service.import_rates_csv(csv_text)
    assert result["imported"] == 1
    assert len(result["errors"]) == 1
    assert "tpl_provider" in result["errors"][0]["error"]
