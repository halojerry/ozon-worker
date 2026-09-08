"""v0.69 Wave4 — ozon_validate 错误收集缺陷修复 + 标题-类目一致性预检（TDD RED→GREEN）。

背景（店铺健康扫描）：DESCRIPTION_DECLINE 37 例（最高频，近半）。前批发现
pre-existing 缺陷：`validation_errors.extend(item_errors)` 之后的 拉丁/中文/
危化品/图片检查 append 到旧 `item_errors` 但**不再 extend**——这些错误实际进不了
validation_errors（critical_errors 关键词表却含对应词，历史意图与实现脱节）。
这正是「本地能拦的没拦住、全靠 Ozon 事后拒」的原因之一。

修复契约：
  T1  所有 item 级检查产生的错误都进 validation_errors（extend 挪到每 item 全部
      检查之后；Step3.5 危化品/图片检查改挂 validation_errors 直接）。
      图片可达性**保持进 errors**（validate 是上传前在线阶段，HTTP 探测有意义），
      但异常安全：网络失败（超时/DNS/SSL）→ warning 不拦截，仅 HTTP ≥400 计失败。
  T2  生成标题（俄文）× 定稿类目 RU 路径 词面一致性：零公共西里尔词（≥4 字符，
      共同前缀 ≥4 容俄语词形变化 кружка↔кружки）→ errors 判 critical。
      阈值宁松勿严：只拦零交集。只对 CREATE 生效（UPDATE/跟卖豁免，对齐本节点
      现有 product_id 类目豁免逻辑）。validate 不调 LLM 重生成——retry 子图
      DESCRIPTION_DECLINE→error_repair_llm 已承担；本地拦截价值 = 快速失败 +
      错误信息带类目名，上游一次修。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_validate_title_consistency_v069.py -q
纯 mock（RU 路径 + 图片探测 monkeypatch），无需 PG/网络。
"""
import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402

from graphs.state import OzonValidateInput  # noqa: E402
from graphs.nodes import ozon_validate_node as ovn  # noqa: E402
from graphs.nodes.ozon_validate_node import ozon_validate_node  # noqa: E402

# 真实类目对（local PG 树内存在，RU 路径 = Строительство и ремонт > … > Трещотка）；
# 测试内 monkeypatch _fetch_ru_category_path → 完全离线。
_DC, _TP = 17028653, 92147
_RU_PATH = "Строительство и ремонт > Инструменты для ремонта и строительства > Трещотка"


class _Resp:
    def __init__(self, status=200):
        self.status_code = status


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """默认：图片探测 200（不依赖网络）、RU 路径返回真实形态路径（不依赖 PG）。"""
    monkeypatch.setattr("requests.head", lambda *a, **k: _Resp(200), raising=False)
    monkeypatch.setattr(ovn, "_fetch_ru_category_path",
                        lambda dc, tp: _RU_PATH if (dc, tp) == (_DC, _TP) else "",
                        raising=False)


def _run(items, attributes_schema=None):
    state = OzonValidateInput(
        ozon_payload={"items": items},
        ozon_client_id="c",
        ozon_api_key="k",
        attributes_schema=attributes_schema or [],
    )
    runtime = type("R", (), {"context": None})()
    return ozon_validate_node(state, {}, runtime)


def _item(**over):
    base = {
        "name": "Трещотка набор 1/4", "offer_id": "sku1", "price": "1990",
        "old_price": "2390", "vat": "0", "weight": 300, "weight_unit": "g",
        "depth": 100, "width": 100, "height": 50, "dimension_unit": "mm",
        "images": ["https://test-bucket.cos.ap-guangzhou.myqcloud.com/draft-images/x.jpg"],
        "primary_image": "https://example.com/img.jpg",
        "description_category_id": _DC, "type_id": _TP,
        "attributes": [],
    }
    base.update(over)
    return base


# ═══════════════ T1：错误收集缺陷修复回归 ═══════════════

def test_chinese_title_enters_validation_errors_and_critical():
    """①标题含汉字 → 进 validation_errors 且判 critical（缺陷回归：此前 append 到
    extend 之后的旧 item_errors，实际被丢弃 → is_valid=True 假放行）。"""
    out = _run([_item(name="Трещотка 桌面收纳盒")])
    errs = [e for e in out.validation_errors if "中文字符" in e and ".name" in e]
    assert errs, f"汉字标题必须进 validation_errors，实际: {out.validation_errors}"
    assert out.is_valid is False, "汉字标题判 critical 必须阻断"


def test_latin_description_enters_errors():
    """②描述含拉丁残留 → 进 errors 且判 critical（同缺陷回归）。"""
    out = _run([_item(description="Отличный вентилятор USB Size")])
    errs = [e for e in out.validation_errors if "拉丁字母" in e and ".description" in e]
    assert errs, f"描述拉丁必须进 validation_errors，实际: {out.validation_errors}"
    assert "USB" in errs[0], f"错误需携带拉丁片段: {errs[0]}"
    assert out.is_valid is False


