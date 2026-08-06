"""B2+B3+B4 单模块精准测试 — follow_sell_import_node v5
验证：定价移除、TypedDict输出、类目错误传播
运行: cd worker && PYTHONPATH=src python3 tests/test_follow_sell_v5.py
"""

import sys, json, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# ── Mock 依赖 ──
class FakeState:
    """模拟 GlobalState — 只包含 follow_sell_import_node 读取的字段"""
    def __init__(self, envelope, ozon_client_id="123", ozon_api_key="key", currency_code="CNY", token="sk-test"):
        self.envelope = envelope
        self.ozon_client_id = ozon_client_id
        self.ozon_api_key = ozon_api_key
        self.currency_code = currency_code
        self.token = token
        # 节点会写入这些字段（v4: 通过返回 dict 合并）
        self.product_id = None
        self.description_category_id = ""
        self.type_id = ""
        self.competitor_price = ""
        self.competitor_name = ""
        self.original_images = []
        self.variants = []
        self.item_id = ""
        self.final_attributes = []
        self.attributes_schema = []
        self.upload_status = ""
        self.error_message = ""
        self.failed_stage = ""

# Mock Ozon API — 模拟 import-by-sku 返回
_original_req_post = None
_original_time_sleep = None

def _setup_mock_ozon(import_ok=True):
    """mock Ozon API。

    import_ok=True  → import-by-sku 成功（返回 product_id，走 UPDATE）
    import_ok=False → import/info 无 product_id（走 Fallback CREATE）
    """
    import requests as req
    import time as _t
    global _original_req_post, _original_time_sleep
    _original_req_post = req.post
    _original_time_sleep = _t.sleep
    _t.sleep = lambda s: None  # 测试加速：轮询 sleep 置空（v0.22 P2a 轮询 180s）

    class MockResp:
        def __init__(self, status_code=200, json_data=None):
            self.status_code = status_code
            self._json = json_data or {}
        def json(self):
            return self._json

    def mock_post(url, headers=None, json=None, timeout=30):
        if "import-by-sku" in url:
            return MockResp(200, {"result": {"task_id": "12345"}})
        elif "import/info" in url:
            if import_ok:
                return MockResp(200, {"result": {"items": [{"product_id": 999888777, "status": "imported"}]}})
            return MockResp(200, {"result": {"items": []}})
        elif "description-category/attribute" in url:
            return MockResp(200, {"result": [{"id": 8229, "name": "Тип", "is_collection": False}]})
        return MockResp(500)

    req.post = mock_post
    return _original_req_post

def _teardown_mock_ozon():
    import requests as req
    import time as _t
    if _original_req_post:
        req.post = _original_req_post
    if _original_time_sleep:
        _t.sleep = _original_time_sleep


def _mock_resolve_category_success(dc_id, type_name_hint=None, token=None):
    """模拟类目解析成功"""
    return ("17027918", "971311385")

def _mock_resolve_category_fail(dc_id, type_name_hint=None, token=None):
    """模拟类目解析失败"""
    return (None, None)


def _run_node(envelope_override=None):
    """运行 follow_sell_import_node 并返回输出 dict"""
    from graphs.nodes.follow_sell_import_node import follow_sell_import_node
    import graphs.nodes.follow_sell_import_node as mod

    # 注入 mock
    mod._resolve_category_by_id = _mock_resolve_category_success
    mod._resolve_category = lambda dc, tp, language="": _mock_resolve_category_success(0)
    mod._verify_category_schema = lambda cid, akey, dc, tp: True  # v0.26 权威信任：避免真实网络请求

    base_envelope = {
        "draft": {
            "ozon_product_id": "3852000144",
            "title": "Тестовый товар",
            "ozon_title": "Оригинальный тестовый товар",
            "images": ["https://cdn.ozon.ru/img1.jpg", "https://cdn.ozon.ru/img2.jpg"],
            "ozon_category": {
                "description_category_id": "17027918",
                "type_id": "971311385",
                "category_path": "Автозапчасти > Подвеска > Амортизаторы",
            },
            "competitor_price": "2500.00",  # v0.14 起 skill 用 competitor_price（price 是 1688 采购价）
            "price": "85.00",
            "purchase_cost": 85.0,
            "currency": "CNY",
            "weight": 500,
            "dimensions": {"length": 200, "width": 150, "height": 100},
            "item_id": "980815374096",
        },
        "extensions": {"follow_sell": True, "follow_type": "api",
                       "margin_rate": 0.25, "commission_rate": 0.10},
    }
    if envelope_override:
        import copy
        env = copy.deepcopy(base_envelope)
        env.update(envelope_override)
        if "draft" in envelope_override:
            env["draft"].update(envelope_override["draft"])
    else:
        env = base_envelope

    state = FakeState(envelope=env)
    result = follow_sell_import_node(state)
    return result


