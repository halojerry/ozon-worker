"""assemble 兜底表假事实清退 + «Тип» 判别属性唯一值闸（v0.81 收口批 defer 闭合）。

#85 清退了 prepare/retry 两张假事实兜底表（8205 保质期 730 天、储存条件等，
8050 同构），并给 A5 LLM 提案接了判别词交叉验证；assemble 侧因文件域隔离登记
defer。本文件锁定 assemble 侧同步闭合：
- KNOWN_DEFAULTS 不再含 8205（编造保质期天数）；
- «Тип» 系判别属性唯一值兜底前跑 _discriminant_conflict（桌面扇×«Напольный»
  根因在最后一条字典兜底路径闭合）；
- 非类型属性（颜色等）不受判别词闸误伤（作用域判定锁）。
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.nodes import assemble_ozon_product_node as asm  # noqa: E402
from utils.attr_defaults import _discriminant_conflict  # noqa: E402


def _node_src() -> str:
    return inspect.getsource(asm)


def test_known_defaults_8205_fabricated_entry_removed():
    """8205「保质期 730 天」编造事实不得再以默认值条目存在（注释提及不限）。"""
    assert "8205:" not in _node_src()


def test_known_defaults_neutral_entries_kept():
    """语义中性条目保留：8962 件数=1（单件事实）、8292 不合并（平台选项）。"""
    src = _node_src()
    assert "8962: \"1\"" in src
    assert "8292: \"0\"" in src


def test_discriminant_gate_wired_into_unique_fallback():
    """«Тип» 系唯一值兜底前必须过判别词交叉验证（#85 defer 闭合锁）。"""
    src = _node_src()
    assert "_discriminant_conflict(draft_title, fallback[1])" in src
    assert '"тип" in attr_name.lower()' in src


def test_discriminant_conflict_desktop_fan_case():
    """功能锚：源标题「桌面」×唯一值 «Напольный»（落地）→ 冲突，应跳过。"""
    assert _discriminant_conflict("USB桌面风扇静音", "Напольный") is True


def test_discriminant_conflict_consistent_case_not_blocked():
    """源标题与唯一值形态一致（或无判别词）→ 不拦。"""
    assert _discriminant_conflict("USB桌面风扇静音", "Настольный") is False
    assert _discriminant_conflict("USB小风扇静音", "Напольный") is False


def test_discriminant_gate_scoped_to_type_attrs_only():
    """作用域锁：闸条件只挂 Тип 系属性名，颜色等非类型属性不进闸分支。"""
    src = _node_src()
    # 闸分支与唯一值兜底同段（pick_dict_fallback_value 之后）
    gate_pos = src.find("_discriminant_conflict(title, fallback[1])")
    fb_pos = src.rfind("pick_dict_fallback_value(missing_id", 0, gate_pos)
    assert fb_pos != -1, "判别闸必须紧跟唯一值兜底之后"
    # 同段内不得把闸扩到属性名无关的无条件形式
    assert "and _discriminant_conflict(" in src  # 存在 attr_name 作用域与运算
