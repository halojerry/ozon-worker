# -*- coding: utf-8 -*-
"""
v0.63 信封契约字段所有权测试（防漂移门禁）。

背景：envelope/draft 为自由 dict，此前 Skill 生产了 category_path 等字段但 Worker 不消费，
导致「有信息却复用不上」。本测试固定「Skill 生产 ↔ Worker 消费」的双向覆盖：
- 生产者：skill/scripts/cloud_probe.py、skill/scripts/lib/ozon_scraper.py
- 消费者：worker/src/graphs/nodes/assemble_ozon_product_node.py、follow_sell_import_node.py
任一「生产了但无消费」或「消费需要但无生产」即失败。

运行: cd worker && PYTHONPATH=src ../skill/.venv314/bin/python3 -m pytest tests/test_envelope_contract.py -q
"""
import os
import re
import sys
import pytest

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
# worker 侧始终存在（worker 镜像 / 仓库均含 src）；skill 侧仅在完整仓库（本地/CI）存在
WORKER_SRC = os.path.abspath(os.path.join(TEST_DIR, "..", "src"))
SKILL_PROD_CANDIDATES = [
    os.path.join(TEST_DIR, "..", "..", "skill", "scripts", "cloud_probe.py"),
    os.path.join(TEST_DIR, "..", "..", "skill", "scripts", "lib", "ozon_scraper.py"),
]
# 归一化：搜 git 根或逐级上溯找 skill/scripts
def _find_repo_root():
    p = TEST_DIR
    for _ in range(6):
        if os.path.exists(os.path.join(p, "skill", "scripts", "cloud_probe.py")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return None

_root = _find_repo_root()
SKILL_PROD = (
    [
        os.path.join(_root, "skill", "scripts", "cloud_probe.py"),
        os.path.join(_root, "skill", "scripts", "lib", "ozon_scraper.py"),
    ]
) if _root else []
WORKER_CONS = [
    os.path.join(WORKER_SRC, "graphs", "nodes", "assemble_ozon_product_node.py"),
    os.path.join(WORKER_SRC, "graphs", "nodes", "follow_sell_import_node.py"),
]
WORKER_QUERY = os.path.join(WORKER_SRC, "utils", "ozon_category_query.py")

# 每条：字段(token) → 生产必须出现、消费必须出现。
FIELD_TOKENS = [
    ("ozon_category.category_path", "category_path"),
    ("ozon_category.source", '"source"'),
    ("ozon_category.namespace", '"namespace"'),
    ("source_category_id", "source_category_id"),
    ("source_category_path", "source_category_path"),
]
RESOLVER_TOKEN = "get_node_by_full_path"  # Worker 专属确定性解析器（仅查 query 侧）


def _read(paths):
    txt = ""
    for p in paths:
        with open(p, encoding="utf-8") as f:
            txt += f.read() + "\n"
    return txt


def test_skill_produces_contract_fields():
    if not SKILL_PROD or not all(os.path.exists(p) for p in SKILL_PROD):
        pytest.skip("skill 源码不在当前 worker 镜像/上下文，跳过生产侧契约（本地/CI 全仓校验）")
    prod = _read(SKILL_PROD)
    for label, token in FIELD_TOKENS:
        # 生产侧必须有该 field 的赋值/写入（字段名出现）
        assert token in prod, f"Skill 未生产字段 {label}（找不到 `{token}`）"


def test_worker_consumes_contract_fields():
    cons = _read(WORKER_CONS)
    query = _read([WORKER_QUERY])
    for label, token in FIELD_TOKENS:
        assert token in cons, f"Worker 未消费字段 {label}（找不到 `{token}`）"
    assert RESOLVER_TOKEN in query, f"Worker 缺确定性解析器 {RESOLVER_TOKEN}"


def test_get_node_by_full_path_returns_shape():
    """解析器签名与方法存在（结构契约）。"""
    from utils.ozon_category_query import OzonCategoryQuery
    assert callable(getattr(OzonCategoryQuery, "get_node_by_full_path", None))
    assert hasattr(OzonCategoryQuery, "get_types_under")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
