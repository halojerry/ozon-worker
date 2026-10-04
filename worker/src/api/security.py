"""鉴权族唯一入口：token 校验 / 限流接线 / 余额判定 / Bearer 守卫。

W3b（main.py composition root 拆解）自 main.py 顶层抽出。此前全仓 ~30 处
（routes ×28、api/deps_tenant、mcp_server、services）只能函数内
``from main import _authenticate_token`` 懒导入；本模块是唯一权威，
main.py 保留同名 re-export（路由裸名调用 + 测试 monkeypatch main.X 兼容面）。

改鉴权链前必读（历史决策全保留，出处见各函数 docstring）：
- 限流后置：_authenticate_token 在凭证校验通过后才写限流键（T21 race-M5，
  未认证洪水不占限流字典内存）。
- 余额唯一真值 = MXOU 平台实查；Supabase users.quota 只是降级兜底；
  字面 balance:0 是哨兵不是欠费（v0.64.1 红绿灯，见 _check_mxou_balance）。
- _task_status_guard 的租户比对对老数据宽容读（无 tenant_id 放行），
  且有意不限流（高频轮询端点）。
- _require_bearer 无独立应急开关；task_status/cancel_task 专用开关是
  env TASK_STATUS_AUTH（勿混用）。
"""

import json
import os

from fastapi import HTTPException

from runtime.rate_limit import RATE_LIMIT_PER_MINUTE, rate_limiter
from storage.database.supabase_client import get_supabase_client
from utils.logger import get_logger
from utils.ozon_client import ozon_post  # F-F01: auth_verify Ozon 校验
from utils.ozon_errors import OzonError  # F-F01: auth_verify Ozon 校验错误分类

logger = get_logger(__name__)


def _extract_token_from_body(body_text: str) -> str:
    """从请求体 JSON 提取 token（解析失败/非 dict → 视为无 token → 鉴权 401）。"""
    try:
        data = json.loads(body_text)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("token", "") or "")


def _key_user_id(clean_token: str) -> str:
    """回退租户:key 哈希派生(PRD M2 前行为;未配置 Supabase 时由 tenant_service 使用)。"""
    from services.tenant_service import key_derived_tenant
    return key_derived_tenant(clean_token)


_revoked_tokens: set[str] = set()


def _authenticate_token(token: str) -> str:
    """鉴权 token → user_id(PRD M2:key 仅鉴权,租户 = Supabase tokens.user_id;
    未配置 Supabase 回退 key 哈希)。失败抛 HTTPException(401/403/429/503)。"""
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    if token in _revoked_tokens:
        raise HTTPException(status_code=401, detail="Token is revoked")
    clean_tmp = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    if clean_tmp in _revoked_tokens:
        raise HTTPException(status_code=401, detail="Token is revoked")
    from services.tenant_service import resolve_tenant
    user_id = resolve_tenant(token)
    # T21(race-M5): 限流后置——通过凭证校验(resolve_tenant 的 401/503)的 token 才写
    # 限流键，未认证洪水不再消耗限流字典内存（键形态保持 raw token 含 sk- 前缀不变）。
    allowed, _remaining = rate_limiter.check(token)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {RATE_LIMIT_PER_MINUTE} requests per minute",
        )
    return user_id


