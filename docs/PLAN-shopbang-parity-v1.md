# 上品帮对标落地执行方案（shopbang-parity v1）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把上品帮（shopbang）逆向分析中确认的差距落成三批可执行代码：A 采集箱备注全栈、B 选品字段补齐（跟卖利润/划线价/国内运费）、C worker 端 Ozon 会话代管（bindShopCookie 对标）。

**Architecture:** 遵循既有链路纪律——skill 生产端一次写全（本地 CSV + 信封/discovery_meta 三出口）→ worker 只存不算 → webui 只读展示。C 批次新增第四条链：skill CDP 收割会话 → worker AES-GCM 加密代管 → 服务端 cookie 直调 seller 内部 API（对标 `bindShopCookie`，但我们凭证不落第三方、加密存储、租户隔离）。批次 D（上架配置面）与 roadmap 项本计划只登记不施工。

**Tech Stack:** Python 3.12/3.14（skill，pip + venv314）、FastAPI + LangGraph + SQLAlchemy（worker）、React 19 + bun（webui）、pytest、CDP（websocket）。

## 全局约束（每个任务隐含遵守）

- 本地测试一律打本地环境：worker 全量命令必须显式 `PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon"`（漏掉会落 `postgres:5432` 主机名 → 33 分钟假阴性）；**禁止打生产 worker.mxou.cn**。
- 共享工作树多会话并行：**逐文件 `git add`，禁止 `git add .`/`-a`/stash**；不碰文件所有权表（见「执行编排」）以外的他人 WIP。
- 架构边界：skill 不调任何 Ozon 上架 API；worker 不抓 1688；notes 等运营态**不进信封 payload**；上架划线价归 worker 三档定价（B 批次抓的是竞品市场参考价，只进 meta/CSV，**不写 `draft.original_price`**）。
- 加密红线：`CREDENTIAL_MASTER_KEY` 启用后不可更换；一切会话/凭证存储必须 AES-256-GCM（复用 `worker/src/utils/credential_cipher.py`），响应里**永不回显 cookie 值**（只回名单与状态）。
- 选品字段纪律：缺失键省略（None/空串/空 dict）、真实 0 保留、扁平键恒 <2KB、CSV 列尾追加不动现有列序。
- 提交格式 `<type>(<scope>): 中文描述`；测试先行（TDD：先写失败测试→实现→绿→提交）。
- 契约同步：批次完成后主会话跑 `CONTRACT-v4.md` / `AGENTS.md` 登记 + worker 端点变更跑 `worker/scripts/gen_api_docs.py`。

## 架构同构映射（零上下文工程师必读）

| 上品帮 | 我们 | 批次 |
|---|---|---|
| 插件（多平台采集+cookie 总线+触发） | skill（CDP 抓取+信封组装） | A6/B/C4 |
| 服务端 batchCreateGoods 上架 | worker LangGraph 管线 | 已有 |
| goods_source_remark 采集备注 | 采集箱 notes | **A** |
| 定价器/利润计算器（服务端） | worker compute_price/estimate | 已有 |
| getOzonSaleDataByIds 数据湖 | discovery_runs+selection_insights | 已有雏形 |
| bindShopCookie 会话代管 | 无 → **C 批次新建**（加密存储+租户隔离） | **C** |
| watermark/img_order_type/offerIdType/定时分批 | 无 | 附录 D |

---

## 批次 A：采集箱备注（notes）——worker+webui 全栈

**文件所有权：** A 拥有 `worker/src/services/draft_service.py` 全部改动（含代 B 执行 `_DRAFT_META_CSV_KEYS` 追加）、`worker/src/api/schemas.py`、`worker/src/routes/drafts_routes.py`、`worker/scripts/init_data.py`、`worker/src/storage/database/shared/model.py`、`skill/scripts/cli.py`、`webui/src/components/CollectionPanel.tsx`、`webui/src/api/client.ts`。

### Task A1: DB 列 + 模型字段

**Files:**
- Modify: `worker/src/storage/database/shared/model.py`（ProductDraft 类内）
- Modify: `worker/scripts/init_data.py`（ALTER 语句区，`moderation_texts` 先例旁）
- Test: `worker/tests/test_draft_notes_v070.py`（新建）

**Interfaces:**
- Produces: `ProductDraft.notes: Optional[str]`（Text, nullable；None 视为无备注）；表列 `product_drafts.notes TEXT`。

- [ ] **Step 1: 写失败测试**（模型列断言，无需 PG）

```python
# worker/tests/test_draft_notes_v070.py
# -*- coding: utf-8 -*-
"""采集箱运营备注 notes（A 批次）：列在场、PATCH 更新、导入导出透传、不进信封。

运行：cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_draft_notes_v070.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_product_draft_model_has_notes_column():
    from storage.database.shared.model import ProductDraft

    assert "notes" in ProductDraft.__table__.columns
    col = ProductDraft.__table__.columns["notes"]
    assert col.nullable is True
    assert col.comment and "不进信封" in col.comment


def test_init_data_contains_notes_alter():
    text = Path(__file__).resolve().parent.parent.joinpath("scripts/init_data.py").read_text("utf-8")
    assert "ALTER TABLE product_drafts ADD COLUMN IF NOT EXISTS notes TEXT" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_draft_notes_v070.py -q`
