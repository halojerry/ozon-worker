# wave P2/P3 修复实施计划 v1（审核取证留存 + 留存表真值透传 + R2b 仲裁池 + 类目文本链姊妹词治理）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 v0.67 wave 真实测试（docs/TEST-v067-wave-plan.md）在案的四个 P2/P3 缺陷：①审核拒绝原始原因丢失 ②listing_result_log 记信封猜测而非管线真值 ③R2b 仲裁池截断导致"正确答案在池内仍被阻断" ④1688 源路径姊妹词借假 sim=1.0 抢占类目。

**Architecture:** 四个缺陷相互独立但共享两条主线——「GraphOutput 透传真值」（问题①②走 state schema 追加，零节点改动）与「assemble 类目决策面收口」（问题③④改候选池构造与打分，皆有既定安全闸兜底）。实施顺序按风险升序：审计留存 → 透传 → 仲裁池 → 文本链，每 task 独立可测试、独立可发版。

**Tech Stack:** Python 3.12 / LangGraph（output_schema 按字段名过滤）/ SQLAlchemy + PG（listing_result_log 新列）/ pytest（mock 优先，真实回归走本地 Docker worker）。

## 全局约束

- 测试命令：`cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q`（当前基线 **1774 passed**，任何 task 落地后不得低于基线）。
- **禁止用云端生产环境（worker.mxou.cn）测试**；真实回归只打本地 Docker（`http://localhost:8080`）+ 测试店 5381204/5371047（`skill/data/config/stores.json`，**绝不用 4718259**）。
- TDD 纪律：每个行为改动先写失败测试（RED），再最小实现（GREEN）。LLM 相关逻辑单测一律 mock `call_mxou_chat_api`，不真调。
- 宁缺毋滥是既定产品原则：文本链收紧导致的"错类目上架→安全阻断"行为变化是**有意方向**，不视为回归；但阻断必须在审计面可见（task 1/3 的留存职责）。
- `category_match_log.match_layer` 列 varchar(10)：`"blocked"` 7 字符可用，禁用更长值。
- 节点要读的 state 字段**必须声明进该节点 Input model**（langgraph 按字段名过滤 channel，v0.66 实证教训）——每个 task 里涉及 retry 子图的字段透传都含这一步。
- 版本发版不走本计划：四个 task 可按 0.67.x/0.68.0 节奏分批发，每 task 自带 CHANGELOG 素材。

## 四缺陷根因速览（取证详情见各 Task 开头）

| # | 缺陷 | 根因（file:line） | 一句话 |
|---|---|---|---|
| ① | 审核拒绝无结构化原因 | validation_retry_loop.py:646 消费即删 + :2719-2720 error_message 被清 + :3085 `_build_notice(state.error_type,…)` 传错参（ERROR_NOTICE_MAP 18 条中文说明成死代码） | 俄语原文进过内存但无任何持久化字段 |
| ② | 留存表记信封猜测 | listing_result_log.py:100-113/:172 dc/tp 只读 envelope；GraphOutput（state.py:191-233）无 dc/tp/meta/weight 通道 | task_processor 层拿不到 GlobalState，只能靠 GraphOutput 扩展 |
| ③ | 正确候选在池内仍阻断 | assemble_ozon_product_node.py:1481 R2b 仲裁池 `candidates[:5]` 按 sim 截断，0.33 的正确答案进不了 LLM 清单 | overlap 校验函数本身会放行，是池子截断了它 |
| ④ | 姊妹词假 sim=1.0 抢类目 | ozon_category_query.py:596-622 sim=token 命中占比（单 token 查询含"头巾"即 1.0）+ assemble:1237-1295 低置信通道换池 + parent 豁免 overlap（:1324-1340） | 单 token 假满分与多 token 诚实分同门槛竞争 |

---

### Task 1: 审核拒绝原文留存（decline_errors 全链透传 + moderation_texts 列）

**根因**：`parse_error_node`（validation_retry_loop.py:620-656）消费 errors 前不留存，:646 把已消费错误（含 DESCRIPTION_DECLINE 的俄语 texts）从 `state.errors` 删除；`revalidate_node` :2719-2720 用空串覆写 `error_message`；终态 `_build_notice`（:181-190）在 :3085 被传 `state.error_type`（恒为 fixable/unfixable）而非 `error_code`，ERROR_NOTICE_MAP 的 18 条 code 级中文说明永不命中 → 落进兜底文案"Ozon 审核拒绝(fixable),重试后仍未通过"。第二轮被拒（recheck_status_node :2992-3001）更是只日志记 code、texts 直接丢。

**Files:**
- Modify: `worker/src/graphs/state.py`（GlobalState + ValidationRetryWrapperOutput + GraphOutput）
- Modify: `worker/src/graphs/validation_retry_loop.py`（parse_error_node / recheck_status_node / _build_notice / Output model / :3085 调用点）
- Modify: `worker/src/graphs/nodes/validation_retry_wrapper_node.py`（回传透传 :79-109 区域）
- Modify: `worker/src/storage/database/shared/model.py`（ListingResultLog 加列）
- Modify: `worker/scripts/init_data.py`（幂等 ALTER，先例 :103-105）
- Modify: `worker/src/utils/listing_result_log.py`（writer 写 moderation_texts）
- Test: `worker/tests/test_v0671_decline_errors.py`（新建）

