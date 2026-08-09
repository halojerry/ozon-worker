#!/usr/bin/env python3
"""生图 prompt 基线/A-B 测量 harness（Wave 1-E / Wave 2 门禁）。

零成本模式（--dry-run，默认）：
  用同一批 draft 渲染旧/新模板，统计 8 必填 + 11 可选视觉变量的 prompt 覆盖度，
  不触发真实生图。用于证明「模板增强确实让变量进 prompt」（Gao G4 基线批评的门禁）。

真实生图模式（--live，需用户批准预算）：
  实际调用 mxou 生图 API，用 image_quality_evaluator 评估出图质量通过率。
  ⚠️ 消耗生图额度，默认禁用；仅当用户明确确认预算后使用。

用法：
  PYTHONPATH=src python3 scripts/image_prompt_ab_test.py --dry-run        # 零成本基线
  PYTHONPATH=src python3 scripts/image_prompt_ab_test.py --dry-run --samples 10
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jinja2 import Template

# ── 8 必填 + 11 可选（对抗收敛 AC-1 重写：8 必填非空 + 11 可选默认化）──
REQUIRED_VARS = ("product", "color", "material", "appearance", "size", "lighting", "effects", "text_areas")
OPTIONAL_VARS = ("model", "action", "scene", "background", "icons", "inset", "gift", "atmosphere", "packaging", "problem_scene", "comparison")

ALL_SLOTS = ["main", "white_bg", "multi_angle", "scene_1", "scene_2", "scene_3", "comparison", "detail", "social_proof", "variant_white_bg"]

DEFAULT_SAMPLES = [
    {"title": "便携小风扇 白色", "attributes": {"材质": "ABS塑料", "颜色": "白色"},
     "weight": 227, "dimensions": {"length": 120, "width": 80, "height": 60}, "category": "风扇"},
    {"title": "硅胶折叠杯 户外", "attributes": {"材质": "硅胶", "颜色": "黑色"},
     "weight": 152, "dimensions": {"length": 80, "width": 60, "height": 40}, "category": "水杯"},
    {"title": "驱蚊棒 60支", "attributes": {"材质": "香茅"},
     "weight": 150, "dimensions": {"length": 200, "width": 30, "height": 30}, "category": "驱蚊"},
    {"title": "智能风扇 露营灯", "attributes": {"材质": "塑料"},
     "weight": 2000, "dimensions": {"length": 188, "width": 141, "height": 94}, "category": "风扇"},
]


def _render(cfg: dict, key: str, vars_: dict) -> str:
    tpl = cfg.get(key, "")
    if not isinstance(tpl, str):
        tpl = json.dumps(tpl, ensure_ascii=False)
    return Template(tpl).render(**vars_)


def _completeness(prompt: str, vars_: dict) -> dict:
    """统计 8 必填 + 11 可选变量在 prompt 中的覆盖度。"""
    hit = {name: (bool(vars_.get(name)) and str(vars_[name]) in prompt) for name in REQUIRED_VARS + OPTIONAL_VARS}
    return {
        "required": sum(1 for n in REQUIRED_VARS if hit[n]),
        "optional": sum(1 for n in OPTIONAL_VARS if hit[n]),
    }


def measure_dry_run(old_cfg_path: Path, new_cfg_path: Path, samples: list[dict]) -> dict:
    from utils.prompt_assembler import extract_visual_vars_from_draft

    old_cfg = json.loads(old_cfg_path.read_text(encoding="utf-8"))
    new_cfg = json.loads(new_cfg_path.read_text(encoding="utf-8"))
    # 旧模板只支持 title/scene_context；新模板走 extract + merge 语义
    total = {"old": {"required": 0, "optional": 0}, "new": {"required": 0, "optional": 0}}
    req_denom = len(REQUIRED_VARS) * len(ALL_SLOTS) * len(samples)
    opt_denom = len(OPTIONAL_VARS) * len(ALL_SLOTS) * len(samples)

    print(f"{'图位':<16} {'旧(必填/可选)':<18} {'新(必填/可选)':<18}")
    print("-" * 52)
    for slot in ALL_SLOTS:
        o_r = o_o = n_r = n_o = 0
        for s in samples:
            vv = extract_visual_vars_from_draft(s)
            old_p = _render(old_cfg, slot, {"title": s["title"], "scene_context": "场景"})
            new_p = _render(new_cfg, slot, {"title": s["title"], "scene_context": "场景", **vv})
            o_c = _completeness(old_p, vv)
            n_c = _completeness(new_p, vv)
            o_r += o_c["required"]; o_o += o_c["optional"]
            n_r += n_c["required"]; n_o += n_c["optional"]
        total["old"]["required"] += o_r; total["old"]["optional"] += o_o
        total["new"]["required"] += n_r; total["new"]["optional"] += n_o
        # 每槽位每样本最多 8 必填 / 11 可选
        r_max = len(REQUIRED_VARS) * len(samples)
        o_max = len(OPTIONAL_VARS) * len(samples)
        print(f"{slot:<16} {o_r}/{r_max}·{o_o}/{o_max}    {n_r}/{r_max}·{n_o}/{o_max}")

    r = total["old"]["required"] / req_denom * 100
    n = total["new"]["required"] / req_denom * 100
    print("-" * 52)
    print(f"必填变量覆盖: 旧 {r:.1f}% → 新 {n:.1f}%（Δ{n-r:+.1f}pp）")
    print(f"可选变量覆盖: 旧 {total['old']['optional']}/{opt_denom} → 新 {total['new']['optional']}/{opt_denom}")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="生图 prompt 基线/A-B 测量")
    parser.add_argument("--dry-run", action="store_true", help="零成本模式（默认，不触发真实生图）")
    parser.add_argument("--live", action="store_true", help="真实生图模式（⚠️ 消耗额度，需用户批准预算）")
    parser.add_argument("--samples", type=int, default=0, help="样本数（0=默认 4 个样例）")
    parser.add_argument("--old-template", type=Path, default=Path("/tmp/old_prompts.json"), help="旧模板路径")
    parser.add_argument("--new-template", type=Path, default=SRC.parent / "config" / "image_prompts.json", help="新模板路径")
    args = parser.parse_args()

    samples = DEFAULT_SAMPLES[: args.samples] if args.samples > 0 else DEFAULT_SAMPLES

    if args.live:
        print("⚠️ 真实生图模式需用户明确批准预算后才可执行（本 harness 不自行触发）")
        print("   请先在本地 Docker + 测试 token 下验证，或用 --dry-run 做零成本基线")
        return 1

    if not args.old_template.exists():
        print(f"❌ 旧模板不存在: {args.old_template}")
        print("   提示: 用 `git show <wave0_commit>:worker/config/image_prompts.json > /tmp/old_prompts.json` 提取")
        return 1

    measure_dry_run(args.old_template, args.new_template, samples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
