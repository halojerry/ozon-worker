# -*- coding: utf-8 -*-
"""feat/multi-sku-9048-only 任务 2 — clone 选品源名语言前置守卫回归锁定。

双 gate 铁证 2：竞品源卡名拉丁 → worker LOCAL_NAME_LATIN 秒拒终态（克隆零 LLM
不可译，unfixable）；正对照西里尔名竞品复制链路 happy path 存在。守卫前移到
skill 选品边界（判定 lib/name_lang_guard 唯一实现，cloud_probe 只留薄调用点）：
  - 直提（auto_submit 且非 --to-box）：拉丁名 → blocked_reason="latin_name"
    不提交（cli 腿 exit 3）；
  - --allow-latin-name 显式豁免：放行走必拒已知路径（用户知情）；
  - --to-box 入箱 / 展示态：仅警示（人工兜底通道，对齐 _source_preflight 口径）；
  - hand 模式（非 clone）：守卫不生效（主链有翻译通道，零变化）。

运行（纯 mock，无 PG、无网络）：
    cd skill && .venv314/bin/python -m pytest tests/test_clone_name_lang_guard.py -q
"""
import unittest.mock as mock

URL = "https://www.ozon.ru/product/pla-plastic-4929923490/"

LATIN_TITLE = "PLA Plastic Filament 1.75mm for 3D Printer"   # 双 gate 实证形态
CYRILLIC_TITLE = "Пластик PLA для 3D-принтера, 1 кг"          # 正对照：happy path


def _drive(title, *, clone=True, auto_submit=True, allow_latin_name=False, to_box=False):
    """复用 test_follow_preflight_v080 全链路 mock 骨架：注入指定竞品卡名与
    clone/豁免参数，返回 (result, submit_envelope mock, submit_draft mock)。"""
    from scripts import cloud_probe as cp

    cdp_data = {
        "success": True,
        "images": ["http://img/ozon/1.jpg"],
        "title": title,
        "price": "1290",
        "attributes": {},
        "characteristics": [],
        "aspects": [],
    }
    cdp_results = [{"id": "980815374096", "title": "宠物饮水器", "price": "5.5",
                    "image": "http://img/1688/1.jpg", "badge": "符合 3/3 个条件"}]
    best = {"id": "980815374096", "badge_score": 3, "title": "宠物饮水器"}

    def _fake_envelope(*a, **k):
        # draft 需过 _source_preflight（图/属性/采购成本齐），让守卫成为唯一变量
        return {"token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
                "envelope": {"draft": {"item_id": "980815374096", "title": title,
                                       "images": ["https://cbu01.alicdn.com/img/ibank/1.jpg"],
                                       "attributes": {"品牌": "x"}, "purchase_cost": 5.5},
                             "extensions": {}}}

    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set"), \
         mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch.object(cp, "_get_ozon_credentials",
                           return_value={"client_id": "1", "api_key": "k"}), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk"), \
         mock.patch.object(cp, "_cached_ozon_scrape", return_value=cdp_data), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp", return_value=(True, "ok")), \
         mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/profile"), \
         mock.patch("scripts.lib.cdp_client.CdpConnection"), \
         mock.patch("scripts.lib.ozon_seller_analytics.fetch_sales_analytics", return_value={}), \
         mock.patch("scripts.lib.ozon_image_search.search_by_image_cdp", return_value=cdp_results), \
         mock.patch("scripts.lib.ozon_discovery._pick_best_match", return_value=best), \
         mock.patch.object(cp, "build_graph_envelope_with_retry", side_effect=_fake_envelope), \
         mock.patch("scripts.lib.config_store.get_store_profile", return_value={}), \
         mock.patch.object(cp, "submit_envelope") as m_submit, \
         mock.patch.object(cp, "submit_draft") as m_box:
        r = cp.follow_sell_cloud(URL, auto_submit=auto_submit, to_box=to_box,
                                 store_id="s1", clone=clone,
                                 allow_latin_name=allow_latin_name)
    return r, m_submit, m_box


# ═══════════════════════════════════════════════════════════
# 1. 纯函数（lib/name_lang_guard 唯一实现）
# ═══════════════════════════════════════════════════════════

