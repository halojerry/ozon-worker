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

from utils import image_url_guard

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


def is_cos_url(url: object) -> bool:
    """判断 URL 是否已托管在本方 COS（幂等判定的唯一共享实现）。

    v0.69 declined IMAGE_ERROR 根因修复的共享件：submit 镜像闸（draft_service）、
    validate 全外链硬拦（ozon_validate_node）、retry pictures/import 取图
    （validation_retry_loop）三处同源判定，禁止各自内联（防漂移）。
    覆盖 区域域名 cos.{region}.myqcloud.com 与全球加速 cos.accelerate.myqcloud.com。
    """
    if not isinstance(url, str):
        return True
    lowered = url.strip().lower()
    if not lowered:
        return True
    return ".myqcloud.com" in lowered or "cos." in lowered


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


def _is_reference_image(url: str) -> bool:
    """判断是否为参考图（竞品图 / 1688 缩略图），E1 兜底必须跳过。

    fix/image-ref-pollution 白名单化：线上「产品A卡片出现产品B图」——旧黑名单
    （ir.ozone.ru / ozonstatic / ir-20.）漏 Ozon CDN 域名形态，且 1688 搜索
    兜底串来的别家 _310x310 缩略图（alicdn 域）畅通无阻被转存直上卡。
    现统一走 utils/image_url_guard 白名单：仅放行 alicdn/1688 域原尺寸图。
    """
    return not image_url_guard.is_product_image_candidate(url)


def salvage_original_images(original_images: List[str], max_n: int = 8,
                            prefix: str = "ozon-1688") -> List[str]:
    """下载原始图(1688 alicdn) → 转存 COS → 返回可访问 URL 列表。

    - 未配置 COS / 下载失败(404/超时) / 参考图(竞品图+1688缩略图) → 跳过
    - 全部失败 → [] (调用方保持原有警告路径)
    """
    saved: List[str] = []
    if not original_images or not cos_enabled():
        return saved
    import requests

    for url in original_images:
        if len(saved) >= max_n:
            break
        if _is_reference_image(url):
            # fix/image-ref-pollution: 拒绝原因可观测（串图取证靠这条日志）
            logger.warning("E1 跳过非合格商品图（非alicdn原图或缩略/竞品图）: %s", url)
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
