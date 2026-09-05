"""v0.65.1 类目多义词保护测试（R1-R4，店铺 4718259 实证：手套/面罩→成人糖果 18+、
护膝→宠物水族、童帽→儿童滑梯）。

根因：
- R2 主 bug：`_search_jieba_like` 对 full_path 做 ILIKE %token%，多义词 token
  （成人/儿童/宠物）把整棵不相关子树拖入候选（「成人帽」的「成人」≠「成人用品」）。
- R1 防护：敏感大类子树（成人用品 18+/情趣/烟草/酒精/药品/武器）不允许普通商品
  无敏感源词时落进。
- R3：skill search_kw 给了合理 dc 时防止文本链把它带偏。
- R4：Ozon 审核拒绝类目（22507「Группа товаров」/8229「Тип」等）→ 整卡重配。

运行：
    cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" \
      PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_category_multiword_protection_v0651.py -q
⚠️ 全部为纯 mock / 纯函数，不连真实 PG。
"""
import os
import sys
from unittest import mock

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 成人糖果 18+ 子树锚（ZH: 成人用品 > 成人的糖果点心；RU: Товары для взрослых > ...）
ADULT_CANDY_DC = 200001462
HAT_DC = 17028976          # 帽子类目（任意非敏感 dc，测试用）
CANDY_TYPE = 971363842
HAT_TYPE = 95701

_CANDY_CAT = {
    "description_category_id": ADULT_CANDY_DC, "type_id": None,
    "node_name": "成人的糖果点心", "full_path": "成人用品 > 成人的糖果点心",
    "top_level_category_name": "成人用品", "depth": 1,
}
_CANDY_TYPE = {
    "description_category_id": ADULT_CANDY_DC, "type_id": CANDY_TYPE,
    "node_name": "成人糖果", "full_path": "成人用品 > 成人的糖果点心 > 成人糖果",
    "top_level_category_name": "成人用品", "depth": 2,
}
_HAT_NODE = {
    "description_category_id": HAT_DC, "type_id": HAT_TYPE,
    "node_name": "儿童帽", "full_path": "儿童用品 > 帽子 > 儿童帽",
    "top_level_category_name": "儿童用品", "depth": 2,
}
# 护膝场景：node_name 含「护膝」的正常候选 vs 仅 full_path 深层含「护膝」的水族节点
_KNEE_GOOD = {
    "description_category_id": 1001, "type_id": 10001,
    "node_name": "运动护膝", "full_path": "运动与休闲 > 装备与护具 > 运动护膝",
    "top_level_category_name": "运动与休闲", "depth": 2,
}
_KNEE_AQUA = {
    "description_category_id": 1002, "type_id": 10002,
    "node_name": "水族箱配件", "full_path": "宠物用品 > 水族 > 水族箱配件 > 鱼缸护膝垫",
    "top_level_category_name": "宠物用品", "depth": 3,
}


def _no_db():
    """patch：搜索链路不得触碰真实 PG（防测试连库）；若代码试图访问即失败。"""
    return mock.patch(
        "utils.ozon_category_query.get_session",
        side_effect=AssertionError("类目搜索测试不应触碰真实 DB（get_session 被调）"),
    )


# ═══════════════════════════════════════════════════════════════════════
# R1: 敏感大类子树识别（纯函数）
# ═══════════════════════════════════════════════════════════════════════
def test_r1_sensitive_top_category_zh_adult():
    from utils.ozon_category_query import is_sensitive_top_category
    assert is_sensitive_top_category("成人用品 > 成人的糖果点心") is True
    assert is_sensitive_top_category("成人用品 > 成人的糖果点心 > 成人糖果") is True
    assert is_sensitive_top_category("成人用品 > 情趣玩具 > 震动棒") is True


def test_r1_sensitive_top_category_ru_and_dc():
    from utils.ozon_category_query import is_sensitive_top_category
    assert is_sensitive_top_category("Товары для взрослых > Кондитерские изделия 18+") is True
    assert is_sensitive_top_category(str(ADULT_CANDY_DC)) is True
    assert is_sensitive_top_category(ADULT_CANDY_DC) is True