def _check_mxou_balance(token_record: dict) -> tuple[float, bool]:
    """检查用户余额（v0.29.3 统一：优先查 MXOU 平台真实余额）。

    背景（2026-08-07 Sentry 实证）：此前查 Supabase users.quota + unlimited_quota
    放行 —— unlimited_quota=true 时永远放行, 但 MXOU 平台按真实余额扣费,
    平台欠费(¥-0.068)仍放行 → 任务入队后 LLM/生图全 403 失败(253 次错误)。

    修复原则（统一余额来源 = MXOU 平台）：
    - 优先调 MXOU /v1/dashboard/billing/subscription 拿真实 balance
      （balance > 0 放行; <= 0 拒绝"MXOU 余额不足, 请充值"）
    - MXOU 查询失败(网络/接口) → 降级 Supabase users.quota（现有逻辑兜底）
    - unlimited_quota 仅作 Supabase 兜底分支的放行标记, 不再跳过 MXOU 实查

    Returns: (balance, ok) — ok=True 表示有额度

    ⚠️ 余额判定红绿灯（v0.64.1，改前必读）：真欠费 = MXOU 实查返回**负数**
    （无 limit 哨兵）；订阅/无限账号字面 ``balance:0`` 是**哨兵不是欠费**；
    Supabase ``users.quota`` 是从不同步的 stale 镜像，只在 MXOU 实查失败时兜底
    且 unlimited 恒放行。**不要**把任一 0.0 简单当欠费拒绝。
    """
    try:
        # ⚠️ 1. MXOU 平台真实余额优先（统一来源）
        raw_key = str(token_record.get("key", "") or "")
        mxou_balance = None
        if raw_key:
            # v0.62 R1: 复用 _check_balance_cached（30s TTL 缓存 + 低余额用户告警），
            # 避免 auth/verify 高频打余额接口；查询失败返回 inf（fail-open），
            # 与旧 get_mxou_balance 返回 None 的降级语义对齐。
            from utils.mxou_api import _check_balance_cached
            _cached = _check_balance_cached(raw_key)
            mxou_balance = None if _cached == float("inf") else _cached
        if mxou_balance is not None:
            return mxou_balance, mxou_balance > 0

        # ⚠️ 2. MXOU 查询失败 → 降级 Supabase users.quota（原逻辑兜底）
        user_id = token_record.get("user_id", "")
        supabase = get_supabase_client()
        # v0.62.4 修复：调用方（submit_task）可能只传 key 哈希租户(user_<hash>)而非真实
        # Supabase users.id，导致真实用户查不到 → 误判「余额不足」。这里用 key 反查真实
        # user_id 与 unlimited_quota（auth_verify 已传全量 record 则不重复查）。
        raw_key = str(token_record.get("key", "") or "")
        if supabase is not None and (
            token_record.get("unlimited_quota") is None
            or not user_id
            or str(user_id).startswith("user_")
        ):
            try:
                _trows = supabase.table("tokens").select(
                    "user_id, unlimited_quota"
                ).eq("key", raw_key).is_("deleted_at", "null").limit(1).execute()
                if _trows.data:
                    _row = _trows.data[0]
                    user_id = str(_row.get("user_id") or user_id)
                    if _row.get("unlimited_quota") is not None:
                        token_record = {
                            **token_record,
                            "unlimited_quota": bool(_row.get("unlimited_quota")),
                        }
            except Exception as exc:
                logger.warning("余额降级-反查 token 失败（user=%s）: %s", user_id, str(exc)[:200])
        if supabase is None or not user_id:
            # 本地开发模式：无 Supabase，不阻断
            return 0.0, True

        # 查 users 表剩余额度 quota（充值直接加 quota，调用扣 quota）
        try:
            user_rows = supabase.table("users").select(
                "quota"
            ).eq("id", user_id).limit(1).execute()
        except Exception as exc:
            # v0.22: 查询失败不再降级 key 级 remain_quota（僵尸字段会负数误判）。
            # unlimited 放行；非 unlimited 拒绝（数据异常应暴露，宁缺毋滥）
            logger.warning("余额查询失败（user=%s）: %s", user_id, exc)
            return 0.0, bool(token_record.get("unlimited_quota"))

        if user_rows.data:
            u = user_rows.data[0]
            balance = float(u.get("quota", 0) or 0)
            if token_record.get("unlimited_quota"):
                return balance, True
            return balance, balance > 0

        # users 表无记录：unlimited 放行；非 unlimited 拒绝（不降级僵尸字段）
        return 0.0, bool(token_record.get("unlimited_quota"))
    except Exception as e:
        logger.warning(f"余额检查异常（不阻断）: {e}")
        return 0.0, True


