#!/usr/bin/env python3
"""批I（fix/follow-reference-image-v1）：跟卖竞品图 = 生图参考语义。

拍板（用户 2026-09-20）：跟卖（follow）的 Ozon 竞品图拿来做**生图参考**——
AI 按竞品参考重绘出图上卡，竞品图本身绝不进 payload。实机取证（2026-09-19
本地 Docker）：follow 信封 draft.images=竞品首图（cloud_probe v0.33.1 设计），
worker 生图链 filter_product_images 白名单把 ozone 域拒掉 → 全部生图节点跳过
→ 批E IMAGE_GEN_ALL_FAILED，follow CREATE 全灭。

本文件锁定：
- filter_reference_images：follow 信封（allow_competitor=True）放行 Ozon 竞品 CDN
  原尺寸图作参考；graph 信封（False）维持旧白名单（竞品图仍拒）；缩略/.webp 恒拒。
- white_bg/multi_angle/main_image 三节点消费 state.extensions.follow_sell：
  follow 单不再「无合格参考图」跳过（mock mxou 生图 API 被调用）。
- 上传禁令不变：enforce_upload_policy 对 ozone URL 恒拒（参考≠上卡）。

运行：
    cd worker && PYTHONPATH=src <venv>/bin/python -m pytest tests/test_follow_reference_image_v078.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.image_url_guard import (  # noqa: E402
    filter_product_images,
    filter_reference_images,
    is_product_image_candidate,
)

OZON_FULL = "https://ir-20.ozone.ru/s3/multimedia-1-9/14356724373.jpg"
OZON_FULL_2 = "https://ir-20.ozone.ru/s3/multimedia-1-3/7361872743.jpg"
ALICDN_FULL = "https://cbu01.alicdn.com/img/ibank/O1CN01abc_!!2217962014548-0-cib.jpg"
ALICDN_THUMB = "https://cbu01.alicdn.com/img/ibank/O1CN01abc_!!2217962014548-0-cib.310x310.jpg"
COS_AI = "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/abc.jpeg"


# ── 纯函数层 ──────────────────────────────────────────────


def test_competitor_image_rejected_by_default_whitelist():
    """旧白名单语义不变：ozone 竞品图非货源图床，is_product_image_candidate=False。"""
    assert is_product_image_candidate(OZON_FULL) is False
    assert filter_product_images([OZON_FULL, ALICDN_FULL]) == [ALICDN_FULL]


def test_reference_filter_follow_allows_fullsize_competitor():
    """跟卖拍板核心：allow_competitor=True 放行 ozone 原尺寸竞品图作参考。"""
    out = filter_reference_images([OZON_FULL, OZON_FULL_2], allow_competitor=True)
    assert out == [OZON_FULL, OZON_FULL_2]


def test_reference_filter_graph_mode_unchanged():
    """graph 信封（allow_competitor=False，默认）逐字保持旧白名单行为。"""
    assert filter_reference_images([OZON_FULL, ALICDN_FULL]) == [ALICDN_FULL]
    assert filter_reference_images([OZON_FULL], allow_competitor=False) == []


def test_reference_filter_thumbnail_and_webp_never_pass():
    """缩略/.webp 恒拒对竞品参考同样生效（质量红线不因 follow 放开）。"""
    ozon_thumb = "https://ir-20.ozone.ru/s3/multimedia-1-9/14356724373.310x310.jpg"
    ozon_webp = "https://ir-20.ozone.ru/s3/multimedia-1-9/14356724373.webp"
    out = filter_reference_images([ozon_thumb, ozon_webp, ALICDN_THUMB], allow_competitor=True)
    assert out == []


def test_reference_filter_mixed_keeps_order_and_sources():
    """混合列表：follow 放行竞品图 + 常规白名单图都保留，顺序不变。"""
    out = filter_reference_images(
        [ALICDN_THUMB, OZON_FULL, ALICDN_FULL, "", None], allow_competitor=True)
    assert out == [OZON_FULL, ALICDN_FULL]


# ── 节点层：follow 单不再跳过生图（mock mxou API） ─────────────


def _state_dict(images, follow: bool) -> dict:
    ext = {"follow_sell": True} if follow else {}
    return {
        "draft": {"title": "Unihopper Dish Drying Rack", "weight": 1200},
        "token": "tok",
        "original_images": images,
        "extensions": ext,
    }


class _StubRuntime:
    """节点签名要求 runtime.context；三个生图节点实际不读 ctx 字段，空壳即可。"""

    class context:  # noqa: N801 - 桩命名从简
        pass


_RUNTIME = _StubRuntime()


def _patch_common(monkeypatch):
    """密闭：生图 API / 质量评估 / 任务缓存 / plan 槽位全部 mock。"""
    calls = {}

    def fake_call(model, token, prompt, ref_images=None, **kw):
        calls["model"] = model
        calls["images"] = ref_images or []
        return "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/gen.jpeg"

    monkeypatch.setattr("graphs.nodes.white_bg_gen_node.call_mxou_image_api", fake_call)
    monkeypatch.setattr(
        "graphs.nodes.white_bg_gen_node.evaluate_image_quality",
        lambda imgs, **kw: [{"url": u, "priority": 1, "size": 999} for u in imgs])
    monkeypatch.setattr("graphs.nodes.white_bg_gen_node.get_image", lambda *a, **k: None)
    monkeypatch.setattr("graphs.nodes.white_bg_gen_node.save_image", lambda *a, **k: None)
    monkeypatch.setattr("graphs.nodes.white_bg_gen_node.slot_enabled", lambda *a, **k: True)
    return calls


def test_white_bg_follow_uses_competitor_reference(monkeypatch):
    """follow 单：竞品图过参考过滤 → mxou 生图被调用（不再「无合格参考图」跳过）。"""
    from graphs.nodes import white_bg_gen_node as node

    calls = _patch_common(monkeypatch)
    out = node.white_bg_gen_node(
        node.WhiteBgInput(**_state_dict([OZON_FULL], follow=True)),
        config={}, runtime=_RUNTIME)
    assert out.white_bg_image and "/file/images/" in out.white_bg_image
    assert calls["images"] == [OZON_FULL]


def test_white_bg_graph_mode_still_skips_competitor_only(monkeypatch):
    """graph 单：仅竞品图时维持旧行为——跳过生图（防串图语义不变）。"""
    from graphs.nodes import white_bg_gen_node as node

    _patch_common(monkeypatch)
    out = node.white_bg_gen_node(
        node.WhiteBgInput(**_state_dict([OZON_FULL], follow=False)),
        config={}, runtime=_RUNTIME)
    assert out.white_bg_image is None


def test_multi_angle_follow_uses_competitor_reference(monkeypatch):
    """multi_angle 同语义：follow 竞品图作参考，不再跳过。"""
    from graphs.nodes import multi_angle_gen_node as node

    monkeypatch.setattr("graphs.nodes.multi_angle_gen_node.call_mxou_image_api",
                        lambda model, token, prompt, ref_images=None, **kw:
                        "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/ma.jpeg")
    monkeypatch.setattr("graphs.nodes.multi_angle_gen_node.get_image", lambda *a, **k: None)
    monkeypatch.setattr("graphs.nodes.multi_angle_gen_node.save_image", lambda *a, **k: None)
    monkeypatch.setattr("graphs.nodes.multi_angle_gen_node.slot_enabled", lambda *a, **k: True)
    state = node.MultiAngleInput(**_state_dict([OZON_FULL], follow=True))
    out = node.multi_angle_gen_node(state, config={}, runtime=_RUNTIME)
    assert out.multi_angle_image, "follow 单 multi_angle 不应再跳过"


def test_main_image_follow_uses_competitor_reference(monkeypatch):
    """main_image 同语义：follow 竞品图作参考。"""
    from graphs.nodes import main_image_gen_node as node

    monkeypatch.setattr("graphs.nodes.main_image_gen_node.call_mxou_image_api",
                        lambda model, token, prompt, ref_images=None, **kw:
                        "https://yss-1256275613.cos.ap-guangzhou.myqcloud.com/file/images/main.jpeg")
    monkeypatch.setattr("graphs.nodes.main_image_gen_node.get_image", lambda *a, **k: None)
    monkeypatch.setattr("graphs.nodes.main_image_gen_node.save_image", lambda *a, **k: None)
    monkeypatch.setattr("graphs.nodes.main_image_gen_node.slot_enabled", lambda *a, **k: True)
    state = node.MainImageInput(**_state_dict([OZON_FULL], follow=True))
    out = node.main_image_gen_node(state, config={}, runtime=_RUNTIME)
    assert out.main_image, "follow 单 main_image 不应再跳过"


# ── 上传禁令回归：参考 ≠ 上卡 ────────────────────────────────


def test_upload_policy_still_bans_competitor_image():
    """竞品图可作参考但 payload 出口恒拒（批E 语义零回退）。"""
    from utils.image_source import classify_image_source, enforce_upload_policy

    assert classify_image_source(OZON_FULL) == "external"
    allowed, violations = enforce_upload_policy([OZON_FULL])
    assert allowed is False and violations


def test_upload_policy_allows_generated_and_blocks_on_mixed():
    """AI 图放行；AI+竞品混合 → 拒（违规清单带来源标签）。"""
    from utils.image_source import enforce_upload_policy

    ok, v = enforce_upload_policy([COS_AI])
    assert ok is True and not v
    ok, v = enforce_upload_policy([COS_AI, OZON_FULL])
    assert ok is False and len(v) == 1
