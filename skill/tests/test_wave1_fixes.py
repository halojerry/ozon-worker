"""v0.26 wave1 修复回归测试（skill 侧）— 空壳拦截 / LLM 语义判定 / 凭证脱敏 / 图搜 title 清洗。

运行（skill venv）：
    cd skill && .venv314/bin/python tests/test_wave1_fixes.py
"""
from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── 修4: 凭证脱敏（cli._redact_keys）──

def test_redact_keys_masks_credentials():
    from scripts.cli import _redact_keys
    obj = {
        "ok": True,
        "envelope": {
            "ozon_client_id": "4718259",
            "ozon_api_key": "cd1d0a10-181a-42a1-8895-8508bb0513d7",
            "token": "Ccpo3ziBuPH6daniA13XPDPGRem7m9OqsXPGZWvA5xK3eJyL",
        },
        "summary": {"title": "Тест"},
    }
    _redact_keys(obj, {"api_key", "token", "ozon_api_key", "mxou_token", "ak", "ali_1688_ak"})
    assert obj["envelope"]["ozon_api_key"] == "cd1d****", obj["envelope"]["ozon_api_key"]
    assert obj["envelope"]["token"] == "Ccpo****"
    assert "cd1d0a10" not in str(obj), "完整 api_key 不应出现"
    assert obj["envelope"]["ozon_client_id"] == "4718259", "client_id 不脱敏（半公开）"
    assert obj["summary"]["title"] == "Тест", "非凭证字段不动"


# ── 修1: 空壳拦截（follow_sell_cloud no_relevant_match → blocked）──

def test_no_relevant_match_blocks_envelope():
    """图搜无货源 → 不再组装 api_fallback 空壳信封。"""
    import scripts.cloud_probe as cp
    result = {"no_relevant_match": True, "ozon_images": ["http://img/1.jpg"],
              "ozon_title": "Тест", "ozon_category": {"description_category_id": "1", "type_id": "1"}}
    # 直接验证: follow_sell_cloud 的 no_relevant_match 分支已改为拦截
    # （原逻辑会组装 envelope + api_fallback=True；现改为 blocked_reason）
    assert "api_fallback" in open(os.path.join(os.path.dirname(cp.__file__), "cloud_probe.py"), encoding="utf-8").read()
    # 检查分支: no_relevant_match 时设置 blocked_reason 而非组装
    src = open(os.path.join(os.path.dirname(cp.__file__), "cloud_probe.py"), encoding="utf-8").read()
    assert 'result["blocked_reason"] = "no_relevant_match"' in src
    assert "import-by-sku 复制竞品卡片" not in src.split("elif result.get(\"no_relevant_match\")")[1].split("# ⚠️ v0.14 E5")[0], \
        "api_fallback 组装逻辑应被删除"


# ── 修3: LLM 语义判定（_llm_semantic_match）──

def test_llm_semantic_match_yes():
    from scripts.lib.ozon_discovery import _llm_semantic_match, _LLM_SEMANTIC_CACHE
    _LLM_SEMANTIC_CACHE.clear()
    class _R:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "YES"}}]}
    with mock.patch("requests.post", return_value=_R()) as m:
        assert _llm_semantic_match("Палочки от комаров 30 шт", "跨境驱蚊香薰棒竹签香", token="t") is True
        m.assert_called_once()


def test_llm_semantic_match_no_and_cache():
    from scripts.lib.ozon_discovery import _llm_semantic_match, _LLM_SEMANTIC_CACHE
    _LLM_SEMANTIC_CACHE.clear()
    class _R:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "NO"}}]}
    with mock.patch("requests.post", return_value=_R()) as m:
        assert _llm_semantic_match("Палочки от комаров", "硅胶手机壳", token="t") is False
        # 缓存: 第二次调用不再发请求
        assert _llm_semantic_match("Палочки от комаров", "硅胶手机壳", token="t") is False
        assert m.call_count == 1


def test_llm_semantic_match_fail_safe():
    """LLM 失败（无 token/网络异常）→ False（维持原拒绝，不放行错误匹配）。"""
    from scripts.lib.ozon_discovery import _llm_semantic_match
    assert _llm_semantic_match("Тест", "测试", token="") is False
    with mock.patch("requests.post", side_effect=Exception("boom")):
        assert _llm_semantic_match("Тест", "测试", token="t") is False


# ── 修3b: top-N LLM 兜底（best 不相关但后面有同品候选 → 救回）──

def test_pick_best_match_llm_rescue_topN():
    """图搜 best 是不相关高分项（檀香贡香），第 3 个候选是同品（驱蚊棒）
    → top-N LLM 兜底应返回第 3 个候选（修"匹配了却不选"根因）。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()

    ozon_title = "Палочки от комаров инсектицидные ароматические палочки 30 шт"
    results = [
        # badge 空 + 无价（被过滤）
        {"id": "0", "title": "无烟檀香开花香卷钱香佛香", "price": 0, "badge": ""},
        # best: 无徽标、有价、标题不相关（贡香）
        {"id": "1", "title": "花开富贵香开花香檀香供佛香纯檀香招财香", "price": 8.0, "badge": ""},
        # 同品：驱蚊棒（LLM 判定 YES）
        {"id": "2", "title": "跨境驱蚊香薰棒竹签香户外防蚊香", "price": 15.0, "badge": ""},
    ]
    # mock LLM: 仅对"驱蚊香薰棒"判定 True（含进程内缓存，先清空）
    real_fn = od._llm_semantic_match
    def fake_llm(oz, cn, token=""):
        return "驱蚊" in cn
    with mock.patch.object(od, "_llm_semantic_match", side_effect=fake_llm):
        best = od._pick_best_match(results, ozon_title, token="t")
    assert best is not None, "top-N LLM 兜底应救回同品候选"
    assert best["id"] == "2", f"应返回同品候选 id=2，实际 {best}"


def test_pick_best_match_llm_rescue_all_false_still_rejects():
    """top-N 全判定 False（真无同款）→ 仍拒绝（宁缺毋滥不破坏）。"""
    import scripts.lib.ozon_discovery as od
    od._LLM_SEMANTIC_CACHE.clear()
    ozon_title = "Палочки от комаров"
    results = [
        {"id": "1", "title": "花开富贵香开花香檀香供佛香", "price": 8.0, "badge": ""},
        {"id": "2", "title": "无烟檀香开花香卷钱香佛香", "price": 9.0, "badge": ""},
    ]
    with mock.patch.object(od, "_llm_semantic_match", return_value=False):
        best = od._pick_best_match(results, ozon_title, token="t")
    assert best is None, "全 False 应拒绝"


if __name__ == "__main__":
    import traceback
    failed = total = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
