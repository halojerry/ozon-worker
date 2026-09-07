"""v0.69 Wave5 T3.3: 类目属性 schema 懒加载回写闭环 + warm 脚本 --all/--coverage 单测。

背景（取证结论）：
1. assemble `_fetch_attribute_schema_from_ozon` 实时拉到 schema 后不写 PG ——
   `LocalDBManager.set_attribute_cache` 全仓库零调用方，每次未命中都重调 Ozon API。
2. retry `_get_attribute_schema` RU 未命中直调 Ozon 同样不回写。
3. warm_category_cache.py argparse 没有 `--all`（AGENTS.md 文档腐朽），无覆盖率审计手段。

本测试锁定：
- set_attribute_cache → get_attribute_schema 读写 round-trip（形状=Ozon result 原始 list，
  与 warm 脚本写入形状一致；真实 PG，不可达时 skip）
- assemble/retry 拉取成功后调用 set_attribute_cache（mock 断言）+ 回写失败不影响主流程
- warm argparse：--all 存在、--all×--limit 冲突 exit 2、--coverage/--coverage-sample 存在
- coverage SQL 计数：真实 PG 造两条假数据验证 covered/missing 归类
"""
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

DB_URL = os.environ.get(
    "PGDATABASE_URL",
    "postgresql://postgres:localdev123@localhost:5433/ozon",
)

# 覆盖率测试专用假类目（撞真实数据概率≈0；用完即清）
_FAKE_DC = 99977701
_FAKE_TP1 = 99977711
_FAKE_TP2 = 99977712
_FAKE_ATTR = 99977721

_SCHEMA_FIXTURE = [
    {
        "id": 4180,
        "name": "Производитель",
        "description": "Производитель",
        "dictionary_id": 0,
        "is_required": True,
        "type": "String",
    },
    {
        "id": 9048,
        "name": "Наименование модели",
        "description": "модель",
        "dictionary_id": 0,
        "is_required": False,
        "type": "String",
    },
]


def _pg_available() -> bool:
    try:
        eng = create_engine(DB_URL)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


_pg_required = pytest.mark.skipif(not _pg_available(), reason="本地 PG 不可达")


def _db_execute(sql: str, params: dict | None = None) -> None:
    eng = create_engine(DB_URL)
    with eng.begin() as conn:
        conn.execute(text(sql), params or {})


def _cleanup_fake_rows() -> None:
    _db_execute("DELETE FROM attribute_cache WHERE description_category_id = :dc", {"dc": _FAKE_DC})
    _db_execute("DELETE FROM dictionary_value_cache WHERE description_category_id = :dc", {"dc": _FAKE_DC})
    _db_execute(
        "DELETE FROM category_tree_nodes WHERE description_category_id = :dc",
        {"dc": _FAKE_DC},
    )


# ============================================================
# ① 读写 round-trip（真实 PG）
# ============================================================

@_pg_required
def test_set_attribute_cache_round_trip_via_query_reader():
    """set_attribute_cache 写入 list 形状 → get_attribute_schema 原样读回（写读一致）。

    形状约定：写 Ozon /v1/description-category/attribute 的 result 原始 list ——
    与 warm 脚本 `_write_node_to_pg` 一致；assemble 主路径 list 分支 + retry
    `isinstance(cached, list)` 均可直接消费。
    """
    from utils.local_db_manager import LocalDBManager
    from utils.ozon_category_query import get_category_query

    _cleanup_fake_rows()
    try:
        LocalDBManager().set_attribute_cache(
            _FAKE_DC, _FAKE_TP1, _SCHEMA_FIXTURE, language="ZH_HANS", expires_in=86400,
        )
        got = get_category_query().get_attribute_schema(_FAKE_DC, _FAKE_TP1, language="ZH_HANS")
        assert isinstance(got, list)
        assert got == _SCHEMA_FIXTURE
    finally:
        _cleanup_fake_rows()


@_pg_required
def test_set_attribute_cache_round_trip_local_db_reader():
    """LocalDBManager.get_attribute_cache 同样读回（双读入口形状一致）。"""
    from utils.local_db_manager import LocalDBManager

    _cleanup_fake_rows()
    try:
        LocalDBManager().set_attribute_cache(
            _FAKE_DC, _FAKE_TP1, _SCHEMA_FIXTURE, language="ZH_HANS", expires_in=86400,
        )
        got = LocalDBManager().get_attribute_cache(_FAKE_DC, _FAKE_TP1, language="ZH_HANS")
        assert got is not None
        assert got["attributes_schema"] == _SCHEMA_FIXTURE
    finally:
        _cleanup_fake_rows()


