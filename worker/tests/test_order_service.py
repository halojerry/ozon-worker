"""P0-4: 订单服务状态映射测试。

验收门（archive/docs/legacy/PRD-orders-v0.47.md §五）：
1. 状态映射全枚举（Ozon raw status → 统一 7 态）

注（2026-10 死代码清扫）：list_orders（实时拉取，无生产调用方）已随清扫删除，
其 mock 提取/错误路径/租户隔离用例一并退役；本文件保留 map_status 用例（该函数
仍在 order_service 内，未列入删除清单）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from services import order_service


# ============================================================
# 1. 状态映射全枚举
# ============================================================

def test_status_map_full():
    assert order_service.map_status("awaiting_registration") == "pending"
    assert order_service.map_status("acceptance_in_progress") == "pending"
    assert order_service.map_status("arbitrary_available") == "awaiting"
    assert order_service.map_status("arbitrary_not_enough_for_package") == "awaiting"
    assert order_service.map_status("arbitrary_waiting_for_shipment") == "waiting"
    assert order_service.map_status("arbitrary_cancelled_by_merchant") == "waiting"
    assert order_service.map_status("driver_pickup") == "delivering"
    assert order_service.map_status("delivering") == "delivering"
    assert order_service.map_status("delivered") == "delivered"
    assert order_service.map_status("cancelled") == "cancelled"
    assert order_service.map_status("cancelled_by_merchant") == "cancelled"
    assert order_service.map_status("cancelled_by_customer") == "cancelled"
    assert order_service.map_status("cancelled_by_ozon") == "cancelled"
    assert order_service.map_status("cancelled_arbitrary") == "cancelled"
    assert order_service.map_status("unknown_future_status") == "other"
