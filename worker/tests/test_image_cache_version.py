# -*- coding: utf-8 -*-
"""T7a: 生图缓存版本化 + params 快照 + image_parent_task_id 回溯（契约 C3）。

验收门（计划 §5 T7a / §6 风险登记表）：
(a) force_regen → 新行 version+1 新 URL（无静默缓存命中——断言 get 返回不同 URL）
(b) resubmit + image_parent_task_id → 复用父图（断言 call_mxou_image_api **不被调用**）
(c) 正常 retry（同 task_id 重跑）→ 命中缓存（回归保留）
(d) params 完整存节点 Input schema（JSON 断言）
(e) save_image version 递增正确

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_image_cache_version.py -v
⚠️ 纯 mock（FakeImageStore 模拟 PG 表），无需 PG/GPU。
"""
import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("PGDATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ozon")

logging.basicConfig(level=logging.WARNING)

from utils import task_image_cache as tic  # noqa: E402


# ══════════════════════════════════════════════════════════════
# Fake PG 存储（模拟 task_generated_images + ozon_product_tasks）
# ══════════════════════════════════════════════════════════════


class FakeRow:
    def __init__(self, values):
        self._values = values

    def __getitem__(self, i):
        return self._values[i]


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return FakeRow(self._rows[0]) if self._rows else None

    def fetchall(self):
        return [FakeRow(r) for r in self._rows]


class FakeConn:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        return self._store.execute(str(stmt), params or {})

    def commit(self):
        pass


class FakeImageStore:
    """模拟 PG 两张表：
    - task_generated_images[(tid, slot, version)] -> {url, params, image_parent_task_id}
    - ozon_product_tasks.parents[tid] -> parent_task_id（payload 任务级血缘）
    """

    def __init__(self):
        self.images = {}
        self.parents = {}
        self.calls = []

    def connect(self):
        return FakeConn(self)

    def execute(self, sql, params):
        self.calls.append((sql, params))
        # INSERT 图片行
        if "INSERT INTO task_generated_images" in sql:
            key = (params["tid"], params["s"], params["v"])
            raw_p = params.get("p")
            self.images[key] = {
                "url": params["u"],
                "params": json.loads(raw_p) if raw_p else None,
                "image_parent_task_id": params.get("ip"),
            }
            return FakeResult()
        # 下一版本号（save 自动递增）
        if "MAX(version)" in sql:
            versions = [v for (t, s, v) in self.images if t == params["tid"] and s == params["s"]]
            return FakeResult([(max(versions) + 1 if versions else 1,)])
        # 任务级血缘
        if "parent_task_id" in sql and "ozon_product_tasks" in sql:
            return FakeResult([(self.parents.get(params["tid"]),)])
        # 图片表读
        if "FROM task_generated_images" in sql:
            if "ORDER BY slot, version" in sql:  # list_images（6 列）
                matches = sorted(
                    [(t, s, v) for (t, s, v) in self.images if t == params["tid"]],
                    key=lambda m: (m[1], m[2]),
                )
                return FakeResult([
                    (s, v, self.images[(t, s, v)]["url"], self.images[(t, s, v)]["params"],
                     self.images[(t, s, v)]["image_parent_task_id"], "2026-01-01")
                    for (t, s, v) in matches
                ])
            matches = [(t, s, v) for (t, s, v) in self.images
                       if t == params["tid"] and s == params["s"]]
            if params.get("v") is not None:
                matches = [m for m in matches if m[2] == params["v"]]
            matches.sort(key=lambda m: m[2], reverse=True)
            if not matches:
                return FakeResult()
            (t, s, v) = matches[0]
            r = self.images[(t, s, v)]
            return FakeResult([(r["url"], r["params"], r["image_parent_task_id"], v)])
        return FakeResult()


@pytest.fixture()
def store(monkeypatch):
    """把 task_image_cache 用到的 get_engine 指向 FakeImageStore。"""
    s = FakeImageStore()
    monkeypatch.setattr("storage.database.db.get_engine", lambda: s)
    return s


# ══════════════════════════════════════════════════════════════
# 1. save/get 版本化（验收 e）
# ══════════════════════════════════════════════════════════════


def test_save_version_auto_increments(store):
    """save_image version=None → 同 (task_id,slot) 自动递增 1→2→3。"""
    assert tic.save_image("T1", "main", "https://img/1.jpg") == 1
    assert tic.save_image("T1", "main", "https://img/2.jpg") == 2
    assert tic.save_image("T1", "main", "https://img/3.jpg") == 3
    assert (("T1", "main", 3) in store.images)


def test_save_explicit_version(store):
    """save_image version 显式传入 → 写指定版本行（regen 用 prev+1）。"""
    tic.save_image("T1", "white_bg", "https://img/v1.jpg", version=1)
    tic.save_image("T1", "white_bg", "https://img/v2.jpg", version=2)
    assert store.images[("T1", "white_bg", 2)]["url"] == "https://img/v2.jpg"
    # 显式 version 不触发自动递增覆盖
    assert store.images[("T1", "white_bg", 1)]["url"] == "https://img/v1.jpg"


