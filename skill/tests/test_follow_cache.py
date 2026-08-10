#!/usr/bin/env python3
"""Q7 (Task 10): follow 链路三级缓存回归测试（TDD）。

① service.py probe_1688_page：标准 cache.py 命名空间缓存（probe1688）优先，
   _find_cached_probe 工件扫描作二级兜底；成功结果回写 cache_set。
② cloud_probe.py _translate_slug_to_cn：LLM 翻译结果缓存（slug_cn, 30d）。
③ follow_sell_cloud：envelope 级缓存（follow, key=product_id:store_id, 6h），
   命中且有 images+1688_matches → 直接复用（auto_submit 照常 submit）。

运行：
    cd skill && .venv314/bin/python tests/test_follow_cache.py
    cd skill && PYTHONPATH=. python3 -m pytest tests/test_follow_cache.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── ① probe_1688_page 缓存 ──

def test_probe_cache_hit_skips_browser_and_fallback():
    """cache_get("probe1688") 命中 → 直接返回，不触发 _find_cached_probe 与浏览器探测。"""
    from scripts.capabilities.browser_probe import service as svc

    url = "https://detail.1688.com/offer/980815374096.html"
    cached = {"ready": True, "probe": {"images": ["http://img/1.jpg"]}, "summary": {}}
    with mock.patch("scripts.lib.cache.cache_get", return_value=cached) as m_get, \
         mock.patch.object(svc, "_find_cached_probe") as m_find, \
         mock.patch.object(svc, "find_browser_executable") as m_bfe:
        result = svc.probe_1688_page(url)
    assert result == cached, f"应返回缓存结果，实际 {result}"
    m_get.assert_called_once_with("probe1688", url)
    m_find.assert_not_called()
    m_bfe.assert_not_called()


def test_probe_cache_miss_falls_back_to_artifact_scan():
    """cache_get miss → _find_cached_probe 二级兜底命中 → 直接返回，不触发浏览器探测。"""
    from scripts.capabilities.browser_probe import service as svc

    url = "https://detail.1688.com/offer/980815374096.html"
    cached = {"ready": True, "probe": {"images": ["http://img/1.jpg"]}, "summary": {}}
    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch.object(svc, "_find_cached_probe", return_value=cached) as m_find, \
         mock.patch.object(svc, "find_browser_executable") as m_bfe:
        result = svc.probe_1688_page(url)
    assert result == cached
    m_find.assert_called_once()
    m_bfe.assert_not_called()


def test_probe_cache_miss_runs_full_path_and_sets():
    """cache 全 miss → 走完整浏览器探测，成功后 cache_set("probe1688", url, result, ttl=86400)。"""
    from scripts.capabilities.browser_probe import service as svc

    url = "https://detail.1688.com/offer/980815374096.html"
    tmp = Path(tempfile.mkdtemp(prefix="probe_cache_"))

    cdp = mock.MagicMock()
    cdp.find_tab.return_value = None
    tab = mock.MagicMock()
    tab._closed = False
    probe = {"ready": True, "images": ["https://cbu01.alicdn.com/img/1.jpg"]}

    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set") as m_set, \
         mock.patch.object(svc, "_find_cached_probe", return_value=None), \
         mock.patch.object(svc, "find_browser_executable", return_value="/fake/chrome"), \
         mock.patch.object(svc, "get_config_profile", return_value="default"), \
         mock.patch.object(svc, "_profile_dir", return_value=tmp), \
         mock.patch.object(svc, "_artifact_path", return_value=tmp / "probe.json"), \
         mock.patch.object(svc, "current_task_id", return_value="t1"), \
         mock.patch.object(svc, "_resolve_browser_session",
                           return_value={"cdp_url": "http://127.0.0.1:9222", "login_detected": True}), \
         mock.patch.object(svc, "_cdp_available", return_value=True), \
         mock.patch.object(svc, "_connect_existing_chrome", return_value=(cdp, True)), \
         mock.patch.object(svc, "_extract_offer_id", return_value="980815374096"), \
         mock.patch.object(svc, "_open_target_page_in_existing_browser", return_value=tab), \
         mock.patch.object(svc, "_probe_opened_target_page_with_retries", return_value=probe), \
         mock.patch.object(svc, "_filter_probe_images", side_effect=lambda imgs: list(imgs)), \
         mock.patch.object(svc, "_build_summary", return_value={"ok": True}), \
         mock.patch.object(svc, "_looks_like_failure_page", return_value=False), \
         mock.patch.object(svc, "_looks_like_captcha_intercept", return_value=False):
        result = svc.probe_1688_page(url)

    assert result.get("ready") is True
    assert m_set.call_count == 1, f"应写缓存，实际 {m_set.call_count} 次"
    args = m_set.call_args
    assert args.args[0] == "probe1688" and args.args[1] == url
    assert args.kwargs.get("ttl", 0) == 86400


# ── ② _translate_slug_to_cn 缓存 ──

def test_translate_slug_cache_hit_skips_llm():
    """cache_get("slug_cn") 命中 → 直接返回，不发 LLM 请求。"""
    from scripts import cloud_probe as cp

    with mock.patch("scripts.lib.cache.cache_get", return_value="驱蚊棒") as m_get, \
         mock.patch("requests.post") as m_post:
        r = cp._translate_slug_to_cn("палочки-от-комаров", "tok")
    assert r == "驱蚊棒"
    m_get.assert_called_once_with("slug_cn", "палочки-от-комаров")
    m_post.assert_not_called()


def test_translate_slug_cache_miss_calls_llm_and_sets():
    """miss → LLM 翻译成功后 cache_set("slug_cn", slug, kw, ttl=30d)。"""
    from scripts import cloud_probe as cp

    resp = mock.Mock(status_code=200)
    resp.json.return_value = {"choices": [{"message": {"content": "驱蚊棒 户外"}}]}
    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set") as m_set, \
         mock.patch("requests.post", return_value=resp):
        r = cp._translate_slug_to_cn("палочки-от-комаров", "tok")
    assert r == "驱蚊棒 户外"
    assert m_set.call_count == 1, f"应写缓存，实际 {m_set.call_count} 次"
    args = m_set.call_args
    assert args.args[0] == "slug_cn" and args.args[1] == "палочки-от-комаров"
    assert args.kwargs.get("ttl", 0) == 30 * 24 * 3600, f"TTL 应为 30 天，实际 {args.kwargs.get('ttl')}"


def test_translate_slug_cache_miss_empty_not_cached():
    """LLM 返回空 → 不写缓存（避免缓存空结果）。"""
    from scripts import cloud_probe as cp

    resp = mock.Mock(status_code=200)
    resp.json.return_value = {"choices": [{"message": {"content": ""}}]}
    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set") as m_set, \
         mock.patch("requests.post", return_value=resp):
        r = cp._translate_slug_to_cn("какой-то-слаг", "tok")
    assert r == ""
    m_set.assert_not_called()


# ── ③ follow_sell_cloud envelope 缓存 ──

def _cached_follow_result() -> dict:
    return {
        "success": True,
        "product_id": "4767514314",
        "slug": "avtopoilka",
        "images": ["http://img/ozon/1.jpg"],
        "title": "Автопоилка",
        "1688_matches": [{"id": "980815374096", "badge_score": 3}],
        "envelope": {
            "token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
            "envelope": {"draft": {"item_id": "980815374096"}, "source": {}, "extensions": {}},
        },
    }


def test_follow_cache_hit_reuses_no_cdp_no_llm():
    """cache_get("follow") 命中（有 images+matches）→ 不抓 Ozon/CDP/LLM，直接复用。"""
    from scripts import cloud_probe as cp

    cached = _cached_follow_result()
    with mock.patch("scripts.lib.cache.cache_get", return_value=cached) as m_get, \
         mock.patch.object(cp, "_cached_ozon_scrape") as m_scrape, \
         mock.patch.object(cp, "_translate_slug_to_cn") as m_slug, \
         mock.patch.object(cp, "submit_envelope",
                           return_value={"task_id": "T1"}) as m_submit, \
         mock.patch("scripts.lib.config_store._require_auth"):
        r = cp.follow_sell_cloud(
            "https://www.ozon.ru/product/avtopoilka-4767514314/",
            auto_submit=True, store_id="s1",
        )
    assert r is cached, "命中缓存应直接返回同一对象"
    m_get.assert_called_once_with("follow", "4767514314:s1")
    m_scrape.assert_not_called()
    m_slug.assert_not_called()
    assert m_submit.call_count == 1, "auto_submit 应照常提交"
    assert r["task_id"] == "T1"


def test_follow_cache_hit_no_auto_submit_skips_submit():
    """命中缓存且 auto_submit=False → 不提交。"""
    from scripts import cloud_probe as cp

    cached = _cached_follow_result()
    with mock.patch("scripts.lib.cache.cache_get", return_value=cached), \
         mock.patch.object(cp, "_cached_ozon_scrape") as m_scrape, \
         mock.patch.object(cp, "submit_envelope") as m_submit, \
         mock.patch("scripts.lib.config_store._require_auth"):
        r = cp.follow_sell_cloud(
            "https://www.ozon.ru/product/avtopoilka-4767514314/",
            auto_submit=False, store_id="s1",
        )
    assert r is cached
    m_scrape.assert_not_called()
    m_submit.assert_not_called()


def test_follow_cache_miss_runs_full_path_and_sets():
    """cache miss → 走完整链路，成功后 cache_set("follow", "pid:sid", result, ttl=21600)。"""
    from scripts import cloud_probe as cp

    url = "https://www.ozon.ru/product/avtopoilka-4767514314/"
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
    envelope = {
        "token": "sk", "ozon_client_id": "1", "ozon_api_key": "k",
        "envelope": {"draft": {"item_id": "980815374096"}, "extensions": {}},
    }

    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set") as m_set, \
         mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch.object(cp, "_get_ozon_credentials",
                           return_value={"client_id": "1", "api_key": "k"}), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk"), \
         mock.patch.object(cp, "_cached_ozon_scrape", return_value=cdp_data), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp", return_value=(True, "ok")), \
         mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/profile"), \
         mock.patch("scripts.lib.cdp_client.CdpConnection") as m_conn, \
         mock.patch("scripts.lib.ozon_seller_analytics.fetch_sales_analytics", return_value={}), \
         mock.patch("scripts.lib.ozon_image_search.search_by_image_cdp", return_value=cdp_results), \
         mock.patch("scripts.lib.ozon_discovery._pick_best_match", return_value=best), \
         mock.patch.object(cp, "build_graph_envelope_with_retry", return_value=envelope), \
         mock.patch("scripts.lib.config_store.get_store_profile", return_value={}):
        r = cp.follow_sell_cloud(url, auto_submit=False, store_id="s1")

    assert r.get("success") is True, f"miss 应走完整链路成功: {r.get('error')}"
    assert r.get("1688_matches"), r
    assert m_set.call_count == 1, f"应写 envelope 缓存，实际 {m_set.call_count} 次"
    args = m_set.call_args
    assert args.args[0] == "follow", f"命名空间应为 follow，实际 {args.args[0]}"
    assert "4767514314" in args.args[1] and "s1" in args.args[1], \
        f"key 应含 product_id+store_id: {args.args[1]}"
    assert args.kwargs.get("ttl", 0) == 21600, f"TTL 应为 6h，实际 {args.kwargs.get('ttl')}"


def test_follow_cache_miss_no_matches_not_cached():
    """miss 且无匹配 → 不写缓存（避免缓存 no_relevant_match 空结果）。"""
    from scripts import cloud_probe as cp

    url = "https://www.ozon.ru/product/avtopoilka-4767514314/"
    cdp_data = {"success": True, "images": ["http://img/ozon/1.jpg"], "title": "Автопоилка"}

    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set") as m_set, \
         mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch.object(cp, "_get_ozon_credentials",
                           return_value={"client_id": "1", "api_key": "k"}), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk"), \
         mock.patch.object(cp, "_cached_ozon_scrape", return_value=cdp_data), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp", return_value=(True, "ok")), \
         mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/profile"), \
         mock.patch("scripts.lib.cdp_client.CdpConnection") as m_conn, \
         mock.patch("scripts.lib.ozon_seller_analytics.fetch_sales_analytics", return_value={}), \
         mock.patch("scripts.lib.ozon_image_search.search_by_image_cdp", return_value=[]), \
         mock.patch("scripts.lib.ak_1688_client.search_by_image", return_value=[]), \
         mock.patch.object(cp, "_translate_slug_to_cn", return_value="китайские ключи"), \
         mock.patch.object(cp, "_search_1688_with_fallback", return_value=[]):
        r = cp.follow_sell_cloud(url, auto_submit=False, store_id="s1")

    assert r.get("1688_matches") == [] or not r.get("1688_matches")
    m_set.assert_not_called()


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
