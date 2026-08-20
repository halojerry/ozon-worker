# -*- coding: utf-8 -*-
"""9048 型号名称前缀方案（Q4 P1-1）— 跨卖家不并卡。

根因：prepare_ozon_upload_node.py L2237 把 9048 = 裸 1688 item_id，同货源竞品
也是同一 item_id → 9048 相同 → Ozon 判定同型号并卡（产品跑到别人卡下变跟卖）。

方案：9048 = f"{item_id}~{sha1(normalize(supplier)|normalize(source_title))[:8]}"
- hash 只依赖信封确定性字段（supplier + 原始中文标题），绝不用 LLM 翻译后标题
- 自家多 SKU：同 item_id+同 supplier+同标题 → 同 hash → 变体仍并入自家卡 ✅
- 跨卖家同货源：supplier 或标题不同 → hash 不同 → 不并入竞品卡 ✅
- normalize：strip + 内部空白归一 + 全角转半角；supplier 空退化只 hash 标题；
  两者都空退化裸 item_id（与现状等价兜底）

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_model_name_9048.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from graphs.nodes.prepare_ozon_upload_node import _derive_model_name_9048  # noqa: E402


# ── 1. 确定性：同输入恒同值（retry/repair 重跑不拆卡）──
def test_deterministic_same_input():
    a = _derive_model_name_9048("980815374096", "义乌市阔折塑料制品厂", "宠物自动饮水器")
    b = _derive_model_name_9048("980815374096", "义乌市阔折塑料制品厂", "宠物自动饮水器")
    assert a == b, "同输入必须恒同值（防 retry 拆卡）"


# ── 2. 跨卖家区分：不同 supplier → 不同 9048（不并卡核心）──
def test_different_supplier_differs():
    a = _derive_model_name_9048("980815374096", "供应商A", "宠物自动饮水器")
    b = _derive_model_name_9048("980815374096", "供应商B", "宠物自动饮水器")
    assert a != b, "不同 supplier 必须产生不同 9048（否则仍并卡）"


# ── 3. 跨卖家区分：不同标题 → 不同 9048 ──
def test_different_title_differs():
    a = _derive_model_name_9048("980815374096", "供应商A", "宠物自动饮水器")
    b = _derive_model_name_9048("980815374096", "供应商A", "宠物饮水机升级款")
    assert a != b, "不同标题必须产生不同 9048"


# ── 4. 自家多 SKU 一致：同 item+同 supplier+同标题 → 同值（变体仍合并自家卡）──
def test_same_variant_same_value():
    values = {
        _derive_model_name_9048("980815374096", "供应商A", "宠物自动饮水器")
        for _ in range(3)
    }
    assert len(values) == 1, "自家多 SKU 必须共用同一 9048（否则变体拆卡）"


# ── 5. 格式：item_id~8位hex ──
def test_format_item_id_tilde_hash():
    v = _derive_model_name_9048("980815374096", "供应商A", "宠物自动饮水器")
    item_id, _, hexpart = v.partition("~")
    assert item_id == "980815374096", f"前缀应为 item_id，实际 {item_id}"
    assert len(hexpart) == 8 and all(c in "0123456789abcdef" for c in hexpart), \
        f"hash 应为 8 位 hex，实际 {hexpart!r}"


# ── 6. 退化路径：supplier 空 → 只 hash 标题；两者空 → 裸 item_id ──
def test_fallback_no_supplier():
    a = _derive_model_name_9048("980815374096", "", "宠物自动饮水器")
    b = _derive_model_name_9048("980815374096", None, "宠物自动饮水器")
    assert a == b, "supplier 空/None 应等值（退化只 hash 标题）"
    assert a.startswith("980815374096~"), f"supplier 空仍须带 hash 前缀，实际 {a}"


def test_fallback_both_empty():
    v = _derive_model_name_9048("980815374096", "", "")
    assert v == "980815374096", f"supplier+标题都空应退化裸 item_id，实际 {v}"


# ── 7. normalize：空白/全角差异 → 同 hash（防同一供应商写法漂移拆卡）──
def test_normalize_whitespace_fullwidth():
    a = _derive_model_name_9048("980815374096", "义乌市阔折塑料制品厂", "宠物自动饮水器")
    b = _derive_model_name_9048("980815374096", " 义乌市 阔折 塑料制品厂 ", "宠物自动饮水器")
    c = _derive_model_name_9048("980815374096", "义乌市阔折塑料制品厂", " 宠物 自动 饮水器 ")
    assert a == b, "supplier 内部空白差异应 normalize 同值"
    assert a == c, "标题首尾/内部空白差异应 normalize 同值"


# ── 8. 值长度合理（Ozon 自由文本无 dict 约束，~25 字符）──
def test_value_length_reasonable():
    v = _derive_model_name_9048("980815374096", "义乌市阔折塑料制品厂", "宠物自动饮水器")
    assert len(v) <= 30, f"9048 值应 ≤30 字符，实际 {len(v)}: {v}"
