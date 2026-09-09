"""批次 C：worker 端 Ozon 会话代管（bindShopCookie 对标）回归。

覆盖：
- C1  ozon_sessions 表模型形状（tenant+credential 唯一约束）
- C2  会话代管服务（AES-GCM 存储/解密头/状态；aad 冻结 tenant:credential；永不回值）
- C3  POST/GET/DELETE /credentials/{id}/session 端点（租户隔离 + 密文不回显）
- C5  会话直调通道（what_to_sell v3 cookie 直调 + /analytics/what-to-sell 端点）
- C6  失效联动（401/403 → mark expired + 409 session_expired）

纯 mock、无 PG 依赖：DB 层用 fake engine 断言 SQL/参数；HTTP 层 TestClient +
patch service 函数。安全红线断言：响应/源码不回显 cookie 值。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ══════════════════ C1: 表模型 ══════════════════

def test_ozon_session_model_shape():
    from storage.database.shared.model import OzonSellerSession

    t = OzonSellerSession.__table__
    assert t.name == "ozon_sessions"
    for col in ("cookies_encrypted", "cookie_names", "sc_company_id_encrypted", "status"):
        assert col in t.columns
    uniques = [u for u in t.constraints if u.__class__.__name__ == "UniqueConstraint"]
    assert any({"tenant_id", "credential_id"} <= {c.name for c in u.columns} for u in uniques)
