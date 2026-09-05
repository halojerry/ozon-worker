"""v0.65.1 属性同义词组扩充（高频缺口）+ prepare 打点扩 skipped 回归测试。

背景：
- attr_synonyms.json 仅 9 组；产地/生产国、形状、图案/花纹、成分、功率等高频
  可选字典属性无组可过 → 大量可选属性不填。
- 本批 P1 为这些缺口新增同义词组（value_map 留空，值域走 1688 原始值
  /values/search 唯一化，防 value_map 文本与类目字典档位不符），并给 packaging
  组补 zh 关键词「装箱」。
- 本批 P2 给 _fill_optional_dict_attrs 的 A2 中文直搜旁路补审计打点：
  skipped_no_value（旁路 0 候选）/ skipped_multi_candidate（旁路多候选放弃），
  给 _infer_attrs_from_vision 补 no_infer（白名单外 + should_fill 可选字典属性）。

测试语义锚点（zh_keywords/ozon_name_keywords 必须与 config 内新组一致）：
- 原产国(4389 schema ZH 名「原产国」/RU「Страна-производитель」) ← 1688「产地」
- 形状(「形状」/「形状特征」/「форма」) ← 1688「形状/外形」
- 图案(「图案/узор/рисунок/принт」) ← 1688「图案/花纹/印花/花色」
- 成分(「成分/состав」) ← 1688「成分/面料成分/材质成分」
- 功率(「功率/мощность」) ← 1688「功率/瓦数/额定功率」

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_attr_synonyms_extended_v0651.py -v
⚠️ 纯 mock（patch search_dictionary_values / log_attr_match），无需 PG/GPU。
"""
import os
import sys
from types import SimpleNamespace
from unittest import mock

_WORKER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # worker/
sys.path.insert(0, os.path.join(_WORKER, "src"))

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("PGDATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ozon")

from graphs.nodes import prepare_ozon_upload_node as mod


def _load_synonyms():
    from utils.attr_synonyms import load_attr_synonyms
    # 显式指向 worker 根（真实 config/attr_synonyms.json），不依赖 pytest cwd
    with mock.patch.dict(os.environ, {"APP_WORKSPACE_PATH": _WORKER}):
        return load_attr_synonyms()