**Interfaces:**
- Produces: `GlobalState.decline_errors: List[Dict]`（append-only，cap 50）；`_accumulate_decline_errors(state, errors) -> None`（模块级，可单测）；`_build_notice(error_code, error_message, upload_status, decline_errors=None) -> str`（扩参，向后兼容）；GraphOutput 新字段 `decline_errors`；ListingResultLog 新列 `moderation_texts JSONB`。

- [ ] **Step 1: 写失败测试**（新建 `worker/tests/test_v0671_decline_errors.py`）

```python
"""审核拒绝原文留存回归（wave ①号缺陷 TDD）。

根因：parse_error_node 消费即删 + revalidate 清 error_message + _build_notice 传错参。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_DECLINE = {"code": "DESCRIPTION_DECLINE", "level": "ERROR_LEVEL_ERROR",
            "texts": {"message": "Категория не соответствует товару"}}


def test_accumulate_dedup_and_cap():
    from graphs.validation_retry_loop import _accumulate_decline_errors
    state = SimpleNamespace(decline_errors=[])
    _accumulate_decline_errors(state, [_DECLINE, _DECLINE])   # 同轮去重
    assert len(state.decline_errors) == 1
    _accumulate_decline_errors(state, [{"code": "MISSING_REQUIRED_ATTRIBUTE",
                                        "texts": {"message": "必填属性缺失: 类型"}}])
    assert len(state.decline_errors) == 2                      # 跨轮累积
    for i in range(60):                                        # cap 50
        _accumulate_decline_errors(state, [{"code": "X", "texts": {"message": f"m{i}"}}])
    assert len(state.decline_errors) == 50


def test_build_notice_error_code_hits_map():
    """传 error_code（而非 error_type）时 ERROR_NOTICE_MAP 必须生效（此前是死代码）。"""
    from graphs.validation_retry_loop import ERROR_NOTICE_MAP, _build_notice
    assert ERROR_NOTICE_MAP, "映射表不应为空"
    code = next(iter(ERROR_NOTICE_MAP))
    assert _build_notice(code, "", "failed") == ERROR_NOTICE_MAP[code]


def test_build_notice_fallback_uses_decline_texts():
    """error_message 被清空后，兜底文案必须携带 decline_errors 里的俄语原文。"""
    from graphs.validation_retry_loop import _build_notice
    got = _build_notice("", "", "failed",
                        decline_errors=[_DECLINE])
    assert "Категория не соответствует товару" in got


def test_build_notice_backward_compat():
    """旧签名行为不变：error_type 不在映射 → 有 msg 用 msg，无 msg 用泛化兜底。"""
    from graphs.validation_retry_loop import _build_notice
    assert _build_notice("fixable", "somе msg", "failed") == "Ozon 审核拒绝(fixable): somе msg"
    assert _build_notice("fixable", "", "failed") == "Ozon 审核拒绝(fixable),重试后仍未通过"
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_v0671_decline_errors.py -q`
Expected: FAIL — `cannot import name '_accumulate_decline_errors'`（功能缺失，非语法错）。

- [ ] **Step 3: 最小实现**

3a. `validation_retry_loop.py` 加模块级函数（放 `_build_notice` 上方）：

```python
def _accumulate_decline_errors(state, errors) -> None:
    """v0.67.1 wave①: 每轮审核/校验错误消费前原样累积（append-only，cap 50）。

    parse_error_node 会把已消费错误从 state.errors 删除、revalidate 会清
    error_message——没有本累积器，DESCRIPTION_DECLINE 的俄语原文就物理消失。
    """
    seen = {(d.get("code"), (d.get("texts") or {}).get("message"))
            for d in (getattr(state, "decline_errors", None) or []) if isinstance(d, dict)}
    add = [e for e in (errors or [])
           if isinstance(e, dict)
           and (e.get("code"), (e.get("texts") or {}).get("message")) not in seen]
    if add:
        state.decline_errors = (list(getattr(state, "decline_errors", None) or []) + add)[-50:]
```

3b. `_build_notice`（:181-190）扩参：

```python
def _build_notice(error_code: str, error_message: str, upload_status: str,
                  decline_errors: list | None = None) -> str:
    if error_code in ERROR_NOTICE_MAP:
        return ERROR_NOTICE_MAP[error_code]
    msg = (error_message or "").strip()
    if not msg:
        for _e in (decline_errors or []):
            msg = str(((_e or {}).get("texts") or {}).get("message") or "").strip()
            if msg:
                break
    if msg:
        return f"Ozon 审核拒绝({error_code or '未知'}): {msg[:200]}"
    return f"Ozon 审核拒绝({error_code or '未知错误'}),重试后仍未通过"
```

3c. `parse_error_node`（:630-646）：`state.errors = [...]` 删除行**之前**插入
`_accumulate_decline_errors(state, errors)`；`recheck_status_node` declined 分支（:2992-3001）`state.errors = list(mod_errors)` 旁插入 `_accumulate_decline_errors(state, mod_errors)`。

3d. 终态调用点（:3085）改为：

```python
notice=_build_notice(
    (getattr(state, "error_code", "") or "") or state.error_type,
    final_error_message,
    state.upload_status,
    decline_errors=list(getattr(state, "decline_errors", None) or []),
),
```