def test_r1_sensitive_top_category_non_sensitive_not_flagged():
    from utils.ozon_category_query import is_sensitive_top_category
    assert is_sensitive_top_category("服装 > 帽子 > 儿童帽") is False
    # 裸「烟」不得误伤烟囱/油烟机（keyword 需避单字）
    assert is_sensitive_top_category("建筑和装修 > 烟囱和配套件") is False
    assert is_sensitive_top_category("家用电器 > 油烟机") is False
    assert is_sensitive_top_category("运动与休闲 > 气动武器和弩") is True  # 武器


def test_r1_sensitive_source_signal():
    from utils.ozon_category_query import has_sensitive_source_signal
    assert has_sensitive_source_signal("情趣内衣 震动棒") is True
    assert has_sensitive_source_signal("电子烟 烟弹") is True
    # 「成人帽」的成人不是敏感信号词（跟卖管帽类普通商品）
    assert has_sensitive_source_signal("成人帽 冬季保暖") is False
    assert has_sensitive_source_signal("冬季保暖帽") is False
    assert has_sensitive_source_signal("") is False
    assert has_sensitive_source_signal(None) is False


def test_r1_sensitive_signal_whitelist_balanced_p14():
    """P1-4: 白名单品类词——宽泛单字不误放行正常商品，真成人叶子词放行 18+。"""
    from utils.ozon_category_query import has_sensitive_source_signal
    # 删过宽项：美工刀/山药/震动闹钟 带「刀/药/震动」但不指向敏感语义 → 不放行
    assert has_sensitive_source_signal("美工刀 裁纸刀") is False
    assert has_sensitive_source_signal("山药 煲汤") is False
    assert has_sensitive_source_signal("震动闹钟 起床神器") is False
    assert has_sensitive_source_signal("仿真花 绢花") is False
    assert has_sensitive_source_signal("润滑油 汽车机油") is False
    assert has_sensitive_source_signal("延时继电器 定时器") is False
    # 真实成人叶子词命中 → 放行对应敏感子树（不阻断）
    assert has_sensitive_source_signal("飞机杯 男用自慰器") is True
    assert has_sensitive_source_signal("硅胶娃娃 实体娃娃") is True
    assert has_sensitive_source_signal("震动棒 情趣用品") is True
    assert has_sensitive_source_signal("阳具 仿真") is True


def test_r1_candidate_filter_removes_sensitive_without_signal():
    """帽类源词候选含成人糖果 → 剔除；标题含情趣 → 放行敏感子树。"""
    from utils.ozon_category_query import sensitive_candidate_filter
    cands = [_CANDY_TYPE, _HAT_NODE]
    out = sensitive_candidate_filter(cands, "冬季保暖帽")
    assert ADULT_CANDY_DC not in [c["description_category_id"] for c in out]
    assert HAT_DC in [c["description_category_id"] for c in out]
    # 全剔除时维持原列表（由定稿闸兜底 veto，不在过滤器里清空）
    only = sensitive_candidate_filter([_CANDY_TYPE], "冬季保暖帽")
    assert [c["description_category_id"] for c in only] == [ADULT_CANDY_DC]


def test_r1_candidate_filter_allows_sensitive_with_signal():
    from utils.ozon_category_query import sensitive_candidate_filter
    cands = [_CANDY_TYPE, _HAT_NODE]
    out = sensitive_candidate_filter(cands, "情趣糖果 震动棒")
    assert ADULT_CANDY_DC in [c["description_category_id"] for c in out]


def test_r1_adoption_blocked_only_without_signal():
    from utils.ozon_category_query import sensitive_adoption_blocked
    assert sensitive_adoption_blocked("成人用品 > 成人的糖果点心", "冬季保暖帽") is True
    assert sensitive_adoption_blocked("成人用品 > 成人的糖果点心", "情趣糖果") is False
    assert sensitive_adoption_blocked("服装 > 帽子", "冬季保暖帽") is False