# ═══════════════════════════════════════════════════════════
# 用例 1: Bug 复现 — 类目解析全部失败（B3）
# ═══════════════════════════════════════════════════════════
def test_case_1_category_failure():
    """复现: Widget ID=99999 在 PG 树中不存在，三层解析均失败 → 应返回 error_message 而非静默降级"""
    print("\n" + "="*60)
    print("用例1 (Bug复现): 类目解析全部失败 → error_message")
    print("="*60)

    import graphs.nodes.follow_sell_import_node as mod
    from graphs.nodes.follow_sell_import_node import follow_sell_import_node
    # 注入失败 mock
    mod._resolve_category_by_id = _mock_resolve_category_fail
    mod._resolve_category = lambda dc, tp, language="": (None, None)

    state = FakeState(envelope={
        "draft": {
            "ozon_product_id": "3852000144",
            "title": "Тест",
            "ozon_title": "Тест",
            "images": ["https://cdn.ozon.ru/img1.jpg"],
            "ozon_category": {
                "description_category_id": "99999",  # ← 不存在的 Widget ID
                "category_path": "Неизвестная > Категория",
            },
            "price": "1000.00",
            "purchase_cost": 50.0,
            "currency": "CNY",
            "weight": 500,
            "dimensions": {"length": 100, "width": 100, "height": 100},
            "item_id": "12345",
        },
        "extensions": {"follow_sell": True, "follow_type": "api"},
    })

    _setup_mock_ozon(import_ok=False)
    try:
        result = follow_sell_import_node(state)
    finally:
        _teardown_mock_ozon()

    # 验证点
    err = result.get("error_message", "")
    failed = result.get("failed_stage", "")

    print(f"  error_message: {err[:100]}")
    print(f"  failed_stage:  {failed}")
    print(f"  category_missing: {result.get('category_missing', 'N/A')}")
    print(f"  dc_id:         {result.get('description_category_id', 'N/A')}")
    print(f"  tp_id:         {result.get('type_id', 'N/A')}")

    checks = [
        ("error_message 含'类目解析失败'", "类目解析失败" in err),
        ("failed_stage='follow_sell_import'", failed == "follow_sell_import"),
        ("description_category_id 为空", not result.get("description_category_id")),
        ("type_id 为空", not result.get("type_id")),
        ("product_id 为空（import 也失败）", not result.get("product_id")),
    ]
    all_pass = True
    for name, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {name}")

    return all_pass


def test_case_1b_import_ok_missing_category():
    """v0.19.1 策略：import-by-sku 成功 → 类目缺失不阻断（官方复制带出），但必须打标可观测。"""
    print("\n" + "="*60)
    print("用例1b: import-by-sku 成功 + 类目缺失 → category_missing=True 不阻断")
    print("="*60)

    import graphs.nodes.follow_sell_import_node as mod
    from graphs.nodes.follow_sell_import_node import follow_sell_import_node
    mod._resolve_category_by_id = _mock_resolve_category_fail
    mod._resolve_category = lambda dc, tp, language="": (None, None)

    state = FakeState(envelope={
        "draft": {
            "ozon_product_id": "3852000144",
            "title": "Тест",
            "ozon_title": "Тест",
            "images": ["https://cdn.ozon.ru/img1.jpg"],
            "ozon_category": {
                "description_category_id": "99999",  # 不存在的 Widget ID
                "category_path": "Неизвестная > Категория",
            },
            "competitor_price": "1000.00",
            "purchase_cost": 50.0,
            "currency": "CNY",
            "weight": 500,
            "dimensions": {"length": 100, "width": 100, "height": 100},
            "item_id": "12345",
        },
        "extensions": {"follow_sell": True, "follow_type": "api"},
    })

    _setup_mock_ozon(import_ok=True)
    try:
        result = follow_sell_import_node(state)
    finally:
        _teardown_mock_ozon()

    print(f"  product_id:      {result.get('product_id')}")
    print(f"  category_missing: {result.get('category_missing')}")
    print(f"  error_message:    {result.get('error_message', '')[:80]}")

    checks = [
        ("product_id='999888777'（import 成功）", result.get("product_id") == "999888777"),
        ("category_missing=True", result.get("category_missing") is True),
        ("error_message 为空（不阻断）", not result.get("error_message")),
        ("failed_stage 为空", not result.get("failed_stage")),
    ]
    all_pass = True
    for name, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {name}")

    return all_pass


