"""T14b: 草稿 AI 单字段重新生成端点测试（mock call_mxou_chat_api + payload，无需 PG / mock DB）。

覆盖：
1. 每 field（title/description/attributes/tags）→ 200 非空 RU，无中文/拉丁残留（正则断言）
2. 未知 field（含 brand）→ 400
3. 未认证（无 token）→ 401
4. mock call_mxou_chat_api 被调用（复用非新客户端）
5. LLM 返回中文/失败 → 422（绝不含中文值）
6. 只读：draft payload 前后未修改
"""
import asyncio
import copy
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

# ── 断言正则（验收门：非空 RU 无中文/拉丁残留）──
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[a-zA-Z]{2,}")

VALID_PAYLOAD = {
    "draft": {
        "title": "跨境爆款 宠物自动饮水器 静音循环过滤",
        "description": "静音设计，适合宠物日常饮水，循环过滤保持水质干净",
        "attributes": {"颜色": "白色", "材质": "ABS", "品牌": "无品牌"},
        "tags": "宠物饮水,自动,循环",
    },
    "source": {"purchase_url": "https://detail.1688.com/offer/1.html", "purchase_cost": 5.5},
    "extensions": {"margin_rate": 0.25},
}

RU_TITLE = "Автопоилка для домашних животных, тихая, с фильтрацией"
RU_DESC = "Тихая автопоилка для домашних животных. Фильтрация воды, безопасный пластик."
RU_ATTRS = '{"Цвет": "Белый", "Материал": "Пластик", "Бренд": "Нет бренда"}'
RU_TAGS = "автопоилка, домашние животные, тихий"
CN_RESULT = "宠物自动饮水器 静音设计"

# ── LLM 响应类别 → 期望状态码 ──
FIELD_EXPECTATIONS = {
    "title": (RU_TITLE, 200),
    "description": (RU_DESC, 200),
    "attributes": (RU_ATTRS, 200),
    "tags": (RU_TAGS, 200),
}


class FakeRequest:
    def __init__(self, body):
        self._body = body

    async def body(self):
        return json.dumps(self._body).encode("utf-8")


def _fake_llm(value):
    def fake(token, system_prompt, user_prompt, model="deepseek-v4-flash",
             temperature=0.0, max_tokens=4096, timeout=90):
        fake.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        return value
    fake.calls = []
    return fake


def _call_endpoint(field, body, monkeypatch, llm_value=None):
    """直接调用端点函数（FakeRequest + monkeypatch DB 读取 / LLM / 鉴权）。"""
    from routes import drafts_routes
    from services import ai_field_service
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)  # 本地鉴权放行
    monkeypatch.setattr(
        drafts_routes, "_load_draft_payload",
        lambda draft_id, tenant_id: copy.deepcopy(VALID_PAYLOAD),
    )
    fake = _fake_llm(llm_value)
    monkeypatch.setattr(ai_field_service, "call_mxou_chat_api", fake)

    fn = drafts_routes.draft_ai_field
    result = asyncio.run(fn("11111111-1111-1111-1111-111111111111", field, FakeRequest(body)))
    return result, fake


def _assert_error(fn_result, status_code, field):
    """断言端点抛 HTTPException(status_code)。"""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(fn(field, FakeRequest({"token": "sk-x"})))
    assert ei.value.status_code == status_code


# ── 1. 每 field 返回非空 RU 无中文/拉丁残留 ──
@pytest.mark.parametrize("field", ["title", "description", "attributes", "tags"])
def test_each_field_returns_clean_russian(field, monkeypatch):
    llm_value, expected_status = FIELD_EXPECTATIONS[field]
    result, fake = _call_endpoint(field, {"token": "sk-x"}, monkeypatch, llm_value=llm_value)

    assert expected_status == 200
    value = result["value"]
    assert isinstance(value, str) and value.strip(), f"{field} 返回空值"
    assert _CYRILLIC_RE.search(value), f"{field} 无西里尔字符: {value}"
    assert not _CJK_RE.search(value), f"{field} 含中文残留: {value}"
    assert not _LATIN_RE.search(value), f"{field} 含拉丁残留: {value}"
    assert result["field"] == field