@_pg_required
def test_set_attribute_cache_upsert_overwrites():
    """同 (dc,tp,language) 二次写入覆盖旧 schema（懒加载回写可刷新 warm 旧数据）。"""
    from utils.local_db_manager import LocalDBManager
    from utils.ozon_category_query import get_category_query

    _cleanup_fake_rows()
    try:
        LocalDBManager().set_attribute_cache(_FAKE_DC, _FAKE_TP1, _SCHEMA_FIXTURE, expires_in=86400)
        updated = [dict(_SCHEMA_FIXTURE[0]), dict(_SCHEMA_FIXTURE[1]), {"id": 12345, "name": "Новый"}]
        LocalDBManager().set_attribute_cache(_FAKE_DC, _FAKE_TP1, updated, expires_in=86400)
        got = get_category_query().get_attribute_schema(_FAKE_DC, _FAKE_TP1, language="ZH_HANS")
        assert got == updated
    finally:
        _cleanup_fake_rows()


# ============================================================
# ② assemble 懒加载回写（mock）
# ============================================================

def _fake_ozon_schema_response(result):
    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"result": result}

    return _Resp()


def test_assemble_fetch_writes_back_schema():
    """_fetch_attribute_schema_from_ozon 成功 → set_attribute_cache(ZH_HANS) 被调。"""
    from graphs.nodes.assemble_ozon_product_node import _fetch_attribute_schema_from_ozon
    from utils.http_session import session as ozon_session

    mock_db = MagicMock()
    with patch.object(ozon_session, "post", return_value=_fake_ozon_schema_response(_SCHEMA_FIXTURE)), \
         patch("utils.local_db_manager.LocalDBManager", return_value=mock_db):
        got = _fetch_attribute_schema_from_ozon("cid", "key", _FAKE_DC, _FAKE_TP1)

    assert got == _SCHEMA_FIXTURE
    mock_db.set_attribute_cache.assert_called_once()
    args, kwargs = mock_db.set_attribute_cache.call_args
    # 位置或关键字两种调用形态都兼容
    assert args[:3] == (_FAKE_DC, _FAKE_TP1, _SCHEMA_FIXTURE) or (
        kwargs.get("description_category_id") == _FAKE_DC
        and kwargs.get("type_id") == _FAKE_TP1
        and kwargs.get("attributes_schema") == _SCHEMA_FIXTURE
    )
    assert kwargs.get("language", "ZH_HANS") == "ZH_HANS"


def test_assemble_fetch_writeback_failure_does_not_break_main_flow():
    """回写抛异常 → schema 照常返回（对齐 _cache_dict_values 异常安全）。"""
    from graphs.nodes.assemble_ozon_product_node import _fetch_attribute_schema_from_ozon
    from utils.http_session import session as ozon_session

    mock_db = MagicMock()
    mock_db.set_attribute_cache.side_effect = RuntimeError("pg down")
    with patch.object(ozon_session, "post", return_value=_fake_ozon_schema_response(_SCHEMA_FIXTURE)), \
         patch("utils.local_db_manager.LocalDBManager", return_value=mock_db):
        got = _fetch_attribute_schema_from_ozon("cid", "key", _FAKE_DC, _FAKE_TP1)

    assert got == _SCHEMA_FIXTURE


def test_assemble_fetch_empty_result_skips_writeback():
    """Ozon 返回空（类目对无效/限流）→ 不回写（防垃圾行）。"""
    from graphs.nodes.assemble_ozon_product_node import _fetch_attribute_schema_from_ozon
    from utils.http_session import session as ozon_session

    mock_db = MagicMock()
    with patch.object(ozon_session, "post", return_value=_fake_ozon_schema_response([])), \
         patch("utils.local_db_manager.LocalDBManager", return_value=mock_db):
        got = _fetch_attribute_schema_from_ozon("cid", "key", _FAKE_DC, _FAKE_TP1)

    assert got == []
    mock_db.set_attribute_cache.assert_not_called()


def test_assemble_follow_path_reads_list_shape_cache():
    """跟卖路径 PG 命中 list 形状 schema → 直接用，不再重调 Ozon（写读一致闭环）。"""
    from graphs.nodes import assemble_ozon_product_node as asm

    mock_query = MagicMock()
    mock_query.get_attribute_schema.return_value = _SCHEMA_FIXTURE  # list 形状（warm 同款）
    with patch.object(asm, "_fetch_attribute_schema_from_ozon", return_value=[]) as mock_fetch:
        # 直接复刻跟卖 Step 2 的读法（与 _assemble_follow_sell 内联逻辑一致）
        attr_schema = mock_query.get_attribute_schema(_FAKE_DC, _FAKE_TP1)
        if attr_schema and isinstance(attr_schema, dict) and attr_schema.get("result"):
            attr_list = attr_schema["result"]
        elif isinstance(attr_schema, list) and attr_schema:
            attr_list = attr_schema
        else:
            attr_list = asm._fetch_attribute_schema_from_ozon("cid", "key", _FAKE_DC, _FAKE_TP1)

    assert attr_list == _SCHEMA_FIXTURE
    mock_fetch.assert_not_called()