# ═══════════════════════════════════════════════════════════════════════
# R2: jieba 修饰词剥离 + 单字品类词兜底（纯函数）
# ═══════════════════════════════════════════════════════════════════════
def test_r2_plan_strips_modifiers_and_residual():
    from utils.ozon_category_query import build_jieba_search_plan
    plan = build_jieba_search_plan("成人帽")
    assert plan["search_tokens"] == [], f"成人帽 修饰词应全剥离: {plan}"
    assert plan["residual"] == "帽", f"单字品类词兜底应为 '帽': {plan}"

    plan = build_jieba_search_plan("冬季保暖帽")
    assert plan["search_tokens"] == []
    assert plan["residual"] == "帽"

    plan = build_jieba_search_plan("成人雷锋帽")
    # 成人 被剥离，雷锋 是核心词 → 用 雷锋 搜索（不会命中 成人糖果 18+ 子树）
    assert plan["search_tokens"] == ["雷锋"], f"成人雷锋帽 应剥离 成人: {plan}"
    assert "成人" not in plan["search_tokens"]
    assert plan["residual"] == ""


def test_r2_plan_keeps_core_tokens():
    from utils.ozon_category_query import build_jieba_search_plan
    plan = build_jieba_search_plan("儿童滑梯")
    assert plan["search_tokens"] == ["滑梯"], f"儿童 是修饰词应剥离: {plan}"
    assert plan["residual"] == ""
    plan = build_jieba_search_plan("护膝")
    assert plan["search_tokens"] == ["护膝"]
    plan = build_jieba_search_plan("汽车儿童安全座椅")
    assert "儿童" not in plan["search_tokens"]
    assert "座椅" in plan["search_tokens"]


def test_r2_plan_modifier_only_query_no_residual():
    from utils.ozon_category_query import build_jieba_search_plan
    plan = build_jieba_search_plan("新款韩版")
    assert plan["search_tokens"] == []
    assert plan["residual"] == ""  # 纯修饰词无品类词 → 交 LLM L3，不做无意义搜索


# ═══════════════════════════════════════════════════════════════════════
# R2: _search_jieba_like 候选质量（DB-free，seam 打桩）
# ═══════════════════════════════════════════════════════════════════════
def _rows_like(rows, pattern, name_only=False):
    out = []
    for r in rows:
        name = str(r.get("node_name") or "")
        path = str(r.get("full_path") or "")
        hit = (pattern in name) if name_only else (pattern in name or pattern in path)
        if hit:
            out.append(dict(r))
    return out


def test_r2_search_adult_cap_no_adult_candy():
    """『成人帽』不得把 成人糖果 18+ 子树带进候选（旧代码 token=成人 → candy 全中）。"""
    from utils.ozon_category_query import OzonCategoryQuery
    q = OzonCategoryQuery()
    called = []

    def fake_fetch(pattern, node_type, top_k, name_only=False):
        called.append((pattern, name_only))
        return _rows_like([_CANDY_TYPE, _HAT_NODE, _KNEE_GOOD], pattern, name_only)[:top_k]

    with _no_db(), mock.patch.object(OzonCategoryQuery, "_ensure_nodes_synced", return_value=None), \
         mock.patch.object(q, "_fetch_rows_like", create=True, side_effect=fake_fetch):
        results = q._search_jieba_like("成人帽", top_k=10, node_type="type")

    dcs = {r["description_category_id"] for r in results}
    assert ADULT_CANDY_DC not in dcs, f"成人帽 候选不得含成人糖果 dc: {results}"
    # 关键：绝不能用「成人」token 去搜（否则整棵 18+ 子树被 ILIKE %成人% 拖入）
    assert "成人" not in [c[0] for c in called], f"不得用 成人 token 搜索: {called}"
    assert results, "应能靠单字『帽』兜底搜到帽类候选"


def test_r2_search_winter_cap_keeps_candy_out():
    from utils.ozon_category_query import OzonCategoryQuery
    q = OzonCategoryQuery()

    def fake_fetch(pattern, node_type, top_k, name_only=False):
        return _rows_like([_CANDY_TYPE, _HAT_NODE], pattern, name_only)[:top_k]

    with _no_db(), mock.patch.object(OzonCategoryQuery, "_ensure_nodes_synced", return_value=None), \
         mock.patch.object(q, "_fetch_rows_like", create=True, side_effect=fake_fetch):
        results = q._search_jieba_like("冬季保暖帽", top_k=10, node_type="type")
    assert ADULT_CANDY_DC not in {r["description_category_id"] for r in results}