3e. schema 透传四处：GlobalState（state.py :120 errors 字段旁）加
`decline_errors: List[Dict[str, Any]] = Field(default_factory=list, description="每轮审核/校验拒绝原文累积（append-only，含俄语 texts）")`；`ValidationRetryLoopOutput`（validation_retry_loop.py:131）与 final_result（:3093-3094 旁）加同名；`ValidationRetryWrapperOutput`（state.py:811-817）加同名；wrapper 回传（validation_retry_wrapper_node.py :79-109 区域）加 `decline_errors=...`；GraphOutput（state.py:232-233 errors 旁）加同名。
⚠️ 同时 grep 两个节点的 Input model 并补声明（input schema 纪律）：
`grep -n "def parse_error_node" -B5 src/graphs/validation_retry_loop.py`、`grep -n "def recheck_status_node" -B5 src/graphs/validation_retry_loop.py`——凡 reads `state.decline_errors` 的节点，其 Input model 必须含该字段（默认 `Field(default_factory=list)`）。

- [ ] **Step 4: 跑测试确认 GREEN**（同 Step 2 命令，4 用例全过）

- [ ] **Step 5: 留存表新列 + writer**

5a. `model.py` ListingResultLog（:1130 errors 列后）：
`moderation_texts = Column("moderation_texts", JSONB, nullable=True, comment="每轮审核拒绝原文累积 [{code,level,attribute_id,texts}]")`
5b. `init_data.py` 按先例（:103-105 category_match_log.source_url 同款）加幂等 ALTER：
`ALTER TABLE listing_result_log ADD COLUMN IF NOT EXISTS moderation_texts JSONB`
5c. writer（listing_result_log.py :266 `"errors": errors_raw` 旁）：
`"moderation_texts": (gr.get("decline_errors") or None),`

- [ ] **Step 6: writer 单测**（追加进同测试文件）

```python
def test_writer_records_moderation_texts(monkeypatch):
    """graph_result.decline_errors 必须落 moderation_texts 列（假 session 捕获 insert）。"""
    import utils.listing_result_log as lrl

    captured = {}

    class _FakeResult:
        def scalar(self): return 1
        def fetchone(self): return None

    class _FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, stmt):
            captured["params"] = stmt.compile().params
            return _FakeResult()

    monkeypatch.setattr("storage.database.db.get_session", lambda: _FakeSession())
    lrl.write_listing_result_log(
        payload={"envelope": {"draft": {"purchase_url": "https://detail.1688.com/offer/1.html",
                                         "title": "t", "weight": 80, "dimensions": {"length": 1, "width": 2, "height": 3}},
                              "source": {}, "extensions": {}}},
        graph_result={"upload_status": "failed", "errors": [{"code": "KEEP_OLD_SEMANTICS"}],
                      "decline_errors": [_DECLINE], "pricing_info": {}},
        task_db_id="11111111-1111-1111-1111-111111111111",
        tenant_id="u1",
    )
    assert captured["params"]["moderation_texts"] == [_DECLINE]
```

若 `stmt.compile().params` 对 JSONB 参数的键名与列名不一致（sqlalchemy 方言差异），改为断言 `captured["params"]` 中存在值 `[_DECLINE]` 的任一键。跑：`pytest tests/test_v0671_decline_errors.py -q` → 5 passed。

- [ ] **Step 7: 全量回归 + 真实单验证**

`pytest tests/ -q` ≥1774+5 全绿。然后本地 Docker 重建（`cd deploy && docker compose up -d --build worker` + `docker compose exec worker python scripts/init_data.py`），重提 wave A3 渔夫帽链接（`https://detail.1688.com/offer/883453054866.html`，`WORKER_URL=http://localhost:8080 OZON_CLIENT_ID=5381204 OZON_API_KEY=<stores.json 取> .venv314/bin/python scripts/cli.py graph --url ... --store 测试店铺5381204`）。它大概率仍审核 declined——终态后查：
`SELECT left(task_db_id::text,8), moderation_texts FROM listing_result_log ORDER BY created_at DESC LIMIT 1;`
Expected: moderation_texts 含 Ozon 俄语原文数组（非空、含 texts.message）；notice/error_message 不再是泛化兜底。

- [ ] **Step 8: Commit**

```bash
git add worker/src/graphs/state.py worker/src/graphs/validation_retry_loop.py \
  worker/src/graphs/nodes/validation_retry_wrapper_node.py \
  worker/src/storage/database/shared/model.py worker/scripts/init_data.py \
  worker/src/utils/listing_result_log.py worker/tests/test_v0671_decline_errors.py
git commit -m "feat(worker): 审核拒绝原文留存 decline_errors 全链透传 + listing_result_log.moderation_texts 列"
```

---

### Task 2: listing_result_log 记管线真值（GraphOutput 透传 dc/tp/meta/weight）

**根因**：GraphOutput（state.py:191-233）没有 description_category_id/type_id/category_match_meta/weight 通道，`output_schema` 按名过滤后 task_processor 的 `graph_result` 恒缺 → writer（listing_result_log.py:172, :250-251, :258-259）只能回落信封 draft，graph 单记的是 skill 猜测（wave 实证 approved 行记着 200001462 成人糖果）。图终态时 GlobalState.description_category_id/type_id 已是 N4/R4 同步后的最终值（validation_retry_loop.py:928-933 → wrapper :79-109 回传），weight 的唯一裁决点是 prepare 的 `_resolve_weight_dimensions`（prepare_ozon_upload_node.py:1671-1704，返回 `(weight_g, depth_mm, width_mm, height_mm)`，日志"最终尺寸…重量=…g"），但它不写 state。

