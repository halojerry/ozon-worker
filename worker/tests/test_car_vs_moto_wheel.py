# -*- coding: utf-8 -*-
"""
v0.62.4 汽车/摩托 类目领域消歧单测（纯函数，无需 PG）。

背景（Sentry /prod 实证 POUDING_OZON-E4/E1/E2/E3/42 等）: 商品「汽车轮毂」被错配成
「摩托车轮毂」（dc=200001531/tp=971447047），导致下游 7387(截面宽度)/7389(直径英寸)/
12882(证书编号) 必填属性缺失 → 任务反复失败。根因：中文类目树把「汽车轮毂」译成
「轮辋/车轮总成」(dc=17028758/tp=970619447)，字面不含「轮毂」，而「摩托车轮毂」字面含
「轮毂」；同时「汽车」上下文在 overlap 验证时被 source_keywords 吸收丢弃。

本测试锁定 _apply_vehicle_disambiguation 的判别行为：
- 明确「汽车」信号 & 无「摩托」→ 剔除摩托车子树候选
- 明确「摩托」信号 & 无「汽车」→ 剔除汽车专属子树候选
- 双信号 / 全无信号 → 不判别（保持原候选）
- 剔除后空 → 维持原样兜底（不硬阻断）

运行: cd worker && PYTHONPATH=src ../skill/.venv314/bin/python3 -m pytest tests/test_car_vs_moto_wheel.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes.assemble_ozon_product_node import _apply_vehicle_disambiguation


# 真实类目树节点（category_tree.json）
MOTO_WHEEL = {
    "description_category_id": 200001531,
    "type_id": 971447047,
    "node_name": "摩托车轮毂",
    "full_path": "汽车用品 > 摩托车零件 > 摩托车轮毂",
    "similarity": 0.9,
}
CAR_RIM = {
    "description_category_id": 17028758,
    "type_id": 970619447,
    "node_name": "车轮总成",
    "full_path": "汽车用品 > 轮辋 > 车轮总成",
    "similarity": 0.6,
}
CAR_TYRE = {
    "description_category_id": 17028758,
    "type_id": 94765,
    "node_name": "乘用车轮胎",
    "full_path": "汽车用品 > 轮胎 > 乘用车轮胎",
    "similarity": 0.55,
}
CANDIDATES = [MOTO_WHEEL, CAR_RIM, CAR_TYRE]


def _names(cands):
    return [c["node_name"] for c in cands]


def test_car_signal_excludes_moto_subtree():
    # 「汽车轮毂」应剔除摩托车轮毂，保留汽车轮辋/乘用车轮胎
    res = _apply_vehicle_disambiguation(CANDIDATES, "汽车轮毂 铝合金 18寸")
    assert "摩托车轮毂" not in _names(res)
    assert "车轮总成" in _names(res)
    assert "乘用车轮胎" in _names(res)


def test_car_signal_keeps_only_car():
    # 默认候选里只有摩托+汽车；剔除摩托后应只剩汽车
    res = _apply_vehicle_disambiguation([MOTO_WHEEL, CAR_RIM], "汽车轮毂")
    assert [c["node_name"] for c in res] == ["车轮总成"]


def test_moto_signal_excludes_car_only():
    res = _apply_vehicle_disambiguation(CANDIDATES, "摩托车轮毂")
    assert "摩托车轮毂" in _names(res)
    assert "车轮总成" not in _names(res)
    assert "乘用车轮胎" not in _names(res)


def test_moto_signal_keeps_moto_candidate_not_car():
    res = _apply_vehicle_disambiguation([MOTO_WHEEL, CAR_RIM, CAR_TYRE], "摩托车轮毂")
    assert [c["node_name"] for c in res] == ["摩托车轮毂"]


def test_dual_signal_no_disambiguation():
    # 「汽车摩托车通用」双信号 → 不判别，保持原三者
    res = _apply_vehicle_disambiguation(CANDIDATES, "汽车摩托车通用配件 轮毂")
    assert set(_names(res)) == {"摩托车轮毂", "车轮总成", "乘用车轮胎"}


def test_no_car_moto_signal_keeps_all():
    # 仅「轮毂」无汽车/摩托信号 → 不判别
    res = _apply_vehicle_disambiguation(CANDIDATES, "铝合金 18寸 轮毂")
    assert len(res) == 3


def test_empty_candidates_or_signal_returns_as_is():
    assert _apply_vehicle_disambiguation([], "汽车轮毂") == []
    assert _apply_vehicle_disambiguation(CANDIDATES, "") == CANDIDATES


def test_car_signal_all_moto_falls_back_to_original():
    # 明确汽车信号但候选全是摩托子树 → 剔除后空 → 维持原样（不硬阻断）
    res = _apply_vehicle_disambiguation([MOTO_WHEEL], "汽车轮毂")
    assert [c["node_name"] for c in res] == ["摩托车轮毂"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
