# -*- coding: utf-8 -*-
"""fix/handover-batch-v1 — follow 腿补 _source_preflight（09-findings #5 收尾）回归锁定。

此前仅 graph 腿接线 preflight：反爬页抓到 46 图 0 属性、或 1688 源失效
（purchase_cost≤0）时 follow 直提不受拦。本批对齐 graph 腿口径：
  - 直提（auto_submit 且非 --to-box）：拦截 blocked_reason=source_preflight，不提交；
  - --to-box 入箱：warning 放行（人工兜底通道）；
  - 展示态：仅警示。

运行（纯 mock，无 PG、无网络）：
    cd skill && ../skill/.venv314/bin/python -m pytest tests/test_follow_preflight_v080.py -q
    （仓库根口径：.venv314/bin/python -m pytest tests/test_follow_preflight_v080.py -q）
"""
import unittest.mock as mock

URL = "https://www.ozon.ru/product/avtopoilka-4767514314/"

# 反爬/源失效疑点 draft（无 attributes、无 purchase_cost → _source_preflight 判源失效）
BAD_DRAFT = {"item_id": "980815374096"}
# 合格 draft（图/属性/采购成本齐 → preflight 通过）
OK_DRAFT = {"item_id": "980815374096", "title": "宠物饮水器",
            "images": ["https://cbu01.alicdn.com/img/ibank/1.jpg"],
            "attributes": {"品牌": "x"}, "purchase_cost": 5.5}


def _drive(draft, *, auto_submit, to_box):
    """复用 test_follow_cache 的全链路 mock 骨架，注入指定 draft 与提交 mock。"""
    from scripts import cloud_probe as cp

    cdp_data = {
        "success": True,
        "images": ["http://img/ozon/1.jpg"],
        "title": "Автопоилка",
        "price": "1290",
        "attributes": {},
        "characteristics": [],
        "aspects": [],
    }
    cdp_results = [{"id": "980815374096", "title": "宠物饮水器", "price": "5.5",
                    "image": "http://img/1688/1.jpg", "badge": "符合 3/3 个条件"}]
    best = {"id": "980815374096", "badge_score": 3, "title": "宠物饮水器"}

    def _fake_envelope(*a, **k):
        # 真实调用是全 kwargs（item_id=/detail_url=/store_id=/cdp=...），与参数无关——
        # 直接返回携带指定 draft 的信封，绕开真实 1688 抓取链
        return {"token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
                "envelope": {"draft": dict(draft), "extensions": {}}}

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
        r = cp.follow_sell_cloud(URL, auto_submit=auto_submit, to_box=to_box, store_id="s1")
    return r, m_submit, m_box


def test_follow_direct_submit_bad_source_blocked():
    """直提 + 源失效 draft → blocked_reason=source_preflight，不提交，success=False。"""
    r, m_submit, m_box = _drive(dict(BAD_DRAFT), auto_submit=True, to_box=False)
    assert r.get("blocked_reason") == "source_preflight", r.get("blocked_reason")
    assert r.get("success") is False
    m_submit.assert_not_called()
    m_box.assert_not_called()


def test_follow_to_box_bad_source_warns_but_submits():
    """--to-box 入箱 + 源失效 draft → warning 放行（人工兜底通道），走 submit_draft。"""
    r, m_submit, m_box = _drive(dict(BAD_DRAFT), auto_submit=True, to_box=True)
    assert r.get("blocked_reason") is None, r.get("blocked_reason")
    m_box.assert_called_once()
    m_submit.assert_not_called()


def test_follow_good_source_submits_normally():
    """合格 draft → preflight 通过，正常直提（零行为变化对照）。"""
    r, m_submit, m_box = _drive(dict(OK_DRAFT), auto_submit=True, to_box=False)
    assert r.get("blocked_reason") is None, r.get("blocked_reason")
    m_submit.assert_called_once()
    m_box.assert_not_called()


def test_follow_display_mode_bad_source_only_warns():
    """展示态（auto_submit=False）+ 源失效 draft → 仅警示，不置 blocked_reason。"""
    r, m_submit, m_box = _drive(dict(BAD_DRAFT), auto_submit=False, to_box=False)
    assert r.get("blocked_reason") is None, r.get("blocked_reason")
    m_submit.assert_not_called()
    m_box.assert_not_called()
