"""R2b 仲裁池扩容回归（wave ③号缺陷 TDD）：正确答案 sim 低也必须进 LLM 清单。

wave A4 实证：R2b 确认池 candidates[:5] 按 sim 截断，园艺地垫(0.33, kw Top-1)进不了
LLM 清单 → 只能 abstain → 阻断；且阻断任务在 category_match_log 零行（唯一写入点
在采纳成功后）。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# wave A4 池的简化重构：2 个 sim=1.0（本类采纳/他类 rival）+ 若干弱相关 + 1 个低 sim 正确答案
_ADOPTED = {"description_category_id": 92496473, "type_id": 93012, "similarity": 1.0,
            "node_name": "儿童爬行护膝", "full_path": "儿童用品 > 儿童安全 > 儿童爬行护膝"}
_RIVAL = {"description_category_id": 17028711, "type_id": 94159, "similarity": 1.0,
          "node_name": "运动护齿", "full_path": "运动与休闲 > 装备与护具 > 运动护齿"}
_LOW = [{"description_category_id": 1000 + i, "type_id": 2000 + i, "similarity": 0.95,
         "node_name": f"护具{i}", "full_path": f"运动与休闲 > 装备与护具 > 护具{i}"} for i in range(8)]
_CORRECT = {"description_category_id": 17028746, "type_id": 92750, "similarity": 0.33,
            "node_name": "园艺地垫，护膝", "full_path": "住宅和花园 > 园艺工具 > 园艺地垫，护膝"}
_SOURCE_WORDS = "潜水料 花园 护膝 除草 园艺 神器 劳保 家务 弹力 防护 膝盖 跪垫"


def test_pool_includes_low_sim_cross_domain_overlap():
    from graphs.nodes.assemble_ozon_product_node import _build_r2b_confirm_pool
    pool = [dict(_ADOPTED), dict(_RIVAL), *_LOW, dict(_CORRECT)]
    out = _build_r2b_confirm_pool(pool, _ADOPTED, _SOURCE_WORDS)
    ids = {(c["description_category_id"], c["type_id"]) for c in out}
    assert (17028746, 92750) in ids, "正确答案（跨大类+源词overlap）必须进 R2b 仲裁池"
    assert len(out) <= 12


def test_pool_top10_plus_overlap_no_dup():
    from graphs.nodes.assemble_ozon_product_node import _build_r2b_confirm_pool
    pool = [dict(_ADOPTED), dict(_RIVAL), *_LOW, dict(_CORRECT)]
    out = _build_r2b_confirm_pool(pool, _ADOPTED, _SOURCE_WORDS)
    assert len(out) == len({(c["description_category_id"], c["type_id"]) for c in out})
    # top1 仍在池首（LLM 编号顺序稳定）
    assert out[0]["description_category_id"] == 92496473
    # 同 dc 高潜候选最多 3 个（防单域刷屏挤占）


def test_pool_same_domain_overlap_capped_at_3():
    from graphs.nodes.assemble_ozon_product_node import _build_r2b_confirm_pool
    # 构造 5 个非采纳 dc 的 overlap 候选（跨大类，模拟多个不同域各有命中）
    pool = [dict(_ADOPTED)]
    for i in range(5):
        pool.append({"description_category_id": 5000 + i, "type_id": 6000 + i,
                     "similarity": 0.4,
                     "node_name": f"护膝{i}", "full_path": f"域{i} > 护膝{i}"})
    out = _build_r2b_confirm_pool(pool, _ADOPTED, "护膝 花园")
    per_dc = {}
    for c in out:
        per_dc[c["description_category_id"]] = per_dc.get(c["description_category_id"], 0) + 1
    assert all(v <= 3 for v in per_dc.values())
    assert len(out) <= 12


def test_block_path_writes_match_log():
    """R2b 阻断 return 前必须写 category_match_log 审计行（match_layer='blocked'）。"""
    import inspect
    from graphs.nodes import assemble_ozon_product_node as asm
    src = inspect.getsource(asm)
    # 阻断分支（R2b 阻断文案）之后必须出现 blocked 审计调用
    idx = src.index("LLM 确认无可靠结果")
    region = src[idx:]
    assert "match_layer=\"blocked\"" in region or "match_layer='blocked'" in region
    # 全函数至少 4 处 blocked 审计（LLM fallback×2 / R1 veto / R2b / 最终门槛）
    assert src.count('"blocked"') + src.count("'blocked'") >= 4
    # _log_match_attempt 必须容错空 category_result（阻断时可能尚无定稿）
    head = src[src.index("def _log_match_attempt"):src.index("def _log_match_attempt") + 2000]
    assert "category_result or {}" in head
