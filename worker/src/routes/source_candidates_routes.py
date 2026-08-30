"""PRD M5b: 货源匹配上报端点(薄层:鉴权 → service)。"""

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from services import source_candidate_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/source-candidates", tags=["source-candidates"])


async def _authenticate(request: Request) -> str:
    from main import _authenticate_token  # 延迟导入防循环

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return _authenticate_token(auth[7:].strip())
    try:
        raw = await request.body()
        if raw:
            data = json.loads(raw.decode("utf-8"))
            token = str(data.get("token", "") or "")
            if token:
                return _authenticate_token(token)
    except Exception:
        pass
    return _authenticate_token("")


@router.post("")
async def report_source_candidates(request: Request):
    """skill 图搜/跟卖匹配结果上报(POST /api/v1/source-candidates)。

    body: {
      token?, credential_id?, client_id?,
      candidates: [{product_id, source_offer_id?, source_url, price_cny?,
                    match_score?, match_method?, status?}]
    }
    有 credential_id/client_id → 校验归属后落对应店;都没有 → 全零占位店
    (工作台展示,绑定店铺后手动补)。
    """
    tenant_id = await _authenticate(request)
    body = await request.json()
    candidates = body.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        raise HTTPException(status_code=400, detail="candidates 不能为空")

    explicit_credential_id = body.get("credential_id")
    credential_id = source_candidate_service.resolve_credential(
        tenant_id,
        credential_id=explicit_credential_id,
        client_id=body.get("client_id"),
    )
    if explicit_credential_id and not credential_id:
        raise HTTPException(status_code=404, detail="凭证不存在或已吊销")
    if not credential_id:
        credential_id = source_candidate_service.NO_STORE_CREDENTIAL
    return source_candidate_service.upsert_source_candidates(
        tenant_id, credential_id, candidates)