**Files:**
- Modify: `worker/src/graphs/state.py`（GraphOutput 3 字段 + PrepareOzonUploadOutput 2 字段 + GlobalState 2 通道）
- Modify: `worker/src/graphs/nodes/prepare_ozon_upload_node.py`（return 补 2 字段）
- Modify: `worker/src/utils/listing_result_log.py`（writer 取值优先级）
- Test: `worker/tests/test_v0671_listing_truth.py`（新建）

**Interfaces:**
- Produces: GraphOutput 新增 `description_category_id: str` / `type_id: str` / `category_match_meta: dict` / `final_weight_g: int` / `final_dims_mm: dict`；GlobalState/PrepareOzonUploadOutput 新增 `final_weight_g`/`final_dims_mm`；writer 取值顺序 graph_result 优先、信封回落。

- [ ] **Step 1: 写失败测试**（`worker/tests/test_v0671_listing_truth.py`）

```python
"""留存表真值透传回归（wave ②号缺陷 TDD）：graph 管线 dc/tp/weight 不得回落信封猜测。"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

_PAYLOAD = {"envelope": {"draft": {"purchase_url": "https://detail.1688.com/offer/1.html",
                                   "title": "帽子", "weight": 1,
                                   "dimensions": {"length": 1, "width": 2, "height": 3},
                                   "ozon_category": {"description_category_id": "200001462",
                                                      "type_id": "971363842", "source": "search_kw"}},
                         "source": {}, "extensions": {}}}


def test_writer_prefers_graph_result_over_envelope(monkeypatch):
    import utils.listing_result_log as lrl

    captured = {}

    class _FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, stmt):
            captured["p"] = stmt.compile().params
            return type("R", (), {"scalar": lambda s: 1, "fetchone": lambda s: None})()

    monkeypatch.setattr("storage.database.db.get_session", lambda: _FakeSession())
    lrl.write_listing_result_log(
        payload=_PAYLOAD,
        graph_result={"upload_status": "completed", "moderation_status": "approved",
                      "description_category_id": "41777465", "type_id": "93167",
                      "category_match_meta": {"match_layer": "L0", "confidence": 0.7},
                      "final_weight_g": 80, "final_dims_mm": {"length": 250, "width": 120, "height": 100},
                      "pricing_info": {}, "errors": []},
        task_db_id="22222222-2222-2222-2222-222222222222", tenant_id="u1",
    )
    p = captured["p"]
    assert p["description_category_id"] == 41777465 and p["type_id"] == 93167
    assert p["match_layer"] == "L0" and p["match_confidence"] == 0.7
    assert p["weight_g"] == 80 and p["dims_mm"] == {"length": 250, "width": 120, "height": 100}


def test_writer_falls_back_to_envelope_when_graph_empty(monkeypatch):
    """graph 早退（auth/类目阻断）dc/tp 为空 → 信封回落（follow/discover 语义保持）。"""
    import utils.listing_result_log as lrl

    captured = {}

    class _FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, stmt):
            captured["p"] = stmt.compile().params
            return type("R", (), {"scalar": lambda s: 1, "fetchone": lambda s: None})()

    monkeypatch.setattr("storage.database.db.get_session", lambda: _FakeSession())
    lrl.write_listing_result_log(
        payload=_PAYLOAD,
        graph_result={"upload_status": "failed", "pricing_info": {}, "errors": []},
        task_db_id="33333333-3333-3333-3333-333333333333", tenant_id="u1",
    )
    assert captured["p"]["description_category_id"] == 200001462  # 信封回落，现状语义
    assert captured["p"]["weight_g"] == 1                          # 无 final_* 时回落 draft


def test_graphoutput_has_truth_fields():
    """GraphOutput 契约含真值字段（output_schema 按名过滤，缺了就透传不出去）。"""
    from graphs.state import GraphOutput
    for f in ("description_category_id", "type_id", "category_match_meta",
              "final_weight_g", "final_dims_mm"):
        assert f in GraphOutput.model_fields, f"GraphOutput 缺 {f}"
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_v0671_listing_truth.py -q`
Expected: FAIL — writer 断言不等（现值来自信封）或 `GraphOutput 缺 description_category_id`。

- [ ] **Step 3: 最小实现**

3a. `state.py` GraphOutput（:232-233 errors/notice 旁，沿用 v0.27/v0.67 透传先例注释风格）：

```python
    # ✅ v0.67.1 wave②: 管线真值透出 — GlobalState 同名通道图终态已是 N4/R4 同步后
    # 终值，output_schema 按名透传，listing_result_log 据此记实际采用的类目/权重。
    description_category_id: str = Field(default="", description="管线最终采用的 Ozon 类目 dc")
    type_id: str = Field(default="", description="管线最终采用的 type")
    category_match_meta: Dict[str, Any] = Field(default_factory=dict, description="match_layer/confidence/dc/tp")
    final_weight_g: int = Field(default=0, description="prepare 归一后实际上传重量(g)")
    final_dims_mm: Dict[str, int] = Field(default_factory=dict, description="prepare 归一后实际上传尺寸(mm)")
```

3b. `PrepareOzonUploadOutput`（state.py:510-527）与 GlobalState 各加 `final_weight_g: int = Field(default=0, …)` / `final_dims_mm: Dict[str, int] = Field(default_factory=dict, …)`；prepare 节点主 return（weight/dims 已算出处，变量名为 `weight_g, depth_mm, width_mm, height_mm = _resolve_weight_dimensions(...)` 的调用点附近）补：

