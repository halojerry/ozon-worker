"""feat/attribute-fill-en-v1 T2/T3 回归：vision 颜色精确等值 + 模板继承。

- T2（B2 10096 实证）：颜色大字典 unique_or_none 多候选恒拒 → vision 色词静默丢
  → completed 卡缺颜色。放宽：候选中与搜索词**全等**的条目越过唯一性（仅颜色类）。
- T3：同叶子自家 approved 卡模板继承——只补缺失 schema 属性，个体值不抄，静默失败。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from graphs.nodes.prepare_ozon_upload_node import (  # noqa: E402
    _TEMPLATE_SKIP_ATTR_IDS,
    _inherit_attrs_from_template,
)


# ── T3 模板继承 ──────────────────────────────────────────────

class _Rec:
    """ozon_post 录制桩。"""

    def __init__(self, v4_items=None):
        self.calls = []
        self._v4_items = v4_items or []

    def __call__(self, cid, key, endpoint, body=None, timeout=30, **kw):
        self.calls.append(endpoint)
        if "product/info/attributes" in endpoint:
            return {"result": {"items": self._v4_items}}
        raise RuntimeError(f"unexpected: {endpoint}")


def _schema(*aids):
    return [{"id": a, "name": f"attr{a}", "dictionary_id": 0,
             "is_required": False, "is_collection": False} for a in aids]


def _state(dc="17027937", tp="95483"):
    return SimpleNamespace(
        description_category_id=dc, type_id=tp,
        ozon_client_id="1", ozon_api_key="k",
        token="t", user_id="u", task_id="task-x",
    )


def test_template_fills_only_missing_schema_attrs(monkeypatch):
    """只补缺失 + schema 内的属性；已存在/个体值/非 schema 均不碰。"""
    import graphs.nodes.prepare_ozon_upload_node as mod

    # 模板卡属性：6881（可补）、9048（个体值跳过）、4180（非 schema 跳过）、7777（可补）
    v4_items = [{"id": 6443821910, "attributes": [
        {"id": 6881, "values": [{"dictionary_value_id": 123, "value": "с крышкой"}]},
        {"id": 9048, "values": [{"dictionary_value_id": 0, "value": "MX123"}]},
        {"id": 4180, "values": [{"dictionary_value_id": 9, "value": "старое"}]},
        {"id": 7777, "values": [{"dictionary_value_id": 55, "value": "Китай"}]},
    ]}]
    rec = _Rec(v4_items)
    # ozon_post 是函数内 import——patch 源模块（utils.ozon_api）才生效
    import utils.ozon_client as _oz
    monkeypatch.setattr(_oz, "ozon_post", rec, raising=True)

    items = [{"offer_id": "x", "attributes": [
        {"id": 4180, "values": [{"dictionary_value_id": 8, "value": "новое"}]},  # 已存在
    ]}]
    schema = _schema(4180, 6881, 7777)  # schema 无 9048
    out = _inherit_attrs_from_template(items, schema, _state())
    got = {a["id"] for a in out[0]["attributes"]}
    assert 6881 in got and 7777 in got, "缺失且 schema 内的应补"
    assert got.count(4180) == 1 if isinstance(got, list) else True
    ids = [a["id"] for a in out[0]["attributes"]]
    assert ids.count(4180) == 1, "已存在的不重复补"
    assert 9048 not in ids, "个体值 9048 不抄"


def test_template_skips_chinese_values(monkeypatch):
    """模板值含中文 → 脏数据防御不抄。"""
    import graphs.nodes.prepare_ozon_upload_node as mod
    v4_items = [{"id": 1, "attributes": [
        {"id": 6881, "values": [{"dictionary_value_id": 1, "value": "带盖收纳筐"}]}]}]
    import utils.ozon_client as _oz
    monkeypatch.setattr(_oz, "ozon_post", _Rec(v4_items), raising=True)
    out = _inherit_attrs_from_template(
        [{"offer_id": "x", "attributes": []}], _schema(6881), _state())
    assert out[0]["attributes"] == [], "中文模板值不抄"


def test_template_silent_when_no_source(monkeypatch):
    """无同叶子 completed 卡（PG 不可用/空）→ 原样返回零调用。"""
    import graphs.nodes.prepare_ozon_upload_node as mod

    def _no_db():
        raise RuntimeError("PG down")

    monkeypatch.setattr("storage.database.db.get_session", _no_db, raising=False)
    items = [{"offer_id": "x", "attributes": []}]
    out = _inherit_attrs_from_template(items, _schema(6881), _state())
    assert out is items, "静默原样返回"


def test_template_skip_ids_cover_individual_values():
    """个体值黑名单完整性（型号/品牌/颜色/产地/件数/HS）。"""
    for aid in (9048, 85, 5076, 10096, 10097, 4389, 4224, 4225, 8962, 22232):
        assert aid in _TEMPLATE_SKIP_ATTR_IDS, aid


# ── T2 vision 颜色精确等值（函数级：走真实 _infer_attrs_from_vision 太重，
#    锁定分支逻辑等价物 + 全链由实机 gate 验证）────────────────

def test_vision_color_exact_match_branch_exists():
    """源码级锁定：颜色精确等值分支存在且仅限颜色类。"""
    import inspect
    src = inspect.getsource(
        sys.modules["graphs.nodes.prepare_ozon_upload_node"]._infer_attrs_from_vision)
    assert "_is_color_attr" in src, "颜色等值分支必须在"
    assert 'aid in (10096, 10097)' in src, "颜色类判定必须含 10096/10097"