def _state(**over):
    base = dict(
        description_category_id="17028830",
        type_id="971206780",
        ozon_client_id="c",
        ozon_api_key="k",
        token="t",
        dictionary_values={},
        task_id="task-1",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _schema_attr(aid, name, dictionary_id=6187952, is_collection=False):
    a = {"id": aid, "name": name, "dictionary_id": dictionary_id, "is_required": False}
    if is_collection:
        a["is_collection"] = True
    return a


def _fill(schema, draft_attrs, item=None, search_return=None, state=None):
    """驱动 _fill_optional_dict_attrs（真实 synonyms config + mock /values/search）。"""
    item = item or {"attributes": []}
    with mock.patch.dict(os.environ, {"APP_WORKSPACE_PATH": _WORKER}), \
         mock.patch("utils.ozon_dict_values.search_dictionary_values",
                    return_value=search_return or []) as m_search, \
         mock.patch("utils.ozon_dict_values.list_dictionary_values", return_value=[]), \
         mock.patch("utils.attr_match_log.log_attr_match"):
        out = mod._fill_optional_dict_attrs([item], schema, {"attributes": draft_attrs},
                                            state or _state())
    return out, m_search


# ══════════════════════════════════════════════════════════════
# P1: 新同义词组存在性 + match_attr_name_synonym 命中/不误命中
# ══════════════════════════════════════════════════════════════

def test_new_groups_exist_for_high_freq_gaps():
    """产地/形状/图案/成分/功率 五组落地，packaging 补「装箱」。"""
    syn = _load_synonyms()
    assert "origin" in syn and syn["origin"]["zh_keywords"], "origin 组缺失"
    assert "shape" in syn and syn["shape"]["zh_keywords"], "shape 组缺失"
    assert "pattern" in syn and syn["pattern"]["zh_keywords"], "pattern 组缺失"
    assert "composition" in syn and syn["composition"]["zh_keywords"], "composition 组缺失"
    assert "power" in syn and syn["power"]["zh_keywords"], "power 组缺失"
    # value_map 留空：值域走 /values/search 唯一化，防文本与档位不符
    assert syn["origin"]["value_map"] == {}
    assert syn["shape"]["value_map"] == {}
    assert syn["pattern"]["value_map"] == {}
    assert syn["composition"]["value_map"] == {}
    assert syn["power"]["value_map"] == {}
    assert "装箱" in syn["packaging"]["zh_keywords"], "packaging 组缺 zh「装箱」"


def test_match_origin_divergence():
    """Ozon「原产国」vs 1688「产地」：origin 组双向命中（此前无组可过）。"""
    from utils.attribute_utils import match_attr_name_synonym
    syn = _load_synonyms()
    assert match_attr_name_synonym("原产国", ["产地"], syn) == "产地"
    assert match_attr_name_synonym("生产国", ["原产地"], syn) == "原产地"


def test_match_shape_divergence():
    """Ozon「形状/形状特征」vs 1688「形状/外形」：shape 组命中。"""
    from utils.attribute_utils import match_attr_name_synonym
    syn = _load_synonyms()
    assert match_attr_name_synonym("形状", ["外形"], syn) == "外形"
    assert match_attr_name_synonym("形状特征", ["形状"], syn) == "形状"


def test_match_pattern_divergence():
    """Ozon「图案」vs 1688「印花/花纹」：pattern 组命中。"""
    from utils.attribute_utils import match_attr_name_synonym
    syn = _load_synonyms()
    assert match_attr_name_synonym("图案", ["印花"], syn) == "印花"
    assert match_attr_name_synonym("花纹", ["图案"], syn) == "图案"
    assert match_attr_name_synonym("рисунок", ["花色"], syn) == "花色"


def test_match_composition_divergence():
    """Ozon「成分」vs 1688「面料成分」：composition 组命中。"""
    from utils.attribute_utils import match_attr_name_synonym
    syn = _load_synonyms()
    assert match_attr_name_synonym("成分", ["面料成分"], syn) == "面料成分"
    assert match_attr_name_synonym("состав", ["材质成分"], syn) == "材质成分"


def test_match_power_divergence():
    """Ozon「功率/мощность」vs 1688「功率/瓦数」：power 组命中。"""
    from utils.attribute_utils import match_attr_name_synonym
    syn = _load_synonyms()
    assert match_attr_name_synonym("功率", ["额定功率"], syn) == "额定功率"
    assert match_attr_name_synonym("мощность", ["瓦数"], syn) == "瓦数"


def test_match_packaging_zhushu():
    """packaging 组 zh「装箱」双向命中（1688「装箱」→ schema「упаковка」）。"""
    from utils.attribute_utils import match_attr_name_synonym
    syn = _load_synonyms()
    assert match_attr_name_synonym("упаковка", ["装箱"], syn) == "装箱"


def test_no_cross_group_false_hit():
    """跨组/无关不误命中（防错误值）。"""
    from utils.attribute_utils import match_attr_name_synonym
    syn = _load_synonyms()
    # 产地值不能灌进制造商「Производитель」（同源不同义，禁止 проив 类宽词）
    assert match_attr_name_synonym("Производитель", ["产地"], syn) is None
    assert match_attr_name_synonym("原产国", ["颜色"], syn) is None
    assert match_attr_name_synonym("形状", ["产地"], syn) is None
    assert match_attr_name_synonym("重量", ["图案"], syn) is None
    assert match_attr_name_synonym("материал", ["图案"], syn) is None


# ══════════════════════════════════════════════════════════════
# P1: 管道级 _fill_optional_dict_attrs 全路径填充（value_map 空 → 原始值直搜）
# ══════════════════════════════════════════════════════════════

def test_fill_origin_country_via_synonyms():
    """schema「原产国」+ 1688「产地:中国」→ 经 origin 组同义词门填入（此前被闸门卡死）。"""
    schema = [_schema_attr(4389, "原产国")]
    out, m_search = _fill(schema, {"产地": "中国", "颜色": "白色"},
                          search_return=[{"id": 90296, "value": "Китай"}])
    attrs = {a["id"]: a for a in out[0]["attributes"]}
    assert 4389 in attrs, "原产国应经 origin 组填入"
    assert attrs[4389]["values"][0]["dictionary_value_id"] == 90296
    assert m_search.called


def test_fill_shape_via_synonyms():
    """schema「形状」+ 1688「形状:圆形」→ shape 组填入。"""
    schema = [_schema_attr(4181, "形状")]
    out, _ = _fill(schema, {"形状": "圆形"}, search_return=[{"id": 9001, "value": "Круглая"}])
    attrs = {a["id"]: a for a in out[0]["attributes"]}
    assert attrs[4181]["values"][0]["dictionary_value_id"] == 9001


def test_fill_pattern_divergence_via_synonyms():
    """schema「图案」+ 1688「印花:条纹」→ pattern 组（中文词分歧）填入。"""
    schema = [_schema_attr(4556, "图案")]
    out, _ = _fill(schema, {"印花": "条纹"}, search_return=[{"id": 9002, "value": "Полосатый"}])
    attrs = {a["id"]: a for a in out[0]["attributes"]}
    assert attrs[4556]["values"][0]["dictionary_value_id"] == 9002


def test_fill_composition_via_synonyms():
    """schema「成分」+ 1688「面料成分:棉」→ composition 组填入。"""
    schema = [_schema_attr(4557, "成分")]
    out, _ = _fill(schema, {"面料成分": "棉"}, search_return=[{"id": 9003, "value": "Хлопок"}])
    attrs = {a["id"]: a for a in out[0]["attributes"]}
    assert attrs[4557]["values"][0]["dictionary_value_id"] == 9003


def test_fill_power_via_synonyms():
    """schema「功率」+ 1688「额定功率:1000W」→ power 组填入。"""
    schema = [_schema_attr(4558, "功率")]
    out, _ = _fill(schema, {"额定功率": "1000W"}, search_return=[{"id": 9004, "value": "1000 Вт"}])
    attrs = {a["id"]: a for a in out[0]["attributes"]}
    assert attrs[4558]["values"][0]["dictionary_value_id"] == 9004


def test_fill_no_false_positive_unrelated_attr():
    """无关属性不误命中：schema「重量」拿不到 1688 产地/图案值，不产生 search。"""
    schema = [_schema_attr(9005, "重量")]
    out, m_search = _fill(schema, {"产地": "中国", "图案": "格纹", "颜色": "白色"})
    assert out[0]["attributes"] == [], "无关属性不应被新组误填"
    assert not m_search.called, "无关属性不应触发字典值搜索"


# ══════════════════════════════════════════════════════════════
# P2: A2 旁路打点 — skipped_no_value / skipped_multi_candidate
# ══════════════════════════════════════════════════════════════

def _fill_and_capture_logs(schema, draft_attrs, search_side_effect):
    """捕获 log_attr_match 的调用（patch 掉真实 DB 写）。"""
    calls = []
    def _capture(*args, **kwargs):
        calls.append(kwargs)
    with mock.patch.dict(os.environ, {"APP_WORKSPACE_PATH": _WORKER}), \
         mock.patch("utils.ozon_dict_values.search_dictionary_values",
                    side_effect=search_side_effect), \
         mock.patch("utils.ozon_dict_values.list_dictionary_values", return_value=[]), \
         mock.patch("utils.attr_match_log.log_attr_match", side_effect=_capture):
        item = {"attributes": []}
        mod._fill_optional_dict_attrs([item], schema, {"attributes": draft_attrs}, _state())
    return calls


def test_bypass_logs_skipped_no_value():
    """旁路 0 候选（有共享字符源但字典无值）→ 写 skipped_no_value。"""
    schema = [_schema_attr(9010, "容量")]
    draft = {"容量": "大号"}
    def _no_hits(cid, key, aid, dc, tp, term):
        return []  # 无论搜什么都 0 候选
    calls = _fill_and_capture_logs(schema, draft, _no_hits)
    statuses = [c["status"] for c in calls]
    assert "skipped_no_value" in statuses, f"应写 skipped_no_value，实际 {statuses}"
    no_val = next(c for c in calls if c["status"] == "skipped_no_value")
    assert no_val["attr_id"] == 9010
    assert no_val["source_value"] == "大号"
    assert no_val["task_id"] == "task-1"
    assert no_val["match_layer"] == "zh_direct_search"


def test_bypass_logs_skipped_multi_candidate():
    """旁路多候选 → unique_or_none 放弃 → 写 skipped_multi_candidate。"""
    schema = [_schema_attr(9011, "宽度")]
    draft = {"宽度": "加宽"}
    def _two_hits(cid, key, aid, dc, tp, term):
        return [{"id": 8001, "value": "Широкий"}, {"id": 8002, "value": "Узкий"}]
    calls = _fill_and_capture_logs(schema, draft, _two_hits)
    statuses = [c["status"] for c in calls]
    assert "skipped_multi_candidate" in statuses, f"应写 skipped_multi_candidate，实际 {statuses}"
    multi = next(c for c in calls if c["status"] == "skipped_multi_candidate")
    assert multi["attr_id"] == 9011
    assert multi["attr_name"] == "宽度"


def test_bypass_both_skip_statuses_in_one_run():
    """同一 items 跑两个缺口属性：0 候选 + 多候选各自落点（writer 收到两种 status）。"""
    schema = [_schema_attr(9010, "容量"), _schema_attr(9011, "宽度")]
    draft = {"容量": "大号", "宽度": "加宽"}
    def _by_aid(cid, key, aid, dc, tp, term):
        if aid == 9010:
            return []
        return [{"id": 8001, "value": "Широкий"}, {"id": 8002, "value": "Узкий"}]
    calls = _fill_and_capture_logs(schema, draft, _by_aid)
    statuses = sorted({c["status"] for c in calls})
    assert "skipped_no_value" in statuses
    assert "skipped_multi_candidate" in statuses


def test_vision_logs_no_infer_outside_whitelist():
    """vision 白名单外 + should_fill 的可选字典属性（如「功率」）→ 写 no_infer 打点。"""
    calls = []
    def _capture(*args, **kwargs):
        calls.append(kwargs)
    schema = [_schema_attr(4558, "功率")]
    draft = {"title": "小风扇", "images": ["http://img/1.jpg"]}
    item = {"attributes": []}
    with mock.patch.dict(os.environ, {"APP_WORKSPACE_PATH": _WORKER}), \
         mock.patch("utils.mxou_api.call_mxou_chat_api") as m_llm, \
         mock.patch("utils.attr_match_log.log_attr_match", side_effect=_capture):
        mod._infer_attrs_from_vision([item], schema, draft, _state())
    statuses = [c["status"] for c in calls]
    assert "no_infer" in statuses, f"应写 no_infer，实际 {statuses}"
    rec = next(c for c in calls if c["status"] == "no_infer")
    assert rec["attr_id"] == 4558
    assert rec["attr_name"] == "功率"
    assert rec["should_fill"] is True
    assert not m_llm.called, "白名单外属性不应触发 LLM 推断"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)