def test_case_4_hand_mode_skips_import_by_sku():
    """hand 防侵权跟卖（v0.22）：不调用 import-by-sku（1:1 复制），
    走 CREATE 重建（类目/属性/生图全部重做）。"""
    print("\n" + "="*60)
    print("用例4 (hand 防侵权跟卖): 跳过 import-by-sku → CREATE")
    print("="*60)

    import graphs.nodes.follow_sell_import_node as mod
    from graphs.nodes.follow_sell_import_node import follow_sell_import_node
    mod._resolve_category_by_id = _mock_resolve_category_success
    mod._resolve_category = lambda dc, tp, language="": _mock_resolve_category_success(0)

    import requests as req
    calls = []
    _orig = req.post
    class _Resp2:
        status_code = 200
        def json(self):
            return {"result": {"task_id": "12345"}}
    def _counting_post(url, *a, **k):
        if "import-by-sku" in url:
            calls.append(url)
        return _Resp2()
    req.post = _counting_post
    try:
        env = {
            "draft": {
                "ozon_product_id": "3852000144",
                "title": "Тест",
                "ozon_title": "Тест",
                "images": ["https://cdn.ozon.ru/img1.jpg"],
                "ozon_category": {
                    "description_category_id": "17027918",
                    "type_id": "971311385",
                },
                "purchase_cost": 50.0,
                "currency": "CNY",
                "weight": 500,
                "dimensions": {"length": 100, "width": 100, "height": 100},
                "item_id": "12345",
            },
            "extensions": {"follow_sell": True, "follow_type": "hand"},
        }
        state = FakeState(envelope=env)
        result = follow_sell_import_node(state)
    finally:
        req.post = _orig

    print(f"  import-by-sku 调用次数: {len(calls)}")
    print(f"  product_id: {result.get('product_id')}")
    print(f"  dc_id: {result.get('description_category_id')}")
    print(f"  tp_id: {result.get('type_id')}")

    checks = [
        ("import-by-sku 未被调用（hand 模式）", len(calls) == 0),
        ("product_id 为空（走 CREATE，由上传节点创建）", not result.get("product_id")),
        ("类目保留（CREATE 用竞品类目）", bool(result.get("description_category_id"))),
        ("error_message 为空", not result.get("error_message")),
    ]
    all_pass = True
    for name, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {name}")

    return all_pass