```python
        final_weight_g=int(weight_g or 0),
        final_dims_mm={"length": int(depth_mm or 0), "width": int(width_mm or 0), "height": int(height_mm or 0)},
```

（prepare 可能有多个 return 出口：`grep -n "return PrepareOzonUploadOutput\|return {" prepare_ozon_upload_node.py` 逐个确认，失败出口带默认值 0 即可。）

3c. writer（listing_result_log.py :172 与 :250-259 区域）改取值优先级：

```python
        dc, tp = _ozon_category_ids(payload)
        # ✅ v0.67.1 wave②: graph 管线真值优先（N4/R4 同步后终值），空值回落信封
        # （follow/discover 信封仍是权威——跟卖 UPDATE 由 Ozon 保留原卡类目）。
        _g_dc = str(gr.get("description_category_id") or "").strip()
        _g_tp = str(gr.get("type_id") or "").strip()
        if _g_dc.isdigit() and _g_tp.isdigit():
            dc, tp = int(_g_dc), int(_g_tp)
        _meta = gr.get("category_match_meta") if isinstance(gr.get("category_match_meta"), dict) else {}
        ...
        # match_layer/match_confidence（原 :268-270 硬编码 None 处）：
        "match_layer": str(_meta.get("match_layer") or "")[:10] or None,
        "match_confidence": float(_meta["confidence"]) if isinstance(_meta.get("confidence"), (int, float)) else None,
        # weight/dims（原 :258-259）：
        "weight_g": _i(gr.get("final_weight_g")) or _i(draft.get("weight")),
        "dims_mm": (gr.get("final_dims_mm") or None) or (draft.get("dimensions") or None),
```

（注意 `_i` 对 0 返回默认值——`final_weight_g=0` 表示早退无值，回落 draft 是正确语义；核对 `_i` 实现后如不等价则写显式 `if gr.get("final_weight_g"):` 判断。）

- [ ] **Step 4: 跑测试确认 GREEN** → **Step 5: 全量回归**（≥1782 passed）

- [ ] **Step 6: 真实验证**：本地重提 A2 链接（`https://detail.1688.com/offer/1065567637763.html`，测试店 5371047）跑完 approve 后查：
`SELECT final_status, description_category_id, type_id, match_layer, match_confidence, weight_g FROM listing_result_log ORDER BY created_at DESC LIMIT 1;`
Expected: dc=41777465/tp=93167（真值，非信封的 200001462）、match_layer=L0、weight_g=80。

- [ ] **Step 7: Commit**

```bash
git add worker/src/graphs/state.py worker/src/graphs/nodes/prepare_ozon_upload_node.py \
  worker/src/utils/listing_result_log.py worker/tests/test_v0671_listing_truth.py
git commit -m "feat(worker): listing_result_log 记管线真值——GraphOutput 透传 dc/tp/category_match_meta/final_weight"
```

---

### Task 3: R2b 仲裁池扩容 + 阻断审计行（assemble）

**根因**（wave A4 实证）：R2b 确认闸（assemble_ozon_product_node.py:1480-1482）给 LLM 的池是 `candidates[:5]` 按 sim 截断——正确答案 园艺地垫，护膝（sim=0.33，kw 搜索 Top-1）进不了清单，LLM 只能 abstain；而 overlap 校验（:1483-1491，`_non_generic_overlap_words`）若能见到该候选**会放行**（花园/园艺/护膝全是非泛词命中）。`_llm_rank_categories` 解析失败/abstain 分支（:3471-3486）静默 return None，116 字符响应无日志可查。`_log_match_attempt` 全仓唯一写入点在 :1535（采纳成功后），R2b 阻断（:1508-1516 return）永远到不了 → 阻断任务 category_match_log 零行。

**Files:**
- Modify: `worker/src/graphs/nodes/assemble_ozon_product_node.py`（R2b 池构造 :1480-1482、_llm_rank_categories 日志与 context 参数、阻断路径审计 :1427-1436/:1453-1456/:1508-1516/:1522-1531、_log_match_attempt 支持无 category_result）
- Test: `worker/tests/test_v0671_r2b_pool.py`（新建）

**Interfaces:**
- Produces: `_build_r2b_confirm_pool(candidates, adopted, source_words) -> list[dict]`（纯函数，可单测：top10 + 跨大类 overlap 候选必进，cap 12，去重保序）；`_llm_rank_categories(..., context: str = "") -> dict | None`（其余 4 个调用点不受影响）；`_log_match_attempt(..., blocked_reason: str = "")`（空 category_result 时不炸）。

- [ ] **Step 1: 写失败测试**（`worker/tests/test_v0671_r2b_pool.py`）

