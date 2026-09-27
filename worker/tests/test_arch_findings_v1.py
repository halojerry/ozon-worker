# -*- coding: utf-8 -*-
"""fix/arch-findings-v1 — 09-findings Top10 #1/#8 + 图片链 #2 三修复的回归锁定。

1. Top10 #1（🔴）：variant 生图失败原图兜底毒化整单——失败产物绝不返回 1688 alicdn
   原图（**有意行为翻转**：v0.60 原图兜底已废除；失败原图被 _enforce_payload_image_policy
   判 external → 整单 IMAGE_GEN_ALL_FAILED。缺图走 prepare :3890 统一主图降级链）。
2. 图片链 #2（🟠）：social_proof 降级循环缺 MxouModelConfigError break——配置错模型
   此前被 except Exception 吞掉继续对下一降级模型重烧 POST；对齐 main_image_gen:138-145。
3. Top10 #8（🟠）：_CONTENT_VIOLATION_KEYWORDS 裸 "content" 误判面收敛——
   "invalid content type" 类普通错误不再判永久违规；grsai status=="violation"
   独立通道不受影响。

运行（纯 mock，无 PG、无网络）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_arch_findings_v1.py -q
"""
import os
import sys
from unittest.mock import patch

import pytest

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.variant_primary_loop_node import variant_primary_loop_node  # noqa: E402
import graphs.nodes.variant_primary_loop_node as _variant_mod  # noqa: E402
import graphs.nodes.social_proof_gen_node as _social_mod  # noqa: E402
import utils.mxou_api as mxou_api  # noqa: E402

# fix/image-ref-pollution 白名单内的合格 alicdn 原尺寸图（v0.60 旧兜底会返回它）
GOOD_ORIGINAL = "https://cbu01.alicdn.com/img/ibank/O1CNgood_!!123456789-0-cib.jpg"
GOOD_ORIGINAL_2 = "https://cbu01.alicdn.com/img/ibank/O1CNgood_!!123456789-1-cib.jpg"
GEN_URL = "https://cos.example.com/file/images/gen_variant.png"

# 无 thread_id → 任务生图缓存不启用（与 test_ref_image_pollution_guard 同口径）
_CONFIG = {}
_RUNTIME = type("FakeRuntime", (), {"context": None})()

_BODY_CONFIG = "400: 模型价格尚未由管理员配置，请联系管理员"


# ═══ Top10 #1: variant 失败产物不再含原图 ═══

def test_variant_mixed_success_and_failure_isolated():
    """失败隔离保持：第 1 变体成功返回 AI 图，第 2 变体失败留空——互不拖累且无原图。"""
    # ⚠️ 用节点真实 Input（含 token）；VariantLoopState 无 token 字段会让 _gen_one
    # 在读 state.token 时就走异常分支（test_ref_image_pollution_guard 同款陷阱）。
    # ⚠️ 两变体用不同参考图 + 按参考图判定 mock 结果：节点是 ThreadPool 并发，
    # 调用计数器在双线程下与变体顺序无保证（全量套件进程里曾稳定翻转出
    # ['', GEN_URL]），按参数判定才是线程安全的确定性写法。
    from graphs.nodes.variant_primary_loop_node import VariantPrimaryLoopInput

    state = VariantPrimaryLoopInput(
        variants=[{"name": "红", "image": GOOD_ORIGINAL},
                  {"name": "蓝", "image": GOOD_ORIGINAL_2}],
        draft={"title": "保温杯"},
        token="t",
    )

    def _ok_only_for_red(*args, **kwargs):
        refs = kwargs.get("ref_images") or (args[4] if len(args) > 4 else [])
        return GEN_URL if refs and refs[0] == GOOD_ORIGINAL else None

    with patch.object(_variant_mod, "call_mxou_image_api", side_effect=_ok_only_for_red), \
         patch.object(_variant_mod, "get_image", return_value=None), \
         patch.object(_variant_mod, "save_image"):
        out = variant_primary_loop_node(state, _CONFIG, _RUNTIME)

    assert list(out.variant_primary_images) == [GEN_URL, ""], out.variant_primary_images
    assert GOOD_ORIGINAL not in out.variant_primary_images, "失败产物绝不含 1688 原图"
    assert GOOD_ORIGINAL_2 not in out.variant_primary_images, "失败产物绝不含 1688 原图"


