import os
import json
import logging
from supabase import create_client, Client
from typing import Optional

logger = logging.getLogger(__name__)

# Supabase客户端单例
_supabase_client: Optional[Client] = None


def get_supabase_client() -> Client:
    """
    获取Supabase客户端单例
    从环境变量读取连接信息（SUPABASE_URL和SUPABASE_KEY）
    如果环境变量不存在，返回None（不抛出异常）
    """
    global _supabase_client
    
    if _supabase_client is not None:
        return _supabase_client
    
    # ✅ 从环境变量读取配置（环境变量优先，如果不存在则使用默认值）
    supabase_url = os.getenv("SUPABASE_URL", "https://kekmppsuiiokdckdeolv.supabase.co")
    supabase_key = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtla21wcHN1aWlva2Rja2Rlb2x2Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NDYyMDA0NCwiZXhwIjoyMDkwMTk2MDQ0fQ.ZkJMnjrlUQKaUpMU3eug9EQLUsoN0mOWI8wzC3jRkAU")
    
    # 如果环境变量不存在，返回None（不抛出异常）
    if not supabase_url or not supabase_key:
        logger.warning("Supabase环境变量未配置：SUPABASE_URL或SUPABASE_KEY缺失")
        _supabase_client = None
        return _supabase_client
    
    # 创建Supabase客户端
    _supabase_client = create_client(supabase_url, supabase_key)
    
    logger.info("Supabase客户端初始化成功")
    return _supabase_client


__all__ = ["get_supabase_client"]