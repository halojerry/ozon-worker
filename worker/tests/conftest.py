"""v0.62.1 P1-6: 测试环境 Supabase 隔离 + v0.62.2 P2-3: 非隔离库守卫。

- Supabase 隔离：测试用 sk-local 假 token 走本地降级路径，生产/CI 若配了真实
  SUPABASE_URL/KEY 会 401。此 autouse fixture 强制清空 Supabase env + 重置单例。
- 非隔离库守卫：ozon_bestsellers / blue_ocean_queries 为全局共享表，在非空库
  （如把 pytest 直接跑在生产库）会假失败。`_HAS_GLOBAL_DATA` 在 collection 前
  探测一次；命中 → 相关用例（见各文件 pytestmark）跳过并提示用隔离测试库。
"""
import os

import pytest
from sqlalchemy import create_engine, text


@pytest.fixture(autouse=True)
def _isolate_supabase_env(monkeypatch):
    """每个用例前清空 Supabase 环境变量并重置客户端单例。"""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    try:
        import storage.database.supabase_client as sbc
        sbc._supabase_client = None
    except Exception:
        pass
    yield
    try:
        import storage.database.supabase_client as sbc
        sbc._supabase_client = None
    except Exception:
        pass


def _db_has_global_data() -> bool:
    """检测运行库是否已含全局共享表数据（非隔离测试库）。"""
    url = os.environ.get(
        "PGDATABASE_URL",
        "postgresql://postgres:ozon123@localhost:5433/ozon",
    )
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            for tbl in ("ozon_bestsellers", "blue_ocean_queries"):
                try:
                    count = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar() or 0
                    if count:
                        return True
                except Exception:
                    pass
        eng.dispose()
    except Exception:
        pass
    return False


_HAS_GLOBAL_DATA = _db_has_global_data()


_GLOBAL_DATA_FILES = {
    "/test_analytics_service.py",
    "/test_queries_service.py",
    "/test_dashboard_api.py",
}


def pytest_collection_modifyitems(items):
    """非隔离库时，跳过依赖全局表数据的用例（避免假失败误导）。"""
    if not _HAS_GLOBAL_DATA:
        return
    for item in items:
        fname = item.nodeid.split("::")[0]
        if any(fname.endswith(f) for f in _GLOBAL_DATA_FILES):
            item.add_marker(
                pytest.mark.skip(
                    reason="运行库非隔离测试库（全局表已有数据）——请用 scripts/test-docker.sh 的隔离库",
                )
            )


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db_after_session():
    """v0.63.1: 会话结束后清空测试库业务表，消除测试残留污染运行态。

    背景：pytest 直连同一 PG（scripts/test-docker.sh），部分用例写入 credentials/
    ozon_product_tasks/store_sync_jobs 等但不清理 → 测试后在同库启动 worker 时，
    残留 active 凭证被 store_sync 每 5s 拉起来用无效 key 刷屏失败，残留任务被自动
    执行并重试（无效 token 401）。本 fixture 在会话结束后 TRUNCATE 全部业务表
    （保留 _mig_backup_* 备份表），下次 init_data 重新播种。

    安全开关：仅当 TEST_DB_CLEANUP=1（docker-compose.test.yml 已设置）才执行，
    防止误把 pytest 跑在真实库/生产库时被清空。
    """
    yield
    if os.environ.get("TEST_DB_CLEANUP", "0") != "1":
        return
    url = os.environ.get(
        "PGDATABASE_URL",
        "postgresql://postgres:ozon123@localhost:5433/ozon",
    )
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text(
                r"""
                DO $$
                DECLARE r RECORD;
                BEGIN
                    FOR r IN SELECT tablename FROM pg_tables
                             WHERE schemaname = 'public'
                               AND tablename NOT LIKE '\_mig\_backup\_%'
                    LOOP
                        EXECUTE 'TRUNCATE TABLE public.' || quote_ident(r.tablename)
                                || ' RESTART IDENTITY CASCADE';
                    END LOOP;
                END $$;
                """
            ))
            conn.commit()
        eng.dispose()
    except Exception:
        # 清理失败不掩盖测试结果（下一轮 init_data 会重建）
        pass