def test_variant_exception_path_no_original_fallback():
    """生图 API 抛普通异常 → 同样留空，不回退 alicdn 原图（v0.60 异常兜底一并废除）。"""
    from graphs.nodes.variant_primary_loop_node import VariantPrimaryLoopInput

    state = VariantPrimaryLoopInput(
        variants=[{"name": "红", "image": GOOD_ORIGINAL}],
        draft={"title": "保温杯"},
        token="t",
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("upstream 500")

    with patch.object(_variant_mod, "call_mxou_image_api", side_effect=_boom), \
         patch.object(_variant_mod, "get_image", return_value=None), \
         patch.object(_variant_mod, "save_image"):
        out = variant_primary_loop_node(state, _CONFIG, _RUNTIME)

    assert list(out.variant_primary_images) == [""], out.variant_primary_images
    assert GOOD_ORIGINAL not in out.variant_primary_images


# ═══ 图片链 #2: social_proof 降级循环配置错快停 ═══

class _ProgressStub:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def test_social_proof_breaks_loop_on_config_error(monkeypatch):
    """降级循环：fast 配置错 → 立即 break，不再对 nano-banana-2-lite 重烧 POST
    （形态对齐 main_image_gen:138-145）。"""
    from graphs.state_image_gen import SocialProofInput

    calls = []

    def _fake_call(*a, **k):
        calls.append(k.get("model"))
        if k.get("model") == "nano-banana-fast":
            # fast 也配置错（其链内 lite 同错）→ API 编排层链耗尽上抛
            raise mxou_api.MxouModelConfigError(model="nano-banana-fast", body=_BODY_CONFIG)
        return None  # 主模型普通失败

    for name, val in (
        ("slot_enabled", lambda *a, **k: True),
        ("get_image", lambda *a, **k: None),
        ("_task_id_from_config", lambda *a, **k: None),
        ("assemble_prompt", lambda *a, **k: "prompt"),
        ("merge_visual_vars", lambda *a, **k: {}),
        ("resolve_color_preset", lambda *a, **k: ""),
        ("get_image_model", lambda *a, **k: "gpt-image-2.5"),
        ("ProgressLogger", _ProgressStub),
        ("call_mxou_image_api", _fake_call),
        ("save_image", lambda *a, **k: None),
    ):
        monkeypatch.setattr(_social_mod, name, val)

    state = SocialProofInput(
        draft={"title": "测试商品"}, token="tok",
        white_bg_image="https://img/wb.png",
    )
    out = _social_mod.social_proof_gen_node(state, {}, _RUNTIME)

    assert out.social_proof_image is None
    assert calls == ["gpt-image-2.5", "nano-banana-fast"], (
        f"配置错后必须 break 跳过 nano-banana-2-lite，实际调用 {calls}"
    )


# ═══ Top10 #8: 违规关键词精确化 ═══

def test_content_violation_no_bare_content_false_positive():
    """裸 "content" 已移除：普通错误文案（invalid content type 等）不再判永久违规。"""
    assert mxou_api._is_content_violation_error("failed", "invalid content type") is False
    assert mxou_api._is_content_violation_error("failed", "invalid content_type parameter") is False
    assert mxou_api._is_content_violation_error("failed", "upstream timeout") is False


def test_content_violation_real_hits_kept():
    """真实违规提示仍命中（精确词 + 中俄文违规词保持）。"""
    assert mxou_api._is_content_violation_error("failed", "content policy violation") is True
    assert mxou_api._is_content_violation_error("failed", "policy: sensitive content") is True
    assert mxou_api._is_content_violation_error("failed", "adult content detected") is True
    assert mxou_api._is_content_violation_error("failed", "request flagged by moderation") is True
    assert mxou_api._is_content_violation_error("failed", "图片内容违规，请调整") is True
    assert mxou_api._is_content_violation_error("failed", "内容包含敏感元素") is True


def test_grsai_violation_status_independent_of_keywords(monkeypatch):
    """grsai 轮询 status=="violation" 独立于关键词表：error 文案不含任何关键词也抛违规。"""

    class _R:
        def __init__(self, j):
            self.status_code = 200
            self._j = j

        def json(self):
            return self._j

    class FakeSession:
        def get(self, url, params=None, timeout=None, **kwargs):
            return _R({"status": "violation", "error": "weird upstream text"})

    monkeypatch.setattr(mxou_api, "_get_session", lambda: FakeSession())
    monkeypatch.setattr(mxou_api, "GRSAI_API_KEY", "k")
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)

    with pytest.raises(mxou_api.MxouContentViolationError):
        mxou_api._poll_grsai_task("task1", max_wait=35, token="tok")