def test_case_5_hand_without_source_falls_back_api():
    """hand 模式但信封缺 1688 货源数据（无 purchase_url/purchase_cost）→
    自动降级 api import-by-sku 复制竞品（不丢单）。"""
    print("\n" + "="*60)
    print("用例5 (hand缺货源自动降级api): 无1688数据 → import-by-sku")
    print("="*60)

    import graphs.nodes.follow_sell_import_node as mod
    from graphs.nodes.follow_sell_import_node import follow_sell_import_node
    mod._resolve_category_by_id = _mock_resolve_category_success
    mod._resolve_category = lambda dc, tp, language="": _mock_resolve_category_success(0)

    import requests as req
    calls = []
    _orig = req.post
    class _Resp2:
        status_code = 200
        def json(self):
            return {"result": {"task_id": "12345"}}
    def _counting_post(url, *a, **k):
        if "import-by-sku" in url:
            calls.append(url)
            # ✅ v0.25: offer_id 统一裸竞品 ID（与 assemble/prepare 一致，防双卡）
            body = k.get("json") or {}
            items = body.get("items") or []
            if items:
                oid = items[0].get("offer_id", "")
                assert oid == "3852000144", f"offer_id 不一致: {oid}"
            return _Resp2()
        if "import/info" in url:
            return type("R", (), {"status_code": 200,
                                  "json": lambda self: {"result": {"items": [{"product_id": 999888777, "status": "imported"}]}}})()
        return _Resp2()
    req.post = _counting_post
    try:
        env = {
            "draft": {
                "ozon_product_id": "3852000144",
                "title": "Тест",
                "ozon_title": "Тест",
                "images": ["https://cdn.ozon.ru/img1.jpg"],
                "ozon_category": {
                    "description_category_id": "17027918",
                    "type_id": "971311385",
                },
                # 无 purchase_url / purchase_cost → 无 1688 货源
                "currency": "CNY",
                "weight": 500,
                "dimensions": {"length": 100, "width": 100, "height": 100},
                "item_id": "12345",
            },
            "extensions": {"follow_sell": True, "follow_type": "hand"},
        }
        state = FakeState(envelope=env)
        result = follow_sell_import_node(state)
    finally:
        req.post = _orig

    print(f"  import-by-sku 调用次数: {len(calls)}")
    print(f"  product_id: {result.get('product_id')}")

    checks = [
        ("import-by-sku 被调用（缺货源自动降级 api）", len(calls) == 1),
        ("product_id='999888777'（复制成功）", result.get("product_id") == "999888777"),
        ("error_message 为空", not result.get("error_message")),
    ]
    all_pass = True
    for name, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {name}")

    return all_pass


# ═══════════════════════════════════════════════════════════
# 用例 2: 正常场景 — 跟卖完整流程（B2+B4）
# ═══════════════════════════════════════════════════════════
def test_case_2_normal_follow_sell():
    """验证: 正常跟卖流程 → 返回 TypedDict 含所有必要字段，定价信息不在此节点"""
    print("\n" + "="*60)
    print("用例2 (正常场景): 完整跟卖 → TypedDict 输出")
    print("="*60)

    _setup_mock_ozon()
    try:
        result = _run_node()
    finally:
        _teardown_mock_ozon()

    # 验证点
    print(f"  product_id:     {result.get('product_id')}")
    print(f"  dc_id:          {result.get('description_category_id')}")
    print(f"  tp_id:          {result.get('type_id')}")
    print(f"  competitor_price: {result.get('competitor_price')}")
    print(f"  competitor_name:  {result.get('competitor_name', '')[:50]}")
    print(f"  error_message:    {result.get('error_message', '')[:50]}")
    print(f"  final_attributes count: {len(result.get('final_attributes', []))}")
    print(f"  attributes_schema count: {len(result.get('attributes_schema', []))}")

    # ⚠️ 核心验证: pricing_info 不应在此节点返回（B2: 定价统一走 pricing_node）
    has_pricing_info = "pricing_info" in result and result.get("pricing_info")
    print(f"  pricing_info in result: {has_pricing_info} (预期: False — B2已移除)")

    checks = [
        ("product_id='999888777'", result.get("product_id") == "999888777"),
        ("description_category_id 非空", bool(result.get("description_category_id"))),
        ("type_id 非空", bool(result.get("type_id"))),
        ("competitor_price='2500.00'", result.get("competitor_price") == "2500.00"),
        ("final_attributes 有5个", len(result.get("final_attributes", [])) == 5),
        ("attributes_schema 非空", len(result.get("attributes_schema", [])) > 0),
        ("pricing_info 不在输出中（B2已移除）", not has_pricing_info),
        ("error_message 为空", not result.get("error_message")),
        ("failed_stage 为空", not result.get("failed_stage")),
    ]
    all_pass = True
    for name, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {name}")

    return all_pass


# ═══════════════════════════════════════════════════════════
# 用例 3: 边界 — ozon_product_id 为空（防复）
# ═══════════════════════════════════════════════════════════
def test_case_3_empty_product_id():
    """防复: ozon_product_id 为空 → 立即返回 error，不发起任何 API 调用"""
    print("\n" + "="*60)
    print("用例3 (边界防复): ozon_product_id 为空 → 立即阻断")
    print("="*60)

    state = FakeState(envelope={
        "draft": {
            # ozon_product_id 缺失
            "title": "Тест",
            "images": [],
        },
        "extensions": {"follow_sell": True},
    })

    from graphs.nodes.follow_sell_import_node import follow_sell_import_node
    result = follow_sell_import_node(state)

    print(f"  error_message: {result.get('error_message', '')}")
    print(f"  failed_stage:  {result.get('failed_stage', '')}")

    checks = [
        ("error_message 含'ozon_product_id'", "ozon_product_id" in result.get("error_message", "")),
        ("failed_stage='follow_sell_import'", result.get("failed_stage") == "follow_sell_import"),
        ("product_id 为 None", result.get("product_id") is None),
    ]
    all_pass = True
    for name, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {name}")

    return all_pass


