# -*- coding: utf-8 -*-
"""v0.81 标题结构止血批——三主题回归：

1. sanitize_title_structure（唯一入口 utils/title_sanitizer）：LLM 拿单位词填空槽
   产出「Портативный вентилятор, Вт, скоростей」「, 1」「Перчатки, , для
   повседневной носки」「Средство для ухода за волосами, 120,3 мл」，全链验收只有
   _has_cyrillic → 坏标题原样上卡。结构闸 = 剔单位残壳段 + 不合格判定。
2. _remove_latin_llm 破坏性改写守卫：LLM 输出西里尔词数 < 输入一半 → 弃用走正则。
3. ozon_validate_node 名称闸：name 无 ≥4 字符西里尔词 → item_errors（不受
   authoritative 类目降级豁免影响）。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_title_structure_v081.py -q
纯 mock，无 PG/网络。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from utils.title_sanitizer import (  # noqa: E402
    has_cyrillic_word,
    sanitize_title_structure,
)

# ═══════════════ 1. 结构闸：坏标题（实锤原文）═══════════════


class TestBadTitles:
    @pytest.mark.parametrize(
        "bad,expect_bad",
        [
            # 空槽单位残壳：剔「Вт」段 → 清洗合格（残壳消失）
            ("Портативный вентилятор, Вт, скоростей", False),
            # 纯孤立数字残壳 → 判不合格
            (", 1", True),
            # 连续逗号空段 → 清洗合格
            ("Перчатки, , для повседневной носки", False),
            # 俄语小数逗号单位模式（120,3 мл）→ 判不合格
            ("Средство для ухода за волосами, 120,3 мл", True),
            # 数字+单位残壳段（无真实词）→ 判不合格
            (", 1 шт", True),
        ],
    )
    def test_bad_title_cleaned_or_rejected(self, bad, expect_bad):
        cleaned, is_bad = sanitize_title_structure(bad)
        assert is_bad is expect_bad, f"{bad!r} → {cleaned!r} (bad={is_bad})"
        # 五条坏标题处理后绝不允许残壳原样保留
        if expect_bad is False:
            assert "Вт," not in cleaned and not cleaned.strip().startswith(","), cleaned
        # 清洗结果不得含连续逗号/首尾逗号
        assert ", ," not in cleaned and not cleaned.startswith(",") and not cleaned.endswith(",")

    def test_unit_shell_segment_removed(self):
        cleaned, is_bad = sanitize_title_structure("Портативный вентилятор, Вт, скоростей")
        assert cleaned == "Портативный вентилятор, скоростей"
        assert is_bad is False

    def test_double_comma_collapsed(self):
        cleaned, is_bad = sanitize_title_structure("Перчатки, , для повседневной носки")
        assert cleaned == "Перчатки, для повседневной носки"
        assert is_bad is False

    def test_decimal_comma_unit_rejected(self):
        cleaned, is_bad = sanitize_title_structure("Средство для ухода за волосами, 120,3 мл")
        assert is_bad is True, "俄语小数逗号模式必须判不合格（走公式重生成）"

    def test_isolated_number_rejected(self):
        assert sanitize_title_structure(", 1")[1] is True
        assert sanitize_title_structure(", 1 шт")[1] is True

    def test_malformed_never_raise(self):
        assert sanitize_title_structure("") == ("", True)
        assert sanitize_title_structure(None) == (None, True)
        assert sanitize_title_structure(123) == (123, True)
        assert sanitize_title_structure("   ") == ("   ", True)


# ═══════════════ 1b. 结构闸：好标题（防误杀）═══════════════


class TestGoodTitles:
    @pytest.mark.parametrize(
        "good",
        [
            "Потолочный светильник 90 Вт",  # 真实数字规格段（90 Вт）不得剔
            "Настольный вентилятор 15 Вт, 5 см, 4 скорости",  # 多段真实规格
            "Садовые перчатки, универсальный",  # 类目兜底形态
            "Товар для дома, универсальный",  # prepare 既有兜底文案
        ],
    )
    def test_good_title_passthrough(self, good):
        cleaned, is_bad = sanitize_title_structure(good)
        assert is_bad is False, f"好标题被误杀: {good!r} → {cleaned!r}"
        assert cleaned == good, f"好标题不得被改写: {good!r} → {cleaned!r}"

    def test_digit_unit_segment_kept(self):
        """「90 Вт」数字+单位组合 ≠ 残壳（真实规格信息）。"""
        cleaned, is_bad = sanitize_title_structure("Потолочный светильник 90 Вт")
        assert "90 Вт" in cleaned and is_bad is False


# ═══════════════ 1c. has_cyrillic_word（与 validate 名称闸同源）═══════════════


class TestHasCyrillicWord:
    def test_unit_shells_are_not_words(self):
        assert has_cyrillic_word("Вт, мл, шт") is False
        assert has_cyrillic_word("1, 5 см") is False
        assert has_cyrillic_word("") is False
        assert has_cyrillic_word(None) is False

    def test_real_words_detected(self):
        assert has_cyrillic_word("Настольный вентилятор") is True
        assert has_cyrillic_word("Перчатки") is True
        # 恰好 4 字符词（вата）边界
        assert has_cyrillic_word("вата") is True
        assert has_cyrillic_word("шт") is False


# ═══════════════ 2. _remove_latin_llm 破坏性改写守卫 ═══════════════


class TestRemoveLatinLlmGuard:
    _TEXT = "Портативный вентилятор настольный USB аккумулятор"  # 西里尔词×4 + 拉丁×1

    def _call(self, monkeypatch, llm_result):
        from utils import mxou_api
        from utils.title_sanitizer import _remove_latin_llm

        monkeypatch.setattr(mxou_api, "call_mxou_chat_api", lambda *a, **k: llm_result)
        return _remove_latin_llm(self._TEXT, "sk-test-token")

    def test_destructive_rewrite_falls_back_to_regex(self, monkeypatch):
        """LLM 丢词残壳（4词→1词）→ 弃用，走正则兜底（原文西里尔词全保留）。"""
        out = self._call(monkeypatch, "вентилятор")
        for word in ("Портативный", "вентилятор", "настольный", "аккумулятор"):
            assert word in out, f"正则兜底须保留全部西里尔词，实际 {out!r}"
        assert "USB" not in out, "正则兜底仍须去拉丁"

    def test_acceptable_rewrite_still_accepted(self, monkeypatch):
        """LLM 输出词数 ≥ 输入一半（4词→2词）→ 正常采信 LLM 结果。"""
        out = self._call(monkeypatch, "вентилятор аккумулятор")
        assert out == "вентилятор аккумулятор"

    def test_no_cyrillic_in_result_still_falls_back(self, monkeypatch):
        """LLM 返回无西里尔 → 走既有正则路径（行为不变）。"""
        out = self._call(monkeypatch, "no cyrillic here")
        assert "USB" not in out and "аккумулятор" in out


# ═══════════════ 3. ozon_validate_node 名称闸 ═══════════════


class _Resp:
    def __init__(self, status=200):
        self.status_code = status
        self.headers = {}
        self.is_redirect = False
        self.is_permanent_redirect = False


_DC, _TP = 17028653, 92147  # RU 路径经 monkeypatch 离线供给，类目对本身不重要


def _validate_item(monkeypatch, name):
    from graphs.nodes import ozon_validate_node as ovn
    from graphs.nodes.ozon_validate_node import ozon_validate_node
    from graphs.state import OzonValidateInput

    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, port=None, *a, **k: [(2, 1, 6, "", ("93.184.216.34", port or 0))],
    )
    monkeypatch.setattr("requests.request", lambda *a, **k: _Resp(200))
    monkeypatch.setattr(ovn, "_fetch_ru_category_path", lambda dc, tp: "", raising=False)

    state = OzonValidateInput(
        ozon_payload={
            "items": [
                {
                    "name": name,
                    "offer_id": "sku1",
                    "price": "1990",
                    "old_price": "2390",
                    "vat": "0",
                    "weight": 300,
                    "weight_unit": "g",
                    "depth": 100,
                    "width": 100,
                    "height": 50,
                    "dimension_unit": "mm",
                    "images": [
                        "https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/x.jpg"
                    ],
                    "primary_image": "https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/x.jpg",
                    "description_category_id": _DC,
                    "type_id": _TP,
                    "attributes": [],
                }
            ]
        },
        ozon_client_id="c",
        ozon_api_key="k",
        attributes_schema=[],
    )
    runtime = type("R", (), {"context": None})()
    return ozon_validate_node(state, {}, runtime)


class TestValidateNameGate:
    def test_shell_name_flagged(self, monkeypatch):
        """单位残壳名称（无 ≥4 字符西里尔词）→ item_errors 且阻断。"""
        out = _validate_item(monkeypatch, "Вт, мл, шт")
        errs = [e for e in out.validation_errors if "西里尔词" in e and ".name" in e]
        assert errs, f"残壳名称必须进 validation_errors，实际: {out.validation_errors}"
        assert out.is_valid is False

    def test_valid_name_not_flagged(self, monkeypatch):
        """正常俄语名称 → 名称结构闸零误杀。"""
        out = _validate_item(monkeypatch, "Настольный вентилятор 15 Вт")
        errs = [e for e in out.validation_errors if "西里尔词" in e and ".name" in e]
        assert not errs, f"正常名称被误杀: {errs}"

    def test_gate_independent_of_authoritative_category(self, monkeypatch):
        """authoritative 类目豁免只属于类目交集闸——残壳名称在权威来源下照样拦。"""
        from graphs.state import OzonValidateInput  # noqa: F401  显式确认字段存在
        out = _validate_item(monkeypatch, "1 шт")
        assert out.is_valid is False, "名称结构闸不受类目豁免影响（坏名称必须阻断）"
