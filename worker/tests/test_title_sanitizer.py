"""v0.28.5 A3 回归: sanitize_title 标题净化 — 零拉丁/零中文(无 LLM, 纯正则路径)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from utils.title_sanitizer import sanitize_title


def test_removes_latin_letters():
    """清拉丁字母(非 LLM 路径, _LATIN_RE)。"""
    result = sanitize_title("Настольный вентилятор USB Fan model X")
    assert result is not None
    assert "USB" not in result
    assert "Fan" not in result
    assert "вентилятор" in result


def test_removes_chinese_chars():
    """清中文字符(_CJK_RE)。"""
    result = sanitize_title("Настольный вентилятор 桌面风扇")
    assert "桌面风扇" not in result
    assert "вентилятор" in result


def test_empty_if_no_cyrillic():
    """无西里尔字符 → 返回空(触发调用方公式生成兜底)。"""
    assert sanitize_title("USB Fan 123") == ""


def test_truncate_to_80():
    """截断到 80 字符。"""
    result = sanitize_title("Вентилятор настольный " + "х" * 100)
    assert result is None or len(result) <= 80


def test_remove_marketing_words():
    """清营销词。"""
    result = sanitize_title("СУПЕР Новый настольный вентилятор скидка")
    assert result is not None
    assert "СУПЕР" not in result


def test_llm_path_fallback_without_token():
    """无 token 时 use_llm=True 也走正则(不抛异常)。"""
    result = sanitize_title("Настольный вентилятор 桌面", token="", use_llm=True)
    assert result is not None
    assert "桌面" not in result
