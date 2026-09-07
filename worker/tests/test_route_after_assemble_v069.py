"""route_after_assemble 回归（v0.69 E2E 实证修复锁定）：

汽油桶双命中用例暴露的两缺陷：
1. `getattr(...) or 1.0` 把合法 0.0 当 falsy 吞成 1.0 → 受限闸出口 match_confidence=0.0
   不阻断 → 流程空跑到上传（终态对、算力白烧 + 拒审文案覆盖阻断文案）；
2. `_blocked_exit` 统一 failed_stage="category_match" 后路由未消费该通道。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from graphs.graph import route_after_assemble  # noqa: E402


def _state(**kw):
    base = {"failed_stage": "", "error_message": "", "match_confidence": 1.0}
    base.update(kw)
    return SimpleNamespace(**base)


def test_01_zero_confidence_not_swallowed_by_falsy_or():
    """受限闸出口 match_confidence=0.0 → 必须阻断（falsy or 1.0 回归锁定）。"""
    assert route_after_assemble(_state(match_confidence=0.0)) == "失败"


def test_02_failed_stage_category_match_blocks():
    """_blocked_exit 统一 failed_stage="category_match" → 阻断（文案无关）。"""
    assert route_after_assemble(_state(
        failed_stage="category_match",
        error_message="需资质/受限品类：……已转入人工确认",
        match_confidence=0.0,
    )) == "失败"


def test_03_restricted_message_blocks_without_failed_stage():
    """无 failed_stage 时受限文案仍兜底阻断。"""
    assert route_after_assemble(_state(error_message="需资质/受限品类：汽油")) == "失败"
    assert route_after_assemble(_state(error_message="类目匹配失败：无候选")) == "失败"


def test_04_operator_add_accumulated_stage_still_blocks():
    """failed_stage 为 operator.add 累积串（如 "authcategory_match"）→ 子串命中阻断。"""
    assert route_after_assemble(_state(failed_stage="authcategory_match")) == "失败"


def test_05_success_path_passes():
    """正常组装 → 继续；高置信/无错误不误伤。"""
    assert route_after_assemble(_state()) == "成功"
    assert route_after_assemble(_state(match_confidence=0.85)) == "成功"
    assert route_after_assemble(_state(failed_stage="", match_confidence=None)) == "成功"


def test_06_other_failed_stage_does_not_block_here():
    """非类目类 failed_stage（如 pricing）不在此路由阻断（各路由管各段）。"""
    assert route_after_assemble(_state(failed_stage="pricing")) == "成功"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
    print(f"{passed}/{len(fns)} passed")
