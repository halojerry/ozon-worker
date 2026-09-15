"""v0.76 T12(api-H1): /node_run 拒绝持久化副作用节点（全局学习表投毒面）。

背景：/node_run/{node_id} 允许调用方以任意 payload 直跑单个图节点。learning_record
节点会按调用方可控的 moderation_status/user_id 写全局共享 category_mapping（W11，
跨租户 L0 类目匹配数据源）与 category_commission——等于向所有租户的学习表投毒。
本测试锁定：有状态节点 → 403；纯计算节点不受影响。
"""
from fastapi.testclient import TestClient

from main import _NODE_RUN_DENIED, app


def test_blocklist_membership_locked():
    """锁定黑名单成员，防无意收窄（评估结论见 main.py _NODE_RUN_DENIED 注释块）。"""
    assert _NODE_RUN_DENIED == frozenset({
        "learning_record",
        "assemble_ozon_product",
        "prepare_ozon_upload",
        "ozon_upload",
        "validation_retry_wrapper",
    })


def test_learning_record_denied(monkeypatch):
    monkeypatch.setattr("main._authenticate_token", lambda t: None)
    r = TestClient(app).post("/node_run/learning_record",
                             json={"token": "sk-x"}, headers={"Authorization": "Bearer sk-x"})
    assert r.status_code == 403
    assert "stateful" in r.json()["detail"]


def test_pure_node_still_allowed(monkeypatch):
    monkeypatch.setattr("main._authenticate_token", lambda t: None)
    # 打桩 service.run_node：锁定「纯节点越过黑名单、抵达执行边界」这一语义本身。
    # （run_node 深层对 runtime 最小替换层有历史性漂移——LangGraphParser/
    #   ErrorClassifier 缺口，属 T12 之外的遗留缺陷链；黑名单测试不依赖它。）
    reached = {}

    async def _stub_run_node(node_id, payload, ctx=None, extra_config=None):
        reached["node_id"] = node_id
        return {"reached_execution": True, "node_id": node_id}

    monkeypatch.setattr("main.service.run_node", _stub_run_node)
    # pricing 已核实无持久化副作用（纯计算 + 只读 Ozon seller-info/物流询价）
    r = TestClient(app).post("/node_run/pricing",
                             json={"token": "sk-x"}, headers={"Authorization": "Bearer sk-x"})
    assert r.status_code != 403
    assert r.status_code == 200
    assert r.json()["reached_execution"] is True
    assert reached["node_id"] == "pricing"