Expected: FAIL（no attribute notes / assert in init_data）

- [ ] **Step 3: 最小实现**

```python
# model.py — class ProductDraft 内 updated_at 列之前追加：
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="运营备注（采集/选品依据人工标注）；不进信封 payload")
```

```python
# init_data.py — moderation_texts 的 ALTER 语句旁追加（同一 stmts 列表）：
"ALTER TABLE product_drafts ADD COLUMN IF NOT EXISTS notes TEXT",
```

- [ ] **Step 4: 跑测试确认通过**（同 Step 2 命令，Expected: PASS）

- [ ] **Step 5: Commit**

```bash
git add worker/src/storage/database/shared/model.py worker/scripts/init_data.py worker/tests/test_draft_notes_v070.py
git commit -m "feat(worker): 采集箱 notes 列——模型+init_data ALTER+列断言"
```

### Task A2: DraftPatch.notes + patch_draft 服务

**Files:**
- Modify: `worker/src/api/schemas.py:314`（DraftPatch）
- Modify: `worker/src/services/draft_service.py`（patch_draft）
- Test: `worker/tests/test_draft_notes_v070.py`（追加）

**Interfaces:**
- Consumes: Task A1 的 `notes` 列。
- Produces: `DraftPatch.notes: Optional[str]`（None=不修改；有值时 strip+cap 2000）；`patch_draft` 的 UPDATE SQL 含 `notes=COALESCE(:notes, notes)`（None 时零影响）。

- [ ] **Step 1: 追加失败测试**

```python
def test_draft_patch_model_accepts_notes():
    from api.schemas import DraftPatch

    p = DraftPatch(version=1, payload={}, notes="利润高，主图偏色待确认")
    assert p.notes.startswith("利润高")
    assert DraftPatch(version=1, payload={}).notes is None  # None=不修改


def test_patch_sql_preserves_notes_when_none():
    from pathlib import Path
    from services import draft_service

    src = Path(draft_service.__file__).read_text("utf-8")
    # patch_draft 的 UPDATE 用 COALESCE：notes=None 时保留原值（不覆盖）
    assert "notes=COALESCE(:notes, notes)" in src
```

- [ ] **Step 2: 跑测试确认失败**（同文件命令，Expected: FAIL）
- [ ] **Step 3: 实现**——先读 `patch_draft` 现有 UPDATE（当前更新 payload/version/updated_at 三处），然后：schemas.py DraftPatch 追加 `notes` 字段；draft_service.patch_draft 的 UPDATE SET 追加 `notes=COALESCE(:notes, notes)`，execute 参数加 `{"notes": body.notes if body.notes is None else body.notes.strip()[:2000]}`；行转 dict 处补 `"notes"`。

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit**

```bash
git add worker/src/api/schemas.py worker/src/services/draft_service.py worker/tests/test_draft_notes_v070.py
git commit -m "feat(worker): PATCH /drafts 支持 notes——None 不改写、COALESCE 单列更新"
```

### Task A3: 创建与导入链路带 notes

**Files:**
- Modify: `worker/src/services/draft_service.py`（create_draft 收 `notes`；import_drafts_csv 行映射）
- Modify: `worker/src/api/schemas.py`（DraftCreate 加 notes 可选）
- Test: `worker/tests/test_draft_notes_v070.py`（追加）

**Interfaces:**
- Produces: `create_draft(tenant_id, body)` body 可带 `"notes"`；`import_drafts_csv` 行支持 `notes` 列；导出列在 Task A4 消费。

- [ ] **Step 1: 追加失败测试**

```python
def test_create_body_notes_extracted(monkeypatch):
    from services import draft_service

    src = Path(draft_service.__file__).read_text("utf-8")
    assert 'body.get("notes")' in src or "body.get('notes')" in src
    src_import = src
    assert '"notes"' in src_import  # 导入行映射含 notes


def test_import_row_notes_stripped_and_capped():
    from services.draft_service import _norm_notes

    assert _norm_notes("  待确认  ") == "待确认"
    assert _norm_notes(None) == ""
    assert _norm_notes("x" * 5000) == "x" * 2000
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**

```python
# draft_service.py 顶部工具函数：
def _norm_notes(val) -> str:
    """备注归一：None→空串、strip、cap 2000。create/import/PATCH 三口共用。"""
    if val is None:
        return ""
    return str(val).strip()[:2000]