# ═══════════════════════════════════════════════════════════
# 用例 6: import-by-sku 超时 → 不 fallback CREATE（防双卡闭环，v0.22 P2a）
# ═══════════════════════════════════════════════════════════
def test_case_6_import_timeout_no_fallback_create():
    """import-by-sku 已提交但轮询超时 → 不 fallback CREATE：返回 pending + import_submitted，
    不调用 v3/product/import（防双卡）。"""
    import graphs.nodes.follow_sell_import_node as mod
    from graphs.nodes.follow_sell_import_node import follow_sell_import_node
    mod._resolve_category_by_id = _mock_resolve_category_success
    mod._resolve_category = lambda dc, tp, language="": _mock_resolve_category_success(0)

    import requests as req
    import time as _t
    calls = []
    _orig_post = req.post
    _orig_sleep = _t.sleep
    class _Resp3:
        status_code = 200
        def json(self):
            return {"result": {"task_id": "12345", "unmatched_sku_list": []}}
    class _RespInfo:
        status_code = 200
        def json(self):
            return {"result": {"items": []}}  # 永不 imported
    def _counting_post(url, *a, **k):
        if "import-by-sku" in url:
            calls.append("ibs")
            return _Resp3()
        if "import/info" in url:
            calls.append("info")
            return _RespInfo()
        if "product/import" in url:
            calls.append("create")  # v3 import = CREATE/UPDATE，必须不被调用
        return _Resp3()
    req.post = _counting_post
    _t.sleep = lambda s: None  # 加速轮询
    try:
        env = {
            "draft": {"ozon_product_id": "3852000144", "title": "T", "ozon_title": "T",
                      "images": ["https://cdn.ozon.ru/1.jpg"],
                      "ozon_category": {"description_category_id": "17027918", "type_id": "971311385"},
                      "purchase_cost": 50.0, "currency": "CNY", "weight": 500,
                      "dimensions": {"length": 100, "width": 100, "height": 100},
                      "item_id": "12345"},
            "extensions": {"follow_sell": True, "follow_type": "api"},
        }
        state = FakeState(envelope=env)
        result = follow_sell_import_node(state)
    finally:
        req.post = _orig_post
        _t.sleep = _orig_sleep
    assert "create" not in calls, "超时不应 fallback CREATE"
    assert result.get("import_submitted") is True
    assert result.get("upload_status") == "pending"
    assert result.get("import_task_id") == "12345"
    return True


# ═══════════════════════════════════════════════════════════
# 用例 7/8: v0.26 wave1 盘子类目修复
# ═══════════════════════════════════════════════════════════
def test_case_7_brand_leaf_category_fallback():
    """v0.26: RU 面包屑末段是品牌名（Canevia，纯拉丁无西里尔）→
    _resolve_category_by_id 应追加「去品牌」候选（品牌前一段 Тарелки），
    而不是只试含品牌的完整路径导致全部失败（wave1 盘子 created=False 根因）。"""
    from unittest import mock
    import importlib
    import graphs.nodes.follow_sell_import_node as mod
    # ⚠️ 前序用例（1/4/5/8）会污染 mod._resolve_category_by_id/_resolve_category
    # mock 且不恢复 → reload 模块恢复真实函数（v0.26 测试隔离修复）
    mod = importlib.reload(mod)

    tried: list[str] = []
    def _capture_resolve(dc, tp, language="RU"):
        tried.append(str(dc))
        if str(dc) == "Тарелки":  # 品牌前一段命中
            return ("17027910", "92532")
        return ("", "")

    class _FakeQuery:
        def get_node_by_description_category_id(self, dc_id):
            return None  # 数字 ID 直查失败（Widget ID 不在 Seller 树）

    orig_resolve = mod._resolve_category
    mod._resolve_category = _capture_resolve
    import utils.ozon_category_query as _ocq
    try:
        with mock.patch.object(_ocq, "get_category_query", return_value=_FakeQuery()):
            result = mod._resolve_category_by_id(
                102080114,
                type_name_hint="Дом и сад > Посуда и кухонные принадлежности > "
                               "Одноразовая посуда и скатерти > Тарелки > Canevia",
            )
    finally:
        mod._resolve_category = orig_resolve

    print(f"  尝试候选: {tried}")
    checks = [
        ("候选含 'Тарелки'（品牌前一段，去品牌兜底）", "Тарелки" in tried),
        ("解析结果非空（不再全候选失败）", bool(result[0])),
        ("dc=17027910", result[0] == "17027910"),
        ("type=92532", result[1] == "92532"),
    ]
    all_pass = True
    for name, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {name}")
    return all_pass