# ── 2. 断言 mock call_mxou_chat_api 被调用（复用非新客户端）──
def test_mock_call_mxou_chat_api_invoked(monkeypatch):
    result, fake = _call_endpoint("title", {"token": "sk-x"}, monkeypatch, llm_value=RU_TITLE)
    assert result["value"] == RU_TITLE
    assert len(fake.calls) == 1, "必须且仅调用一次 call_mxou_chat_api（复用非新客户端）"
    assert "title" in fake.calls[0]["user_prompt"].lower() or "Автопоилка" not in fake.calls[0]["user_prompt"] \
        or "Translate" in fake.calls[0]["system_prompt"]


# ── 3. 未知 field → 400（含 brand——品牌强制 Нет бренда 约定，不提供 AI 生成）──
def test_unknown_field_returns_400(monkeypatch):
    from routes import drafts_routes
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    with pytest.raises(Exception) as ei:
        asyncio.run(drafts_routes.draft_ai_field(
            "11111111-1111-1111-1111-111111111111", "brand", FakeRequest({"token": "sk-x"})))
    from fastapi import HTTPException
    assert isinstance(ei.value, HTTPException) and ei.value.status_code == 400


def test_garbage_field_returns_400(monkeypatch):
    from routes import drafts_routes
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    with pytest.raises(Exception) as ei:
        asyncio.run(drafts_routes.draft_ai_field(
            "11111111-1111-1111-1111-111111111111", "price", FakeRequest({"token": "sk-x"})))
    from fastapi import HTTPException
    assert isinstance(ei.value, HTTPException) and ei.value.status_code == 400


# ── 4. 未认证 → 401 ──
def test_no_token_returns_401(monkeypatch):
    from routes import drafts_routes
    import main

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(drafts_routes.draft_ai_field(
            "11111111-1111-1111-1111-111111111111", "title", FakeRequest({"token": ""})))
    assert ei.value.status_code == 401


# ── 5. 翻译失败 / 仍含中文 → 422（绝不含中文值）──
def test_llm_chinese_result_returns_422(monkeypatch):
    from routes import drafts_routes
    import main
    from services import ai_field_service
    from fastapi import HTTPException

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    monkeypatch.setattr(drafts_routes, "_load_draft_payload",
                        lambda draft_id, tenant_id: copy.deepcopy(VALID_PAYLOAD))
    fake = _fake_llm(CN_RESULT)
    monkeypatch.setattr(ai_field_service, "call_mxou_chat_api", fake)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(drafts_routes.draft_ai_field(
            "11111111-1111-1111-1111-111111111111", "title", FakeRequest({"token": "sk-x"})))
    assert ei.value.status_code == 422
    assert "中文" in ei.value.detail or "不合格" in ei.value.detail


def test_llm_failure_returns_422(monkeypatch):
    from routes import drafts_routes
    import main
    from services import ai_field_service
    from fastapi import HTTPException

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    monkeypatch.setattr(drafts_routes, "_load_draft_payload",
                        lambda draft_id, tenant_id: copy.deepcopy(VALID_PAYLOAD))
    fake = _fake_llm(None)  # LLM 调用失败
    monkeypatch.setattr(ai_field_service, "call_mxou_chat_api", fake)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(drafts_routes.draft_ai_field(
            "11111111-1111-1111-1111-111111111111", "title", FakeRequest({"token": "sk-x"})))
    assert ei.value.status_code == 422


# ── 6. 只读：draft payload 未被修改 ──
def test_payload_readonly(monkeypatch):
    before = copy.deepcopy(VALID_PAYLOAD)
    result, fake = _call_endpoint("title", {"token": "sk-x"}, monkeypatch, llm_value=RU_TITLE)
    assert result["value"] == RU_TITLE
    assert VALID_PAYLOAD == before, "端点不得修改 draft payload（只读，前端决定 PATCH 保存）"


# ── 7. 草稿不存在 → 404 ──
def test_draft_not_found_returns_404(monkeypatch):
    from routes import drafts_routes
    import main
    from fastapi import HTTPException

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    monkeypatch.setattr(drafts_routes, "_load_draft_payload", lambda draft_id, tenant_id: None)

    with pytest.raises(HTTPException) as ei:
        asyncio.run(drafts_routes.draft_ai_field(
            "11111111-1111-1111-1111-111111111111", "title", FakeRequest({"token": "sk-x"})))
    assert ei.value.status_code == 404