```

create_draft：body 取 `notes=_norm_notes(body.get("notes"))`，INSERT 列表加 notes；import_drafts_csv 的 `_row_to_envelope` 返回 tuple 改 `(envelope, notes)` 或在行循环里取 `row.get("notes")` 传给 create（跟随现有实现最自然的方式）。schemas.py DraftCreate 加 `notes: Optional[str] = Field(None, max_length=2000)`（路由是 raw-body，schema 只为 OpenAPI 文档真实化）。

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit**

```bash
git add worker/src/services/draft_service.py worker/src/api/schemas.py worker/tests/test_draft_notes_v070.py
git commit -m "feat(worker): 采集箱创建/CSV 导入支持 notes（_norm_notes 三口共用）"
```

### Task A4: export_drafts_csv 加 notes 列 + 代 B 追加 meta key

**Files:**
- Modify: `worker/src/services/draft_service.py`（export_drafts_csv + `_DRAFT_META_CSV_KEYS`）
- Test: `worker/tests/test_draft_export_discovery_meta.py`（追加）+ `worker/tests/test_draft_notes_v070.py`（追加）

**Interfaces:**
- Consumes: **B 批次冻结契约**（本计划文末「冻结契约」节）——B 的 4 个 meta key 名。
- Produces: 导出表头 `[... "source", "notes", "submission_status", ...]`；`_DRAFT_META_CSV_KEYS` 尾部含 B 的 4 键。

- [ ] **Step 1: 追加失败测试**

```python
# test_draft_notes_v070.py
def test_export_contains_notes_column(monkeypatch):
    from services import draft_service
    import csv as _csv, io as _io

    drafts = [{"id": "d1", "tenant_id": "t1", "payload": {"draft": {}, "source": {},
               "extensions": {}}, "source": "skill", "version": 1,
               "submission_status": None, "notes": "高利润", "created_at": "t", "updated_at": "t"}]
    monkeypatch.setattr(draft_service, "list_drafts", lambda t: drafts)
    rows = list(_csv.DictReader(_io.StringIO(draft_service.export_drafts_csv("t1"))))
    assert rows[0]["notes"] == "高利润"


# test_draft_export_discovery_meta.py
def test_export_b_batch_meta_keys_in_header():
    from services.draft_service import _DRAFT_META_CSV_KEYS

    for key in ("follow_profit_cny", "follow_margin", "ozon_old_price", "match_1688_freight_cny"):
        assert key in _DRAFT_META_CSV_KEYS, f"缺 B 契约列: {key}"
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**：export header 的 `"source"` 后插 `"notes"`、row 对应位置 `str(d.get("notes") or "")`；`_DRAFT_META_CSV_KEYS` 追加 4 键（列序与 skill export_to_csv v0.70 段对齐）。
- [ ] **Step 4: 跑测试确认通过**（两个测试文件）
- [ ] **Step 5: Commit**

```bash
git add worker/src/services/draft_service.py worker/tests/test_draft_notes_v070.py worker/tests/test_draft_export_discovery_meta.py
git commit -m "feat(worker): 采集箱导出加 notes 列 + 代 B 契约追加 4 meta 列"
```

### Task A5: webui 备注编辑

**Files:**
- Modify: `webui/src/api/client.ts`（Draft 类型 + updateDraft body）
- Modify: `webui/src/components/CollectionPanel.tsx`（EditDraftDrawer 备注区）
- Test: `cd webui && bunx tsc -b && bun run build`（门禁）

**Interfaces:**
- Consumes: Task A2 的 PATCH notes 字段。

- [ ] **Step 1: client.ts Draft 接口加 `notes?: string | null`；updateDraft 请求体透传 notes**
- [ ] **Step 2: EditDraftDrawer 加受控 textarea**（label「备注」、placeholder「采集备注：利润依据/货源风险/待确认项…」、maxLength 2000、保存时并入 PATCH body；drawer 现有 state 模式跟随）
- [ ] **Step 3: `cd webui && bunx tsc -b && bun run build`** Expected: 0 错误
- [ ] **Step 4: Commit**

```bash
git add webui/src/api/client.ts webui/src/components/CollectionPanel.tsx
git commit -m "feat(webui): 采集箱编辑抽屉支持备注查看与保存"
```

### Task A6: skill discover --to-box --note

**Files:**
- Modify: `skill/scripts/cli.py`（cmd_discover 的 --to-box 分支 + argparse）
- Modify: `skill/scripts/cloud_probe.py`（submit_draft 加 note 参数；**只加请求字段，不进 envelope**）
- Test: `skill/tests/test_discover_to_box.py`（追加）

**Interfaces:**
- Consumes: Task A3 的 POST /drafts notes 字段。
- Produces: `discover ... --to-box --note "备注"`；`cloud_probe.submit_draft(..., note: str | None = None)`。

- [ ] **Step 1: 追加失败测试**

