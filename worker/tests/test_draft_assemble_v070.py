"""v0.70 采集箱一键预组装测试（纯 mock，无需 PG）。

覆盖：
1. 中文标题+空描述 → title RU + title_source 留档中文 + description 由标题合成 RU + assembled 清单正确
2. 已西里尔 title/description → skipped，regenerate_field 未被调用（幂等不烧 LLM）
3. 属性合并：新键写入 ozon_attributes；同键不覆盖（_merge_ru_attributes 纯函数）；
   ozon_attributes 已有内容（跟卖竞品属性）→ 整字段跳过不混源
4. suggested_category 写 draft.suggested_category；draft.ozon_category 恒不动（防劫持仲裁链，关键防回归）
5. estimated_pricing 只进展示键（draft.price 等不动）；估算失败 → None；显式 RUB 汇率入参
6. 单字段 LLM 失败不阻断（进 skipped）
7. 二次组装全 skipped 零 LLM（幂等）+ 类目建议查空保留旧值
8. service 层写回 version++（fake get_draft + fake engine 捕获 UPDATE）
9. 端点层：200 响应结构与 version、404（不存在/跨租户）、401（无 token）、openapi 注册

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_draft_assemble_v070.py -q
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# ── 断言正则 ──
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

CN_TITLE = "跨境爆款 宠物自动饮水器 静音循环过滤"
RU_TITLE = "Автопоилка для животных, тихая, с фильтрацией"
RU_DESC = "Тихая автопоилка для домашних животных с фильтрацией воды."
RU_ATTRS = {"Цвет": "Белый", "Материал": "Пластик", "Бренд": "Нет бренда"}
# v0.70 品牌剥离契约：LLM 返回的品牌键（85/5076/Бренд）被剥掉，不得进 ozon_attributes
RU_ATTRS_NO_BRAND = {"Цвет": "Белый", "Материал": "Пластик"}
RU_TAGS = "автопоилка, домашние животные, тихая"
CN_TAGS = "宠物饮水,自动,循环"
CAT_ROW = {
    "description_category_id": "17029651",
    "type_id": "91633",
    "node_name": "自动饮水器",
    "full_path": "宠物用品/喂食饮水/自动饮水器",
    "similarity": 0.9,
}
CAT_SUGGESTION = {
    "description_category_id": "17029651",
    "type_id": "91633",
    "category_name": "自动饮水器",
}
ESTIMATE_RESULT = {"price": 729.0, "old_price": 910.0, "promo_price": 547.0, "currency": "RUB"}
DRAFT_ID = "11111111-1111-1111-1111-111111111111"


def _payload(desc=None, attrs=None, tags=None, extra_draft=None) -> dict:
    draft = {
        "item_id": "812345678901",
        "title": CN_TITLE,
        "images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"],
        "weight": 120,
        "dimensions": {"length": 150, "width": 90, "height": 60},
        "purchase_cost": 8.5,
    }
    if desc is not None:
        draft["description"] = desc
    if attrs is not None:
        draft["attributes"] = attrs
    if tags is not None:
        draft["tags"] = tags
    if extra_draft:
        draft.update(extra_draft)
    return {
        "draft": draft,
        "source": {"purchase_url": "https://detail.1688.com/offer/812345678901.html",
                   "purchase_cost": 8.5},
        "extensions": {},
    }


def _fake_regenerate(mapping, calls):
    """mock regenerate_field：记录 (field) 调用，按 mapping 返回（缺省 None=fail）。"""
    def fake(field, current_value, token, traffic_keywords=None):
        calls.append(field)
        return mapping.get(field)
    return fake


class _FakeCategoryQuery:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def search_nodes(self, query_text, top_k=15, node_type=None, language="ZH_HANS"):
        self.calls.append({"q": query_text, "language": language, "top_k": top_k})
        return self.rows


def _patch_all(monkeypatch, regenerate_map=None, cat_rows=None, estimate_result=ESTIMATE_RESULT,
               estimate_error=None):
    """一键 mock ai_field_service 的全部外呼（LLM/类目树/估价/汇率）。"""
    import services.ai_field_service as afs

    calls = []
    monkeypatch.setattr(afs, "regenerate_field", _fake_regenerate(regenerate_map or {}, calls))
    fake_cat = _FakeCategoryQuery(cat_rows if cat_rows is not None else [CAT_ROW])
    monkeypatch.setattr(afs, "get_category_query", lambda: fake_cat)

    est_calls = []

    def fake_estimate(payload, **kwargs):
        est_calls.append(kwargs)
        if estimate_error is not None:
            raise estimate_error
        return estimate_result

    monkeypatch.setattr(afs, "estimate_from_envelope", fake_estimate)
    monkeypatch.setattr(afs, "_get_cny_rub_rate", lambda: 12.0)
    return calls, fake_cat, est_calls


# ============================================================
# 1. 全组装：中文标题 + 空描述 → 全字段 RU + title_source 留档
# ============================================================

def test_assemble_chinese_title_empty_description(monkeypatch):
    import services.ai_field_service as afs

    payload = _payload(desc="", attrs={"颜色": "白色", "材质": "ABS"}, tags=CN_TAGS)
    calls, fake_cat, est_calls = _patch_all(monkeypatch, regenerate_map={
        "title": RU_TITLE,
        "description": RU_DESC,  # 空描述 → 标题+属性材料合成
        "attributes": json.dumps(RU_ATTRS, ensure_ascii=False),
        "tags": RU_TAGS,
    })
    result = afs.assemble_draft(payload, "mxou-key")

    draft = payload["draft"]
    assert _CYRILLIC_RE.search(draft["title"]) and not _CJK_RE.search(draft["title"])
    assert draft["title"] == RU_TITLE
    assert draft["title_source"] == CN_TITLE, "原中文标题必须挪 title_source 留档"
    assert draft["description"] == RU_DESC, "空描述必须由标题+属性材料合成 RU"
    assert draft["ozon_attributes"] == RU_ATTRS_NO_BRAND, \
        "品牌键（Бренд）必须被剥离——防侵权红线，管线会无条件强制 Нет бренда"
    assert draft["tags"] == RU_TAGS
    assert result["assembled"] == ["title", "description", "attributes", "tags"]
    assert result["skipped"] == []
    assert result["suggested_category"] == CAT_SUGGESTION
    assert draft["estimated_pricing"] == {"price": 729.0, "old_price": 910.0, "promo_price": 547.0}
    # 类目建议用中文标题走 ZH_HANS（jieba 路径）
    assert fake_cat.calls and fake_cat.calls[0]["q"] == CN_TITLE
    assert fake_cat.calls[0]["language"] == "ZH_HANS"
    # 估价显式 RUB
    assert est_calls and est_calls[0].get("currency_code") == "RUB"
    # LLM 每 field 恰好一次
    assert sorted(calls) == ["attributes", "description", "tags", "title"]


# ============================================================
# 2. 已西里尔 → skipped 零 LLM（幂等）
# ============================================================

def test_cyrillic_fields_skipped_without_llm(monkeypatch):
    import services.ai_field_service as afs

    payload = _payload(desc=RU_DESC, attrs={"颜色": "白色"}, extra_draft={"title": RU_TITLE})
    calls, _, _ = _patch_all(monkeypatch)  # 空 mapping：任何 LLM 调用都会返回 None
    result = afs.assemble_draft(payload, "mxou-key")

    assert "title" in result["skipped"]
    assert "description" in result["skipped"]
    assert "title" not in calls and "description" not in calls, "已西里尔字段不得烧 LLM"
    assert payload["draft"]["title"] == RU_TITLE
    assert "title_source" not in payload["draft"]


# ============================================================
# 3. 属性合并
# ============================================================

def test_attributes_merged_new_keys_written(monkeypatch):
    import services.ai_field_service as afs

    payload = _payload(attrs={"颜色": "白色", "材质": "ABS"})
    calls, _, _ = _patch_all(monkeypatch, regenerate_map={
        "attributes": json.dumps(RU_ATTRS, ensure_ascii=False),
    })
    result = afs.assemble_draft(payload, "mxou-key")

    assert payload["draft"]["ozon_attributes"] == RU_ATTRS_NO_BRAND, \
        "品牌键剥离（v0.70 防侵权红线）"
    assert "attributes" in result["assembled"]
    # 中文源 attributes 保持原样（管线兜底源不动）
    assert payload["draft"]["attributes"] == {"颜色": "白色", "材质": "ABS"}


def test_merge_ru_attributes_no_overwrite():
    """已有同键不被覆盖、新键写入（纯函数单测——防回归锁定合并语义）。"""
    from services.ai_field_service import _merge_ru_attributes

    base = {"Цвет": "Синий", "Бренд": "Нет бренда"}
    generated = {"Цвет": "Белый", "Материал": "Пластик"}
    merged = _merge_ru_attributes(base, generated, protected_keys=set(base) | {"颜色"})
    assert merged == {"Цвет": "Синий", "Бренд": "Нет бренда", "Материал": "Пластик"}
    assert base == {"Цвет": "Синий", "Бренд": "Нет бренда"}, "纯函数不得改 base"


def test_existing_ozon_attributes_skip_no_mixing(monkeypatch):
    """ozon_attributes 已有内容（跟卖竞品 RU 属性）→ attributes 整字段跳过不混源。"""
    import services.ai_field_service as afs

    payload = _payload(attrs={"颜色": "白色"},
                       extra_draft={"ozon_attributes": {"Цвет": "Синий"}})
    calls, _, _ = _patch_all(monkeypatch, regenerate_map={
        "attributes": json.dumps({"Материал": "Пластик"}, ensure_ascii=False)})
    result = afs.assemble_draft(payload, "mxou-key")

    assert "attributes" in result["skipped"]
    assert "attributes" not in calls, "ozon_attributes 非空不得烧 attributes 的 LLM"
    assert payload["draft"]["ozon_attributes"] == {"Цвет": "Синий"}, "竞品属性不被 AI 值污染"


# ============================================================
# 4. suggested_category 写展示键；ozon_category 恒不动（关键防回归）
# ============================================================

def test_suggested_category_never_touches_ozon_category(monkeypatch):
    import services.ai_field_service as afs

    payload = _payload(desc=RU_DESC, extra_draft={"ozon_category": None})
    calls, _, _ = _patch_all(monkeypatch, regenerate_map={"title": RU_TITLE})
    result = afs.assemble_draft(payload, "mxou-key")

    assert payload["draft"]["ozon_category"] is None, "预组装绝不写 draft.ozon_category"
    assert payload["draft"]["suggested_category"] == CAT_SUGGESTION
    assert result["suggested_category"] == CAT_SUGGESTION
    assert "ozon_category" not in str(result["assembled"])


def test_suggestion_preserved_when_search_empty(monkeypatch):
    """标题已 RU 的二次组装：中文树查空 → 保留旧建议，不用 None 冲掉。"""
    import services.ai_field_service as afs

    payload = _payload(desc=RU_DESC, extra_draft={
        "title": RU_TITLE,
        "title_source": CN_TITLE,
        "suggested_category": CAT_SUGGESTION,
        "ozon_attributes": dict(RU_ATTRS),  # 非空 → attributes 也跳过
    })
    calls, fake_cat, _ = _patch_all(monkeypatch, cat_rows=[])  # 类目树查空
    result = afs.assemble_draft(payload, "mxou-key")

    assert calls == [], "全字段已 RU/已存在 → 零 LLM"
    assert set(result["skipped"]) == {"title", "description", "attributes", "tags"}
    assert payload["draft"]["suggested_category"] == CAT_SUGGESTION, "查空必须保留旧建议"
    assert result["suggested_category"] == CAT_SUGGESTION
    # 查询文本回退 title_source 中文（RU 标题查中文树零命中）
    assert fake_cat.calls and fake_cat.calls[0]["q"] == CN_TITLE


# ============================================================
# 5. estimated_pricing 只进展示键；失败 → None
# ============================================================

def test_estimated_pricing_display_only(monkeypatch):
    import services.ai_field_service as afs

    payload = _payload(desc=RU_DESC, extra_draft={"price": 123.0, "old_price": 150.0})
    _patch_all(monkeypatch, regenerate_map={"title": RU_TITLE})
    afs.assemble_draft(payload, "mxou-key")

    assert payload["draft"]["estimated_pricing"]["price"] == 729.0
    assert payload["draft"]["price"] == 123.0, "估价绝不写 draft.price（pricing_node 永远重算）"
    assert payload["draft"]["old_price"] == 150.0


def test_estimated_pricing_failure_returns_none(monkeypatch):
    import services.ai_field_service as afs

    payload = _payload(desc=RU_DESC)
    calls, _, _ = _patch_all(monkeypatch, regenerate_map={"title": RU_TITLE},
                             estimate_error=RuntimeError("pg down"))
    result = afs.assemble_draft(payload, "mxou-key")

    assert result["estimated_pricing"] is None
    assert "estimated_pricing" not in payload["draft"]
    assert result["assembled"] == ["title"], "估价失败不阻断字段组装"


# ============================================================
# 6. 单字段 LLM 失败不阻断
# ============================================================

def test_field_llm_failure_nonblocking(monkeypatch):
    import services.ai_field_service as afs

    payload = _payload(desc="", attrs={"颜色": "白色"}, tags=CN_TAGS)
    calls, _, _ = _patch_all(monkeypatch, regenerate_map={
        "title": RU_TITLE,
        "description": None,  # LLM 失败
        "attributes": json.dumps({"Цвет": "Белый"}, ensure_ascii=False),
        "tags": None,  # LLM 失败
    })
    result = afs.assemble_draft(payload, "mxou-key")

    assert result["assembled"] == ["title", "attributes"]
    assert set(result["skipped"]) == {"description", "tags"}
    assert payload["draft"]["title"] == RU_TITLE
    assert payload["draft"]["tags"] == CN_TAGS, "失败的 tags 保持原值"


# ============================================================
# 7. 全失败容错：LLM 异常抛错也不崩（单字段 try/except）
# ============================================================

def test_regenerate_exception_nonblocking(monkeypatch):
    import services.ai_field_service as afs

    payload = _payload(desc="中文描述", attrs={"颜色": "白色"})

    def boom(field, current_value, token, traffic_keywords=None):
        raise RuntimeError("mxou 500")

    _patch_all(monkeypatch)
    monkeypatch.setattr(afs, "regenerate_field", boom)  # 覆盖为抛异常版
    result = afs.assemble_draft(payload, "mxou-key")

    assert set(result["assembled"]) == set()
    assert set(result["skipped"]) == {"title", "description", "attributes", "tags"}

# ============================================================
# 8. service 层：version++ 写回（fake get_draft + fake engine）
# ============================================================

class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConnection:
    def __init__(self, store):
        self.store = store

    def execute(self, stmt, params=None):
        self.store["stmt"] = str(stmt)
        self.store["params"] = params
        return _FakeResult((7,))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeEngine:
    def __init__(self, store):
        self.store = store

    def begin(self):
        return _FakeConnection(self.store)


def test_service_assemble_writes_back_version(monkeypatch):
    from services import draft_service as ds

    fake_draft = {"id": DRAFT_ID, "payload": _payload(desc=""), "version": 6}
    monkeypatch.setattr(ds, "get_draft", lambda tenant, did: fake_draft)
    store: dict = {}
    monkeypatch.setattr(ds, "get_engine", lambda: _FakeEngine(store))

    calls, _, _ = _patch_all(monkeypatch, regenerate_map={"title": RU_TITLE})

    result = ds.assemble_draft("tenant-A", DRAFT_ID, "sk-demo-key")

    assert result["version"] == 7, "version 必须 ++（6 → 7）"
    assert result["assembled"] == ["title"]
    assert "version=version+1" in store["stmt"]
    assert "updated_at=NOW()" in store["stmt"]
    assert store["params"]["tenant_id"] == "tenant-A"
    written = json.loads(store["params"]["payload"])
    assert written["draft"]["title"] == RU_TITLE
    assert written["draft"]["title_source"] == CN_TITLE
    # 调 ai 层前 sk- 前缀已剥离；空描述走合成路径 → description 的 LLM 也被尝试（失败→skipped 不阻断）
    assert calls == ["title", "description"]


# ============================================================
# 9. 端点层：200 结构与 version、404、401、openapi 注册
# ============================================================

class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def body(self):
        return json.dumps(self._body).encode("utf-8")


def _make_client(monkeypatch):
    import main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "get_supabase_client", lambda: None)
    # 不进 with（不触发 lifespan → 不连 PG / 不启动 worker）
    return TestClient(main.app)


def test_endpoint_200_structure_and_version(monkeypatch):
    from services import draft_service as ds

    captured = {}

    def fake_service(tenant_id, draft_id, token):
        captured.update({"tenant_id": tenant_id, "draft_id": draft_id, "token": token})
        return {
            "assembled": ["title", "description"],
            "skipped": ["tags"],
            "suggested_category": dict(CAT_SUGGESTION),
            "estimated_pricing": {"price": 729.0, "old_price": 910.0, "promo_price": 547.0},
            "version": 3,
        }

    monkeypatch.setattr(ds, "assemble_draft", fake_service)
    client = _make_client(monkeypatch)

    resp = client.post(f"/api/v1/drafts/{DRAFT_ID}/assemble", json={"token": "sk-demo"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "assembled": ["title", "description"],
        "skipped": ["tags"],
        "suggested_category": CAT_SUGGESTION,
        "estimated_pricing": {"price": 729.0, "old_price": 910.0, "promo_price": 547.0},
        "version": 3,
    }
    assert captured["draft_id"] == DRAFT_ID
    assert captured["token"] == "sk-demo", "token 原样传 service（sk- 剥离在 service 层）"
    assert captured["tenant_id"], "鉴权必须产出非空 tenant_id"


def test_endpoint_404_unknown_draft(monkeypatch):
    from fastapi import HTTPException
    from services import draft_service as ds

    def fake_get_draft(tenant_id, draft_id):
        raise HTTPException(status_code=404, detail="草稿不存在或无权访问")

    monkeypatch.setattr(ds, "get_draft", fake_get_draft)  # 真实 service 走到 404
    client = _make_client(monkeypatch)

    resp = client.post(f"/api/v1/drafts/{DRAFT_ID}/assemble", json={"token": "sk-demo"})
    assert resp.status_code == 404
    assert "草稿不存在" in resp.json()["detail"]


def test_endpoint_401_without_token(monkeypatch):
    import main
    from fastapi import HTTPException

    def fake_auth(token):
        if not token:
            raise HTTPException(status_code=401, detail="Token is required")
        return "tenant-A"

    monkeypatch.setattr(main, "_authenticate_token", fake_auth)
    client = _make_client(monkeypatch)

    resp = client.post(f"/api/v1/drafts/{DRAFT_ID}/assemble", json={"token": ""})
    assert resp.status_code == 401


def test_endpoint_registered_in_openapi():
    import main

    paths = main.app.openapi()["paths"]
    assert "/api/v1/drafts/{draft_id}/assemble" in paths
    op = paths["/api/v1/drafts/{draft_id}/assemble"]["post"]
    assert op.get("requestBody", {}).get("content", {}).get("application/json"), \
        "raw-body 路由必须 openapi_extra 声明请求体"
