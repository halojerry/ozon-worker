"""v0.69 Wave4 T2.4 — 受限/需资质品类前置拦截（TDD RED→GREEN）。

背景（店铺健康扫描）：BR_hazard_class1（易燃）5 例——汽柴油容器类商品在自动
匹配里打转 8 分钟才被 Ozon 拒。本闸在 assemble 类目匹配前/定稿后做本地双命中
预检：商品标题/货源 与 定稿类目 full_path **都**命中受限词表才拦（单侧命中
放行防误伤——卖汽油桶配件的标题未必命中，普通水桶类目名带 канистра 也不误伤），
拦截走 T0.3 入箱机制（blocked_reason=需资质/受限品类 + notice）转人工确认。

红线：与 R1（成人内容防护线）完全独立——本闸不触碰 _r1_veto /
_SENSITIVE_SOURCE_SIGNALS，R1 语义零改动（⑧回归锁定）。

词表：worker/config/restricted_keywords.json（运营可维护），热加载语义对齐
utils/image_prompts.py：每次现读磁盘（无缓存），改文件下一次调用生效；
缺失/损坏回退内置默认词表。

运行：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_restricted_keywords_v069.py -q
全部纯 mock，不连 PG/不触网/不调 LLM。
"""
import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.restricted_keywords import (  # noqa: E402
    _DEFAULT_KEYWORDS,
    is_restricted_double_hit,
    load_restricted_config,
    match_restricted_keywords,
    restricted_keywords,
    restricted_notice,
)


# ═══════════════ 纯函数：词表命中 + 双命中判定 ═══════════════

def test_01_match_ru_lowercase_substring():
    """俄文按小写子串、大小写不敏感：БЕНЗИНА / Канистра 大写混合都命中
    （词表词取主干形态，子串匹配天然覆盖 большинство 词形）。"""
    hits = match_restricted_keywords("Канистра для БЕНЗИНА 20л")
    assert "канистра" in hits and "бензин" in hits, f"大写混合必须命中: {hits}"


def test_02_match_multiword_and_chinese():
    """多词俄文短语（газовый баллон）与中文包含（打火机/锂电）命中。"""
    assert "газовый баллон" in match_restricted_keywords("плита газовый баллон 220в")
    hits = match_restricted_keywords("防风充电打火机 金属锂电款")
    assert "打火机" in hits and "锂电" in hits, f"中文包含必须命中: {hits}"


def test_03_match_miss_and_empty():
    """未命中/空文本 → 空列表（绝不误报）。"""
    assert match_restricted_keywords("Трещотка набор для ремонта") == []
    assert match_restricted_keywords("普通不锈钢水桶") == []
    assert match_restricted_keywords("") == []
    assert match_restricted_keywords(None) == []


def test_04_double_hit_requires_both_sides():
    """双命中判定：货源侧 × 类目侧都命中才 True；单侧命中一律放行（防误伤）。"""
    assert is_restricted_double_hit("汽油桶 20升 加油桶", "汽车用品 > 汽车油品容器 > 汽油桶")
    assert is_restricted_double_hit("Канистра для ГСМ", "Авто и мото > Канистра стальная 20л")
    # 仅货源命中（类目是普通水桶）→ 放行
    assert not is_restricted_double_hit("汽油桶 20升", "Дом и сад > Ведра пластиковые")
    # 仅类目命中（标题是普通配件）→ 放行
    assert not is_restricted_double_hit("汽车油桶盖密封圈", "汽车用品 > 汽车油品容器 > 汽油桶")
    assert not is_restricted_double_hit("", "Авто > Канистры")


# ═══════════════ 配置加载 + 热加载语义（对齐 image_prompts 测试手法）═══════════════

@contextmanager
def _fake_workspace(config_obj=None, raw=None, no_config=False):
    """临时 APP_WORKSPACE_PATH：可注入 restricted_keywords.json（dict/原文/缺失）。"""
    tmp = tempfile.mkdtemp(prefix="restricted_kw_test_")
    old = os.environ.get("APP_WORKSPACE_PATH")
    try:
        if not no_config:
            cfg_dir = os.path.join(tmp, "config")
            os.makedirs(cfg_dir, exist_ok=True)
            path = os.path.join(cfg_dir, "restricted_keywords.json")
            if raw is not None:
                with open(path, "w", encoding="utf-8") as fd:
                    fd.write(raw)
            elif config_obj is not None:
                with open(path, "w", encoding="utf-8") as fd:
                    json.dump(config_obj, fd, ensure_ascii=False)
        os.environ["APP_WORKSPACE_PATH"] = tmp
        yield tmp
    finally:
        if old is None:
            os.environ.pop("APP_WORKSPACE_PATH", None)
        else:
            os.environ["APP_WORKSPACE_PATH"] = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_05_config_missing_falls_back_to_defaults():
    """配置缺失 → 内置默认词表兜底（含 канистра/бензин/打火机），notice 默认文案。"""
    with _fake_workspace(no_config=True):
        assert "канистра" in restricted_keywords()
        assert "бензин" in restricted_keywords()
        assert "打火机" in restricted_keywords()
        assert "资质" in restricted_notice()
        assert load_restricted_config() == {}