```python
def test_submit_draft_note_in_body_not_envelope(monkeypatch):
    from scripts import cloud_probe

    captured = {}
    def fake_post(url, json=None, **kw):
        captured.update(url=url, body=json)
        class R: status_code = 200; ok = True
        return R()
    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("scripts.lib.config_store.get_mxou_token", lambda: "sk-t")
    env = {"token": "sk-t", "ozon_client_id": "1", "ozon_api_key": "k",
           "envelope": {"draft": {}, "source": {}, "extensions": {}}}
    cloud_probe.submit_draft(env, note="竞品月销高")   # 签名以现有实现为准：envelope 或 GraphInput
    assert captured["body"]["notes"] == "竞品月销高"
    assert "notes" not in captured["body"]["envelope"]["extensions"]
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**：submit_draft 加 keyword-only `note=None`，body 组装处 `if note: body["notes"] = note`；cli.py cmd_discover 的 to-box argparse 加 `--note`，调用处透传。**红线：notes 绝不写入 envelope/extensions。**
- [ ] **Step 4: 跑 `cd skill && .venv314/bin/python -m pytest tests/test_discover_to_box.py -q`** Expected: PASS
- [ ] **Step 5: Commit**

```bash
git add skill/scripts/cli.py skill/scripts/cloud_probe.py skill/tests/test_discover_to_box.py
git commit -m "feat(skill): discover --to-box 支持 --note 采集备注（不入信封）"
```

---

## 批次 B：skill 三字段（跟卖利润/划线价/国内运费）

**文件所有权：** B 拥有 `skill/scripts/lib/ozon_discovery.py`、`skill/scripts/cloud_probe.py`（meta 组装，与 A6 的 submit_draft 改动不同函数区）、`skill/tests/test_discovery_*.py`、`webui/src/components/DiscoveryPanel.tsx`。**不碰** `worker/src/services/draft_service.py`（A4 已代办）。

**冻结契约（B 新增 4 键，A4 已按此名落导出列）：** `follow_profit_cny` / `follow_margin` / `ozon_old_price` / `match_1688_freight_cny`。

### Task B1: candidate 字段 + 跟卖利润测算

**Files:**
- Modify: `skill/scripts/lib/ozon_discovery.py`（ProductCandidate 字段区 + `_calculate_profit` 尾部）
- Test: `skill/tests/test_discovery_export_csv.py`（追加）或新建 `test_discover_follow_profit.py`

**Interfaces:**
- Produces: `candidate.follow_profit_cny/follow_margin`（min_competing_price>0 时非零）；`candidate.ozon_old_price/match_1688_freight_cny` 默认 0.0。

- [ ] **Step 1: 写失败测试**

```python
# skill/tests/test_discover_follow_profit.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scripts.lib.ozon_discovery import ProductCandidate, _calculate_profit


def _cand(**kw):
    c = ProductCandidate(ozon_product_id="p1", ozon_title="t", ozon_price=953.0)
    c.match_1688_price = 60.0
    c.min_competing_price = 900.0
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_follow_profit_computed_from_min_competing_price():
    c = _cand()
    c.estimated_logistics_cny = 40.0
    c.estimated_commission = 0.0   # 让 commission 分支走 rate 重算前的显式值
    _calculate_profit(c, fx_rate=12.0, commission_rate=0.10)
    # 竞品现价 953×12=11436 → 主利润已算；跟卖口径 900×12=10800
    assert c.follow_profit_cny > 0
    assert c.follow_profit_cny < (c.ozon_price * 12 - (60 + 40) * 1)  # 比主口径低
    assert 0 < c.follow_margin < c.profit_margin


def test_follow_profit_zero_without_competitors():
    c = _cand(min_competing_price=0.0)
    _calculate_profit(c, fx_rate=12.0, commission_rate=0.10)
    assert c.follow_profit_cny == 0.0 and c.follow_margin == 0.0
```

- [ ] **Step 2: 跑测试确认失败**（`cd skill && .venv314/bin/python -m pytest tests/test_discover_follow_profit.py -q`，Expected: FAIL 属性不存在）
- [ ] **Step 3: 实现**

```python
# ProductCandidate 字段区（裂变字段之后）追加：
    # v0.70 上品帮对标（B 批次）：跟卖利润空间 + 竞品划线价 + 货源国内运费单列
    follow_profit_cny: float = 0.0       # 按 min_competing_price 卖出的利润 CNY（无跟卖=0，真实数据保留）
    follow_margin: float = 0.0           # 同口径利润率 %
    ozon_old_price: float | None = None  # 竞品划线价 RUB（widget originalPrice；None=未知，区别于真实 0）
    match_1688_freight_cny: float | None = None  # 1688 国内运费单列 CNY（详情页 freightCny；None=未抓到）
```

```python
# _calculate_profit 末尾（candidate.profit_margin 赋值后）追加：
    # 跟卖利润空间：跟到跟卖最低价还能剩多少（同一成本链，仅换收入口径）
    if candidate.min_competing_price > 0:
        follow_revenue = candidate.min_competing_price * fx_rate
        follow_cost = cost_cny + candidate.estimated_logistics_cny \
            + follow_revenue * effective_commission
        follow_profit = follow_revenue - follow_cost
        candidate.follow_profit_cny = round(follow_profit, 2)
        candidate.follow_margin = round(follow_profit / follow_revenue * 100.0, 1)
