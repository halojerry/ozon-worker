"""批C fix/card-assert-cos-v1（0.78.0）— 卡片图断言 COS 白名单 + 3×20s 复查 + unverified 升可见性。

背景（取证 I4，docs/PLAN-image-source-hardening-v1.md §0）：import 刚完成时
/v3/product/info/list 先返回我方 COS 源 URL（Ozon 转存 CDN 前的合法返回），白名单
仅 Ozon CDN 三域 → 拒下载 → 恒 unverified → 单次 15s 复查后静默放行。本文件锁定：

1. 白名单放行我方 COS（ap-guangzhou 区域桶 / cos.accelerate 全域加速两形态），
   Ozon CDN 与外链拒绝回归；
2. 复查 1×15s → 3×20s（env CARD_ASSERT_RETRIES / CARD_ASSERT_INTERVAL_S 可覆写）；
3. 用尽复查仍 unverified → logger.error（进 Sentry）+ capture_task_event + warning
   放行（不可验证 ≠ 不一致，不 fail 任务）；mismatch 语义不变（任务 failed
   CARD_IMAGE_MISMATCH）。

纯 mock：不触网、不落 PG。
"""
from __future__ import annotations

import logging
import os
import struct
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.card_image_assert import (
    VERIFY_MISMATCH,
    VERIFY_OK,
    VERIFY_SKIPPED,
    VERIFY_UNVERIFIED,
    is_allowed_card_url,
    verify_card_images,
)

FAKE_RUNTIME = SimpleNamespace(context=None)
EP_IMPORT_INFO = "/v1/product/import/info"
EP_INFO_LIST = "/v3/product/info/list"

AI = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/ai1.jpeg"
CARD = "https://ir-20.ozone.ru/s3/multimedia-1-s/15112568188.jpg"
# 我方 COS 源 URL 两种形态（区域桶 / 全域加速）——Ozon 转存前的合法返回
COS_REGION = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/ozon-1688/card/1.jpg"
COS_ACCEL = "https://yss-1256275613.cos.accelerate.myqcloud.com/ozon-1688/card/1.jpg"


def _jpg(w: int, h: int) -> bytes:
    # SOI + APP0(最小) + SOF0{len,prec,h,w}（同 test_card_image_assert.py）
    return (
        b"\xff\xd8"
        b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
        + b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w)
        + b"\x00" * 3
    )


# ── 1. 域白名单 ──

def test_cos_source_urls_now_allowed():
    assert is_allowed_card_url(COS_REGION) is True
    assert is_allowed_card_url(COS_ACCEL) is True


def test_ozon_cdn_allowlist_regression():
    assert is_allowed_card_url(CARD) is True
    assert is_allowed_card_url("https://cdn1.ozone.ru/s3/a.jpg") is True
    assert is_allowed_card_url("https://ir.ozonstatic.cn/x.jpg") is True
    assert is_allowed_card_url("https://a.b.ozonstatic.com/x.jpg") is True


def test_external_and_http_still_rejected():
    assert is_allowed_card_url("https://evil.example.com/x.jpg") is False
    assert is_allowed_card_url("http://ir-20.ozone.ru/x.jpg") is False  # 非 https


def test_default_fetch_accepts_cos_url():
    """I4 真实闸口：_default_fetch_size 的白名单前置不再拒我方 COS 源 URL。"""
    import utils.card_image_assert as cia
    import utils.image_url_processor as iup

    with mock.patch.object(iup, "_download_image", return_value=_jpg(896, 1200)) as dl:
        assert cia._default_fetch_size(COS_REGION) == (896, 1200)
        assert dl.call_count == 1
    with mock.patch.object(iup, "_download_image") as dl2:
        assert cia._default_fetch_size("https://evil.example.com/x.jpg") is None
        assert dl2.call_count == 0  # 外链在白名单闸前拒绝，不触下载器


def test_cos_card_url_verifies_ok_not_unverified():
    """verify 层锁：COS 卡图可下载后正常判 ok，不再恒 unverified。"""
    st, detail = verify_card_images([AI], [COS_REGION], fetch_size=lambda u: (896, 1200))
    assert st == VERIFY_OK, detail


# ── 2. ozon_status_node 复查策略（3×20s，env 可覆写） ──

def _make_state(payload_images=None):
    return SimpleNamespace(
        ozon_task_id="123", product_id="",
        ozon_payload={"items": [{"offer_id": "x_0", "images": list(payload_images if payload_images is not None else [AI])}]},
        ozon_client_id="5371047", ozon_api_key="key",
        purchase_url="", purchase_cost="", sku_id="", profit_estimation={},
    )


def _approved_info_list():
    return {"items": [{
        "id": 5812496806, "offer_id": "x_0",
        "statuses": {"validation_status": "success", "is_created": True, "moderate_status": "approved"},
        "errors": [],
        "images": [CARD],
    }]}


def _run_node(state, verify_side_effects):
    """跑 ozon_status_node：mock ozon_post（import/info imported → info/list approved）
    + verify_card_images（side_effect 序列）+ time.sleep + capture_task_event。
    返回 (output, verify_mock, sleep_mock, event_mock)。"""
    from graphs.nodes import ozon_status_node as mod

    import_info = {"result": {"items": [{"offer_id": "x_0", "product_id": 5812496806, "status": "imported"}]}}

    def _fake_post(client_id, api_key, endpoint, body, timeout=60, **kw):
        if endpoint == EP_IMPORT_INFO:
            return import_info
        return _approved_info_list()

    with mock.patch.object(mod, "ozon_post", side_effect=_fake_post), \
         mock.patch("utils.card_image_assert.verify_card_images", side_effect=list(verify_side_effects)) as v_mock, \
         mock.patch("time.sleep") as s_mock, \
         mock.patch("utils.sentry_setup.capture_task_event") as e_mock:
        out = mod.ozon_status_node(state, None, FAKE_RUNTIME)
    return out, v_mock, s_mock, e_mock


