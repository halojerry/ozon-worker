#!/usr/bin/env python3
"""P4: follow_sell_cloud 在 envelope 构建失败（如图片为空）时必须返回 success=False。

背景：follow_sell_cloud 此前在图搜命中（best_match）时就置 result["success"] = True，
envelope 组装/提交还没发生。若 build_graph_envelope_with_retry 抛
ProductValidationError("产品图片为空")，异常被 except Exception 捕获并写入
envelope_error，但 success 保持 True → 无 task_id，batch_test 却报成功。

修复：移除提前置位，envelope 异常分支显式 success=False。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_follow_success_after_submit.py -q
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts import cloud_probe  # noqa: E402

URL = "https://www.ozon.ru/product/avtopoilka-4767514314/"
CDP_DATA = {"success": True, "images": ["http://img/ozon/1.jpg"], "title": "Автопоилка"}
CDP_RESULTS = [{
    "id": "980815374096", "title": "宠物饮水器", "price": "5.5",
    "image": "http://img/1688/1.jpg", "badge": "符合 3/3 个条件",
}]
BEST = {"id": "980815374096", "badge_score": 3, "title": "宠物饮水器"}


def _run_follow(auto_submit: bool = True, **env_builder_kw) -> dict:
    """mock 完整外部依赖跑 follow_sell_cloud 全链路。"""
    with mock.patch("scripts.lib.cache.cache_get", return_value=None), \
         mock.patch("scripts.lib.cache.cache_set"), \
         mock.patch("scripts.lib.config_store._require_auth"), \
         mock.patch.object(cloud_probe, "_get_ozon_credentials",
                           return_value={"client_id": "1", "api_key": "k"}), \
         mock.patch("scripts.lib.config_store.get_mxou_token", return_value="sk"), \
         mock.patch.object(cloud_probe, "_cached_ozon_scrape", return_value=CDP_DATA), \
         mock.patch("scripts.lib.chrome_launcher.ensure_chrome_cdp", return_value=(True, "ok")), \
         mock.patch("scripts.cli._chrome_profile_dir", return_value="/tmp/profile"), \
         mock.patch("scripts.lib.cdp_client.CdpConnection"), \
         mock.patch("scripts.lib.ozon_seller_analytics.fetch_sales_analytics", return_value={}), \
         mock.patch("scripts.lib.ozon_image_search.search_by_image_cdp", return_value=CDP_RESULTS), \
         mock.patch("scripts.lib.ozon_discovery._pick_best_match", return_value=BEST), \
         mock.patch.object(cloud_probe, "build_graph_envelope_with_retry", **env_builder_kw), \
         mock.patch("scripts.lib.config_store.get_store_profile", return_value={}):
        return cloud_probe.follow_sell_cloud(URL, auto_submit=auto_submit, store_id="s1")


def test_envelope_build_failure_returns_success_false():
    """build_graph_envelope_with_retry 抛 ProductValidationError("产品图片为空")
    → 结果 success=False 且 envelope_error 置位（不再因图搜命中而 success=True）。"""
    def _raise(*_a, **_kw):
        raise cloud_probe.ProductValidationError("产品图片为空")

    r = _run_follow(auto_submit=True, side_effect=_raise)

    assert r.get("success") is False, \
        f"envelope 构建失败不应 success: {r.get('success')}"
    assert r.get("envelope_error") == "产品图片为空", r.get("envelope_error")
    assert not r.get("task_id"), "无 task_id"


def test_envelope_build_failure_dry_run_also_success_false():
    """dry-run（auto_submit=False）下 envelope 构建失败同样 success=False。"""
    def _raise(*_a, **_kw):
        raise cloud_probe.ProductValidationError("产品图片为空")

    r = _run_follow(auto_submit=False, side_effect=_raise)

    assert r.get("success") is False, r.get("success")
    assert r.get("envelope_error") == "产品图片为空"


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