```

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit**

```bash
git add skill/scripts/lib/ozon_discovery.py skill/tests/test_discover_follow_profit.py
git commit -m "feat(skill): 跟卖利润空间测算——min_competing_price 同成本链换收入口径"
```

### Task B2: 划线价接线（widget originalPrice）

**Files:**
- Modify: `skill/scripts/lib/ozon_discovery.py:543` 附近（`fetch_product_info` 消费处）
- Test: `skill/tests/test_discover_follow_profit.py`（追加）

**Interfaces:**
- Consumes: `fetch_product_info` 已返回的 `originalPrice`（ozon_widget.py:143/195/523，**无需改 widget**）。
- Produces: `candidate.ozon_old_price: float | None`（parse 为 0 → None，未知≠真实 0）。

- [ ] **Step 1: 写失败测试**

```python
def test_old_price_plumbed_from_info():
    from scripts.lib.utils import parse_price

    # widget info 的 originalPrice → candidate；空/缺 → None（未知，不冒充 0）
    for raw, want in (("7695", 7695.0), ("", None), (None, None)):
        val = parse_price(raw or "") or None
        assert val == want, f"originalPrice={raw!r}"
```

- [ ] **Step 2: 跑测试确认失败**（None 分支当前写法会是 0.0）
- [ ] **Step 3: 实现**——`candidate.ozon_price = _parse_price(...)` 行（ozon_discovery.py:543 附近）之后追加：

```python
        candidate.ozon_old_price = _parse_price(
            info.get("originalPrice", "") or "") or None   # 竞品划线价（市场参考；不写 draft.original_price）
```

- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: Commit**

```bash
git add skill/scripts/lib/ozon_discovery.py skill/tests/test_discover_follow_profit.py
git commit -m "feat(skill): 竞品划线价接线——widget originalPrice 入 candidate（仅 meta/CSV，不碰上架划线价）"
```

### Task B3: 货源国内运费透传（锚点唯一）

**Files:**
- Modify: `skill/scripts/lib/ozon_discovery.py:922`（`candidate.match_1688_price = float(match.get("price", 0))` 之后——**全仓唯一赋值点，已核实**）
- Test: `skill/tests/test_discover_follow_profit.py`（追加）

**Interfaces:**
- Produces: `candidate.match_1688_freight_cny: float | None`（match 源 dict 带 freightCny 且 >0 → 值；否则 None）。
- 说明：图搜（aibuy）/AK 源候选多数不带 freightCny → 恒 None → meta 缺省省略；这是如实透传不是缺失缺陷——freight 的权威来源是货源详情页，若 aibuy 后续补充该键则自动生效。

- [ ] **Step 1: 写失败测试**

```python
def test_freight_plumbed_from_match_dict():
    # :922 赋值表达式的三态镜像断言：正 freight → 值；0/缺失 → None（未知≠真实 0）
    for raw, want in (({"freightCny": 2.0}, 2.0), ({"freightCny": 0}, None), ({}, None)):
        got = float(raw.get("freightCny", 0) or 0) or None
        assert got == want, f"freightCny={raw!r}"


def test_candidate_freight_defaults_none():
    c = _cand()
    assert c.match_1688_freight_cny is None
    assert c.ozon_old_price is None
```

### Task B4: 三出口扩（meta / REPORT_FIELDS / export_to_csv）

**Files:**
- Modify: `skill/scripts/cloud_probe.py`（`_assemble_discovery_meta` 键 tuple 追加 4 键）
- Modify: `skill/scripts/lib/ozon_discovery.py`（REPORT_FIELDS 追加 `ozon_old_price`/`match_1688_freight_cny` 2 标量；export_to_csv fields/rows 追加 4 列）
- Test: `skill/tests/test_discovery_meta_envelope.py`、`skill/tests/test_discovery_export_csv.py`（各追加断言）

**Interfaces:**
- Consumes: B1-B3 的 candidate 字段（`ozon_old_price`/`match_1688_freight_cny` 默认 None → meta/REPORT 沿用既有 None-省略纪律自动不落键；CSV 落空串）。
- Produces: 三出口均带 4 新键——meta 4 键、REPORT_FIELDS 4 键、export_to_csv 4 列（`_opt` 模式 None→空串）。

- [ ] **Step 1: 追加失败测试**

```python
# test_discovery_meta_envelope.py
def test_assemble_b_batch_keys():
    cand = _mk_candidate(follow_profit_cny=12.3, follow_margin=10.5,
                         ozon_old_price=7695.0, match_1688_freight_cny=2.0)
    cand.ozon_images = ["https://ir-20.ozonstatic.cn/a.jpg"]
    meta = cloud_probe._assemble_discovery_meta(cand)
    assert meta["follow_profit_cny"] == 12.3 and meta["follow_margin"] == 10.5
    assert meta["ozon_old_price"] == 7695.0 and meta["match_1688_freight_cny"] == 2.0
    assert len(json.dumps(meta, ensure_ascii=False)) < 2048