```python
"""R2b 仲裁池扩容回归（wave ③号缺陷 TDD）：正确答案 sim 低也必须进 LLM 清单。"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# wave A4 池的简化重构：3 个 sim=1.0 弱相关 + 1 个 sim=0.33 正确答案（跨大类）
_ADOPTED = {"description_category_id": 92496473, "type_id": 93012, "similarity": 1.0,
            "node_name": "儿童爬行护膝", "full_path": "儿童用品 > 儿童安全 > 儿童爬行护膝"}
_RIVAL = {"description_category_id": 17028711, "type_id": 94159, "similarity": 1.0,
          "node_name": "运动护齿", "full_path": "运动与休闲 > 装备与护具 > 运动护齿"}
_LOW = [{"description_category_id": 1000 + i, "type_id": 2000 + i, "similarity": 0.95,
         "node_name": f"护具{i}", "full_path": f"运动与休闲 > 装备与护具 > 护具{i}"} for i in range(6)]
_CORRECT = {"description_category_id": 17028746, "type_id": 92750, "similarity": 0.33,
            "node_name": "园艺地垫，护膝", "full_path": "住宅和花园 > 园艺工具 > 园艺地垫，护膝"}
_SOURCE_WORDS = "潜水料 花园 护膝 除草 园艺 神器 劳保 家务 弹力 防护 膝盖 跪垫"


def test_pool_includes_low_sim_cross_domain_overlap():
    from graphs.nodes.assemble_ozon_product_node import _build_r2b_confirm_pool
    pool = [dict(_ADOPTED), dict(_RIVAL), *_LOW, dict(_CORRECT)]
    out = _build_r2b_confirm_pool(pool, _ADOPTED, _SOURCE_WORDS)
    ids = {(c["description_category_id"], c["type_id"]) for c in out}
    assert (17028746, 92750) in ids, "正确答案（跨大类+源词overlap）必须进 R2b 仲裁池"
    assert len(out) <= 12


def test_pool_top10_plus_overlap_no_dup():
    from graphs.nodes.assemble_ozon_product_node import _build_r2b_confirm_pool
    pool = [dict(_ADOPTED), dict(_RIVAL), *_LOW, dict(_CORRECT)]
    out = _build_r2b_confirm_pool(pool, _ADOPTED, _SOURCE_WORDS)
    assert len(out) == len({(c["description_category_id"], c["type_id"]) for c in out})
    # top1 仍在池首（LLM 编号顺序稳定）
    assert out[0]["description_category_id"] == 92496473


def test_block_path_writes_match_log(monkeypatch):
    """R2b 阻断 return 前必须写 category_match_log 审计行（match_layer='blocked'）。"""
    from graphs.nodes import assemble_ozon_product_node as asm
    calls = {}
    monkeypatch.setattr(asm, "_log_match_attempt",
                        lambda *a, **kw: calls.setdefault("args", (a, kw)))
    # 直接驱动阻断分支太重——这里只锁"阻断 return 前调用点存在且参数含 blocked 语义"：
    # 以源码静态断言替代重型集成（更脆的 run-node 断言放 Task 5 真实回归）。
    import inspect
    src = inspect.getsource(asm)
    block_region = src[src.index("R2b (同分跨大类歧义)"):]
    assert "match_layer=\"blocked\"" in block_region or "match_layer='blocked'" in block_region
```

- [ ] **Step 2: 跑测试确认 RED**（`pytest tests/test_v0671_r2b_pool.py -q`）
Expected: FAIL — `cannot import name '_build_r2b_confirm_pool'`。

- [ ] **Step 3: 最小实现**

3a. 纯函数（放 `_find_close_top_category_rival` 后）：

```python
def _build_r2b_confirm_pool(candidates: list[dict], adopted: dict | None,
                            source_words: str) -> list[dict]:
    """v0.67.1 wave③: R2b 仲裁池 = sim top10 + 跨大类且与源词有非泛词 overlap 的
    高潜候选（各域前 3，cap 12）。此前固定 [:5]，sim 0.33 的正确答案进不了 LLM
    清单（A4 园艺地垫实证）。去重保序：top 段保持原相对顺序。"""
    pool: list[dict] = []
    seen: set[tuple[int, int]] = set()

    def _push(c: dict) -> None:
        key = (int(c.get("description_category_id") or 0), int(c.get("type_id") or 0))
        if key in seen:
            return
        seen.add(key)
        pool.append(c)

    for c in (candidates or [])[:10]:
        _push(c)
    _adopted_dc = int((adopted or {}).get("description_category_id") or 0)
    by_dc: dict[int, int] = {}
    for c in (candidates or [])[10:]:
        dc = int(c.get("description_category_id") or 0)
        if dc == _adopted_dc:
            continue
        if not _non_generic_overlap_words(str(c.get("full_path") or ""), [source_words]):
            continue
        if by_dc.get(dc, 0) >= 3:
            continue
        by_dc[dc] = by_dc.get(dc, 0) + 1
        _push(c)
    return pool[:12]
```

3b. R2b 调用点（:1480-1482）：

```python
                _confirm = _llm_rank_categories(
                    _build_r2b_confirm_pool(candidates, category_result,
                                            source_keywords or keywords),
                    source_keywords or keywords, draft, state,
                    context=f"当前拟采纳: {category_result.get('full_path') if category_result else '?'}"
                            f"；1688源类目: {source_category or '未知'}",
                )
```

3c. `_llm_rank_categories`（:3399-3491）签名加 `context: str = ""`，prompt 候选清单后拼 `f"\n消歧上下文：{context}" if context else ""`；解析失败/越界/abstain 分支（:3474-3486）各加
`logger.warning(f"🔍 R2b LLM 响应不可用: raw={str(result)[:200]}")`（`result` 为空时打 `raw=<empty>`）。

3d. 阻断审计：`_log_match_attempt` 开头对 `category_result` 判空容错（现实现假定非空则 `category_result.get(...)` 会炸——改为 `category_result = category_result or {}`）；四个阻断 return 前（R1 veto :1453-1456、LLM fallback 失败 :1427-1436、R2b 阻断 :1508-1516、最终 sim 门槛 :1522-1531）插入：

