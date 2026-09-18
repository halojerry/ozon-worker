# -*- coding: utf-8 -*-
"""
v0.77.1 — S1/S2 同步窗修复回归（纯 mock，无 PG）。

生产实证（2026-09-12 上报 / 2026-09-18 v0.77.0 升级核验仍 ❌）：

S1 订单同步 `POST /v4/posting/fbs/list` → 400 "filter.to must be after the filter.since"。
根因：`orders_last_synced_at` 是 timestamptz 列，psycopg2 取回带**会话时区**
（生产 Asia/Shanghai +08:00）的 aware datetime；旧代码 `_orders_since()` 返回
`last - 1h` 后直接 `.strftime("%Y-%m-%dT%H:%M:%SZ")`——把 +08 挂钟时间硬标成
UTC → since 落到真实 UTC 未来 8 小时 → since > to → Ozon 400。每轮调度必现
（~5500 条/天刷屏，租户 127/637 订单/退货进不来）。续传窗口分支
（orders_window_since/to 同为 timestamptz 取回）同款隐患；_sync_returns 的
strftime 同款写法一并收口（防御 naive/+08 存量值）。

S2 促销同步 `POST /v1/actions` → 405。官方 swagger（mcp ozon_describe_method）
明确 /v1/actions 仅 GET（method=GET, parameters=[]，请求体 schema=null），
且 200 响应 `result` 是**数组**——修方法的同时必须修解析（`result.get("actions")`
对 list 会 AttributeError，否则 405 变新异常）。

契约：
- 任意 watermark 形态（aware 任意时区 / naive）→ 窗口字符串恒为真 UTC（"…Z"）；
- since < to 恒成立（防御性 clamp：watermark 在未来 → since 收敛到 to-1h）；
- _sync_actions 走 ozon_get("/v1/actions")，绝不 POST；数组/对象两种 result 形态可解析。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_sync_window_tz_v077.py -q
"""
import datetime
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import services.store_sync_service as sss  # noqa: E402

TZ8 = datetime.timezone(datetime.timedelta(hours=8))

# 2026-09-18 14:00 +08:00 == 2026-09-18 06:00:00 UTC（对齐生产水位形态）
WATERMARK_8 = datetime.datetime(2026, 9, 18, 14, 0, 0, tzinfo=TZ8)
SINCE_EXPECTED = "2026-09-18T05:00:00Z"  # UTC 水位 − 1h 重叠
TO_EXPECTED = "2026-09-18T06:00:00Z"


def _state_row(orders_last_synced_at=None, incomplete=False,
               window_since=None, window_to=None):
    return SimpleNamespace(
        orders_sync_incomplete=incomplete,
        orders_last_synced_at=orders_last_synced_at,
        orders_window_since=window_since,
        orders_window_to=window_to,
        orders_sync_cursor="",
        products_last_synced_at=None,
    )


def _run_orders(state_row):
    """驱动 _sync_orders：mock 水位 + ozon_post 捕获 + DB 写收口。

    返回 (captured_filter, complete_window_args)。ozon_post 返回空 postings
    （首层即 break，不触 _upsert_orders）。
    """
    captured = {}
    complete_args = {}

    def _fake_post(client_id, api_key, endpoint, body, **kw):
        captured["endpoint"] = endpoint
        captured["filter"] = (body or {}).get("filter") or {}
        return {"postings": []}

    def _fake_complete(t, c, to_dt):
        complete_args["to_dt"] = to_dt

    with patch.object(sss, "_sync_state_row", return_value=state_row), \
         patch("utils.ozon_client.ozon_post", side_effect=_fake_post), \
         patch.object(sss, "_upsert_orders") as up_mock, \
         patch.object(sss, "_complete_orders_window", side_effect=_fake_complete):
        out = sss._sync_orders("t1", "c1", "cid", "key")
    assert out["error"] == "", f"同步不应报错: {out}"
    up_mock.assert_not_called()
    return captured["filter"], complete_args


# ── S1 ①：+08:00 水位 → since 恒为真 UTC（水位−1h）；to=真实当前 UTC，since<to ──
def test_orders_since_plus08_watermark_formats_utc():
    flt, complete = _run_orders(_state_row(orders_last_synced_at=WATERMARK_8))
    assert flt.get("since") == SINCE_EXPECTED, (
        f"since 必须是真 UTC（水位 {WATERMARK_8} − 1h = {SINCE_EXPECTED}），"
        f"实际 {flt.get('since')!r}——+08 挂钟直标 Z 是生产 400 根因"
    )
    # to = 真实当前 UTC（不与夹具同源）；断言格式 + 时间序
    assert flt.get("to", "").endswith("Z") and "T" in flt.get("to", "")
    assert flt["since"] < flt["to"], "since 必须 < to（Ozon 400 红线）"
    # 水位推进值也是 aware UTC（续传/留存语义一致）
    assert complete["to_dt"].tzinfo is not None


