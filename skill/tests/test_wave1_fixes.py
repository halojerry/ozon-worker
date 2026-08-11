"""v0.26 wave1 修复回归测试（skill 侧）— 空壳拦截 / LLM 语义判定 / 凭证脱敏 / 图搜 title 清洗。

运行（skill venv）：
    cd skill && .venv314/bin/python tests/test_wave1_fixes.py
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── 修4: 凭证脱敏（cli._redact_keys）──

def test_redact_keys_masks_credentials():
    from scripts.cli import _redact_keys
    obj = {
        "ok": True,
        "envelope": {
            "ozon_client_id": "4718259",
            "ozon_api_key": "cd1d0a10-181a-42a1-8895-8508bb0513d7",
            "token": "Ccpo3ziBuPH6daniA13XPDPGRem7m9OqsXPGZWvA5xK3eJyL",
        },
        "summary": {"title": "Тест"},
    }
    _redact_keys(obj, {"api_key", "token", "ozon_api_key", "mxou_token", "ak", "ali_1688_ak"})
    assert obj["envelope"]["ozon_api_key"] == "cd1d****", obj["envelope"]["ozon_api_key"]
    assert obj["envelope"]["token"] == "Ccpo****"
    assert "cd1d0a10" not in str(obj), "完整 api_key 不应出现"
    assert obj["envelope"]["ozon_client_id"] == "4718259", "client_id 不脱敏（半公开）"
    assert obj["summary"]["title"] == "Тест", "非凭证字段不动"


# ── 修1: 空壳拦截（follow_sell_cloud no_relevant_match → blocked）──

def test_no_relevant_match_blocks_envelope():
    """图搜无货源 → 不再组装 api_fallback 空壳信封。"""
    import scripts.cloud_probe as cp
    result = {"no_relevant_match": True, "ozon_images": ["http://img/1.jpg"],
              "ozon_title": "Тест", "ozon_category": {"description_category_id": "1", "type_id": "1"}}
    # 直接验证: follow_sell_cloud 的 no_relevant_match 分支已改为拦截
    # （原逻辑会组装 envelope + api_fallback=True；现改为 blocked_reason）
    assert "api_fallback" in open(os.path.join(os.path.dirname(cp.__file__), "cloud_probe.py"), encoding="utf-8").read()
    # 检查分支: no_relevant_match 时设置 blocked_reason 而非组装
    src = open(os.path.join(os.path.dirname(cp.__file__), "cloud_probe.py"), encoding="utf-8").read()
    assert 'result["blocked_reason"] = "no_relevant_match"' in src
    assert "import-by-sku 复制竞品卡片" not in src.split("elif result.get(\"no_relevant_match\")")[1].split("# ⚠️ v0.14 E5")[0], \
        "api_fallback 组装逻辑应被删除"


# ── 修3: LLM 语义判定（_llm_semantic_match）──

def test_llm_semantic_match_yes():
    from scripts.lib.ozon_discovery import _llm_semantic_match, _LLM_SEMANTIC_CACHE
    _LLM_SEMANTIC_CACHE.clear()
    class _R:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "YES"}}]}
    with mock.patch("requests.post", return_value=_R()) as m:
        assert _llm_semantic_match("Палочки от комаров 30 шт", "跨境驱蚊香薰棒竹签香", token="t") is True
        m.assert_called_once()


def test_llm_semantic_match_no_and_cache():
    from scripts.lib.ozon_discovery import _llm_semantic_match, _LLM_SEMANTIC_CACHE
    _LLM_SEMANTIC_CACHE.clear()
    class _R:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "NO"}}]}
    with mock.patch("requests.post", return_value=_R()) as m:
        assert _llm_semantic_match("Палочки от комаров", "硅胶手机壳", token="t") is False
        # 缓存: 第二次调用不再发请求
        assert _llm_semantic_match("Палочки от комаров", "硅胶手机壳", token="t") is False
        assert m.call_count == 1


def test_llm_semantic_match_fail_safe():
    """LLM 失败（无 token/网络异常）→ False（维持原拒绝，不放行错误匹配）。"""
    from scripts.lib.ozon_discovery import _llm_semantic_match
    assert _llm_semantic_match("Тест", "测试", token="") is False
    with mock.patch("requests.post", side_effect=Exception("boom")):
        assert _llm_semantic_match("Тест", "测试", token="t") is False


# ── 修3b: top-N LLM 兜底（best 不相关但后面有同品候选 → 救回）──

def test_pick_best_match_llm_rescue_topN():
    """图搜 best 是不相关高分项（檀香贡香），第 3 个候选是同品（驱蚊棒）
    → top-N LLM 兜底应返回第 3 个候选（修"匹配了却不选"根因）。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()

    ozon_title = "Палочки от комаров инсектицидные ароматические палочки 30 шт"
    results = [
        # badge 空 + 无价（被过滤）
        {"id": "0", "title": "无烟檀香开花香卷钱香佛香", "price": 0, "badge": ""},
        # best: 无徽标、有价、标题不相关（贡香）
        {"id": "1", "title": "花开富贵香开花香檀香供佛香纯檀香招财香", "price": 8.0, "badge": ""},
        # 同品：驱蚊棒（LLM 判定 YES）
        {"id": "2", "title": "跨境驱蚊香薰棒竹签香户外防蚊香", "price": 15.0, "badge": ""},
    ]
    # mock LLM: 仅对"驱蚊香薰棒"判定 True（含进程内缓存，先清空）
    real_fn = od._llm_semantic_match
    def fake_llm(oz, cn, token=""):
        return "驱蚊" in cn
    with mock.patch.object(od, "_llm_semantic_match", side_effect=fake_llm):
        best = od._pick_best_match(results, ozon_title, token="t")
    assert best is not None, "top-N LLM 兜底应救回同品候选"
    assert best["id"] == "2", f"应返回同品候选 id=2，实际 {best}"