def test_hazard_keyword_enters_errors():
    """⑨Step3.5 危化品关键词（stale item_errors 第二处）→ 进 errors 且判 critical。
    此前 append 到上一个 item 循环遗留的 item_errors，从未 extend → 被丢弃。"""
    out = _run([_item(name="Зажигалка настольная офисная")])
    errs = [e for e in out.validation_errors if "危化品" in e]
    assert errs, f"危化品关键词必须进 validation_errors，实际: {out.validation_errors}"
    assert out.is_valid is False


def test_image_all_unreachable_still_blocks(monkeypatch):
    """⑧图片可达性保持进 errors：HTTP 404（明确失效）全部不可达 → 判 critical 阻断。
    （缺陷回归：此前该错误同样被丢弃；修复后保留拦截语义。）"""
    monkeypatch.setattr("requests.head", lambda *a, **k: _Resp(404))
    out = _run([_item()])
    errs = [e for e in out.validation_errors if "不可访问" in e]
    assert errs, f"全部图片 HTTP≥400 必须进 errors，实际: {out.validation_errors}"
    assert out.is_valid is False


def test_image_probe_network_error_degrades_warning(monkeypatch):
    """⑦图片探测网络异常（超时/DNS/SSL）→ warning 放行不拦截（异常安全）。"""
    def _boom(*a, **k):
        raise TimeoutError("probe timeout")

    monkeypatch.setattr("requests.head", _boom)
    out = _run([_item()])
    assert not any("不可访问" in e for e in out.validation_errors), \
        f"网络异常不得计为图片失效: {out.validation_errors}"
    assert out.is_valid is True


def test_clean_item_no_errors():
    """干净 payload → 零错误放行（以上修复不引入误报）。"""
    out = _run([_item()])
    assert out.validation_errors == [], f"干净 payload 不应有错误: {out.validation_errors}"
    assert out.is_valid is True


# ═══════════════ T2：标题-类目一致性（DESCRIPTION_DECLINE 本地预检）═══════════════

def test_zero_overlap_title_category_flagged():
    """③标题与定稿类目零公共西里尔词 → errors 判 critical，错误带两者内容。"""
    out = _run([_item(name="Носки женские теплые")])
    errs = [e for e in out.validation_errors if "标题与类目不一致" in e]
    assert errs, f"零交集必须报「标题与类目不一致」: {out.validation_errors}"
    assert "Носки" in errs[0], f"错误需附标题: {errs[0]}"
    assert "Трещотка" in errs[0], f"错误需附类目路径: {errs[0]}"
    assert out.is_valid is False


def test_one_common_word_passes():
    """④有一个公共词（≥4 字符西里尔）→ 放行（阈值宁松勿严，只拦零交集）。"""
    out = _run([_item(name="Трещотка набор 1/4 дюйма")])
    assert not any("标题与类目不一致" in e for e in out.validation_errors), \
        f"有公共词必须放行: {out.validation_errors}"
    assert out.is_valid is True


def test_update_item_exempt_from_consistency():
    """⑤UPDATE（带 product_id，跟卖/编辑）豁免一致性检查——对齐本节点类目必填豁免。"""
    out = _run([_item(name="Носки женские теплые", product_id=123456)])
    assert not any("标题与类目不一致" in e for e in out.validation_errors), \
        f"UPDATE 必须豁免一致性检查: {out.validation_errors}"
    assert out.is_valid is True


def test_missing_ru_path_skips_check(monkeypatch):
    """⑥RU 路径缺失（树缺行/PG 异常）→ 跳过检查（宁松勿严，不因数据缺失误拦）。"""
    monkeypatch.setattr(ovn, "_fetch_ru_category_path", lambda dc, tp: "")
    out = _run([_item(name="Носки женские теплые")])
    assert not any("标题与类目不一致" in e for e in out.validation_errors)
    assert out.is_valid is True


def test_common_cyr_words_pure_function():
    """词面公共词纯函数：相等/前缀≥4 算公共词；短词/无关联不算（容词形变化）。"""
    assert ovn.common_cyr_words("Кружка для кофе", "Дом > Кружки"), "кружка↔кружки 前缀≥4"
    assert ovn.common_cyr_words("Моторное масло 5W30", "Авто > Моторные масла"), "масло↔масла"
    assert not ovn.common_cyr_words("Носки женские", "Инструменты > Трещотка")
    # 短词（для/и）不计入公共词
    assert not ovn.common_cyr_words("Набор для барбекю", "Дом и сад > Гриль")


def test_critical_keyword_covers_consistency_error():
    """一致性错误命中 critical 关键词表（否则只算 warning 不拦截）。"""
    out = _run([_item(name="Носки женские теплые")])
    assert out.is_valid is False, "零交集必须阻断（critical），不是警告放行"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:
            traceback.print_exc()
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
