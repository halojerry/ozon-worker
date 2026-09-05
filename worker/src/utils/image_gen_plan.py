"""T7b: image_gen_plan 类型选择（受限映射，C3b 冻结）。

`image_gen_plan` = 现有 slot 子集选择 + 计数（type→count），只控制「执行/跳过」，
**不新增 slot、不做 graph 层重构**（BLOCKER 1 修复方案，见 docs/PLAN-webui-v1.md §2 C3b）。

| UI 类型 | slot 映射 | 说明 |
|---|---|---|
| 白底图 | white_bg | 现有节点 |
| 场景图 | scene_1/2/3 | 计数 0-3（plan key "scene" 按 count 展开） |
| 卖点图 | main_image | 主图兼卖点 |
| 细节图 | detail | 现有节点 |
| 对比图 | comparison | 现有节点 |
| 社交证明 | social_proof | 现有节点 |
| 多角度 | multi_angle | 现有节点 |
| 材质图/尺寸图 | 无现成节点 | v1 置灰，不提供（plan 中出现 → 忽略） |

- **默认 plan** = 精简 5 张（2026-09-05 成本决策）：white_bg / multi_angle / main_image /
  detail / scene×1 + variant_primary_loop（多 SKU 变体主图，上传必需）。
  关闭槽位（social_proof / comparison / scene_2 / scene_3）只是默认不生成，
  slot 本身仍在 ALL_SLOTS 冻结集合中——**未来重开只需经 config.configurable.image_gen_plan
  或 state.image_gen_plan 覆盖 plan，无需改代码/拓扑**。
- **plan 校验（Momus W1）**：plan 必须含 Phase1（white_bg 或 multi_angle）——
  Phase2 节点依赖 Phase1 输出作参考图（graph.py:235-245），仅 Phase2 类型 → 拒绝。
- **注入通道**：config.configurable.image_gen_plan（与 force_regen/regen_version 同源，
  main.py:400-402）→ 回退 state.image_gen_plan（Input schema 字段）→ DEFAULT_PLAN。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# C3b 冻结：Phase1 = white_bg + multi_angle（Phase2 节点依赖其输出作参考图）
PHASE1_SLOTS = frozenset({"white_bg", "multi_angle"})

# 10 个既有生图节点 slot（graph.py:76-85，不新增）
ALL_SLOTS = (
    "white_bg",
    "multi_angle",
    "main_image",
    "detail",
    "social_proof",
    "comparison",
    "scene_1",
    "scene_2",
    "scene_3",
    "variant_primary_loop",
)
_ALL_SLOTS_SET = frozenset(ALL_SLOTS)

# 场景图计数 0-3（C3b）
SCENE_SLOTS = ("scene_1", "scene_2", "scene_3")

# 默认 plan = 精简 5 张（2026-09-05 成本优化决策；"scene": 1 展开为 scene_1；
# variant 为多 SKU 路径，保留——变体主图缺失会 image_absent）。
# 保留槽位功能各不重叠：white_bg（合规净图 + Phase1 参考 + 主图兜底）、
#   multi_angle（多视角）、main_image（卡片首图）、detail（细节信任）、scene_1（使用场景）。
# 关闭槽位 = 暂时不用，可经 config.configurable.image_gen_plan / state.image_gen_plan
#   覆盖重开（全 10 张 = 旧 DEFAULT_PLAN 集合 + scene:3 + social_proof/comparison）。
DEFAULT_PLAN: Dict[str, int] = {
    "white_bg": 1,
    "multi_angle": 1,
    "main_image": 1,
    "detail": 1,
    "scene": 1,
    "variant_primary_loop": 1,
}


def validate_plan(plan: Optional[Dict[str, Any]]) -> None:
    """校验 image_gen_plan；非法 → 抛 ValueError。

    Momus W1：plan 必须含 Phase1（white_bg 或 multi_angle，count>=1）——Phase2
    节点依赖 Phase1 输出作参考图（graph.py:235-245）。仅 Phase2 类型 → 拒绝并提示
    「需至少包含白底图或多角度图」。未知类型（材质/尺寸 v1 置灰）不阻断。
    """
    if not isinstance(plan, dict) or not plan:
        raise ValueError("image_gen_plan 不能为空：需至少包含白底图或多角度图")
    has_phase1 = any(plan.get(slot, 0) for slot in PHASE1_SLOTS)
    if not has_phase1:
        raise ValueError("image_gen_plan 需至少包含白底图或多角度图（Phase2 节点依赖 Phase1 输出作参考图）")


def plan_to_slots(plan: Optional[Dict[str, Any]]) -> set[str]:
    """type→slot 展开：返回启用的 slot 集合。

    - "scene" 按 count 展开为 scene_1..scene_N（上限 3，C3b：场景图计数 0-3）
    - 其余类型 1:1（count>=1 启用；count=0 → 跳过）
    - 未知类型忽略（材质/尺寸 v1 置灰，不映射任何 slot）
    """
    slots: set[str] = set()
    if not isinstance(plan, dict):
        return slots
    for typ, count in plan.items():
        n = _count(count)
        if typ == "scene":
            for i in range(min(n, len(SCENE_SLOTS))):
                slots.add(SCENE_SLOTS[i])
        elif typ in _ALL_SLOTS_SET and n >= 1:
            slots.add(typ)
    return slots


def slot_enabled(config: Any, slot: str, state: Any = None) -> bool:
    """节点前置条件：plan 含该 slot → True（执行）；否则 False（跳过，不调生图 API）。

    读取优先级：config.configurable.image_gen_plan → state.image_gen_plan → DEFAULT_PLAN。
    非法 plan（非 dict）→ 回退默认全开（fail-safe，绝不静默全禁）。
    """
    plan = _plan_from_config(config)
    if plan is None and state is not None:
        plan = getattr(state, "image_gen_plan", None)
    if not isinstance(plan, dict):
        plan = DEFAULT_PLAN
    return slot in plan_to_slots(plan)


def _plan_from_config(config: Any) -> Optional[Dict[str, Any]]:
    """读 config.configurable.image_gen_plan（提交/regen 端点注入通道，与 force_regen 同源）。"""
    try:
        if config is None:
            return None
        conf = config.get("configurable", {}) if isinstance(config, dict) else {}
        return conf.get("image_gen_plan")
    except Exception:
        return None


def _count(value: Any) -> int:
    """plan count 宽松转 int（None/非数字 → 0，负数 → 0）。"""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
