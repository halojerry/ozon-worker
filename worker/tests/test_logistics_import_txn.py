# worker/tests/test_logistics_import_txn.py
"""B2a-1: import_logistics.upsert_records 单事务化（纯 mock，无 PG 依赖）。

背景：原实现 DELETE 后先 commit、INSERT 后再 commit——两次提交之间的空窗里，
并发运费查询（logistics_service 按重量查费率）会读到刚被清空的表。改法：
DELETE+INSERT 同事务、末尾单次 commit。

运行：PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_logistics_import_txn.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import import_logistics  # noqa: E402


class _FakeSession:
    """记录操作序列的假 Session（记录 execute/add/commit 顺序供断言）。"""

    def __init__(self):
        self.ops = []  # [("execute", sql), ("add", obj), ("commit", None)]
        self.commits = 0

    def execute(self, stmt, *args, **kwargs):
        self.ops.append(("execute", str(stmt)))

    def add(self, obj):
        self.ops.append(("add", obj))

    def commit(self):
        self.commits += 1
        self.ops.append(("commit", None))

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_RECORD = [{
    "scoring_group": "Small",
    "service_level": "Standard",
    "tpl_provider": "test-tpl",
    "delivery_method": "",
    "base_cost": 5.0,
    "per_gram_rate": 0.01,
    "weight_min": 0,
    "weight_max": 500,
    "sum_limit_cm": 100,
    "longest_limit_cm": 60,
    "charge_type": "actual",
    "vol_weight_divisor": 0,
}]


def test_upsert_records_single_transaction(monkeypatch):
    """DELETE → INSERT 之间无 commit；全程恰好一次 commit 且在最后。"""
    fake = _FakeSession()
    monkeypatch.setattr(import_logistics, "Session", lambda engine: fake)

    import_logistics.upsert_records(engine=object(), records=[dict(_RECORD[0]) for _ in range(3)])

    # 恰好一次 commit
    assert fake.commits == 1
    # commit 是最后一个操作（DELETE + 全部 INSERT 之后）
    assert fake.ops[-1][0] == "commit"
    # DELETE 是第一个操作，且在其后、首个 add 之前没有任何 commit
    assert fake.ops[0][0] == "execute"
    assert "DELETE FROM logistics_rates" in fake.ops[0][1]
    adds = [i for i, op in enumerate(fake.ops) if op[0] == "add"]
    first_add = adds[0]
    assert not any(op[0] == "commit" for op in fake.ops[1:first_add])
    # 3 条记录全部插入
    assert len(adds) == 3