```python
        _log_match_attempt(state, title, source_category, keywords,
                           {"description_category_id": (category_result or {}).get("description_category_id"),
                            "type_id": (category_result or {}).get("type_id"),
                            "full_path": (category_result or {}).get("full_path", "")},
                           "blocked", 0.0, candidates, config=config)
```

（match_layer="blocked"，confidence=0.0；既有下游查询若按 confidence>0 过滤不受影响——阻断行本就不应进学习/统计。）

- [ ] **Step 4: 跑测试确认 GREEN** → **Step 5: 全量回归**（≥1786 passed）

- [ ] **Step 6: 真实验证**：重提 A4 链接（`https://detail.1688.com/offer/1057307655307.html`，测试店 5381204）。两种可接受终态：①LLM 从扩容池选出 园艺地垫，护膝 → 采纳继续走完（最理想）；②仍阻断但 `category_match_log` 出现 `match_layer='blocked'` 行且 worker 日志有 `R2b LLM 响应不可用: raw=…`。**不可接受**：正确答案进池后 LLM 选它却被 overlap 误杀（日志可判）。

- [ ] **Step 7: Commit**

```bash
git add worker/src/graphs/nodes/assemble_ozon_product_node.py worker/tests/test_v0671_r2b_pool.py
git commit -m "feat(worker): R2b 仲裁池扩容(top10+跨大类overlap必进) + 阻断审计行 + LLM 原始响应日志"
```

---

### Task 4: 类目文本链姊妹词治理（单 token 假满分 + 低置信换池通道收口）

**根因**（wave A2/A3/A7/A8 实证，四帽全中）：①`_search_jieba_like` 的 sim = 命中 token 数/总 token 数（ozon_category_query.py:596-622）——单 token 查询「头巾」子串命中「三角头巾」即得 1/1=1.0，与多 token 诚实查询的 0.29 同门槛比较；②assemble 低置信通道（:1237-1295）在**池内有候选**时也用 parent 词换池（`candidates = [_chosen]`），0.29 的诚实候选被整池丢弃；③「帽子/头巾」被 `/` 拆词后姊妹词「头巾」进 parent_terms 首位（:994 `reversed`），parent 命中豁免 overlap 验证（:1324-1340）被假 1.0 白嫖；④leaf「成人帽」被 jieba 切剩修饰词「成人」，R2 剥离后 leaf 信号≈0，category_synonyms.json 无帽类泛化词条（:105-117 只有 遮阳帽/草帽/防晒帽）。

**Files:**
- Modify: `worker/src/utils/ozon_category_query.py`（单 token 查询分档打分，_search_jieba_like 计分段）
- Modify: `worker/src/graphs/nodes/assemble_ozon_product_node.py`（删低置信换池通道 :1237-1295；parent 通道 confidence 封顶；:1307 日志标签）
- Modify: `worker/config/category_synonyms.json`（补帽类泛化词条）
- Test: `worker/tests/test_v0671_sibling_token.py`（新建）

**Interfaces:**
- Produces: `_score_token_hit(node_name: str, token: str) -> float`（纯函数：相等 1.0 / 前缀 0.7 / 包含 0.6，复用 score_residual_rows 分档语义，仅单 token 查询生效）；低置信路径行为从"换池重搜"变为"既有 LLM fallback 链"。

- [ ] **Step 1: 写失败测试**（`worker/tests/test_v0671_sibling_token.py`）

```python
"""姊妹词假满分治理回归（wave ④号缺陷 TDD）。"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("LOG_LEVEL", "WARNING")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_single_token_contains_hit_capped():
    """单 token 查询包含命中（头巾→三角头巾）不得得满分 1.0——同前缀分档 0.6。"""
    from utils.ozon_category_query import _score_token_hit
    assert _score_token_hit("三角头巾", "头巾") == 0.6
    assert _score_token_hit("头巾", "头巾") == 1.0      # 精确相等仍满分
    assert _score_token_hit("头巾佩饰", "头巾") == 0.7   # 前缀 0.7


def test_search_single_token_tiered_similarity():
    """端到端：单 token jieba 搜索的 contains 命中 similarity<1.0（需真实 PG 树）。"""
    from utils.ozon_category_query import get_category_query
    q = get_category_query()
    rows = q.search_nodes("头巾", top_k=5, node_type="type")
    assert rows, "树中应有头巾类节点"
    top = rows[0]
    if top["node_name"] != "头巾":
        assert float(top["similarity"]) < 1.0, f"包含命中不得满分: {top}"


def test_low_conf_no_parent_repool():
    """低置信时不得再用 parent 词换池（A2/A3/A7/A8 错配路径）——源码静态断言。"""
    import inspect
    from graphs.nodes import assemble_ozon_product_node as asm
    src = inspect.getsource(asm)
    assert "上级类目词重搜+LLM选子类" not in src, "低置信换池通道必须移除"
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_v0671_sibling_token.py -q`
Expected: FAIL — `_score_token_hit` 不存在；`search_single_token_tiered` 满分断言炸；换池通道仍在。

- [ ] **Step 3: 最小实现**

3a. `ozon_category_query.py`：`score_residual_rows`（:282-319）上方加纯函数：

