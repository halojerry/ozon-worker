# -*- coding: utf-8 -*-
"""
v0.69 T2.2 — CREATE 前 Ozon 侧 offer 存在性检查 → 转 UPDATE（消 _0 尸体卡）

生产实证：对已存在的 declined 死卡重跑 graph → worker 走 CREATE（/v3/product/import
不带 product_id），Ozon 对已存在 offer_id 自动加 _0 后缀新建卡（549733785579 →
549733785579_0），旧卡残留 → 修一张死卡多一张尸体卡。

契约：
- ozon_upload_node CREATE 分支前置闸：非 UPDATE（item 无 product_id）、非跟卖
  （is_follow_sell）、非 import-by-sku pending（import_submitted）→ 提交 import 前
  查 find_product_by_offer；存在（任何 state 含 declined/archived）→ item 注入
  product_id 转 UPDATE；不存在/查询失败 → CREATE 照旧。
- ozon_client.find_product_by_offer：/v3/product/list filter offer_id+visibility=ALL，
  返回首个匹配 dict 或 None；API 异常吞掉返回 None + warning（非致命）。
- 跟卖/UPDATE/import-by-sku 三路径显式不受影响。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_upsert_by_offer_v069.py -q
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.ozon_upload_node import UPSERT_BY_OFFER, ozon_upload_node
from graphs.state import OzonUploadInput

OFFER = "549733785579"
FOUND_PID = 6254565982


def _payload(offer_id=OFFER, product_id=None):
    """最小合法 payload（节点对缺字段只告警不阻断）"""
    item = {
        "name": "Тест",
        "offer_id": offer_id,
        "price": "100",
        "old_price": "120",
    }
    if product_id is not None:
        item["product_id"] = product_id
    return {"items": [item]}


def _state(payload=None, is_follow_sell=False, import_submitted=False):
    return OzonUploadInput(
        ozon_payload=payload if payload is not None else _payload(),
        ozon_client_id="123",
        ozon_api_key="key",
        sku_id=OFFER,
        is_follow_sell=is_follow_sell,
        import_submitted=import_submitted,
    )


def _mock_import_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.ok = True
    resp.json.return_value = {"result": {"task_id": "task-1"}}
    resp.text = "{}"
    return resp


def _run(state, finder_return="MISSING", finder_exc=None):
    """跑 upload 节点：mock 配额 ok + offer 查找 + import POST。

    Returns:
        (output, import_post_mock, finder_mock)
    """
    output_holder = {}

    def _finder(offer_id, *args, **kwargs):
        if finder_exc is not None:
            raise finder_exc
        if finder_return == "MISSING":
            return None
        return finder_return

    finder = MagicMock(side_effect=_finder)

    with patch("graphs.nodes.ozon_upload_node.ozon_check_quota",
               return_value={"ok": True, "daily_used": 1, "daily_limit": 100,
                             "total_used": 1, "total_limit": 1000,
                             "remaining_daily": 99, "remaining_total": 999}), \
         patch("graphs.nodes.ozon_upload_node.find_product_by_offer", finder), \
         patch("graphs.nodes.ozon_upload_node.session"), \
         patch("graphs.nodes.ozon_upload_node.ozon_post") as ozon_post_mock:
        # F-F01 收敛：upload 走 ozon_post（返回 dict，非 2xx 抛 OzonError）
        ozon_post_mock.return_value = {"result": {"task_id": "task-1"}}
        out = ozon_upload_node(state, None, SimpleNamespace(context=None))
        output_holder["out"] = out
        output_holder["post"] = ozon_post_mock
        output_holder["finder"] = finder
    return output_holder["out"], output_holder["post"], output_holder["finder"]


# ── ① offer 已存在（含 declined 死卡）→ 转 UPDATE：payload 带 product_id ──
def test_offer_exists_declined_converts_to_update():
    dead_card = {"product_id": FOUND_PID, "offer_id": OFFER, "state": "declined"}
    out, post, finder = _run(_state(), finder_return=dead_card)
    # 查找用了 payload 的 offer_id
    assert finder.called, "应执行 offer 存在性查询"
    called_offer = (finder.call_args.kwargs.get("offer_id")
                    if finder.call_args.kwargs else finder.call_args.args[0])
    assert called_offer == OFFER
    # import 仍提交，但 payload 已带 product_id（UPDATE 语义，不再裸 CREATE）
    assert post.called, "转 UPDATE 后仍应提交 /v3/product/import"
    sent = post.call_args.args[3]
    assert sent["items"][0].get("product_id") == FOUND_PID, \
        f"offer 已存在应注入 product_id 转 UPDATE: {sent['items'][0]}"
    assert out.upload_status == "success"


# ── ② offer 不存在 → CREATE 照旧（无 product_id）──
def test_offer_absent_creates_normally():
    out, post, finder = _run(_state(), finder_return=None)
    assert finder.called
    sent = post.call_args.args[3]
    assert "product_id" not in sent["items"][0], "offer 不存在应保持 CREATE（无 product_id）"
    assert out.upload_status == "success"


# ── ③ 查询抛异常 → CREATE 照旧（预检失败绝不阻塞上架）──
def test_lookup_error_creates_anyway():
    out, post, finder = _run(_state(), finder_exc=RuntimeError("network down"))
    assert finder.called
    assert post.called, "查询失败必须放行 CREATE，不阻塞上架"
    sent = post.call_args.args[3]
    assert "product_id" not in sent["items"][0]
    assert out.upload_status == "success"


# ── ③b client 层非致命封装：API 异常 → 返回 None（不 raise）──
def test_find_product_by_offer_swallows_api_error():
    from utils.ozon_client import find_product_by_offer
    with patch("utils.ozon_client.ozon_post", side_effect=RuntimeError("boom")):
        assert find_product_by_offer(client_id="1", api_key="k", offer_id=OFFER) is None


# ── ④ 跟卖 is_follow_sell → 不查不转（回归：跟卖本就要并卡）──
def test_follow_sell_skips_lookup():
    out, post, finder = _run(_state(is_follow_sell=True), finder_return={"product_id": 1})
    assert not finder.called, "跟卖路径绝不查 offer 存在性"
    sent = post.call_args.args[3]
    assert "product_id" not in sent["items"][0], "跟卖 CREATE 重建保持原行为"
    assert out.upload_status == "success"


# ── ⑤ import-by-sku pending → 提前返回 pending（回归：不查不 POST）──
def test_import_by_sku_pending_skips_upload():
    out, post, finder = _run(_state(import_submitted=True))
    assert not finder.called, "import-by-sku 处理中不做 offer 查询"
    assert not post.called, "import-by-sku 处理中不提交 v3 import"
    assert out.upload_status == "pending"


# ── ⑥ 开关关闭 → 行为与现状一致（不查直接 CREATE）──
def test_switch_off_disables_gate(monkeypatch):
    monkeypatch.setattr("graphs.nodes.ozon_upload_node.UPSERT_BY_OFFER", False)
    assert UPSERT_BY_OFFER in (True, False)  # 模块常量存在
    out, post, finder = _run(_state(), finder_return={"product_id": FOUND_PID})
    assert not finder.called, "开关关闭不查询"
    sent = post.call_args.args[3]
    assert "product_id" not in sent["items"][0], "开关关闭 = 现状 CREATE"
    assert out.upload_status == "success"


# ── ⑦a item 已带 product_id（编辑更新/跟卖 UPDATE）→ 不查不覆盖 ──
def test_item_with_product_id_skips_lookup():
    out, post, finder = _run(_state(payload=_payload(product_id=777777)),
                             finder_return={"product_id": FOUND_PID})
    assert not finder.called, "已是 UPDATE 语义的 payload 不再查询"
    sent = post.call_args.args[3]
    assert sent["items"][0]["product_id"] == 777777, "原 product_id 不被覆盖"


# ── ⑦b find_product_by_offer 解析 /v3/product/list 响应形状 ──
def test_find_product_by_offer_parses_result_items():
    from utils.ozon_client import find_product_by_offer
    resp = {"result": {"items": [
        {"product_id": 111, "offer_id": "other"},
        {"product_id": FOUND_PID, "offer_id": OFFER, "state": "declined", "archived": False},
    ], "last_id": "x"}}
    with patch("utils.ozon_client.ozon_post", return_value=resp) as post:
        found = find_product_by_offer(client_id="1", api_key="k", offer_id=OFFER)
    assert found is not None and found["product_id"] == FOUND_PID, "应返回首个 offer_id 匹配项"
    assert found["state"] == "declined"
    # 请求形状：filter.offer_id 数组 + visibility=ALL（skill 侧已验证可查到死卡）
    body = post.call_args.args[3] if len(post.call_args.args) > 3 else post.call_args.kwargs["body"]
    assert body["filter"]["offer_id"] == [OFFER]
    assert body["filter"]["visibility"] == "ALL"


def test_find_product_by_offer_tolerates_top_level_items():
    """容错：响应 items 在顶层（非 result.items）也能解析"""
    from utils.ozon_client import find_product_by_offer
    resp = {"items": [{"product_id": FOUND_PID, "offer_id": OFFER}]}
    with patch("utils.ozon_client.ozon_post", return_value=resp):
        found = find_product_by_offer(client_id="1", api_key="k", offer_id=OFFER)
    assert found is not None and found["product_id"] == FOUND_PID


def test_find_product_by_offer_no_match_returns_none():
    from utils.ozon_client import find_product_by_offer
    resp = {"result": {"items": [{"product_id": 1, "offer_id": "someone_else"}]}}
    with patch("utils.ozon_client.ozon_post", return_value=resp):
        assert find_product_by_offer(client_id="1", api_key="k", offer_id=OFFER) is None
    with patch("utils.ozon_client.ozon_post", return_value={"result": {}}):
        assert find_product_by_offer(client_id="1", api_key="k", offer_id=OFFER) is None


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for fn in tests:
        try:
            fn()
            print(f"  OK {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
