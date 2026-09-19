"""上架图来源唯一分类器（fix/image-source-hardgate-v1 批A）。

生产取证（docs/PLAN-image-source-hardening-v1.md §0）：Ozon 卡片出现 1688 原图。
唯一现存来源链 = ①生图全败 → E1 salvage 兜底（原图转存 COS 上卡）+
②AI 图识别 marker 盲区——retry 守卫用 ``"/file/images/"`` 字面子串判 AI 图，
b64 兜底图落 ``mxou-b64/`` key 时守卫全盲 → 草稿原图覆盖重传。

本模块是「这张图从哪来」的唯一事实源，消费方一律 import 此处，禁止再内联
URL 子串判定（防漂移）：

- ``"ai"``           本方 COS 且 key ∈ {file/images/, mxou-b64/}（AI 生成/生图兜底产物）
- ``"salvage"``      ozon-1688/salvage/（E1 原图转存；v0.78 起默认停用，仅逃生门）
- ``"mirror_draft"`` draft-images/（draft_image_mirror 镜像的草稿原图 1:1 副本）
- ``"external"``     非本方 COS 外链（alicdn/竞品 CDN 等），及本方 COS 上不属于
                     任何已知通道 key 的 URL（保守不可上卡，宁缺毋滥）
- ``"invalid"``      空 / 非 http(s) / 非字符串

is_cos_url 复用 cos_uploader 现有实现（其本身是 utils/image_url_guard 唯一实现
的模块级 re-export）——import，不复制逻辑。

消费方：graphs/validation_retry_loop（AI 载荷判定，A2）、
graphs/nodes/prepare_ozon_upload_node（上传 policy 硬闸，A3）、
graphs/nodes/assemble_ozon_product_node（无图补位收窄，A4）。
utils/card_image_assert.py 的 AI 载荷判定同源接线归批C（控制器统一合流）。
"""
from __future__ import annotations

import os
from typing import Iterable, List, Tuple
from urllib.parse import urlparse

from utils.cos_uploader import is_cos_url  # 唯一实现 re-export（迁移自 image_url_guard）

# AI 生成图 COS key 前缀：
#   file/images/  = 生图节点主产物（prepare 组装上传的 AI 营销图）
#   mxou-b64/     = mxou 同步响应 b64_json 兜底转存（mxou_api._b64_to_cos_url）
# ⚠️ 旧 ``"/file/images/" in url`` 字面判定对 mxou-b64/ 全盲（本批盲区根因）。
AI_IMAGE_KEY_PREFIXES = ("file/images/", "mxou-b64/")

# E1 salvage 原图转存 key 前缀（cos_uploader._stable_key(prefix="ozon-1688")）
SALVAGE_KEY_PREFIX = "ozon-1688/salvage/"

# draft_image_mirror._mirror_one 镜像草稿 key 前缀（默认 prefix="draft-images"）
MIRROR_DRAFT_KEY_PREFIX = "draft-images/"

# 上架上传载荷允许的图来源（enforce_upload_policy 用；salvage 需逃生门显式并入）
IMAGE_SOURCE_ALLOWLIST_UPLOAD = frozenset({"ai"})

# 逃生门 env（默认 "0"）：E1 原图转存兜底开关。v0.78 起生图全败默认任务级失败
# （IMAGE_GEN_ALL_FAILED，非永久 → task_processor 整任务重试），绝不出 1688 原图卡。
SALVAGE_FALLBACK_ENV = "IMAGE_SALVAGE_FALLBACK"


class ImageGenAllFailedError(RuntimeError):
    """生图全部失败硬闸异常（批A）。

    故意用 RuntimeError 子类承载且**非永久**：task_processor._is_permanent_task_error
    对它判 False → 整任务自动重试一轮（生图抖动值得重试）；重试仍全败才终态 failed。
    禁止把它加进永久错误清单。
    """


def _cos_key_path(url: str) -> str:
    """URL → COS key 路径（path 去查询串、去首部斜杠、小写）。异常输入返回 ""。"""
    try:
        return urlparse(str(url).strip()).path.lstrip("/").lower()
    except Exception:
        return ""


def classify_image_source(url: object) -> str:
    """判定单张图来源，返回 ai / salvage / mirror_draft / external / invalid 之一。

    顺序：先验形（非 str/空/非 http(s) → invalid），再验域（非本方 COS →
    external），最后按 key 前缀分通道（未知 key 保守 external）。
    不复用 is_cos_url 对「非 str/空串 → True」的既有宽容契约——本函数面向
    上卡闸门，宁缺毋滥。
    """
    if not isinstance(url, str):
        return "invalid"
    u = url.strip()
    if not u or not u.lower().startswith(("http://", "https://")):
        return "invalid"
    if not is_cos_url(u.lower()):
        return "external"
    key = _cos_key_path(u)
    if key.startswith(AI_IMAGE_KEY_PREFIXES):
        return "ai"
    if key.startswith(SALVAGE_KEY_PREFIX):
        return "salvage"
    if key.startswith(MIRROR_DRAFT_KEY_PREFIX):
        return "mirror_draft"
    # 本方 COS 域但 key 不属任何已知通道（手工上传/历史遗留等）→ 保守 external，
    # 不具备上卡资格（enforce_upload_policy 会拦）。
    return "external"


def has_generated_images(urls: Iterable[object]) -> bool:
    """任一 URL 为 AI 生成图（ai）→ True。b64 兜底图计入（marker 盲区修复）。"""
    if not urls:
        return False
    return any(classify_image_source(u) == "ai" for u in urls)


def enforce_upload_policy(urls: Iterable[object], allow_salvage: bool = False) -> Tuple[bool, List[str]]:
    """上架上传载荷 policy 闸：全部 URL ∈ {ai}∪({salvage} if allow_salvage) 才放行。

    返回 (True, []) 或 (False, 违规清单)；违规清单每项形如 ``"<来源>:<URL>"``
    （来源标签便于取证定位写入链）。空列表恒放行（空图走既有空图分支语义，
    不在本闸管辖）。
    """
    allowed = set(IMAGE_SOURCE_ALLOWLIST_UPLOAD)
    if allow_salvage:
        allowed.add("salvage")
    violations: List[str] = []
    for u in (urls or []):
        src = classify_image_source(u)
        if src not in allowed:
            violations.append(f"{src}:{u}")
    return (len(violations) == 0, violations)


def salvage_fallback_enabled() -> bool:
    """读取 E1 逃生门 env（IMAGE_SALVAGE_FALLBACK，默认 "0"）。仅逃生门用。"""
    return os.environ.get(SALVAGE_FALLBACK_ENV, "0").strip().lower() in ("1", "true", "yes", "on")