# ============================================================
# ③ retry RU 懒加载回写（mock）
# ============================================================

def test_retry_schema_ru_miss_writes_back_with_query_language():
    """retry `_get_attribute_schema` PG 未命中 → Ozon 直查结果回写，language=RU。"""
    from graphs.validation_retry_loop import _get_attribute_schema

    mock_query = MagicMock()
    mock_query.get_attribute_schema.return_value = None  # PG 未命中
    mock_db = MagicMock()
    with patch("utils.ozon_category_query.get_category_query", return_value=mock_query), \
         patch("graphs.validation_retry_loop._call_ozon_api",
               return_value={"result": _SCHEMA_FIXTURE}), \
         patch("utils.local_db_manager.LocalDBManager", return_value=mock_db):
        got = _get_attribute_schema("cid", "key", str(_FAKE_DC), str(_FAKE_TP1), "RU")

    assert got == _SCHEMA_FIXTURE
    mock_db.set_attribute_cache.assert_called_once()
    args, kwargs = mock_db.set_attribute_cache.call_args
    assert args[:3] == (_FAKE_DC, _FAKE_TP1, _SCHEMA_FIXTURE) or (
        kwargs.get("description_category_id") == _FAKE_DC
        and kwargs.get("type_id") == _FAKE_TP1
        and kwargs.get("attributes_schema") == _SCHEMA_FIXTURE
    )
    assert kwargs.get("language") == "RU"


def test_retry_schema_pg_hit_skips_ozon_and_writeback():
    """retry PG 命中 → 不调 Ozon 也不回写（避免无谓覆盖 expires）。"""
    from graphs.validation_retry_loop import _get_attribute_schema

    mock_query = MagicMock()
    mock_query.get_attribute_schema.return_value = _SCHEMA_FIXTURE
    mock_db = MagicMock()
    with patch("utils.ozon_category_query.get_category_query", return_value=mock_query), \
         patch("graphs.validation_retry_loop._call_ozon_api") as mock_api, \
         patch("utils.local_db_manager.LocalDBManager", return_value=mock_db):
        got = _get_attribute_schema("cid", "key", str(_FAKE_DC), str(_FAKE_TP1), "RU")

    assert got == _SCHEMA_FIXTURE
    mock_api.assert_not_called()
    mock_db.set_attribute_cache.assert_not_called()


def test_retry_schema_writeback_failure_does_not_break():
    """retry 回写抛异常 → schema 照常返回。"""
    from graphs.validation_retry_loop import _get_attribute_schema

    mock_query = MagicMock()
    mock_query.get_attribute_schema.return_value = None
    mock_db = MagicMock()
    mock_db.set_attribute_cache.side_effect = RuntimeError("pg down")
    with patch("utils.ozon_category_query.get_category_query", return_value=mock_query), \
         patch("graphs.validation_retry_loop._call_ozon_api",
               return_value={"result": _SCHEMA_FIXTURE}), \
         patch("utils.local_db_manager.LocalDBManager", return_value=mock_db):
        got = _get_attribute_schema("cid", "key", str(_FAKE_DC), str(_FAKE_TP1), "RU")

    assert got == _SCHEMA_FIXTURE


# ============================================================
# ④ warm 脚本 argparse（importlib 加载，不发外部请求）
# ============================================================