def test_verdict_latin_only_truth_table():
    from scripts.lib.name_lang_guard import name_language_verdict
    # 拉丁名（实证形态）→ 拦截
    v = name_language_verdict(LATIN_TITLE)
    assert v["latin_only"] is True and v["latin_ratio"] > 0.5 and v["cyrillic_count"] == 0
    # 西里尔名（正对照）→ 放行
    v = name_language_verdict(CYRILLIC_TITLE)
    assert v["latin_only"] is False and v["cyrillic_count"] > 0
    # 混排（含西里尔）→ 放行（不拦）
    v = name_language_verdict("PLA пластик 1кг")
    assert v["latin_only"] is False
    # 数字/标点不进占比基数；纯型号数字无字母证据 → fail-open
    v = name_language_verdict("AB-1.75mm 1000")
    assert v["latin_only"] is True, "字母全拉丁（数字标点不计）→ 拦截"
    v = name_language_verdict("12345-!")
    assert v["latin_only"] is False, "无字母无证据 → 放行（worker 终态守卫仍在）"
    # 空/非字符串 → 放行
    assert name_language_verdict("")["latin_only"] is False
    assert name_language_verdict(None)["latin_only"] is False


def test_verdict_ratio_threshold_strict():
    from scripts.lib.name_lang_guard import LATIN_RATIO_THRESHOLD, name_language_verdict
    assert LATIN_RATIO_THRESHOLD == 0.5
    # 字母 1 拉丁 + 2 希腊（非西里尔）→ 占比 0.333 ≤ 50% → 不拦
    v = name_language_verdict("aβγ")
    assert v["latin_only"] is False and v["latin_ratio"] < 0.5
    # 恰好 50% 不拦（>50% 严格）
    v = name_language_verdict("abαβ")
    assert v["latin_only"] is False and v["latin_ratio"] == 0.5


def test_block_reason_message_actionable():
    from scripts.lib.name_lang_guard import latin_name_block_reason
    msg = latin_name_block_reason(LATIN_TITLE)
    assert "LOCAL_NAME_LATIN" in msg
    assert "西里尔" in msg, "可行动指引：换西里尔名竞品"
    assert "--allow-latin-name" in msg
    assert latin_name_block_reason(CYRILLIC_TITLE) == ""
    assert latin_name_block_reason("") == ""


# ═══════════════════════════════════════════════════════════
# 2. follow 腿接线（全链路 mock）
# ═══════════════════════════════════════════════════════════

def test_follow_clone_latin_name_blocked_before_submit():
    """clone 直提 + 拉丁名竞品 → blocked_reason="latin_name"，不提交（默认阻断）。"""
    r, m_submit, m_box = _drive(LATIN_TITLE, clone=True, auto_submit=True)
    assert r.get("blocked_reason") == "latin_name", r.get("blocked_reason")
    assert r.get("success") is False
    assert r.get("competitor_name_language", {}).get("latin_only") is True, "抓到卡名即判留痕"
    m_submit.assert_not_called()
    m_box.assert_not_called()


def test_follow_clone_cyrillic_name_submits_normally():
    """clone 直提 + 西里尔名竞品（正对照）→ 守卫放行，正常提交。"""
    r, m_submit, m_box = _drive(CYRILLIC_TITLE, clone=True, auto_submit=True)
    assert r.get("blocked_reason") is None, r.get("blocked_reason")
    m_submit.assert_called_once()
    m_box.assert_not_called()


def test_follow_clone_latin_name_allow_flag_waives():
    """--allow-latin-name 豁免 → 放行提交（走 worker 必拒已知路径，用户知情）。"""
    r, m_submit, m_box = _drive(LATIN_TITLE, clone=True, auto_submit=True,
                                allow_latin_name=True)
    assert r.get("blocked_reason") is None, "豁免时不置 blocked_reason"
    m_submit.assert_called_once()


def test_follow_hand_mode_latin_name_not_blocked():
    """hand 模式（非 clone）+ 拉丁名 → 守卫不生效（主链有翻译通道，零变化）。"""
    r, m_submit, m_box = _drive(LATIN_TITLE, clone=False, auto_submit=True)
    assert r.get("blocked_reason") is None
    m_submit.assert_called_once()


def test_follow_clone_to_box_latin_name_warns_but_boxes():
    """--to-box 入箱 + 拉丁名 → warning 放行走 submit_draft（人工兜底通道）。"""
    r, m_submit, m_box = _drive(LATIN_TITLE, clone=True, auto_submit=True, to_box=True)
    assert r.get("blocked_reason") is None
    m_box.assert_called_once()
    m_submit.assert_not_called()