def test_case_8_hand_category_fail_falls_back_api():
    """v0.26: hand 模式类目解析失败（dc/tp 空）→ 自动降级 api import-by-sku
    （官方复制带出完整类目，不硬失败；wave1 盘子 follow_type=hand + Canevia
    解析失败 → created=False 实证）。"""
    import graphs.nodes.follow_sell_import_node as mod

    mod._resolve_category_by_id = _mock_resolve_category_fail
    mod._resolve_category = lambda dc, tp, language="": (None, None)
    # v0.26 权威类目自校验：Widget 无效 ID → 返回 False → 回退二次解析
    mod._verify_category_schema = lambda cid, akey, dc, tp: False

    import requests as req
    calls = []
    _orig = req.post
    class _Resp8:
        status_code = 200
        def json(self):
            return {"result": {"task_id": "12345"}}
    class _RespInfo8:
        status_code = 200
        def json(self):
            return {"result": {"items": [{"product_id": 999888777, "status": "imported"}]}}
    def _counting_post8(url, *a, **k):
        if "import-by-sku" in url:
            calls.append("ibs")
            return _Resp8()
        if "import/info" in url:
            return _RespInfo8()
        return _Resp8()
    req.post = _counting_post8
    try:
        env = {
            "draft": {
                "ozon_product_id": "4474160291",
                "title": "Одноразовые тарелки",
                "ozon_title": "Одноразовые тарелки из багассы",
                "images": ["https://cdn.ozon.ru/img1.jpg"],
                "ozon_category": {
                    "description_category_id": "102080114",  # Widget ID，直查失败
                    "type_id": "102080114",
                    "category_path": "Дом и сад > Посуда > Тарелки > Canevia",
                },
                "purchase_cost": 2.4,
                "purchase_url": "https://detail.1688.com/offer/840720791119.html",
                "currency": "CNY",
                "weight": 160,
                "dimensions": {"length": 100, "width": 100, "height": 10},
                "item_id": "840720791119",
            },
            "extensions": {"follow_sell": True, "follow_type": "hand"},
        }
        from graphs.nodes.follow_sell_import_node import follow_sell_import_node
        state = FakeState(envelope=env)
        result = follow_sell_import_node(state)
    finally:
        req.post = _orig

    print(f"  import-by-sku 调用: {len(calls)}")
    print(f"  product_id: {result.get('product_id')}")

    checks = [
        ("import-by-sku 被调用（类目失败自动降级 api）", len(calls) == 1),
        ("product_id='999888777'（复制成功）", result.get("product_id") == "999888777"),
        ("error_message 为空（不硬失败）", not result.get("error_message")),
    ]
    all_pass = True
    for name, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {name}")
    return all_pass