def test_pick_best_match_llm_rescue_all_false_still_rejects():
    """top-N 全判定 False（真无同款）→ 仍拒绝（宁缺毋滥不破坏）。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    ozon_title = "Палочки от комаров"
    results = [
        {"id": "1", "title": "花开富贵香开花香檀香供佛香", "price": 8.0, "badge": ""},
        {"id": "2", "title": "无烟檀香开花香卷钱香佛香", "price": 9.0, "badge": ""},
    ]
    with mock.patch.object(od, "_llm_semantic_match", return_value=False):
        best = od._pick_best_match(results, ozon_title, token="t")
    assert best is None, "全 False 应拒绝"


# ── 修5: 徽标降级（badge 不可靠，图搜顺序/图片符合度为主）──

def test_pick_best_match_badge_deprioritized():
    """v0.26 徽标降级: 图搜第 1 位（图片符合度最高）无徽标候选应胜过
    第 2 位徽标强匹配候选（旧分制 badge×40 会让第 2 位赢，新分制图搜顺序为主）。
    标题相关性弱时仍走 LLM 语义判定护栏（判同品 → 放行第 1 位）。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    ozon_title = "Палочки от комаров 30 шт"
    results = [
        # 图搜第 1 位: 无徽标、有价、与竞品同品（LLM 判 YES）
        {"id": "1", "title": "跨境驱蚊香薰棒竹签香户外防蚊香", "price": 12.0, "badge": ""},
        # 图搜第 2 位: 徽标"符合 2/3"（badge_eff=0.67），但标题是不同品（蚊香盘）
        {"id": "2", "title": "蚊香器灭蚊盘家用驱蚊香", "price": 10.0, "badge": "符合 2/3 个条件"},
    ]
    with mock.patch.object(od, "_llm_semantic_match", return_value=True):
        best = od._pick_best_match(results, ozon_title, token="t")
    assert best is not None
    assert best["id"] == "1", f"图搜第 1 位（图片符合度最高）应胜出，实际 {best}"


# ── 修7: 密度荒谬时保留商家重量（v0.26，尺寸脏数据不砍重量）──

def test_density_absurd_volume_preserves_merchant_weight():
    """一次性盘子 160g / 10×10×10mm（1cm³）→ 保留 160g + 重估尺寸，
    不再被密度护栏砍成 50g（运费/售价/利润全错的根因）。"""
    from scripts.cloud_probe import _validate_and_fix_product_data
    weight, dims, errors, estimated, weight_estimated = _validate_and_fix_product_data(
        item_id="840720791119", title="一次性盘子", cost_cny=2.4,
        images=["http://img/1.jpg"], weight_g=160,
        dimensions={"length": 10, "width": 10, "height": 10},
        variants=[], option_groups=[],
    )
    assert weight == 160, f"应保留商家重量 160g，实际 {weight}"
    assert errors == []
    assert estimated is True, "尺寸应标记为估算值"
    v = dims["length"] * dims["width"] * dims["height"]
    assert v >= 3000, f"重估尺寸体积不应荒谬: {dims}"