def test_get_latest_version_default(store):
    """get_image(version=None) → 最新版本 URL。"""
    tic.save_image("T1", "main", "https://img/1.jpg")
    tic.save_image("T1", "main", "https://img/2.jpg")
    assert tic.get_image("T1", "main") == "https://img/2.jpg"
    assert tic.get_image("T1", "main", version=1) == "https://img/1.jpg"
    assert tic.get_image("T1", "main", version=99) is None


def test_get_image_info(store):
    """get_image_info → 完整行（version/url/params/image_parent_task_id）。"""
    tic.save_image("T1", "main", "https://img/1.jpg", params={"draft": {"title": "x"}, "token": "t"})
    info = tic.get_image_info("T1", "main")
    assert info["version"] == 1
    assert info["url"] == "https://img/1.jpg"
    assert info["params"] == {"draft": {"title": "x"}, "token": "t"}
    assert tic.get_image_info("T1", "nonexist_slot") is None


def test_get_latest_version_helper(store):
    """get_latest_version → 0（无行）/ 最新版本号。"""
    assert tic.get_latest_version("T1", "main") == 0
    tic.save_image("T1", "main", "https://img/1.jpg")
    tic.save_image("T1", "main", "https://img/2.jpg")
    assert tic.get_latest_version("T1", "main") == 2


# ══════════════════════════════════════════════════════════════
# 2. params 快照（验收 d）
# ══════════════════════════════════════════════════════════════


def test_params_json_roundtrip(store):
    """save params 完整存节点 Input schema（JSON 可反序列化）。"""
    params = {"draft": {"title": "保温杯", "category": "家居"}, "token": "sk-x", "visual_vars": {"a": "b"}}
    tic.save_image("T2", "scene_1", "https://img/s1.jpg", params=params)
    info = tic.get_image_info("T2", "scene_1")
    assert info["params"] == params  # 完整原样，无字段丢失


# ══════════════════════════════════════════════════════════════
# 3. force_regen → version+1 新 URL（验收 a）
# ══════════════════════════════════════════════════════════════


def test_force_regen_new_version_new_url(store):
    """(a) force_regen 绕过缓存读 → 新行 version+1 新 URL，get 返回不同 URL。"""
    tic.save_image("T3", "white_bg", "https://img/v1.jpg")
    # force_regen → get 无视已有缓存（无静默缓存命中）
    assert tic.get_image("T3", "white_bg", force_regen=True) is None
    # regen 写 version=prev+1 = 2
    tic.save_image("T3", "white_bg", "https://img/v2.jpg", version=tic.get_latest_version("T3", "white_bg") + 1)
    assert tic.get_image("T3", "white_bg") == "https://img/v2.jpg"
    assert tic.get_image("T3", "white_bg") != "https://img/v1.jpg"
    assert store.images[("T3", "white_bg", 1)]["url"] == "https://img/v1.jpg"  # 旧版本保留


# ══════════════════════════════════════════════════════════════
# 4. resubmit parent 回溯（验收 b）
# ══════════════════════════════════════════════════════════════


def test_parent_backtrace_reuses_parent_image(store):
    """(b) task B payload.parent_task_id=A → B 的 get_image miss → 回溯 A 复用。"""
    tic.save_image("A", "main", "https://img/A_main.jpg", params={"draft": {"title": "x"}})
    store.parents["B"] = "A"
    assert tic.get_image("B", "main") == "https://img/A_main.jpg"
    # 回溯时复制一行到当前 task，带 image_parent_task_id=父id（图片级血缘，非任务级）
    assert store.images[("B", "main", 1)]["url"] == "https://img/A_main.jpg"
    assert store.images[("B", "main", 1)]["image_parent_task_id"] == "A"


def test_parent_backtrace_no_parent(store):
    """无 parent_task_id → miss 正常返回 None（不产生假命中）。"""
    assert tic.get_image("SOLO", "main") is None
    assert ("SOLO", "main", 1) not in store.images


# ══════════════════════════════════════════════════════════════
# 5. 节点级验收：正常 retry / force_regen / resubmit（验收 a/b/c）
# ══════════════════════════════════════════════════════════════

_CONFIG_BASE = {"metadata": {"execute_id": "test"}, "configurable": {"thread_id": "N1"}}
_RUNTIME = type("FakeRuntime", (), {"context": None})()


def _node_config(thread_id, force_regen=False, regen_version=None):
    conf = {"configurable": {"thread_id": thread_id}}
    if force_regen:
        conf["configurable"]["force_regen"] = True
    if regen_version is not None:
        conf["configurable"]["regen_version"] = regen_version
    return conf


def _fake_api(url="https://img/node_new.jpg"):
    return MagicMock(return_value=url)