# ═══════════════════════════════════════════════════════════
# 用例 9: v0.26 权威类目信任（what_to_sell 组合不二次解析）
# ═══════════════════════════════════════════════════════════
def test_case_9_authoritative_category_trusted():
    """v0.26 时序根因修复：skill 已从 what_to_sell 拿到权威组合
    dc=17028990/type=93418（schema 验证有效），worker 必须直接信任、
    不得二次解析（否则 _resolve_category_by_id 数字直查返回该 dc 下
    第一个 type=93409 覆盖权威 93418 → 类目又错 → DESCRIPTION_DECLINE）。"""
    import importlib
    import graphs.nodes.follow_sell_import_node as mod
    mod = importlib.reload(mod)  # 恢复真实函数（前序用例污染）

    called_by_id = []
    mod._verify_category_schema = lambda cid, akey, dc, tp: True  # 权威组合 → 校验通过
    mod._resolve_category_by_id = lambda dc_id, type_name_hint="", token="": (
        called_by_id.append(dc_id) or ("17028990", "93409")  # 若被调=bug，返回错误 type
    )
    mod._resolve_category = lambda dc, tp, language="": (None, None)

    import requests as req
    _orig = req.post
    class _Resp9:
        status_code = 200
        def json(self):
            return {"result": {"task_id": "12345"}}
    def _counting_post9(url, *a, **k):
        if "import-by-sku" in url:
            return _Resp9()
        if "import/info" in url:
            return type("R", (), {"status_code": 200,
                                  "json": lambda self: {"result": {"items": [{"product_id": 999888777, "status": "imported"}]}}})()
        return _Resp9()
    req.post = _counting_post9
    try:
        env = {
            "draft": {
                "ozon_product_id": "4401035335",
                "title": "Маркер для бровей",
                "ozon_title": "Маркер для бровей двойной наконечник",
                "images": ["https://cdn.ozon.ru/img1.jpg"],
                "ozon_category": {
                    "description_category_id": "17028990",  # 权威 dc（what_to_sell category2）
                    "type_id": "93418",                     # 权威 type（what_to_sell category3）
                    "language": "RU",
                    "category_path": "Красота и здоровье > Макияж > Брови > Карандаши и лайнеры",
                },
                "purchase_cost": 3.99,
                "purchase_url": "https://detail.1688.com/offer/955730486059.html",
                "currency": "CNY",
                "weight": 100,
                "dimensions": {"length": 69, "width": 52, "height": 35},
                "item_id": "955730486059",
            },
            "extensions": {"follow_sell": True, "follow_type": "hand"},
        }
        from graphs.nodes.follow_sell_import_node import follow_sell_import_node
        state = FakeState(envelope=env)
        result = follow_sell_import_node(state)
    finally:
        req.post = _orig

    print(f"  _resolve_category_by_id 调用: {called_by_id}")
    print(f"  dc_id: {result.get('description_category_id')}")
    print(f"  tp_id: {result.get('type_id')}")

    checks = [
        ("_resolve_category_by_id 未被调（权威信任）", len(called_by_id) == 0),
        ("dc=17028990 保留（未被覆盖）", result.get("description_category_id") == "17028990"),
        ("type=93418 保留（未被覆盖成 93409）", result.get("type_id") == "93418"),
        ("error_message 为空", not result.get("error_message")),
    ]
    all_pass = True
    for name, passed in checks:
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} {name}")
    return all_pass


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("follow_sell_import_node v5 — 单模块精准测试")
    print("  B2: 定价移除 | B3: 类目错误传播 | B4: TypedDict 输出")
    print("=" * 60)

    results = {
        "用例1 (Bug复现-类目失败)": test_case_1_category_failure(),
        "用例1b (import成功-类目缺失打标)": test_case_1b_import_ok_missing_category(),
        "用例4 (hand防侵权跟卖)": test_case_4_hand_mode_skips_import_by_sku(),
        "用例5 (hand缺货源降级api)": test_case_5_hand_without_source_falls_back_api(),
        "用例6 (import超时不fallback CREATE)": test_case_6_import_timeout_no_fallback_create(),
        "用例2 (正常-跟卖流程)": test_case_2_normal_follow_sell(),
        "用例3 (边界-空product_id)": test_case_3_empty_product_id(),
        "用例7 (品牌末段类目兜底)": test_case_7_brand_leaf_category_fallback(),
        "用例8 (hand类目失败降级api)": test_case_8_hand_category_fail_falls_back_api(),
        "用例9 (权威类目信任)": test_case_9_authoritative_category_trusted(),
    }

    print("\n" + "=" * 60)
    print("测试结论")
    print("=" * 60)
    all_pass = all(results.values())
    for name, passed in results.items():
        print(f"  {'✅' if passed else '❌'} {name}")
    print(f"\n  结论: {'全部通过 ✅' if all_pass else '存在失败 ❌'}")
    sys.exit(0 if all_pass else 1)