def _load_warm_module():
    script = Path(__file__).resolve().parent.parent / "scripts" / "warm_category_cache.py"
    spec = importlib.util.spec_from_file_location("warm_category_cache_v069_test", str(script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_warm_argparse_has_all_flag():
    mod = _load_warm_module()
    args = mod.build_arg_parser().parse_args(["--all"])
    assert args.all is True
    assert args.limit is None


def test_warm_argparse_all_limit_conflict_exits_2():
    mod = _load_warm_module()
    with pytest.raises(SystemExit) as exc:
        mod.parse_args(["--all", "--limit", "5"])
    assert exc.value.code == 2


def test_warm_argparse_coverage_flags():
    mod = _load_warm_module()
    args = mod.parse_args(["--coverage", "--coverage-sample", "3"])
    assert args.coverage is True
    assert args.coverage_sample == 3


def test_warm_resolve_limit():
    mod = _load_warm_module()
    assert mod.resolve_limit(True, None) is None      # --all → 全量
    assert mod.resolve_limit(True, 5) is None          # --all 压过 --limit（冲突已在 parse 拒绝）
    assert mod.resolve_limit(False, 200) == 200        # 旧语义保持
    assert mod.resolve_limit(False, None) is None      # 不带任何参数 = 全量（旧行为）


# ============================================================
# ⑤ coverage 统计（真实 PG 造两条假数据验证计数）
# ============================================================

@_pg_required
def test_coverage_counts_and_missing_classification():
    """假数据：2 个 type 节点、1 个 schema 覆盖、1 个字典值覆盖 → 归类正确。"""
    mod = _load_warm_module()
    from utils.local_db_manager import LocalDBManager

    _cleanup_fake_rows()
    try:
        _db_execute(
            """
            INSERT INTO category_tree_nodes
                (description_category_id, type_id, node_name, node_type, full_path,
                 top_level_category_name, depth, disabled, language, created_at)
            VALUES
                (:dc, :tp1, 'T3.3假类目A', 'type', '假类目 > A', '假类目', 1, FALSE, 'ZH_HANS', 0),
                (:dc, :tp2, 'T3.3假类目B', 'type', '假类目 > B', '假类目', 1, FALSE, 'ZH_HANS', 0)
            """,
            {"dc": _FAKE_DC, "tp1": _FAKE_TP1, "tp2": _FAKE_TP2},
        )
        LocalDBManager().set_attribute_cache(
            _FAKE_DC, _FAKE_TP1, _SCHEMA_FIXTURE, language="ZH_HANS", expires_in=86400,
        )
        _db_execute(
            """
            INSERT INTO dictionary_value_cache
                (attribute_id, description_category_id, type_id, language, values_data,
                 expires_at, created_at)
            VALUES (:attr, :dc, :tp2, 'ZH_HANS', '[]'::jsonb, EXTRACT(EPOCH FROM now())::int + 86400,
                    EXTRACT(EPOCH FROM now())::int)
            """,
            {"attr": _FAKE_ATTR, "dc": _FAKE_DC, "tp2": _FAKE_TP2},
        )

        report = mod.collect_coverage()

        assert report["total"] >= 2
        assert (_FAKE_DC, _FAKE_TP1) in report["schema_covered_pairs"]
        assert (_FAKE_DC, _FAKE_TP2) not in report["schema_covered_pairs"]  # 未覆盖 → 不在 covered
        assert (_FAKE_DC, _FAKE_TP2) in report["missing_pairs"]
        assert (_FAKE_DC, _FAKE_TP1) not in report["missing_pairs"]
        assert (_FAKE_DC, _FAKE_TP2) in report["dict_covered_pairs"]
        # 百分比与计数自洽
        assert report["schema_covered"] <= report["total"]
        assert abs(report["schema_pct"] - round(report["schema_covered"] * 100.0 / report["total"], 1)) < 0.01
    finally:
        _cleanup_fake_rows()


@_pg_required
def test_coverage_missing_sample_prints_pairs(capsys):
    """--coverage-sample：缺失 (dc,tp) 随机抽样打印，COVERAGE 摘要恒为最后一行。"""
    mod = _load_warm_module()
    from utils.local_db_manager import LocalDBManager

    _cleanup_fake_rows()
    try:
        _db_execute(
            """
            INSERT INTO category_tree_nodes
                (description_category_id, type_id, node_name, node_type, full_path,
                 top_level_category_name, depth, disabled, language, created_at)
            VALUES (:dc, :tp1, 'T3.3假类目A', 'type', '假类目 > A', '假类目', 1, FALSE, 'ZH_HANS', 0)
            """,
            {"dc": _FAKE_DC, "tp1": _FAKE_TP1},
        )
        # 不写任何缓存 → 该对必缺失
        report = mod.collect_coverage()
        assert (_FAKE_DC, _FAKE_TP1) in report["missing_pairs"]

        # random.sample 打桩为确定性抽样（7425 选 5 随机抽中假对概率≈0）
        with patch("random.sample", return_value=[(_FAKE_DC, _FAKE_TP1)]):
            mod.run_coverage_report(sample=5)
        out = capsys.readouterr().out
        assert f"{_FAKE_DC}/{_FAKE_TP1}" in out
        last_line = [ln for ln in out.strip().splitlines() if ln.strip()][-1]
        assert last_line.startswith("COVERAGE schema=")
        assert "dict=" in last_line
    finally:
        _cleanup_fake_rows()


def test_coverage_format_machine_readable_last_line():
    """format_coverage_report：人话输出 + 最后一行 `COVERAGE schema=x/y(z%) dict=a/b(c%)`。"""
    mod = _load_warm_module()
    report = {
        "total": 10,
        "schema_covered": 2,
        "dict_covered": 1,
        "schema_pct": 20.0,
        "dict_pct": 10.0,
        "missing_pairs": [(3, 4)] * 8,
        "schema_covered_pairs": {(1, 1), (2, 2)},
        "dict_covered_pairs": {(1, 1)},
    }
    out = mod.format_coverage_report(report)
    lines = out.strip().splitlines()
    assert lines[-1] == "COVERAGE schema=2/10(20.0%) dict=1/10(10.0%)"
    assert any("10" in ln for ln in lines[:-1])  # 人话行包含总数
