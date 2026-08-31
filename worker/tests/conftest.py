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