def test_r2_search_knee_guard_drops_fullpath_only():
    """『护膝』不得被仅 full_path 深层含『护膝』的水族节点拖入（node_name 命中约束）。"""
    from utils.ozon_category_query import OzonCategoryQuery
    q = OzonCategoryQuery()

    def fake_fetch(pattern, node_type, top_k, name_only=False):
        return _rows_like([_KNEE_GOOD, _KNEE_AQUA, _CANDY_TYPE], pattern, name_only)[:top_k]

    with _no_db(), mock.patch.object(OzonCategoryQuery, "_ensure_nodes_synced", return_value=None), \
         mock.patch.object(q, "_fetch_rows_like", create=True, side_effect=fake_fetch):
        results = q._search_jieba_like("护膝", top_k=10, node_type="type")
    dcs = {r["description_category_id"] for r in results}
    assert _KNEE_GOOD["description_category_id"] in dcs, "node_name 含 护膝 的候选应保留"
    assert _KNEE_AQUA["description_category_id"] not in dcs, \
        f"仅 full_path 深层含 护膝 的水族节点不应进候选: {results}"


def test_r2_search_glove_no_adult():
    """『手套』不落 成人/18+ 子树。"""
    from utils.ozon_category_query import OzonCategoryQuery
    q = OzonCategoryQuery()

    def fake_fetch(pattern, node_type, top_k, name_only=False):
        return _rows_like([_CANDY_TYPE, _HAT_NODE], pattern, name_only)[:top_k]

    with _no_db(), mock.patch.object(OzonCategoryQuery, "_ensure_nodes_synced", return_value=None), \
         mock.patch.object(q, "_fetch_rows_like", create=True, side_effect=fake_fetch):
        results = q._search_jieba_like("手套", top_k=10, node_type="type")
    assert ADULT_CANDY_DC not in {r["description_category_id"] for r in results}


# ═══════════════════════════════════════════════════════════════════════
# R2b/R3 辅助：非泛词 overlap（排除 成人/儿童/用品 等）
# ═══════════════════════════════════════════════════════════════════════
def test_r2b_non_generic_overlap_excludes_modifiers():
    """overlap 验证必须排除 成人/儿童 等修饰词——否则儿童滑梯靠「儿童」overlap 蒙混过关。"""
    from graphs.nodes.assemble_ozon_product_node import _non_generic_overlap_words
    # 儿童滑梯路径 vs 帽子源词 → 无非泛词 overlap
    ov = _non_generic_overlap_words("儿童用品 > 户外玩具 > 儿童滑梯", ["儿童 帽"])
    assert ov == set(), f"『儿童』是修饰词不应算 overlap: {ov}"
    # 摩托车后视镜 vs 后视镜 → overlap
    ov = _non_generic_overlap_words("运动与休闲 > 摩托车后视镜", ["后视镜 汽车"])
    assert "后视镜" in ov


def test_p11_search_kw_not_skill_authoritative():
    """P1-1: search_kw（非权威）恒 False——不得因候选 top5 成员升级 Skill 豁免。"""
    from graphs.nodes.assemble_ozon_product_node import _is_skill_authoritative
    skill = {"description_category_id": HAT_DC, "type_id": HAT_TYPE, "_resolved_by_path": False}
    assert _is_skill_authoritative("search_kw", "", skill) is False
    assert _is_skill_authoritative("page", "", skill) is True
    assert _is_skill_authoritative("mapping", "", skill) is True
    assert _is_skill_authoritative("what_to_sell", "", skill) is True
    # widget 命名空间数字 ID：仅路径精配成功才权威
    assert _is_skill_authoritative("page", "widget", skill) is False
    skill_path = dict(skill, _resolved_by_path=True)
    assert _is_skill_authoritative("page", "widget", skill_path) is True