# ── S1 ②：续传窗口（timestamptz 取回 +08）同款收口 ──
def test_orders_continuation_window_formats_utc():
    flt, _ = _run_orders(_state_row(
        incomplete=True,
        window_since=datetime.datetime(2026, 9, 18, 13, 0, 0, tzinfo=TZ8),
        window_to=WATERMARK_8,
    ))
    assert flt.get("since") == "2026-09-18T05:00:00Z", (
        f"续传窗口 since 必须真 UTC，实际 {flt.get('since')!r}"
    )
    assert flt.get("to") == TO_EXPECTED
    assert flt["since"] < flt["to"]


# ── S1 ③：naive 水位按 UTC 解释（历史行为兜底，不引入回归）──
def test_orders_naive_watermark_treated_as_utc():
    flt, _ = _run_orders(_state_row(
        orders_last_synced_at=datetime.datetime(2026, 9, 18, 6, 0, 0)))
    assert flt.get("since") == SINCE_EXPECTED
    assert flt["since"] < flt["to"]


# ── S1 ④：防御 clamp——水位在未来（时钟漂移/脏数据）→ since 收敛到 to−1h，绝不 since>=to ──
def test_orders_future_watermark_clamped():
    flt, _ = _run_orders(_state_row(
        orders_last_synced_at=datetime.datetime(2200, 1, 1, 8, 0, 0, tzinfo=TZ8)))
    assert flt["since"] < flt["to"], "watermark 在未来也必须保证 since < to"


# ── S1 ⑤：_sync_returns 同款 strftime 收口（+08 ISO 存量值 → 真 UTC）──
def test_returns_stored_plus08_iso_formats_utc():
    captured = {}

    def _fake_post(client_id, api_key, endpoint, body, **kw):
        captured["endpoint"] = endpoint
        captured["filter"] = (body or {}).get("filter") or {}
        return {"returns": []}

    with patch.object(sss, "_domain_due", return_value=True), \
         patch.object(sss, "_domain_state_row",
                      return_value={"returns": {"last_synced_at": "2026-09-18T14:00:00+08:00"}}), \
         patch("utils.ozon_client.ozon_post", side_effect=_fake_post), \
         patch.object(sss, "_upsert_returns"), \
         patch.object(sss, "_domain_state_update") as upd_mock:
        out = sss._sync_returns("t1", "c1", "cid", "key")
    assert out["error"] == "", f"退货同步不应报错: {out}"
    flt = captured["filter"]
    # returns 语义：since = 上次同步点本身（无 −1h 重叠）→ +08 ISO 转真 UTC = 06:00Z
    assert flt.get("since") == "2026-09-18T06:00:00Z", (
        f"returns since 必须真 UTC（+08 ISO 直接转），实际 {flt.get('since')!r}"
    )
    assert flt["since"] < flt.get("to", "")
    upd_mock.assert_called_once()


# ── S2 ①：/v1/actions 必须走 GET（POST 永久 405），result 数组形态可解析 ──
def test_actions_uses_get_and_parses_array_result():
    captured = {}

    def _fake_get(client_id, api_key, endpoint, **kw):
        captured["endpoint"] = endpoint
        captured["kwargs"] = kw
        return {"result": [{"id": 1}, {"id": 2}, {"id": 3}]}

    def _post_must_not_fire(*a, **k):
        raise AssertionError("POST /v1/actions 是 405 死路，禁止回潮")

    with patch.object(sss, "_domain_due", return_value=True), \
         patch("utils.ozon_client.ozon_get", side_effect=_fake_get), \
         patch("utils.ozon_client.ozon_post", side_effect=_post_must_not_fire), \
         patch.object(sss, "_domain_state_update") as upd_mock:
        out = sss._sync_actions("t1", "c1", "cid", "key")
    assert out["error"] == "", f"促销同步不应报错: {out}"
    assert out["count"] == 3, f"result 数组形态必须正确计数，实际 {out}"
    assert captured["endpoint"] == "/v1/actions"
    upd_mock.assert_called_once()


# ── S2 ②：兼容对象形态（result.actions），防上游响应结构变动再断 ──
def test_actions_parses_legacy_object_shape():
    def _fake_get(client_id, api_key, endpoint, **kw):
        return {"result": {"actions": [{"id": 1}]}}

    with patch.object(sss, "_domain_due", return_value=True), \
         patch("utils.ozon_client.ozon_get", side_effect=_fake_get), \
         patch.object(sss, "_domain_state_update"):
        out = sss._sync_actions("t1", "c1", "cid", "key")
    assert out["error"] == "", f"对象形态解析不应报错: {out}"
    assert out["count"] == 1
