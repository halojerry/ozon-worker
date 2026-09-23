"""v0.76 安全修复 T1：全局共享读端点不得回显贡献者明文 key（api-C1 跨租户接管链）。

两层钉住（brief：服务层打脱敏实现层，discovery/runs 打 HTTP 路由组装层）：
1. 服务层 services.analytics_service.list_bestsellers —— 组装 dict 只发
   contributed_by_fp，contributed_by_token_id 键消失（不是置空）；
2. HTTP 层 GET /api/v1/discovery/runs —— 响应体零明文、零 contributed_by_token_id 键。

行形态以真实 SELECT 列序为准（brief 行号/列数基于旧基线，按符号定位平移）：
- ozon_bestsellers SELECT: sku_or_id, brand, category_path, ordering_amount,
  ordering_count, avg_price_rub, contributed_by_token_id（明文 = r[6]）
- discovery_runs SELECT: id, keyword, filters_json, candidates_json, created_at,
  tenant_id（明文 = r[5]）
"""
from datetime import datetime

PLAIN = "probe-plain-key-0001"


def test_list_bestsellers_service_strips_plaintext(monkeypatch):
    from services import analytics_service as a
    # 行形态以真实 SELECT 列序为准：r[6] = 明文 key
    fake = [("sku-1", "x", "宠物/饮水机", 1.0, 2, 10.0, PLAIN)]
    monkeypatch.setattr(a, "_fetch_bestseller_rows", lambda **kw: (fake, 1))  # 本任务抽出的行查询封装
    out = a.list_bestsellers("probe", brand="x")
    assert all("contributed_by_token_id" not in row for row in out["items"])
    assert out["items"][0]["contributed_by_fp"]          # fp 保留且非空

    from services.tenant_service import token_fingerprint
    assert out["items"][0]["contributed_by_fp"] == token_fingerprint(PLAIN)[:8]  # 与写侧同源等值


def test_discovery_runs_http_response_has_no_plaintext(monkeypatch):
    from fastapi.testclient import TestClient
    import main as m
    monkeypatch.setattr("main._verify_analytics_token", lambda t: None)
    monkeypatch.setattr(
        m, "_fetch_discovery_runs_rows",
        lambda **kw: ([("t1", "宠物饮水机", {"min_margin": 0.25}, [],
                        datetime(2026, 9, 16, 10, 0, 0), PLAIN)], 1),
    )
    r = TestClient(m.app).get("/api/v1/discovery/runs", headers={"Authorization": "Bearer sk-probe"})
    assert r.status_code == 200
    assert PLAIN not in r.text                  # 明文绝不出响应
    assert "contributed_by_token_id" not in r.text
    assert "contributed_by_fp" in r.text