def test_p11_search_kw_candidate_stays_l1_layer():
    """P1-1(a): search_kw skill 候选作为普通 L1 候选（不过 Skill 豁免闸）。"""
    # 语义锚：assemble 只用 _is_skill_authoritative 判定 Skill 层，search_kw=False
    from graphs.nodes.assemble_ozon_product_node import _is_skill_authoritative
    assert _is_skill_authoritative("search_kw", "", None) is False


def test_p12_authoritative_numeric_dc_sensitive_vetoed():
    """P1-2: 权威 what_to_sell 数字 dc 落敏感子树且无信号 → R1 veto（不再豁免）。"""
    from graphs.nodes.assemble_ozon_product_node import _r1_veto
    # 数字权威 skill 命中（无 _resolved_by_path）+ 无敏感信号 → veto
    cat = {"description_category_id": ADULT_CANDY_DC, "type_id": CANDY_TYPE,
           "category_path": "成人用品 > 成人的糖果点心 > 成人糖果"}
    assert _r1_veto(cat, "冬季保暖帽") is True
    # 竞品 category_path 精确解析（_resolved_by_path=True）→ 信任，不 veto
    cat_path = dict(cat, _resolved_by_path=True)
    assert _r1_veto(cat_path, "冬季保暖帽") is False
    # 源词含白名单敏感信号（真实成人商品）→ 放行
    assert _r1_veto(cat, "飞机杯 男用 成人用品") is False
    # 非敏感子树恒放行
    assert _r1_veto({"category_path": "服装 > 帽子 > 儿童帽"}, "冬季保暖帽") is False


def test_p13_close_score_cross_top_ambiguity_detected():
    """P1-3: 同分不同顶层大类 → 检测到歧义（护膝：运动护膝 vs 园艺/水族全高 sim）。"""
    from graphs.nodes.assemble_ozon_product_node import _find_close_top_category_rival
    adopted = {"description_category_id": 1001, "similarity": 1.0,
               "full_path": "运动与休闲 > 装备与护具 > 运动护膝"}
    cands = [
        {"description_category_id": 1001, "type_id": 1, "similarity": 1.0,
         "full_path": "运动与休闲 > 装备与护具 > 运动护膝"},
        {"description_category_id": 17028746, "type_id": 2, "similarity": 1.0,
         "full_path": "住宅和花园 > 园艺工具 > 园艺地垫，护膝"},
        {"description_category_id": 17027487, "type_id": 3, "similarity": 0.4,
         "full_path": "宠物用品 > 宠物服饰 > 宠物护膝"},
    ]
    rival = _find_close_top_category_rival(adopted, cands)
    assert rival is not None, "同分跨大类（运动 vs 园艺）应触发消歧"
    assert rival["description_category_id"] == 17028746
    # 同顶层大类（不同品类）不算歧义
    same_top = [
        {"description_category_id": 1001, "type_id": 1, "similarity": 1.0,
         "full_path": "运动与休闲 > 台球 > 台球手套"},
        {"description_category_id": 1002, "type_id": 2, "similarity": 1.0,
         "full_path": "运动与休闲 > 保龄球 > 保龄球手套"},
    ]
    assert _find_close_top_category_rival(same_top[0], same_top) is None
    # sim 差距大（宠物 0.4 vs 1.0）不算歧义
    assert _find_close_top_category_rival(adopted, [cands[2]]) is None


def test_p13_residual_scoring_deterministic():
    """P1-3: 单字兜底确定性排序 + 真实 sim（精确 1.0 > 前缀 0.7 > 含 0.6 > 仅路径 0.5）。"""
    from utils.ozon_category_query import score_residual_rows
    rows = [
        {"description_category_id": 2, "type_id": 20, "node_name": "帽子",
         "full_path": "服装 > 帽子", "depth": 1},
        {"description_category_id": 1, "type_id": 10, "node_name": "帽",
         "full_path": "服装 > 帽", "depth": 1},
        {"description_category_id": 3, "type_id": 30, "node_name": "泳帽",
         "full_path": "运动与休闲 > 泳帽", "depth": 2},
        {"description_category_id": 4, "type_id": 40, "node_name": "箱包配件",
         "full_path": "服装 > 箱包配件 > 帽夹", "depth": 3},
    ]
    out = score_residual_rows(rows, "帽", top_k=10)
    # 精确命中 node_name==帽 排第一 sim=1.0
    assert out[0]["node_name"] == "帽" and out[0]["similarity"] == 1.0
    # 前缀命中 帽子 sim=0.7；含命中 泳帽 sim=0.6；仅 full_path 命中 sim=0.5
    sims = {r["node_name"]: r["similarity"] for r in out}
    assert sims["帽子"] == 0.7
    assert sims["泳帽"] == 0.6
    assert sims["箱包配件"] == 0.5
    assert [r["similarity"] for r in out] == sorted(
        [r["similarity"] for r in out], reverse=True), "同分必须确定性降序"