def test_density_genuine_heavy_preserves_weight():
    """体积合理（≥10cm³）且密度>10（实心铅块）→ v0.37 A3: 保留商家重量，
    标记 weight_estimated（旧逻辑按体积×1.0 改写成 100g 销毁真实值）。"""
    from scripts.cloud_probe import _validate_and_fix_product_data
    weight, dims, errors, estimated, weight_estimated = _validate_and_fix_product_data(
        item_id="t1", title="实心铅块", cost_cny=1.0,
        images=["http://img/1.jpg"], weight_g=5000,
        dimensions={"length": 100, "width": 50, "height": 20},  # 100cm³
        variants=[], option_groups=[],
    )
    assert weight == 5000, f"v0.37 A3: 应保留商家重量 5000g，实际 {weight}"
    assert dims["length"] == 100
    assert weight_estimated is True, "应标记 weight_estimated（密度存疑但保留真实值）"


# ── 修8: seller analytics 借道 + 分段佣金解析（毛子 CROSS_TAB 对照）──

def test_analytics_segmented_commission_parsed():
    """v0.26: fbp_rate/rfbs_rate 是分段对象 {fbp_leq_1500, fbp_leq_5000, fbp_gt_5000}，
    旧代码 _to_float(dict) → 恒 0（毛子实测根因）。分段解析应取中间段。"""
    from scripts.lib.ozon_seller_analytics import _extract_metrics
    item = {
        "soldCount": "120",
        "soldSum": "5000",
        "fbp_rate": {"fbp_leq_1500": 0.15, "fbp_leq_5000": 0.12, "fbp_gt_5000": 0.09},
        "rfbs_rate": {"rfbs_leq_5000": 0.08},
        "attributes": [{"id": 4497, "value": "250"}, {"id": 9454, "value": "10"}, {"id": 9456, "value": "20"}],
    }
    m = _extract_metrics(item)
    assert m["sold_count"] == 120, m
    assert m["commission_fbp"] == 0.12, f"应取中间段 0.12，实际 {m['commission_fbp']}"
    assert m["commission_rfbs"] == 0.08, m["commission_rfbs"]
    assert m["weight_g"] == 250
    assert m["length_mm"] == 10.0
    assert m["height_mm"] == 20.0


def test_analytics_scalar_commission_fallback():
    """标量佣金（旧结构）仍兼容。"""
    from scripts.lib.ozon_seller_analytics import _extract_metrics
    m = _extract_metrics({"soldCount": "5", "fbpRate": 0.2})
    assert m["commission_fbp"] == 0.2, m["commission_fbp"]
    assert m["sold_count"] == 5


def test_seller_analytics_js_has_credentials():
    """v0.26: 借道 JS 必须带 credentials:'include'（毛子对照，分区 cookie 关键）。"""
    from scripts.lib import ozon_seller_analytics as osa
    assert "credentials: 'include'" in osa._SELLER_ANALYTICS_JS, \
        "fetch 缺 credentials:'include' → 分区 cookie 可能不带上"
    # 非 2xx 时返回带 status 的错误（不再吞 401/403/429）
    assert "HTTP \" + resp.status" in osa._SELLER_ANALYTICS_JS


# ── 修9: premium 解锁注入（学习上品帮 ozon_min.js XHR/fetch 深度拦截）──

def test_premium_unlock_js_structure():
    """v0.26: premium 解锁脚本应幂等 + 拦截 premium/status 与 graphs + 伪造
    PREMIUM_PLUS 权限（上品帮机制移植，仅本地自用）。"""
    from scripts.lib import ozon_seller_analytics as osa
    js = osa._PREMIUM_UNLOCK_JS
    assert "__OZON_PREMIUM_UNLOCK__" in js, "应幂等（防重复安装）"
    assert r"\/premium\/status" in js, "应拦截 premium/status"
    assert r"\/analytics\/graphs" in js and r"\/statistics\/data" in js, "应拦截 graphs 类接口"
    assert "PREMIUM_PLUS" in js, "应伪造 PREMIUM_PLUS 权限"
    assert "window.XMLHttpRequest = UnlockXHR" in js, "应深度拦截 XHR"
    assert "new Response" in js, "应拦截 fetch（返回伪造 Response）"
    assert "features" in js and "analytics: \"full\"" in js, "应伪造全量功能权限"


