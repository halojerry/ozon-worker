"""C3 (sentry-attribute-fixes): 描述西里尔社交词净化 + FB_INSTA unfixable 路由回归。

背景: 俄罗斯认定 Meta(Instagram/Facebook)/Telegram/YouTube 为极端组织,
描述含这些词被 Ozon 拒(FB_INSTA)。prepare 节点词边界过滤社交词,
retry loop 将 FB_INSTA 标 unfixable(不自动重试, 用户/agent 手动处理)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from graphs.nodes.prepare_ozon_upload_node import (
    _SOCIAL_MEDIA_WORDS_RU,
    _append_spec_table,
    _sanitize_description,
    _sanitize_rich_description,
)
from graphs.validation_retry_loop import (
    ERROR_NOTICE_MAP,
    FIX_TYPE_ATTRIBUTES,
    FIX_TYPE_PRODUCT_IMPORT,
    FIX_TYPE_UNFIXABLE,
    REPAIR_STRATEGY,
    classify_fix_type,
)


# ── _sanitize_description: 西里尔社交词移除 ──

def test_description_removes_insta():
    """描述含 инстаграм 被移除。"""
    out = _sanitize_description("инстаграм follow")
    assert "инстаграм" not in out


def test_description_removes_vk_case_insensitive():
    """ВКонтакте 大小写不敏感移除。"""
    out = _sanitize_description("ВКонтакте group")
    assert "вконтакте" not in out.lower()


def test_description_all_social_words_removed():
    """清单内全部 5 个西里尔社交词均被移除。"""
    assert len(_SOCIAL_MEDIA_WORDS_RU) == 5
    for w in _SOCIAL_MEDIA_WORDS_RU:
        out = _sanitize_description(f"{w} тест")
        assert w not in out, f"社交词 {w} 未被移除: '{out}'"


# ── 词边界: 不误杀合法词 ──

def test_description_word_boundary_keeps_telegramma():
    """词边界: телеграмма(电报) 不被 телеграм 子串误杀。"""
    out = _sanitize_description("телеграмма старинная")
    assert "телеграмма" in out


def test_description_keeps_odnoklassniki():
    """одноклассники(同学们) 不在清单 → 保留。"""
    out = _sanitize_description("подарок одноклассникам")
    assert "одноклассникам" in out


# ── _sanitize_rich_description: HTML 标签结构保留 ──

def test_rich_description_removes_insta_keeps_tags():
    """富文本删社交词且保留 <p> 标签结构。"""
    out = _sanitize_rich_description("<p>Подпишись на инстаграм</p>")
    assert "инстаграм" not in out
    assert "<p>" in out and "</p>" in out


def test_rich_description_removes_social_word_boundary():
    """富文本同样词边界: телеграмма 保留。"""
    out = _sanitize_rich_description("<p>Телеграмма отправлена вовремя</p>")
    assert "Телеграмма" in out
    assert "<p>" in out


# ── _append_spec_table: 规格表属性值同步净化 ──

def test_spec_table_removes_social_value():
    """规格表属性值含 инстаграм → 清空后该行跳过。"""
    attrs = [{"id": 1, "name": "Соцсеть", "value": "инстаграм"}]
    out = _append_spec_table("Описание.", attrs)
    assert "инстаграм" not in out
    assert "<td>Соцсеть" not in out  # 值被清空 → 行跳过


def test_spec_table_keeps_legal_word():
    """规格表值含合法词(одноклассникам) → 保留行。"""
    attrs = [{"id": 1, "name": "Назначение", "value": "подарок одноклассникам"}]
    out = _append_spec_table("Описание.", attrs)
    assert "одноклассникам" in out
    assert "<td>Назначение" in out


# ── _append_spec_table: 中文属性名净化 (Sentry C2 根因锁定) ──

def test_spec_table_cleans_chinese_attr_name():
    """schema 以 ZH_HANS 返回时属性名是中文(品牌/原产国) → 规格表必须净化,
    否则描述含中文字符 → ozon_validate 拦截 (Sentry POUDING_OZON-C2)。"""
    attrs = [{"id": 85, "name": "品牌", "value": "Нет бренда"},
             {"id": 4389, "name": "原产国", "value": "Китай"}]
    out = _append_spec_table("Описание.", attrs)
    # 中文属性名被清空 → 整行跳过（不渲染 <td>品牌</td>）
    assert "品牌" not in out
    assert "原产国" not in out
    assert "<td>Нет бренда" not in out  # 值虽合法但属性名净化后为空 → 行跳过


def test_spec_table_keeps_cyrillic_attr_name():
    """俄语属性名(Характеристики/Материал) → 正常保留。"""
    attrs = [{"id": 10, "name": "Материал", "value": "полипропилен"}]
    out = _append_spec_table("Описание.", attrs)
    assert "<td>Материал</td>" in out
    assert "полипропилен" in out


# ── retry loop: FB_INSTA → unfixable ──

def test_classify_fix_type_fb_insta_unfixable():
    """FB_INSTA 分类为 unfixable(不浪费重试次数)。"""
    assert classify_fix_type("FB_INSTA") == "unfixable"


def test_repair_strategy_fb_insta_unfixable():
    """FB_INSTA 修复策略 = unfixable(不自动重写描述)。"""
    assert REPAIR_STRATEGY.get("FB_INSTA") == "unfixable"


def test_fb_insta_in_unfixable_set():
    """FB_INSTA 在 FIX_TYPE_UNFIXABLE 且不在其他修复类型。"""
    assert "FB_INSTA" in FIX_TYPE_UNFIXABLE
    assert "FB_INSTA" not in FIX_TYPE_ATTRIBUTES
    assert "FB_INSTA" not in FIX_TYPE_PRODUCT_IMPORT


def test_error_notice_fb_insta():
    """FB_INSTA 用户提示存在且说明社交媒体原因。"""
    notice = ERROR_NOTICE_MAP["FB_INSTA"]
    assert notice
    assert "社交媒体" in notice