# ═══════════════════════════════════════════════════════════════════════
# R4: retry 整卡类目重配
# ═══════════════════════════════════════════════════════════════════════
def _candy_declined_state(**over):
    from graphs.validation_retry_loop import ValidationRetryLoopState
    base = dict(
        error_code="DESCRIPTION_DECLINE",
        attribute_id=22507,  # Группа товаров → 类目错
        token="t", ozon_client_id="c", ozon_api_key="k",
        description_category_id=str(ADULT_CANDY_DC), type_id=str(CANDY_TYPE),
        draft={"title": "冬季保暖帽 渔夫帽", "attributes": {}},
        product_name="Шапка зимняя",
        ozon_payload={"items": [{"name": "Шапка зимняя", "description_category_id": ADULT_CANDY_DC,
                                 "type_id": CANDY_TYPE, "price": "1990", "currency_code": "RUB",
                                 "depth": 10, "width": 20, "height": 30, "weight": 200, "images": []}]},
        final_attributes=[], attributes_schema=[], dictionary_values={},
        errors=[], error_message="",
    )
    base.update(over)
    return ValidationRetryLoopState(**base)


class _FakeQuery:
    """打桩 search_nodes：按标题子串返回候选。"""

    def __init__(self, candidates):
        self._cands = candidates

    def search_nodes(self, query, top_k=10, node_type="type", language="ZH_HANS"):
        return [dict(c) for c in self._cands[:top_k]]


def _fake_rebuild(cap):
    def _rb(new_dc, new_type, draft, images, ozon_client_id, ozon_api_key,
            weight_grams, dimensions, price_rub, old_price_rub, currency_code,
            token, ru_category_path="", traffic_keywords=None):
        cap["new_dc"] = new_dc
        cap["new_type"] = new_type
        return {
            "items": [{"description_category_id": new_dc, "type_id": new_type,
                       "name": "Шапка вязаная", "attributes": []}],
            "final_attributes": [{"attribute_id": 4180, "value": "Шапка вязаная",
                                  "dictionary_value_id": 0}],
            "llm_name": "Шапка вязаная",
            "attr_list": [],
            "dict_lookup": {},
        }
    return _rb


def test_r4_recategorize_on_22507_switches_dc_and_rebuilds():
    """糖果 dc + 22507 类目错 → 重配到非敏感帽子 dc + 调用 _rebuild_for_new_category。"""
    from graphs.validation_retry_loop import error_repair_llm_node
    state = _candy_declined_state()
    cap = {}
    fake_q = _FakeQuery([_HAT_NODE])
    with mock.patch("utils.ozon_category_query.get_category_query", return_value=fake_q), \
         mock.patch("graphs.nodes.assemble_ozon_product_node._rebuild_for_new_category",
                    side_effect=_fake_rebuild(cap)):
        out = error_repair_llm_node(state)

    assert out.description_category_id == str(HAT_DC), \
        f"应重配到帽子 dc，实际 {out.description_category_id}"
    assert out.type_id == str(HAT_TYPE)
    assert cap.get("new_dc") == HAT_DC, f"rebuild 应以新 dc 调用: {cap}"
    assert cap.get("new_type") == HAT_TYPE
    assert out.final_attributes and out.final_attributes[0]["value"] == "Шапка вязаная"
    assert out.ozon_payload["items"][0]["description_category_id"] == HAT_DC
    assert out.needs_recategorization is False, "重配成功不应残留 needs_recategorization"