def test_node_normal_retry_hits_cache(store):
    """(c) 正常 retry（同 task_id 重跑）→ 命中缓存，不调生图 API。"""
    from graphs.nodes.white_bg_gen_node import white_bg_gen_node
    import graphs.nodes.white_bg_gen_node as white_mod
    from graphs.state_image_gen import WhiteBgInput

    tic.save_image("N1", "white_bg", "https://img/cached_white.jpg")
    api = _fake_api()
    with patch.object(white_mod, "call_mxou_image_api", api):
        out = white_bg_gen_node(
            WhiteBgInput(draft={"title": "x"}, token="t", original_images=[]),
            _node_config("N1"),
            _RUNTIME,
        )
    assert out.white_bg_image == "https://img/cached_white.jpg"
    api.assert_not_called()


def test_node_force_regen_bypasses_cache_and_increments(store):
    """(a) 节点 config.force_regen → 绕过缓存读，调 API，save version=prev+1。"""
    from graphs.nodes.white_bg_gen_node import white_bg_gen_node
    import graphs.nodes.white_bg_gen_node as white_mod
    from graphs.state_image_gen import WhiteBgInput

    tic.save_image("N2", "white_bg", "https://img/v1.jpg")
    api = _fake_api("https://img/v2.jpg")
    with patch.object(white_mod, "call_mxou_image_api", api):
        out = white_bg_gen_node(
            WhiteBgInput(draft={"title": "x"}, token="t", original_images=[]),
            _node_config("N2", force_regen=True, regen_version=2),
            _RUNTIME,
        )
    api.assert_called_once()  # 无静默缓存命中
    assert out.white_bg_image == "https://img/v2.jpg"
    assert store.images[("N2", "white_bg", 2)]["url"] == "https://img/v2.jpg"
    assert store.images[("N2", "white_bg", 2)]["params"] is not None  # Input schema 快照
    assert tic.get_image("N2", "white_bg") == "https://img/v2.jpg"


def test_node_resubmit_backtrace_no_api_call(store):
    """(b) B(parent=A) → B 节点 get_image miss → 回溯 A 复用，call_mxou_image_api 不被调用。"""
    from graphs.nodes.white_bg_gen_node import white_bg_gen_node
    import graphs.nodes.white_bg_gen_node as white_mod
    from graphs.state_image_gen import WhiteBgInput

    tic.save_image("A", "white_bg", "https://img/A_white.jpg")
    store.parents["B"] = "A"
    api = _fake_api()
    with patch.object(white_mod, "call_mxou_image_api", api):
        out = white_bg_gen_node(
            WhiteBgInput(draft={"title": "x"}, token="t", original_images=[]),
            _node_config("B"),
            _RUNTIME,
        )
    api.assert_not_called()  # ⚠️ 关键断言：resubmit 不重烧额度
    assert out.white_bg_image == "https://img/A_white.jpg"
    assert store.images[("B", "white_bg", 1)]["image_parent_task_id"] == "A"


def test_node_params_snapshot_is_input_schema(store):
    """(d) 节点 save_image 的 params = 节点 Input model_dump 原样（JSON 断言）。"""
    from graphs.nodes.white_bg_gen_node import white_bg_gen_node
    import graphs.nodes.white_bg_gen_node as white_mod
    from graphs.state_image_gen import WhiteBgInput

    state = WhiteBgInput(draft={"title": "保温杯"}, token="t", original_images=["https://img/ref.jpg"])
    api = _fake_api()
    with patch.object(white_mod, "call_mxou_image_api", api):
        white_bg_gen_node(state, _node_config("N3"), _RUNTIME)
    info = tic.get_image_info("N3", "white_bg")
    assert info["params"]["draft"] == {"title": "保温杯"}
    assert info["params"]["token"] == "t"
    assert info["params"]["original_images"] == ["https://img/ref.jpg"]
    # params 覆盖节点 Input 全部字段（JSON 断言完整）
    assert set(info["params"].keys()) >= {"draft", "token", "original_images"}


def test_variant_loop_versioned_save(store):
    """variant_primary_loop：每个 variant 槽位独立版本化存取（force_regen → version++）。"""
    from graphs.nodes.variant_primary_loop_node import variant_primary_loop_node, VariantPrimaryLoopInput
    import graphs.nodes.variant_primary_loop_node as var_mod

    tic.save_image("NV", "variant_0", "https://img/v0_v1.jpg")
    api = _fake_api("https://img/v0_v2.jpg")
    state = VariantPrimaryLoopInput(
        variants=[{"name": "v0", "image": "https://img/v0.jpg"}],
        draft={"title": "x"}, token="t",
    )
    with patch.object(var_mod, "call_mxou_image_api", api):
        out = variant_primary_loop_node(state, _node_config("NV", force_regen=True, regen_version=2), _RUNTIME)
    api.assert_called_once()  # force_regen → 无静默缓存命中
    assert out.variant_primary_images == ["https://img/v0_v2.jpg"]
    assert store.images[("NV", "variant_0", 2)]["url"] == "https://img/v0_v2.jpg"
    assert store.images[("NV", "variant_0", 2)]["params"]["variants"] == [{"name": "v0", "image": "https://img/v0.jpg"}]
    assert tic.get_image("NV", "variant_0") == "https://img/v0_v2.jpg"