def _balance_source_label(token_record: dict, balance: float) -> str:
    """402 文案定位（B3, v0.64.1）：推断本次余额拒绝的数字来自哪条数据源。

    source ∈ {mxou_real, mxou_session, supabase, unknown}
    - mxou_real:  数字由 MXOU billing/subscription 实查给出（字面 balance；
                  B1 后仅剩真欠费负数/无 limit 哨兵的 0.0 会走到拒绝）
    - mxou_session: 无字面 balance 字段、经用户会话 /api/user/self quota 换算
                  （旧响应形态；现 newapi 均带 balance 字段，罕见）
    - supabase:   MXOU 实查降级(None→fail-open inf)后，数字来自 Supabase
                  users.quota 兜底判定
    - unknown:    缓存未被本次判定填充（如 _check_mxou_balance 被 mock）或异常

    轻量实现：只读 _check_mxou_balance 刚写入的 30s 余额缓存判定 MXOU 实查是否
    降级（fp 必须匹配本 token，避免读到别的用户）——**不**调 _check_balance_cached/
    get_mxou_balance，绝不因此多发 HTTP。任何异常/信息不足 → unknown（不影响拒绝）。
    """
    try:
        from utils.mxou_api import _BALANCE_CACHE, _token_fingerprint
        token = str(token_record.get("key") or "")
        if not token:
            return "unknown"
        cached = _BALANCE_CACHE.get("value")
        if cached is None or _BALANCE_CACHE.get("fp") != _token_fingerprint(token):
            return "unknown"
        if cached == float("inf"):
            # MXOU 实查返回 None → fail-open inf → 拒绝数字来自 Supabase users.quota 兜底
            return "supabase"
        if float(cached) == float(balance):
            return "mxou_real"
    except Exception:
        pass
    return "unknown"


def _auth_verify_sync(token: str, client_id: str = "", api_key: str = "") -> dict:
    """auth_verify 同步实现（纯阻塞 IO，供 asyncio.to_thread 调用）。"""
    if not token:
        return {"valid": False, "reason": "token_invalid", "expires_in": 0}

    # 去掉 sk- 前缀（tokens 表 key 列存储的是不带前缀的值）
    clean_token = token.replace("sk-", "", 1) if token.startswith("sk-") else token

    # 1. 验证 token（查 Supabase tokens 表，和 submit_task 相同逻辑）
    supabase = get_supabase_client()
    if supabase is None:
        logger.warning("auth_verify: Supabase未配置，跳过token鉴权（本地开发模式）")
    else:
        try:
            token_records = supabase.table("tokens").select(
                "user_id, key, remain_quota, status, expired_time, unlimited_quota"
            ).eq("key", clean_token).is_("deleted_at", "null").execute()

            if not token_records.data or len(token_records.data) == 0:
                return {"valid": False, "reason": "token_invalid", "expires_in": 0}

            token_record = token_records.data[0]
            status = int(token_record.get("status", 0))

            # 2. 检查 token 状态（1=active, 2=disabled, 3=expired, 4=quota exhausted/欠费）
            #    status=4 明确映射 balance_insufficient（与 n8n AUTH_EXHAUSTED 一致）
            if status == 4:
                return {"valid": False, "reason": "balance_insufficient", "expires_in": 0}
            if status != 1:
                return {"valid": False, "reason": "account_inactive", "expires_in": 0}

            # 3. 检查余额（查 users 表 quota-used_quota，无限额度放行；
            #    原实现只查 remain_quota 会把无限额度 token 误判余额不足）
            balance, has_quota = _check_mxou_balance(token_record)
            if not has_quota:
                return {"valid": False, "reason": "balance_insufficient", "expires_in": 0}

        except Exception as e:
            logger.warning(f"auth_verify DB error: {e}")
            return {"valid": False, "reason": "service_unavailable", "expires_in": 0}

    # 4. 可选：验证 Ozon API
    ozon_valid = None
    if client_id and api_key:
        try:
            # F-F01（2026-09-09 审计）：收敛 ozon_post——OzonError(4xx/5xx)=凭证/平台问题
            # → False（保持原「非 200」语义），网络不可达 → None（未知，不冤枉凭证）
            ozon_post(client_id, api_key, "/v1/seller/info", {}, timeout=10)
            ozon_valid = True
        except OzonError:
            ozon_valid = False
        except Exception:
            ozon_valid = None

    return {
        "valid": True,
        "reason": "ok",
        "expires_in": 86400,
        "ozon_valid": ozon_valid,
    }