def test_r4_recategorize_no_solution_marks_needs_and_no_rebuild():
    """搜索只返回敏感糖果候选（源无信号）→ 无解：needs_recategorization + 不调 rebuild。"""
    from graphs.validation_retry_loop import error_repair_llm_node
    state = _candy_declined_state()
    cap = {}
    fake_q = _FakeQuery([_CANDY_TYPE])
    with mock.patch("utils.ozon_category_query.get_category_query", return_value=fake_q), \
         mock.patch("graphs.nodes.assemble_ozon_product_node._rebuild_for_new_category",
                    side_effect=_fake_rebuild(cap)):
        out = error_repair_llm_node(state)

    assert out.needs_recategorization is True
    assert "人工" in (out.error_message or ""), f"应提示人工确认类目: {out.error_message}"
    assert cap == {}, f"无解场景不应调 rebuild: {cap}"


def test_r4_should_continue_hard_blocks_recategorization():
    """needs_recategorization 且无解 → should_continue 直接 exit（不再重传烧轮次）。"""
    from graphs.validation_retry_loop import should_continue
    from graphs.validation_retry_loop import ValidationRetryLoopState
    state = ValidationRetryLoopState(
        needs_recategorization=True, is_valid=True, retry_count=1,
        error_message="类目需人工确认：自动重配无解",
    )
    out = should_continue(state)
    assert out == "exit"
    assert state.is_valid is False
    assert state.upload_status == "failed"


def test_r4_looks_like_category_mismatch():
    from graphs.validation_retry_loop import _looks_like_category_mismatch
    s = _candy_declined_state()  # DESCRIPTION_DECLINE + attr 22507
    assert _looks_like_category_mismatch(s) is True
    s2 = _candy_declined_state(error_code="BR_chinese_hieroglyphs_in_attribute", attribute_id=8229)
    assert _looks_like_category_mismatch(s2) is False, "BR_chinese 不属类目错，防误重配"
    s3 = _candy_declined_state(error_code="INVALID_ATTRIBUTE_VALUE", attribute_id=8229,
                               error_message="Тип не соответствует категории товара")
    assert _looks_like_category_mismatch(s3) is True
    s4 = _candy_declined_state(error_code="MISSING_REQUIRED_ATTRIBUTE", attribute_id=8229)
    assert _looks_like_category_mismatch(s4) is True, "8229(Тип) 缺失视作类目错"


def test_r4_declined_backfeeds_mod_errors_for_reparse():
    """recheck 见 declined → mod_errors 回灌 state.errors → should_reupload 走 parse_error。"""
    import time as _time_module
    from graphs.validation_retry_loop import (
        ValidationRetryLoopState, recheck_status_node, should_reupload, time as _retry_time,
    )
    from utils.http_session import session

    import_resp = mock.Mock()
    import_resp.status_code = 200
    import_resp.json.return_value = {"result": {"items": [{"status": "imported", "product_id": "111", "errors": []}]}}
    mod_resp = mock.Mock()
    mod_resp.json.return_value = {"items": [{"statuses": {"moderate_status": "declined"},
                                             "errors": [{"code": "DESCRIPTION_DECLINE",
                                                         "attribute_id": 22507,
                                                         "texts": {"message": "категория"}}]}]}

    def side_effect(url, **kwargs):
        if "info/list" in url:
            return mod_resp
        return import_resp

    state = ValidationRetryLoopState(
        task_id="1234567890", token="t", ozon_client_id="c", ozon_api_key="k",
        ozon_payload={"items": []},
    )
    with mock.patch.object(session, "post", side_effect=side_effect), \
         mock.patch.object(_retry_time, "sleep"), \
         mock.patch.object(_time_module, "sleep"):
        out = recheck_status_node(state)

    assert out.moderation_status == "declined"
    assert out.upload_status == "failed"
    codes = [e.get("code") for e in out.errors] if isinstance(out.errors, list) else []
    assert "DESCRIPTION_DECLINE" in codes, f"declined mod_errors 应回灌 state.errors: {out.errors}"
    assert should_reupload(out) == "parse_error", "回灌后应回到 parse_error 再循环（重配结果被再验证）"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
        except Exception:
            traceback.print_exc()
            print(f"  ❌ {fn.__name__}: 异常")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