def test_retry_unverified_then_ok_on_third_verify_passes(monkeypatch):
    """前 2 次校验 unverified、第 3 次 ok → 任务通过；verify 共调 3 次、复查间隔默认 20s。"""
    monkeypatch.delenv("CARD_ASSERT_RETRIES", raising=False)
    monkeypatch.delenv("CARD_ASSERT_INTERVAL_S", raising=False)
    out, v_mock, s_mock, e_mock = _run_node(
        _make_state(),
        [(VERIFY_UNVERIFIED, "d1"), (VERIFY_UNVERIFIED, "d2"), (VERIFY_OK, "ok")],
    )
    assert out.status == "imported"
    assert out.moderation_status == "approved"
    assert v_mock.call_count == 3, "第 3 次校验 ok 即通过"
    assert s_mock.call_count == 2, "每次复查前 sleep 一次"
    assert all(c.args[0] == 20.0 for c in s_mock.call_args_list), "默认复查间隔 20s"
    assert e_mock.call_count == 0, "最终 ok 不触发 unverified 上报"


def test_retries_exhausted_unverified_escalates_but_passes(caplog, monkeypatch):
    """复查用尽仍 unverified → logger.error（进 Sentry）+ capture_task_event + 放行（不 fail）。"""
    monkeypatch.delenv("CARD_ASSERT_RETRIES", raising=False)
    monkeypatch.delenv("CARD_ASSERT_INTERVAL_S", raising=False)
    effects = [(VERIFY_UNVERIFIED, f"d{i}") for i in range(4)]  # 首校验 + 3 次复查
    with caplog.at_level(logging.ERROR):
        out, v_mock, s_mock, e_mock = _run_node(_make_state(), effects)
    assert out.status == "imported", "不可验证 ≠ 不一致，放行不拦任务"
    assert out.moderation_status == "approved"
    assert v_mock.call_count == 4, "1 次首校验 + 3 次复查（默认预算）"
    assert s_mock.call_count == 3
    assert e_mock.call_count == 1, "capture_task_event 必须上报"
    kw = e_mock.call_args.kwargs
    assert kw.get("level") == "error"
    assert kw.get("product_id") == "5812496806"
    err_records = [r for r in caplog.records
                   if r.levelno == logging.ERROR and "不可验证" in r.getMessage()]
    assert err_records, "必须有 ERROR 级留痕（进 Sentry）"
    msg = err_records[0].getMessage()
    assert "5812496806" in msg, "留痕须带 product_id"
    assert "卡片图" in msg and "载荷图" in msg, "留痕须带卡片图数 vs 载荷图数"


def test_mismatch_still_fails_after_retries(monkeypatch):
    """mismatch 回归：复查用尽仍 mismatch → 任务 failed（CARD_IMAGE_MISMATCH），语义不变。"""
    monkeypatch.delenv("CARD_ASSERT_RETRIES", raising=False)
    monkeypatch.delenv("CARD_ASSERT_INTERVAL_S", raising=False)
    effects = [(VERIFY_MISMATCH, "数量不符")] * 4
    out, v_mock, s_mock, e_mock = _run_node(_make_state(), effects)
    assert out.status == "failed"
    assert out.error_code == "CARD_IMAGE_MISMATCH"
    assert v_mock.call_count == 4, "mismatch 同样走满复查预算再判"
    assert e_mock.call_count == 0, "unverified 专属上报不用于 mismatch"


def test_env_override_retries_and_interval(monkeypatch):
    """env 覆写生效：CARD_ASSERT_RETRIES=1 / CARD_ASSERT_INTERVAL_S=5。"""
    monkeypatch.setenv("CARD_ASSERT_RETRIES", "1")
    monkeypatch.setenv("CARD_ASSERT_INTERVAL_S", "5")
    effects = [(VERIFY_UNVERIFIED, "d0"), (VERIFY_UNVERIFIED, "d1")]
    out, v_mock, s_mock, e_mock = _run_node(_make_state(), effects)
    assert out.status == "imported"
    assert v_mock.call_count == 2, "1 次首校验 + 1 次复查（覆写后预算）"
    assert s_mock.call_count == 1
    assert s_mock.call_args_list[0].args[0] == 5.0, "覆写间隔 5s 生效"


def test_empty_payload_skips_assert_without_retry(monkeypatch):
    """载荷无图（跟卖/编辑流）→ skipped，不触发复查与上报（回归）。"""
    monkeypatch.delenv("CARD_ASSERT_RETRIES", raising=False)
    monkeypatch.delenv("CARD_ASSERT_INTERVAL_S", raising=False)
    out, v_mock, s_mock, e_mock = _run_node(
        _make_state(payload_images=[]), [(VERIFY_SKIPPED, "payload 无图")],
    )
    assert out.status == "imported"
    assert v_mock.call_count == 1
    assert s_mock.call_count == 0, "skipped 不触发复查 sleep"
    assert e_mock.call_count == 0


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn(); print(f"PASS {name}")
            except Exception:
                failed += 1; print(f"FAIL {name}"); traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