def test_06_config_corrupt_falls_back_to_defaults():
    """JSON 损坏 → 兜底默认词表，绝不抛异常阻断主流程。"""
    with _fake_workspace(raw="{ 这不是合法 JSON !!!"):
        assert "канистра" in restricted_keywords()
        assert "资质" in restricted_notice()


def test_07_hot_reload_picks_up_file_change_next_call():
    """热加载：每次现读磁盘（无缓存）——改文件下一次调用立即生效。"""
    with _fake_workspace(config_obj={"keywords": ["канистра"], "notice": "自定义提示A"}):
        assert restricted_keywords() == ["канистра"]
        assert restricted_notice() == "自定义提示A"
        # 运营改词表（同一路径重写）→ 下一次调用生效
        path = os.path.join(os.environ["APP_WORKSPACE_PATH"], "config",
                            "restricted_keywords.json")
        with open(path, "w", encoding="utf-8") as fd:
            json.dump({"keywords": ["спирт", "зажигалка"], "notice": "自定义提示B"},
                      fd, ensure_ascii=False)
        assert restricted_keywords() == ["спирт", "зажигалка"], "热加载必须无进程内缓存"
        assert restricted_notice() == "自定义提示B"


def test_08_real_repo_config_matches_contract():
    """仓库真实 config/restricted_keywords.json：词表非空且含任务锚点词，notice 含「资质」。"""
    repo_cfg = os.path.join(os.path.dirname(__file__), "..", "config",
                            "restricted_keywords.json")
    with open(repo_cfg, "r", encoding="utf-8") as fd:
        data = json.load(fd)
    kws = [str(k).lower() for k in data.get("keywords", [])]
    assert data.get("notice") and "资质" in data["notice"]
    for anchor in ("канистра", "бензин", "зажигалка", "моторное масло", "汽油", "打火机"):
        assert anchor in kws, f"词表缺任务锚点词: {anchor}"


# ═══════════════ assemble 接线：双命中入箱 / 单侧放行 / R1 回归 ═══════════════

from graphs.nodes import assemble_ozon_product_node as asm  # noqa: E402

_CAN_CAND = {"description_category_id": 99990001, "type_id": 88880001,
             "node_name": "汽油桶", "full_path": "汽车用品 > 汽车油品容器 > 汽油桶",
             "similarity": 0.9, "matcher": "jieba"}
_PLAIN_CAND = {"description_category_id": 99990002, "type_id": 88880002,
               "node_name": "手提桶", "full_path": "日用杂货 > 塑料容器 > 手提桶",
               "similarity": 0.9, "matcher": "jieba"}


class _FakeQuery:
    """search_nodes 返回预置候选；其余方法不触网/不触 PG。"""

    def __init__(self, candidates):
        self._candidates = candidates
        self.schema_calls = 0

    def search_nodes(self, *_a, **_k):
        return list(self._candidates)

    def score_candidates_by_fingerprint(self, candidates, _kw):
        return candidates

    def get_attribute_schema(self, *_a, **_k):
        self.schema_calls += 1
        return None  # 触发 _fetch_attribute_schema_from_ozon（测试里 patch）

    def get_dictionary_values(self, *_a, **_k):
        return []


class _FakeState:
    """模拟 GlobalState — 只含 assemble 主路径 + 入箱读取的字段。"""

    def __init__(self, title, envelope=None):
        self.draft = {"item_id": "gz-1", "sku_id": "gz-1", "title": title,
                      "images": ["https://cbu01.alicdn.com/img/ibank/x.jpg"],
                      "weight": 500, "dimensions": {"length": 200, "width": 120, "height": 80},
                      "purchase_cost": 10.0, "purchase_url": "https://detail.1688.com/1.html"}
        self.token = ""
        self.ozon_client_id = "c"
        self.ozon_api_key = "k"
        self.currency_code = "RUB"
        self.pricing_info = {"price": "990", "old_price": "1290"}
        self.envelope = envelope if envelope is not None else {
            "draft": self.draft, "source": {}, "extensions": {}}
        self.source = {}
        self.user_id = "u-1"
        self.task_id = ""  # 空 → _log_match_attempt 直接跳过（不触 PG）
        self.assembly_retry_count = 0


def _run_assemble(title, candidates, fake_state):
    query = _FakeQuery(candidates)
    with mock.patch.object(asm, "get_category_query", return_value=query), \
         mock.patch.object(asm, "_fetch_attribute_schema_from_ozon", return_value=[]) as fa, \
         mock.patch.object(asm, "_llm_rank_categories", return_value=None), \
         mock.patch("utils.blocked_draft_box.create_blocked_draft") as cb:
        out = asm.assemble_ozon_product_node(fake_state, {}, None)
    return out, {"schema_calls": query.schema_calls, "fetch_schema": fa, "box": cb}