def test_premium_unlock_installed_in_flow():
    """v0.26: 借道流程必须安装 premium 解锁（复用 Tab 运行时注入 / 新建预注入）。"""
    from scripts.lib import ozon_seller_analytics as osa
    src = open(os.path.join(os.path.dirname(osa.__file__), "ozon_seller_analytics.py"), encoding="utf-8").read()
    assert "_install_premium_unlock(reused, reused=True)" in src, "复用 Tab 应运行时注入"
    assert "tab.add_init_script(_PREMIUM_UNLOCK_JS)" in src, "新建 Tab 应导航前预注入"


# ── 修10: 竞品运营数据真机验证（登录 seller 后 what_to_sell 对任意 SKU 可查）──

def test_extract_metrics_real_competitor_item():
    """v0.26 真机验证（2026-08-05，seller.ozon.ru 已登录）：
    what_to_sell/data/v3 对竞品 SKU 2485870561 返回真实运营数据——
    月销量 5931 / 月销售额 122万₽ / 广告占比 19.63% / 退货率 3.5%。
    用真实 item 结构固化解析（竞品视图无重量/尺寸/佣金字段 → None 不崩）。"""
    from scripts.lib.ozon_seller_analytics import _extract_metrics
    real_item = {
        "variantId": 2485870561, "soldCount": "5931", "minSellerPrice": 15.36,
        "salesDynamics": -22.8, "stock": "4542", "gmvSum": 1220223.012,
        "qtyViewPdp": "52344", "pdpToCartConversion": 23.36,
        "skuName": "NEATIFY Fridge Odor Absorber", "sellerName": "BONNMM",
        "sessionCountSearch": "51158", "convToCartSearch": 2.16,
        "drr": 19.63, "nullableRedemptionRate": 96.5,
        "nullableCreateDate": "2025-07-17T00:00:00Z", "views": "2057028",
        "convViewToOrder": 0.288, "avgOrdersOnAccDays": 219.885,
        "avgGmvOnAccDays": 45425.611,
    }
    m = _extract_metrics(real_item)
    assert m["sold_count"] == 5931, f"月销量应 5931，实际 {m['sold_count']}"
    assert abs(m["gmv_sum"] - 1220223.012) < 0.01, m["gmv_sum"]
    assert abs(m["sales_dynamics"] - (-22.8)) < 0.01, m["sales_dynamics"]
    assert abs(m["drr"] - 19.63) < 0.01, m["drr"]
    assert m["create_date"] == "2025-07-17T00:00:00Z", m["create_date"]
    # 竞品视图无重量/尺寸/佣金字段 → 缺省不崩（1688 数据兜底）
    assert not m.get("weight_g"), m
    assert not m.get("length_mm"), m
    assert not m.get("commission_fbp"), m
    assert m["has_sales_data"] is True


# ── 修11: 竞品权威类目透出（what_to_sell category2/3 → 覆盖 Widget 面包屑）──

def test_extract_metrics_category_ids():
    """v0.26: what_to_sell 返回 Seller 空间权威类目 category1/2/3Id，
    _extract_metrics 必须解析（wave2 眉笔 dc=6522 Widget ID → 权威 dc=17028990/type=93418）。"""
    from scripts.lib.ozon_seller_analytics import _extract_metrics
    item = {
        "soldCount": "5931", "category1Id": "17027489",
        "category2Id": "17028990", "category3Id": "93418",
    }
    m = _extract_metrics(item)
    assert m["category1_id"] == 17027489, m
    assert m["category2_id"] == 17028990, m
    assert m["category3_id"] == 93418, m


def test_follow_uses_authoritative_category():
    """v0.26 时序根因修复：follow 拿到 what_to_sell 权威类目（category2=dc, category3=type）
    必须覆盖页面面包屑的 Widget 空间 ID（否则 worker pg_trgm 猜 sim=0.353 误匹配）。"""
    from scripts.lib import ozon_seller_analytics as osa
    # 1. _extract_metrics 解析出权威类目
    m = osa._extract_metrics({
        "soldCount": "10", "category1Id": "17027489",
        "category2Id": "17028990", "category3Id": "93418",
    })
    assert m["category2_id"] == 17028990 and m["category3_id"] == 93418
    # 2. cloud_probe follow 流程必须有「权威类目覆盖面包屑」逻辑
    src = open(os.path.join(os.path.dirname(osa.__file__), "..", "cloud_probe.py"), encoding="utf-8").read()
    assert 'result["ozon_category"] = {' in src, "follow 应有权威类目覆盖"
    assert '"description_category_id": str(_m["category2_id"])' in src, "应把 category2 当 dc"
    assert '"type_id": str(_m["category3_id"])' in src, "应把 category3 当 type"


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
