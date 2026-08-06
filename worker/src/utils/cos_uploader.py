"""v0.28.5 E1: 原始图转存 COS 兜底。

背景: 1688 alicdn 原图对 Ozon 不可访问/已失效, AI 生图全失败时商品卡 0 图被下架。
方案: 下载原始图 → 转存到 COS(兼容 S3 协议) → 用 COS 公网 URL 补位。

配置(环境变量, deploy/.env):
  COS_SECRET_ID / COS_SECRET_KEY / COS_BUCKET / COS_REGION(默认 ap-guangzhou)
  COS_PUBLIC_DOMAIN(可选, 默认 https://{bucket}.cos.{region}.myqcloud.com)

⚠️ 未配置 COS 时所有函数优雅降级(返回 None/[]), 不阻断主流程。
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)


def _cos_env() -> tuple:
    return (
        os.environ.get("COS_SECRET_ID", "").strip(),
        os.environ.get("COS_SECRET_KEY", "").strip(),
        os.environ.get("COS_BUCKET", "").strip(),
        os.environ.get("COS_REGION", "ap-guangzhou").strip(),
    )


def cos_enabled() -> bool:
    """是否配置了 COS 凭证。"""
    sid, skey, bucket, _ = _cos_env()
    return bool(sid and skey and bucket)


def _get_client():
    """懒加载 boto3 S3 客户端(COS 兼容)。未配置/失败 → None。"""
    sid, skey, bucket, region = _cos_env()
    if not (sid and skey and bucket):
        return None
    try:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=f"https://cos.{region}.myqcloud.com",
            aws_access_key_id=sid,
            aws_secret_access_key=skey,
            region_name=region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "virtual"},  # COS 要求 virtual-styled domain
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 2},
            ),
        )
        return client
    except Exception as e:
        logger.warning("COS 客户端初始化失败(转存兜底禁用): %s", e)
        return None


def cos_upload_bytes(data: bytes, key: str, content_type: str = "image/jpeg") -> Optional[str]:
    """上传字节到 COS, 返回公网 URL。未配置/失败 → None。"""
    sid, skey, bucket, region = _cos_env()
    if not (sid and skey and bucket) or not data:
        return None
    client = _get_client()
    if client is None:
        return None
    try:
        client.put_object(Bucket=bucket, Key=key, Body=io.BytesIO(data), ContentType=content_type)
        public_domain = os.environ.get("COS_PUBLIC_DOMAIN", "").strip()
        if not public_domain:
            public_domain = f"https://{bucket}.cos.{region}.myqcloud.com"
        return f"{public_domain}/{key}"
    except Exception as e:
        logger.warning("COS 上传失败(%s): %s", key, e)
        return None


def _stable_key(url: str, prefix: str) -> str:
    """URL → 稳定 COS key(跨进程一致)。"""
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}/salvage/{digest}.jpg"


def salvage_original_images(original_images: List[str], max_n: int = 8,
                            prefix: str = "ozon-1688") -> List[str]:
    """下载原始图(1688 alicdn) → 转存 COS → 返回可访问 URL 列表。

    - 未配置 COS / 下载失败(404/超时) / 竞品图(ir.ozone.ru) → 跳过
    - 全部失败 → [] (调用方保持原有警告路径)
    """
    saved: List[str] = []
    if not original_images or not cos_enabled():
        return saved
    import requests

    for url in original_images:
        if len(saved) >= max_n:
            break
        if not isinstance(url, str) or not url.strip() or "ir.ozone.ru" in url:
            continue
        try:
            resp = requests.get(url.strip(), timeout=15,
                                headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200 or not resp.content:
                logger.warning("E1 原始图下载失败(HTTP %s): %s", resp.status_code, url)
                continue
            purl = cos_upload_bytes(resp.content, _stable_key(url, prefix))
            if purl:
                saved.append(purl)
        except Exception as e:
            logger.warning("E1 原始图转存失败: %s (%s)", url, e)
    if saved:
        logger.info("✅ E1 原始图转存成功 %d 张(共尝试 %d)", len(saved), len(original_images))
    return saved
