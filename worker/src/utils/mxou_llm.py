# mxou LLM Chat API — 转发到 mxou_api.py（共享 Session，避免重复代码）
# ⚠️ 纯 re-export 不包 try/except：MxouOutOfQuotaError（余额不足）必须天然
# 冒泡到调用节点 → graph → task fail「余额不足请充值」，不能在这里被吞掉。
from utils.mxou_api import MxouOutOfQuotaError, call_mxou_chat_api

__all__ = ["call_mxou_chat_api", "MxouOutOfQuotaError"]