# test_discovery_export_csv.py
def test_export_b_batch_columns():
    c = _mk_candidate()
    c.follow_profit_cny = 12.3
    c.ozon_old_price = 7695.0
    out = tmp_path / "e.csv"
    export_to_csv([c], str(out))
    with open(out, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        assert {"follow_profit_cny", "follow_margin", "ozon_old_price",
                "match_1688_freight_cny"} <= set(reader.fieldnames)
```

（REPORT 侧在 `test_discovery_report_hook.py` 补一行：`assert row["ozon_old_price"] == 7695.0` 于既有 v070 测试内。）
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**：三处 tuple/list 尾部追加（meta 4 键、REPORT_FIELDS 4 键、export_to_csv fields+rows 4 列，行值用既有 `_opt`/getattr 模式）。
- [ ] **Step 4: 跑 `cd skill && .venv314/bin/python -m pytest tests/test_discovery_meta_envelope.py tests/test_discovery_export_csv.py tests/test_discovery_report_hook.py -q`** Expected: 全绿
- [ ] **Step 5: Commit**

```bash
git add skill/scripts/cloud_probe.py skill/scripts/lib/ozon_discovery.py skill/tests/test_discovery_meta_envelope.py skill/tests/test_discovery_export_csv.py skill/tests/test_discovery_report_hook.py
git commit -m "feat(skill): 跟卖利润/划线价/运费三出口扩——meta+REPORT+CSV 按 B 契约"
```

### Task B5: webui 选品档案导出扩 4 列

**Files:**
- Modify: `webui/src/components/DiscoveryPanel.tsx`（exportCandidates rows 对象）
- Test: `bunx tsc -b && bun run build`

- [ ] **Step 1: rows 对象追加 4 行**（跟随现有 `String(pick(o, [...]) ?? "")` 模式）：`follow_profit_cny`、`follow_margin`、`ozon_old_price`、`match_1688_freight_cny`
- [ ] **Step 2: `cd webui && bunx tsc -b && bun run build`** Expected: 0 错误
- [ ] **Step 3: Commit**

```bash
git add webui/src/components/DiscoveryPanel.tsx
git commit -m "feat(webui): 选品档案导出扩 B 契约 4 列"
```

---

## 批次 C：worker 端 Ozon 会话代管（bindShopCookie 对标）

**文件所有权：** C 拥有 `worker/src/storage/database/shared/model.py`（新表）、`worker/src/services/ozon_session_service.py`（新建）、`worker/src/routes/credentials_routes.py`（新端点）、`worker/src/utils/ozon_session_client.py`（新建）、`skill/scripts/cli.py`（新子命令；与 A6 同文件——**A6 先行合入后 C4 再动**，或由主会话串行）。webui 不动（后续批次接 UI）。

### Task C1: ozon_sessions 表 + 模型

**Files:**
- Modify: `worker/src/storage/database/shared/model.py`
- Test: `worker/tests/test_ozon_session_v070.py`（新建）

**Interfaces:**
- Produces: 表 `ozon_sessions`：`id UUID PK`、`tenant_id String(50)`、`credential_id UUID`、`cookies_encrypted LargeBinary`、`cookie_names JSONB`（名单，不存值）、`sc_company_id_encrypted LargeBinary`、`status String(16) 'active'|'expired'`、`harvested_at/last_checked_at DateTime(timezone)`、`UniqueConstraint(tenant_id, credential_id)`。

- [ ] **Step 1: 写失败测试**

```python
# worker/tests/test_ozon_session_v070.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

def test_ozon_session_model_shape():
    from storage.database.shared.model import OzonSellerSession

    t = OzonSellerSession.__table__
    assert t.name == "ozon_sessions"
    for col in ("cookies_encrypted", "cookie_names", "sc_company_id_encrypted", "status"):
        assert col in t.columns
    uniques = [u for u in t.constraints if u.__class__.__name__ == "UniqueConstraint"]
    assert any({"tenant_id", "credential_id"} <= {c.name for c in u.columns} for u in uniques)
```

- [ ] **Step 2: 跑失败** → **Step 3: 实现模型**（照 DraftPatch 同文件风格；新表由 init_data 的 `Base.metadata.create_all` 自动建，无需 ALTER）→ **Step 4: 绿** → **Step 5: Commit**

```bash
git add worker/src/storage/database/shared/model.py worker/tests/test_ozon_session_v070.py
git commit -m "feat(worker): ozon_sessions 表——会话代管存储模型（tenant+credential 唯一）"
```

### Task C2: session_service（存储/解密/状态）

**Files:**
- Create: `worker/src/services/ozon_session_service.py`
- Test: `worker/tests/test_ozon_session_v070.py`（追加）

**Interfaces:**
- Consumes: `worker/src/utils/credential_cipher.py` 的 `encrypt_with_key(value, aad, key_raw)` / `decrypt_with_key(...)`；master key 读取方式照 `credential_service.py` 现状。
- Produces: `store_session(tenant_id, credential_id, cookies: dict[str,str]) -> None`（原子 upsert，aad=`f"{tenant_id}:{credential_id}"`）、`get_cookie_header(tenant_id, credential_id) -> str | None`（`"a=b; c=d"`；无会话/解密失败→None）、`mark_status(..., status)`、`session_status(tenant_id, credential_id) -> dict`（`{status, harvested_at, cookie_names}`，**永不回值**）。

- [ ] **Step 1: 写失败测试**

```python
import base64
import os

import pytest


@pytest.fixture()
def key32() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_cipher_roundtrip_with_session_aad(key32):
    # 加密基元（复用 credential_cipher，不新造加密）——aad 格式冻结为 tenant:credential
    from utils import credential_cipher

    aad = "t1:c1"
    blob = credential_cipher.encrypt_with_key('{"sc_company_id":"5371047"}', aad, key32)
    assert credential_cipher.decrypt_with_key(blob, aad, key32) == '{"sc_company_id":"5371047"}'
    with pytest.raises(Exception):
        credential_cipher.decrypt_with_key(blob, "t1:c2", key32)  # aad 绑定：跨租户不可解


def test_service_sql_upsert_and_never_return_values():
    from pathlib import Path
    import services.ozon_session_service as svc

    src = Path(svc.__file__).read_text("utf-8")
    assert "ON CONFLICT" in src and "DO UPDATE" in src          # 原子 upsert
    assert 'aad = f"{tenant_id}:{credential_id}"' in src        # aad 格式
    # session_status 返回结构只含名单与状态，永不回 cookie 值
    assert "cookie_names" in src and "status" in src
```

（master key 的读取方式照 `credential_service.py` 现状抄——不新造密钥管理；service 实现 Step 3 见任务文本。）

- [ ] **Step 2: 失败** → **Step 3: 实现**（JSON 序列化→encrypt→upsert；`get_cookie_header` 捕获 GCM 认证失败→`mark_status("expired")`+None，**换 key 后旧会话不可解密是预期行为**）→ **Step 4: 绿** → **Step 5: Commit** `feat(worker): 会话代管服务——AES-GCM 存储/解密头/状态，永不回值`

### Task C3: 端点（POST/GET/DELETE /credentials/{id}/session）

**Files:**
- Modify: `worker/src/routes/credentials_routes.py`
- Test: `worker/tests/test_ozon_session_v070.py`（追加 TestClient 用例，照 `test_credentials_validation_422.py` 模式）

**Interfaces:**
- Produces: `POST /api/v1/credentials/{credential_id}/session`（body `{"cookies": {...}}`，未配 key→500 文案同凭证端点惯例）、`GET .../session`（→ `{status, harvested_at, cookie_names}`）、`DELETE .../session`（204）。跨租户/不存在 credential → 404（复用 `credential_service.get_decrypted` 的校验先例）。schema 用 `openapi_extra` 声明请求体（照 `v1_submit_task` 先例）。

- [ ] **Step 1: 写失败测试**（空 token 401、跨租户 404、GET 不回值、DELETE 204）
- [ ] **Step 2: 失败** → **Step 3: 实现三个端点** → **Step 4: 绿 + 跑 `python worker/scripts/gen_api_docs.py`**（新端点进 REFERENCE，防 CI Step 5d 漂移红）→ **Step 5: Commit** `feat(worker): 会话代管端点——上传/状态/撤销（租户隔离+密文不回显）`

### Task C4: skill `session-sync` 子命令

**Files:**
- Modify: `skill/scripts/cli.py`（argparse 子命令 + cmd_session_sync；**A6 已合入本文件后才动工**）
- Test: `skill/tests/test_session_sync.py`（新建，mock `_fetch_seller_session_cookies` 与 requests.post）

**Interfaces:**
- Consumes: `ozon_seller_analytics._fetch_seller_session_cookies(cdp_url)`（现成，core cookie=`sc_company_id`）；Task C3 端点。
- Produces: `cli.py session-sync --credential-id <uuid> [--status] [--worker-url URL]`。

- [ ] **Step 1: 写失败测试**

```python
# skill/tests/test_session_sync.py 核心断言：
def test_sync_requires_sc_company_id(monkeypatch, capsys):
    monkeypatch.setattr("scripts.lib.ozon_seller_analytics._fetch_seller_session_cookies",
                        lambda cdp_url="": {"Abt": "x"})  # 无 sc_company_id
    import pytest
    from scripts.cli import cmd_session_sync
    with pytest.raises(SystemExit) as e:
        cmd_session_sync(_ns(credential_id="c1"))
    assert e.value.code == 2  # fail-fast：无核心 cookie 不上传

def test_sync_uploads_cookies(monkeypatch):
    ...  # mock 收割返回含 sc_company_id → 断言 POST body={"cookies": {...}} 且 URL 含 /session
```

- [ ] **Step 2: 失败** → **Step 3: 实现**（无 sc_company_id → print 人话 + exit 2；成功 print 脱敏摘要——只打 cookie 名单与 harvested_at）→ **Step 4: 绿** → **Step 5: Commit** `feat(skill): session-sync——CDP 收割 seller 会话上传 worker 代管`

### Task C5: worker cookie 直调客户端 + what-to-sell 端点

**Files:**
- Create: `worker/src/utils/ozon_session_client.py`
- Modify: `worker/src/routes/`（新路由或挂 store_routes 下；建议 `analytics_routes.py` 新文件，注册 `GET /api/v1/analytics/what-to-sell`）
- Test: `worker/tests/test_ozon_session_v070.py`（追加，mock requests）

**Interfaces:**
- Consumes: Task C2 `get_cookie_header`；毛子移植的 what_to_sell v3 契约（`POST https://seller.ozon.ru/api/site/seller-analytics/what_to_sell/data/v3`，headers `Cookie + x-o3-company-id + x-o3-language: zh-Hans`，payload `{limit, offset, filter:{stock:"any_stock", period:"monthly", categories:[], sku}, sort:{key:"sum_gmv_desc"}}`，sku 是 int64 无 _0 后缀——见 git 7657414 系列与 `ozon_seller_analytics.py` 既有实现）。
- Produces: `what_to_sell(cookie_header, sc_company_id, payload) -> (dict | None, error_code)`；端点 `GET /api/v1/analytics/what-to-sell?credential_id=&sku=&limit=` → `{found, data}` / 409 `session_expired` / 404 跨租户。

- [ ] **Step 1: 写失败测试**（mock requests：200→found；401/403→mark expired+409；无会话→404 文案「尚未同步会话，先在 skill 执行 session-sync」）
- [ ] **Step 2: 失败** → **Step 3: 实现**（client 只拼请求与判废；`sc_company_id` 从会话加密字段解出——cookie 里本身有 sc_company_id，直接从 cookie dict 取，勿另存）→ **Step 4: 绿 + `gen_api_docs.py`** → **Step 5: Commit** `feat(worker): 会话直调通道——what_to_sell 服务端查询（对标商品级运营数据入口）`

### Task C6: 失效检测与重同步闭环（文档+联动）

**Files:**
- Modify: `worker/src/utils/ozon_session_client.py`（任何调用遇 401/403/登录 302 → `mark_status("expired")`）
- Modify: `skill/SKILL.md` + `skill/references/`（session-sync 使用时机：`check` 提示会话过期时执行；命令表加行）
- Test: C5 测试文件内断言 expired 联动。

- [ ] **Step 1: 失效联动测试** → **Step 2: 实现** → **Step 3: SKILL.md 命令表 + reference 段落** → **Step 4: 全绿** → **Step 5: Commit** `docs(skill)+feat(worker): 会话失效联动——expired 自动标记与 session-sync 重同步闭环`

---

## 附录 D：待拍板批次（本计划不施工，需用户点头后另拆 plan）

| 项 | 上品帮对标 | 说明 | 预估 |
|---|---|---|---|
| D1 分批节奏上架 | timingUpGoods/createTimedTask | drafts batch-submit / skill batch_test 加 `--stagger-seconds`（提交侧分批），防风控 | 小 |
| D2 图片水印模板 | watermark_id | prepare 生图后缀水印——需图片处理管线，另立 PRD | 中 |
| D3 主图排序策略 | img_order_type | prepare 图序配置化 | 小 |
| D4 offerIdType 对标调研 | offerIdType/ocType | 对比我们 `_derive_model_name_9048`，看他们防并卡编码 | 调研 |
| D5 渠道约束预检 | China_scoring xlsx | 货值上限/电池液体/时效灌进 logistics quote 或粗筛（xlsx 版本 2026-04-21，需最新版） | 中 |

## 附录 Roadmap（记录不动工）

1688 `entrypoint-api.bx` 第三通道调研（aibuy token 替代）；跨平台货源采集地图（淘宝 mtop/AliExpress lib.mtop 桥/WB v4.detail/PDD——见记忆 `ozon-plugin-reference-projects`）；采购跟踪闭环（cgTracking 对标）；复制铺货（已过审卡衍生）；商品级流量词归因 + 按 ID 批量销量查询（依赖 C 批次会话数据积累 + discovery_runs 数据湖）；`window.rawData` 通用兜底提取。

## 附录：遗留已知问题台账（与本计划并行跟踪，非本计划范围）

早夭失败不写 listing_result_log 留存行；生图后 404 参考图守卫失效；A3 残余（估算尺寸过小 Ozon 重量×体积交叉校验报错误导）；R4 换类目目标无域守卫；pounding-mcp PyPI publish 待 token；Sentry 断流待服务器核验；**发版实机 gate（≥3 单全链路）多批累积未跑**。

## 执行编排（主会话职责）

1. **并行派发**：Agent A（Task A1→A6 顺序）、Agent B（Task B1→B5 顺序）同时开工；**C 待 A6 合入后由主会话或第三 agent 串行**（A6 与 C4 同文件 `cli.py`）。
2. **接口冻结即本计划**：冻结契约 4 键已在 A4/B4 双向锁定（A 的导出列测试 × B 的 meta/CSV 测试互为防回归）。
3. **收尾归主会话**：`CONTRACT-v4.md`/`AGENTS.md` 登记；三面全量回归（worker `PGDATABASE_URL=localhost:5433` + skill venv314 + webui build）+ ruff 零新增对照 HEAD；逐文件 add 统一提交；记忆归档。
