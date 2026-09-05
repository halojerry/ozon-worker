"""T14b SURFACE 演示：TestClient 走真实 HTTP 路由层 POST /api/v1/drafts/{id}/ai/title。

不进入 `with TestClient(app)`（避免触发 lifespan → init_db + worker 消费 PG 任务）；
monkeypatch：鉴权放行（本地模式）、草稿读取（无需 PG）、LLM（返回俄语标题）。
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

RU_TITLE = "Автопоилка для домашних животных, тихая, с фильтрацией"
PAYLOAD = {
    "draft": {
        "title": "跨境爆款 宠物自动饮水器 静音循环过滤",
        "description": "静音设计，适合宠物日常饮水",
        "attributes": {"颜色": "白色", "材质": "塑料"},
    },
    "source": {"purchase_url": "https://detail.1688.com/offer/1.html", "purchase_cost": 5.5},
    "extensions": {},
}


def _fake_llm(value):
    def fake(token, system_prompt, user_prompt, model="deepseek-v4-flash-vision-exp", image_urls=None,
             temperature=0.0, max_tokens=4096, timeout=90):
        fake.calls.append((system_prompt, user_prompt))
        return value
    fake.calls = []
    return fake


def test_surface_testclient_title_returns_russian(monkeypatch):
    import main
    from fastapi.testclient import TestClient
    from routes import drafts_routes
    from services import ai_field_service

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)  # 本地鉴权放行
    monkeypatch.setattr(drafts_routes, "_load_draft_payload",
                        lambda draft_id, tenant_id: copy.deepcopy(PAYLOAD))
    fake = _fake_llm(RU_TITLE)
    monkeypatch.setattr(ai_field_service, "call_mxou_chat_api", fake)

    client = TestClient(main.app)  # 不带 with → 不触发 lifespan（不连 PG / 不启动 worker）
    resp = client.post(
        "/api/v1/drafts/11111111-1111-1111-1111-111111111111/ai/title",
        json={"token": "sk-demo"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["field"] == "title"
    assert body["value"] == RU_TITLE
    assert any("Автопоилка" in call[1] or "Перевод" in call[0] or "Translate" in call[0] for call in fake.calls) \
        or len(fake.calls) == 1
    assert len(fake.calls) == 1, "复用 call_mxou_chat_api（非新客户端）"