def test_09_double_hit_blocks_with_box_failed_and_notice():
    """⑥「汽油桶标题 + 汽油桶类目」双命中 → 入箱 + failed 终态 + notice 含「资质」，
    且在属性 schema 获取前止损（不再烧下游）。"""
    state = _FakeState("汽油桶 20升 加油桶")
    out, probe = _run_assemble("汽油桶 20升 加油桶", [_CAN_CAND], state)
    assert "需资质" in str(out.get("error_message")), f"阻断原因需含「需资质」: {out}"
    assert "受限" in str(out.get("error_message"))
    assert out.get("failed_stage") == "category_match", f"failed 终态: {out}"
    assert probe["box"].called, "双命中必须入采集箱"
    reason = str(probe["box"].call_args[0][3])
    assert "需资质/受限品类" in reason, f"blocked_reason: {reason}"
    notice = str(out.get("notice") or "")
    assert "资质" in notice, f"notice 必须含「资质」提示: {notice}"
    assert "已入采集箱" in notice, f"notice 必须含入箱结果: {notice}"
    assert probe["schema_calls"] == 0, "止损：不得再取属性 schema"
    probe["fetch_schema"].assert_not_called()


def test_10_source_only_hit_passes_gate():
    """⑦仅货源命中、定稿类目不命中 → 受限闸放行（无「需资质」字样、不入受限箱）。
    标题补「塑料/手提」保证与普通类目字面 overlap（不触发 LLM fallback 分支）。"""
    state = _FakeState("汽油桶专用塑料手提桶")
    out, probe = _run_assemble("汽油桶专用塑料手提桶", [_PLAIN_CAND], state)
    assert "需资质" not in str(out.get("error_message")), f"单侧命中不得拦: {out}"
    assert "资质" not in str(out.get("notice") or "")
    # 走到类目定稿之后的流程（本 mock 链在 schema 处止步，说明闸已放行）
    assert probe["schema_calls"] == 1, "放行后应继续取属性 schema"
    probe["box"].assert_not_called(), "单侧命中不得入箱"


def test_11_r1_veto_regression_unaffected():
    """⑧R1 veto 路径回归不受影响：敏感类目无敏感源词仍否决；竞品路径精配豁免仍在。"""
    # 敏感子树 + 无敏感信号词 → veto
    assert asm._r1_veto(
        {"category_path": "成人用品 > 成人糖果 18+", "description_category_id": 1,
         "type_id": 2}, "普通遮阳帽")
    # 敏感子树 + 白名单敏感信号词 → 放行
    assert not asm._r1_veto(
        {"category_path": "成人用品 > 成人糖果 18+", "description_category_id": 1,
         "type_id": 2}, "成人用品 飞机杯")
    # 竞品 category_path 精配豁免仍在
    assert not asm._r1_veto(
        {"category_path": "成人用品 > 成人糖果 18+", "_resolved_by_path": True},
        "普通遮阳帽")
    # 受限词与 R1 敏感词表互不掺和：受限闸命中不依赖也不修改 R1 判定
    hits = match_restricted_keywords("спиртовой растворитель")
    assert "спиртовой" in hits
    assert not asm._r1_veto({"category_path": "日用杂货 > 溶剂", "description_category_id": 3,
                             "type_id": 4}, "спиртовой растворитель"), "R1 判定未被受限词表影响"


def test_12_restricted_exit_helper_notice_prepend():
    """受限出口 helper：notice 前置受限提示 + 保留入箱 notice；failed 字段齐全。"""
    state = _FakeState("汽油桶")
    with mock.patch("utils.blocked_draft_box.create_blocked_draft",
                    return_value={"draft_id": "d-77", "reused": False,
                                  "top1_name": "汽车用品 > 汽车油品容器 > 汽油桶",
                                  "top1_confidence": 0.9}):
        out = asm._restricted_category_exit(state, state.draft, [_CAN_CAND],
                                            ["汽油"], ["汽油"], match_confidence=0.9)
    assert out["failed_stage"] == "category_match"
    assert out["assembly_retry_count"] == 1
    assert "需资质/受限品类" in out["error_message"]
    assert out["notice"].index("资质") < out["notice"].index("已入采集箱"), \
        f"受限提示须前置: {out['notice']}"


def test_13_default_keywords_constant_guard():
    """内置默认词表非空且含中俄国界锚点词（config 全缺时闸仍有效）。"""
    assert len(_DEFAULT_KEYWORDS) >= 10
    for anchor in ("канистра", "бензин", "зажигалка", "аэрозоль", "汽油", "锂电"):
        assert anchor in _DEFAULT_KEYWORDS


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