```python
def _score_token_hit(node_name: str, token: str) -> float:
    """v0.67.1 wave④: 单 token 查询的分档命中分（与 score_residual_rows 同档）。

    单 token 子串命中给满分 1.0 会让姊妹词（帽子/头巾 的「头巾」→三角头巾）
    碾压多 token 诚实查询（遮阳帽 0.29），A2/A3/A7/A8 全中。
    """
    name, tk = str(node_name or ""), str(token or "")
    if not name or not tk:
        return 0.0
    if name == tk:
        return 1.0
    if name.startswith(tk):
        return 0.7
    if tk in name:
        return 0.6
    return 0.0
```

`_search_jieba_like` 计分段（:596-622）：token 命中计分处，当 `len(tokens) == 1` 时该 token 的贡献改用 `_score_token_hit(node_name, token)`（乘 1.0，非泛词不加成），多 token 查询保持原 `1.0/0.3` 计分不变。注意命中判定（token in node_name）不变，只改分值——搜得到、分得诚实。

3b. `assemble`：删除 :1237-1295 低置信换池通道整段（`if parent_terms:` 的重搜 + `_llm_rank_categories(_filtered[:8]…)` 子类选择 + `candidates = [_chosen]`），低置信场景自然落入既有 LLM fallback 链（:1310 起）与 Step 6.5 一致性重配。保留 :1085-1092 的 0 候选通道（工业品 绝缘子→电工电气 的原设计场景）；该通道采纳处（parent_fallback_used 分支 :1324-1340）给 `match_confidence` 封顶：

```python
            match_confidence = min(match_confidence, 0.5)  # parent 通道无 overlap 豁免佐证，压低置信走 Step 6.5 复核
```

3c. `assemble` :1307 日志标签改真实 matcher：`f"✅ 类目匹配 ({best.get('matcher', 'pg_trgm')}): …"`（原硬编码 "pg_trgm" 误导取证）。

3d. `config/category_synonyms.json` 补词条（key=1688 leaf，value=Ozon ZH 搜索词）：

```json
  "成人帽": "帽子 太阳帽 遮阳帽",
  "儿童帽": "帽子 太阳帽 遮阳帽",
  "女帽": "帽子 遮阳帽",
  "男帽": "帽子 鸭舌帽"
```

（外置表触发条件是 key 精确等于 leaf（assemble :1040），成人帽/儿童帽正是 wave 素材 leaf，能直接恢复品类词信号。）

- [ ] **Step 4: 跑测试确认 GREEN** → **Step 5: 全量回归**

⚠️ 本 task 触面最大：全量之外必须重点跑 `pytest tests/test_category_multiword_protection_v0651.py tests/test_category_match_v021.py tests/test_v067_wave_fixes.py tests/test_skill_category_direct.py -q`。已知行为变化（非回归，CHANGELOG 记录）：低置信+parent 词可救的场景改为走 LLM fallback/阻断；单 token contains 候选从直接采纳变为 0.6 分参与竞争。若 v0651 既有用例锁定了旧分值/旧通道，**修测试预期对齐新行为**（在 commit message 注明）。

- [ ] **Step 6: 真实回归（wave 三帽 + A4）**：重建本地 Docker 后依次重提 A2 `1065567637763` / A3 `883453054866` / A7 `785771958953` / A4 `1057307655307`（测试店轮换 5381204/5371047）。验收：≥2 帽终态类目落在 41777465 域且 type ∈ {遮阳帽 93167, 帽子 93181, 草帽系}（不再是三角头巾 93131/93258）；A4 同 Task 3 验收。若 LLM fallback 把某帽救到别域但非 18+，记录 P3 不阻塞。

- [ ] **Step 7: Commit**

```bash
git add worker/src/utils/ozon_category_query.py worker/src/graphs/nodes/assemble_ozon_product_node.py \
  worker/config/category_synonyms.json worker/tests/test_v0671_sibling_token.py
git commit -m "feat(worker): 类目姊妹词治理——单token分档打分+低置信换池通道移除+帽类同义词+matcher日志标签"
```

---

### Task 5: wave 终验 + CHANGELOG/AGENTS

- [ ] **Step 1**: 全量测试一次（`pytest tests/ -q`，预期 ≥1790 passed）；skill 侧零改动不需跑。
- [ ] **Step 2**: `docs/TEST-v067-wave-plan.md` 末尾追加「P2/P3 修复回归记录」小节：Task 1-4 的真实单终态表（对照 wave 原始结果）。
- [ ] **Step 3**: CHANGELOG 新版本块 + AGENTS.md 顶部块（沿用 v0.67.0 格式：新增 4 条修复 + 行为变化说明 + 测试基线）。
- [ ] **Step 4**: 版本四源 bump（建议 0.68.0——含行为变更；若拆批发则 0.67.1/0.67.2 递进）→ 本地全绿 → tag → push → 确认 CI/Build-Skill/CD 三 workflow success → 服务器 `bash deploy/cos-update.sh`（用户手动）。

## 已知边界（本计划不解决，记录在案）

- `assembly_retry_count`（state.py:28）无消费者——类目阻断后自动重试属产品决策，待拍板。
- TimeoutError/Exception 终态路径（task_processor.py:658-689）不写留存行——盲区保留。
- R2b overlap 循环"首个命中"改"最优命中"（assemble:1344-1364）语义变化大，本轮不动。
- 俄语 decline 原文的 LLM 翻译：原始文本先落库，翻译后置（可复用 call_mxou_chat_api 链）。