def _verify_analytics_token(clean_token: str) -> None:
    """analytics 上报鉴权（与 logistics_quote 一致：Supabase 未配置 → 本地放行）。"""
    supabase = get_supabase_client()
    if supabase is None:
        return
    try:
        token_records = supabase.table("tokens").select(
            "status"
        ).eq("key", clean_token).is_("deleted_at", "null").execute()
        if not token_records.data or len(token_records.data) == 0 or int(token_records.data[0].get("status", 0)) != 1:
            raise HTTPException(status_code=401, detail="token_invalid or account_inactive")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="service_unavailable")


def _require_bearer(request) -> str:
    """T8(api-M2): Bearer 提取 + 有效性校验共享入口（/progress 已接入；
    task_statistics 的内联已收敛至此；后续 read-only 端点同款复用）。

    规则：
    - 无 Authorization Bearer → 401 "Token is required"（与 forensics /
      ``_task_status_guard`` 同文案）。
    - Bearer 剥 ``sk-`` 前缀一层后走 ``_verify_analytics_token`` 有效性校验，
      其 401/503 原样透传（本函数不吞不换）。
    - 返回 clean token（无 sk- 前缀）。⚠️ 调用方后续若做租户解析可直传本返回值
      ——``resolve_tenant`` 内部自剥 sk-（``_clean_token``），raw/clean 等价。

    ⚠️ 应急门语义见 ``_task_status_guard``（env ``TASK_STATUS_AUTH``）——那是
    task_status/cancel_task 专用的应急开关；本 helper **无独立开关**，接入端点
    如需应急放行走端点级回退（镜像回滚或临时 try 包裹），勿混用 TASK_STATUS_AUTH。
    """
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    clean = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    _verify_analytics_token(clean)
    return clean


def _task_status_guard(request, task_row: dict) -> None:
    """鉴权 + 租户校验（v0.73 为 GET /task_status 收口；v0.76 T6(api-H2) 起
    cancel_task 同源复用——语义完全一致，见下）。

    此前该端点（旧路径 + /api/v1 别名）完全无鉴权——任何拿到 task uuid 的人
    可读全量任务数据（tenant_id / 采购链接 / 定价成本）。规则：
    - env ``TASK_STATUS_AUTH=0`` → 直接放行（应急开关，默认开；仅生产事故
      回滚用，勿长期关闭）。
    - 无 Authorization Bearer → 401 "Token is required"（与 forensics 同文案）。
    - Bearer 无效 → ``_verify_analytics_token`` 的 401/503 原样透传。
    - 租户比对：``resolve_tenant(token)``（与任务写入侧同源）≠ task_row 的
      tenant_id → 404 "task not found"（等价不存在，不泄漏存在性，与
      forensics 同语义）。
    - task_row 无 tenant_id 键（历史老数据）→ 跳过比对放行（宽容读：老数据
      无租户归属可校验，硬拒会让存量任务轮询全挂）。

    ⚠️ 有意不加 rate_limiter：task_status 是前端 / skill / harness 轮询的
    高频端点，逐 token 限流会误伤正常轮询；鉴权 + 租户校验足矣（与
    analytics 读端点的区别在此）。
    """
    if os.environ.get("TASK_STATUS_AUTH", "").strip() == "0":
        return
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    clean_token = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    _verify_analytics_token(clean_token)
    task_tenant = str(task_row.get("tenant_id") or "")
    if not task_tenant:
        return  # 老数据无租户归属 → 宽容读（见 docstring）
    from services.tenant_service import resolve_tenant
    if resolve_tenant(token) != task_tenant:
        raise HTTPException(status_code=404, detail="task not found")
