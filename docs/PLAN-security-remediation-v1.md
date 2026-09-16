# 安全修复批次 v1（security-remediation-v1）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 2026-09-16 五域安全审计（api-auth/注射/密钥/竞态/供应链，5 subagent 实证）发现的 2 条 Critical 攻击链 + 12 条 High + 全部可落地 Medium，零生产接触、全程 TDD。

**Architecture:** 六个 PR 依次合入 dev（Tier A，每 PR 独立可测可回滚）：PR-1 凭证外泄面收口 → PR-2 鉴权矩阵补齐 → PR-3 SSRF 统一收敛（新 `utils/secure_fetch.py` 唯一入口）→ PR-4 本地/部署面 → PR-5 加固排期项 → PR-6 文档收口。所有修复只动 worker/src、pounding-mcp、webui、deploy、.github，不动信封契约（CONTRACT-v4 零变更）。

**Tech Stack:** Python 3.14（`skill/.venv314` 解释器）、FastAPI TestClient、pytest、SQLAlchemy text()、bun/webui、GitHub Actions。

## Global Constraints

- 测试命令（worker，漏 PGDATABASE_URL 会假阴性，见 AGENTS.md 高频坑）：
  `cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/<file> -q`
- webui 验证：`cd webui && bunx tsc -b && bun run build`（不跑 `bun install`，避免动 bun.lock；如必须装依赖，结束前 `git checkout -- webui/bun.lock`）。
- lint：worker `ruff check src/ --select E,F,W --ignore E501`；pounding-mcp 在自身 venv `cd pounding-mcp && ruff check pounding_mcp/ --select E,F,W --ignore E501`。
- 改任何 API 响应 schema 后必跑：`cd worker && PYTHONPATH=src ../skill/.venv314/bin/python scripts/gen_api_docs.py`（CI Step 5d 漂移即红）。
- Commit 格式 `<type>(<scope>): 中文描述`；**逐文件 `git add`，禁 `-a`/stash**（AGENTS.md 纪律）。
- **禁止连接生产**：worker.mxou.cn / api.mxou.cn / 端口 15433 / 真实 COS。一切验证走本地 PG 5433（先 `lsof -iTCP:5433 -sTCP:LISTEN` 核实）。
- pytest exit 2 = conftest 生产哨兵闸触发 → 立即停止当前命令。
- 本批**有意不做**（defer，勿顺手做）：明文 token 列 DB 侧退役、task_status 404 化、单飞锁跨副本版、legacy `GET /task/{task_id}` 退役、容器切非 root。

## 审计发现 → 任务映射

| 审计编号 | 发现 | 任务 |
|---|---|---|
| api-C1 | 读端点回显明文 MXOU key（跨租户接管链，探针实证） | T1 |
| crypto-C1 | /run 系日志打完整 body（token/api_key 明文） | T2 |
| crypto-H1 | task_status 回显完整 payload（双凭证） | T3 |
| crypto-H3 | Sentry 捕获栈帧局部变量 | T4 |
| crypto-L1 | auth_node 打 Api-Key 前 10 位 | T5 |
| api-H2 | cancel_task 无鉴权（实证 200 跨租户） | T6 |
| api-H3 | task_statistics 无鉴权 + 租户枚举 | T7 |
| api-M2 | /progress 无鉴权 | T8 |
| api-M3 | store/health 无鉴权 + api_key 走 query | T9 |
| api-M4 | logistics/quote 匿名无限流 | T10 |
| race-H1 | submit_task 不校验 token status | T11 |
| api-H1 | node_run 可投毒全局学习表 | T12 |
| inj-C1/inj-H1/inj-H2 | 镜像链全读 SSRF + 白名单子串绕过 + 盲探测面 | T13-T16 |
| inj-I1 | file/file.py 死代码含任意路径读原语 | T17 |
| cicd-H1 | 8902 无鉴权 + CORS *（网页 drive-by） | T18 |
| race-M4 | 重启默认复活 failed 且 retry_count 归零 | T19 |
| cicd-M6 | test/e2e compose PG 绑 0.0.0.0:5433 | T20 |
| race-M5 | 限流器鉴权前计数 + 字典永不清扫 | T21 |
| cicd-M2/inj-M1 | CSV 公式注入（worker+webui，跨租户投毒源） | T22 |
| cicd-M3 | cd.yml tag 名 `${{ }}` 双重插值 | T23 |
| crypto-M2 | KDF=单次无盐 SHA-256 | T24 |
| crypto-M1/cicd-M1 | 明文 pg_dump 可上 COS | T25 |
| crypto-M3 | gitleaks 对非标形态密钥的覆盖缺口 | T31 |
| cicd-H2 | COS 升级分发链无签名（信任根= bucket 写权限） | T32 |
| race-L1 | resubmit 无余额预检 + IntegrityError 500 | T26 |
| race-L2 | purchase_cost 负数可入定价 | T27 |
| race-L3 | template set_default 三段非原子 | T28 |
| inj-L1 | ILIKE 通配符未转义 | T29 |
| cicd-M4 | actions tag-pin + curl\|bash 无校验 | T30 |
| — | 文档/发版物料收口 | T33 |

---

## Task 0: 开工 worktree + 分支（Tier A 纪律）

**Files:** 无仓库文件改动（worktree 在仓库外）。

- [ ] **Step 1: 建 worktree**

```bash
cd /Volumes/os/dev/ozon-worker
git fetch origin dev
git worktree add ../ozon-worker-secfix -b fix/security-remediation-v1 origin/dev
cd ../ozon-worker-secfix
```

- [ ] **Step 2: 核实本地 PG**

```bash
lsof -iTCP:5433 -sTCP:LISTEN   # 必须是 Docker com.docke；若被临时 PG 占位先处理
```

- [ ] **Step 3: 冒烟基线**（确认开工时测试是绿的，后续每个 PR 结束跑同一命令对比）

```bash
cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_tenant_guard_phase3.py tests/test_mcp_server.py -q
```

Expected: 8+19 passed。

---

# PR-1 凭证外泄面收口（api-C1 + crypto-C1/H1/H3/L1）

## Task 1: 全局读端点停发明文贡献者 key

跨租户接管链的读侧出口。DB 明文列保留（defer 退役），但 **API 响应只发 `contributed_by_fp`**。

**Files:**
- Modify: `worker/src/services/analytics_service.py:63,81-82`（list_bestsellers SELECT + 返回 dict）
- Modify: `worker/src/main.py:2953-2957`（discovery_runs 返回 dict）
- Modify: `webui/src/api/hooks.ts:601`、`webui/src/components/DiscoveryPanel.tsx:49,166`
- Test: `worker/tests/test_no_plaintext_contrib_key.py`（新建）

**Interfaces:**
- Produces: 响应字段 `contributed_by_fp`（str，`token_fingerprint(plain)[:8]`）保持不变；`contributed_by_token_id` 从两个读端点响应中**消失**（不是置空）。

- [ ] **Step 1: 写失败测试**

先读 `services/analytics_service.py` 的 `list_bestsellers` 全文，确认 SELECT 元组列序（下记 `N` 为明文 key 所在下标）。测试打在**服务层**（脱敏实现层），discovery/runs 打 HTTP 层（路由组装层）：

```python
# worker/tests/test_no_plaintext_contrib_key.py
"""v0.76 安全修复 T1：全局共享读端点不得回显贡献者明文 key（api-C1 跨租户接管链）。"""
PLAIN = "probe-plain-key-0001"


def test_list_bestsellers_service_strips_plaintext(monkeypatch):
    from services import analytics_service as a
    # 行形态以真实 SELECT 列序为准：r[N] = 明文 key
    fake = [("x", "q", 1, 1.0, 2, 1.0, 2.0, PLAIN)]
    monkeypatch.setattr(a, "_fetch_bestseller_rows", lambda **kw: fake)  # 本任务抽出的行查询封装
    out = a.list_bestsellers(brand="x")
    assert all("contributed_by_token_id" not in row for row in out)
    assert out[0]["contributed_by_fp"]          # fp 保留且非空


def test_discovery_runs_http_response_has_no_plaintext(monkeypatch):
    from fastapi.testclient import TestClient
    import main as m
    monkeypatch.setattr("main._verify_analytics_token", lambda t: None)
    monkeypatch.setattr(
        m, "_fetch_discovery_runs_rows",
        lambda **kw: [("t1", "tenant-a", "2026-09-16", 1, 2, PLAIN, "fp000001", "{}")],
    )
    r = TestClient(m.app).get("/api/v1/discovery/runs", headers={"Authorization": "Bearer sk-probe"})
    assert r.status_code == 200
    assert PLAIN not in r.text                  # 明文绝不出响应
    assert "contributed_by_token_id" not in r.text
    assert "contributed_by_fp" in r.text
```

> 注：`_fetch_bestseller_rows` / `_fetch_discovery_runs_rows` 是本任务从现有函数里抽出的行查询封装（把 DB 访问与组装分层，测试才能钉住脱敏行为）——先写测试所以先按接口名引用。列下标/形参以文件现状平移为准，零语义变更。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_no_plaintext_contrib_key.py -q
```

Expected: FAIL（响应含明文 / `_fetch_discovery_runs_rows` 不存在）。

- [ ] **Step 3: 实现**

`analytics_service.py` `list_bestsellers`：把 DB 行查询抽成模块级 `_fetch_bestseller_rows(**kw)`（原 SQL 平移，零语义变更）；SELECT 保留明文列（Python 内算 fp 用），**组装 dict 只放 fp**。第 81-82 行区域改为：

```python
        rows.append({
            "brand": r[0], "query": r[1], "queries_wca": r[2],
            "custom_click_rate": r[3], "sold_count": r[4],
            "price_min": r[5], "price_max": r[6],
            # v0.76 安全修复(api-C1): 明文 key 不出服务层——只发指纹前 8 位
            "contributed_by_fp": token_fingerprint(str(r[7] or ""))[:8] if r[7] else "",
        })
```

（SELECT 列序以文件现状为准，`r[N]` 下标同步对齐；文件顶部补 `from services.tenant_service import token_fingerprint`——该函数已存在，勿新写。同时删掉 28/31 行 docstring 里「保留 contributed_by_token_id 贡献者列」的过时表述。）

`main.py` discovery_runs 段（2937-2957）：把行查询抽成模块级函数供测试 monkeypatch，返回 dict 删掉明文键：

```python
def _fetch_discovery_runs_rows(tenant: str, limit: int, offset: int):
    """discovery/runs 行查询封装（T1 抽出以便测试 monkeypatch）。SQL 主体从原内联处平移，零语义变更。"""
    ...  # 原 SELECT 平移进来，:bind 参数照旧


# 组装处（原 2953-2957）：
        "contributed_by_fp": token_fingerprint(str(r[5] or ""))[:8],
        # v0.76(api-C1): "contributed_by_token_id" 明文键已删除——读侧只发 fp
```

webui 三处：

```ts
// hooks.ts:601
contributed_by_token_id: string   // 删除此行，替换为：
contributed_by_fp: string
```

```tsx
// DiscoveryPanel.tsx:49
contributor: r.contributed_by_fp ?? "",
// DiscoveryPanel.tsx:166
<span>{r.contributed_by_fp ? r.contributed_by_fp + "…" : "—"}</span>
```

- [ ] **Step 4: 跑测试 + 存量回归**

```bash
cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_no_plaintext_contrib_key.py tests/test_analytics_endpoints.py -q
```

Expected: 全 PASS（存量 analytics 测试若断言了明文字段，改断言为 `contributed_by_fp`——这是本任务的有意行为变更）。

- [ ] **Step 5: 全仓 grep 兜底**（确认没有第三个回显点）

```bash
grep -rn "contributed_by_token_id" worker/src --include="*.py" | grep -v "INSERT\|写入\|row\[" 
```

Expected: 剩余出现点全部是**写侧**（analytics 上报归属、selection_insights、five-table 写入）与 SQL SELECT 内部列名，无任何进 HTTP 响应/日志的路径。发现即当场按同法修。

- [ ] **Step 6: gen_api_docs + webui 构建**

```bash
cd worker && PYTHONPATH=src ../skill/.venv314/bin/python scripts/gen_api_docs.py
cd ../webui && bunx tsc -b && bun run build
```

- [ ] **Step 7: Commit**

```bash
git add worker/src/services/analytics_service.py worker/src/main.py worker/tests/test_no_plaintext_contrib_key.py webui/src/api/hooks.ts webui/src/components/DiscoveryPanel.tsx docs/API-REFERENCE.md docs/api/openapi.json webui/src/imports/generated.d.ts webui/src/imports/openapi.json
git commit -m "fix(security): 全局读端点停发贡献者明文 key，只回 contributed_by_fp（api-C1 跨租户接管链收口）"
```

## Task 2: /run 系日志与 4xx/5xx detail 脱敏

**Files:**
- Modify: `worker/src/main.py:882,899,997,1017,1094-1097`（三端点日志 + 400 detail）、`main.py:966-973`（/run 500 detail 的 stack_trace）、`main.py:2124`（submit 500 `str(e)`）
- Test: `worker/tests/test_log_scrub_v076.py`（新建）

**Interfaces:**
- Produces: `_log_request_receipt(endpoint: str, run_id: str, request: Request, raw_body: bytes) -> None`（main.py 模块级）；400/500 detail 不再含 body 与 traceback。

- [ ] **Step 1: 写失败测试**

```python
# worker/tests/test_log_scrub_v076.py
"""v0.76 安全修复 T2：/run 系日志不落 body 原文；错误响应不回显 body/traceback（crypto-C1/api-M1）。"""
import logging
from fastapi.testclient import TestClient


def test_run_receipt_log_has_no_body(monkeypatch, caplog):
    from main import _log_request_receipt
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "stub", None, None)
    monkeypatch.setattr(logging.Logger, "handle", lambda self, r: None)
    captured = {}
    monkeypatch.setattr(logging.Logger, "info", lambda self, msg, *a, **k: captured.update(msg=msg))
    import asyncio
    class FakeReq:  # query_params 误打误撞只用于摘要
        query_params = {}
    _log_request_receipt("/run", "rid-1", FakeReq(), b'{"token":"sk-secret123","ozon_api_key":"AK-xyz"}')
    assert "sk-secret123" not in captured["msg"]
    assert "AK-xyz" not in captured["msg"]
    assert "body_bytes=49" in captured["msg"]


def test_invalid_json_detail_has_no_body_echo(client_factory=None):
    from main import app
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/run", content=b'{"token":"sk-leaky-key","bad":',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert "sk-leaky-key" not in r.text
    assert "Traceback" not in r.text
```

- [ ] **Step 2: 跑失败**（同 Task 1 命令模式，换文件名）Expected: FAIL。

- [ ] **Step 3: 实现**

main.py 新增模块级函数（放 `_task_status_guard` 附近）：

```python
def _log_request_receipt(endpoint: str, run_id: str, request: Request, raw_body: bytes) -> None:
    """T2(crypto-C1): /run 系请求回执日志——绝不落 body 原文（含 token/ozon_api_key），
    只落端点/run_id/query 键名列表/字节数。"""
    try:
        qkeys = ",".join(sorted(request.query_params.keys())) if request.query_params else "-"
    except Exception:
        qkeys = "-"
    logger.info("Received request for %s: run_id=%s query_keys=%s body_bytes=%d",
                endpoint, run_id, qkeys, len(raw_body))
```

三处调用点（885-900、1000-1018、1085-1097 区域）把：

```python
logger.info(f"Received request for /run: run_id={run_id}, "
            f"query={dict(request.query_params)}, body={body_text}")
```

替换为 `_log_request_receipt("/run", run_id, request, raw_body)`（/stream_run、/node_run 同法，endpoint 字符串各自替换）。

两处 400（882、997）：`detail=f"Invalid JSON format: {body_text}, traceback: ..."` →

```python
            logger.warning("Invalid JSON body on %s: %s", endpoint_label, traceback.format_exc()[-500:])
            raise HTTPException(status_code=400, detail="Invalid JSON format")
```

/run 500 detail（966-973）：响应 dict 删 `"stack_trace": extract_core_stack()` 键，改为 `logger.error("run failed stack: %s", extract_core_stack())`。submit 500（2124）：`detail=f"Failed to submit task: {str(e)}"` → `detail="Failed to submit task"` + `logger.exception(e)`。

- [ ] **Step 4: 跑过 + Commit**

```bash
cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_log_scrub_v076.py -q
git add worker/src/main.py worker/tests/test_log_scrub_v076.py
git commit -m "fix(security): /run 系回执日志脱敏 + 错误响应不回显 body/traceback（crypto-C1/api-M1）"
```

## Task 3: task_status 出口剥离 payload 凭证

**Files:**
- Modify: `worker/src/main.py`（`_task_status_guard` 上方新增 `_redact_payload`；在 2162-2213 任务状态返回组装处应用）
- Test: `worker/tests/test_task_status_redact_v076.py`（新建）

**Interfaces:**
- Produces: `_redact_payload(payload: dict) -> dict`——递归把键名 ∈ `{"token","ozon_api_key","api_key","secret","password","client_secret"}` 的字符串值替换为 `"[REDACTED]"`。REST 与 MCP（回调同 REST）自动同源生效。

- [ ] **Step 1: 写失败测试**

```python
# worker/tests/test_task_status_redact_v076.py
"""v0.76 T3(crypto-H1): task_status 响应 payload 内 token/ozon_api_key 必须脱敏。"""
from main import _redact_payload


def test_redact_top_and_nested():
    p = {"token": "sk-live-abc", "ozon_api_key": "AK-1", "draft": {"title": "t", "token": "sk-nested"},
         "items": [{"api_key": "K", "keep": 1}], "count": 3}
    out = _redact_payload(p)
    assert out["token"] == "[REDACTED]" and out["ozon_api_key"] == "[REDACTED]"
    assert out["draft"]["token"] == "[REDACTED]" and out["draft"]["title"] == "t"
    assert out["items"][0]["api_key"] == "[REDACTED]" and out["items"][0]["keep"] == 1
    assert out["count"] == 3
    assert p["token"] == "sk-live-abc"   # 不改入参（深拷贝语义）


def test_task_status_http_response_redacted(monkeypatch):
    from fastapi.testclient import TestClient
    from main import app
    fake = {"task_id": "t1", "status": "completed", "tenant_id": "999",
            "payload": {"token": "sk-secret-xyz", "envelope": {"ozon_api_key": "AK-9", "draft": {"title": "x"}}}}
    monkeypatch.setenv("TASK_STATUS_AUTH", "0")   # 应急门：本测试只验脱敏不验鉴权
    monkeypatch.setattr("main.task_processor.get_task_status", lambda tid: fake)
    r = TestClient(app).get("/api/v1/task_status/t1", headers={"Authorization": "Bearer sk-probe"})
    assert r.status_code == 200
    assert "sk-secret-xyz" not in r.text and "AK-9" not in r.text
    assert r.json()["payload"]["draft"]["title"] == "x"   # 业务数据保留
```

（若 `get_task_status` 实际签名带默认参数，monkeypatch 以真实签名为准——实现 Step 先读 `main.py:2180-2213` 现状再定 stub 形态。）

- [ ] **Step 2: 跑失败**。Expected: FAIL（`_redact_payload` 不存在；响应含明文）。

- [ ] **Step 3: 实现**

```python
_PAYLOAD_SECRET_KEYS = frozenset({"token", "ozon_api_key", "api_key", "secret", "password", "client_secret"})


def _redact_payload(payload):
    """T3(crypto-H1): task_status 出口对 payload 做键名级凭证脱敏（深拷贝，不改原 dict）。
    覆盖顶层与任意嵌套 dict/list；非字符串值不动。REST/MCP 同源生效。"""
    import copy
    def walk(obj):
        if isinstance(obj, dict):
            return {k: ("[REDACTED]" if str(k).lower() in _PAYLOAD_SECRET_KEYS and isinstance(v, str) else walk(v))
                    for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        return obj
    return walk(copy.deepcopy(payload))
```

在 task_status 组装最终 response dict 处（`return task_status` 前）：`task_status["payload"] = _redact_payload(task_status.get("payload")) if isinstance(task_status.get("payload"), dict) else task_status.get("payload")`。

- [ ] **Step 4: 跑过 + 存量 task_status 测试回归 + Commit**

```bash
cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_task_status_redact_v076.py tests/test_task_status_auth.py -q
git add worker/src/main.py worker/tests/test_task_status_redact_v076.py
git commit -m "fix(security): task_status 出口递归脱敏 payload 凭证字段（crypto-H1）"
```

## Task 4: Sentry 关闭局部变量捕获

**Files:**
- Modify: `worker/src/utils/sentry_setup.py:255-261`（init kwargs）
- Test: `worker/tests/test_sentry_no_locals_v076.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
# worker/tests/test_sentry_no_locals_v076.py
"""v0.76 T4(crypto-H3): Sentry init 必须 include_local_variables=False——防 token/api_key 随栈帧上报。"""
import utils.sentry_setup as ss


def test_init_disables_local_variables(monkeypatch):
    captured = {}
    monkeypatch.setattr("sentry_sdk.init", lambda **kw: captured.update(kw))
    monkeypatch.setenv("SENTRY_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0")
    assert ss.init_sentry() is True
    assert captured.get("include_local_variables") is False
```

- [ ] **Step 2: 跑失败**。Expected: FAIL（kwargs 无该键）。

- [ ] **Step 3: 实现**：`sentry_sdk.init(` 调用（255 行）kwargs 增加一行 `include_local_variables=False,`，并在 `before_send=_before_send,` 上方加注释 `# T4(crypto-H3): 栈帧局部变量含 payload token/api_key，一律不上报`。

- [ ] **Step 4: 跑过 + Commit**

```bash
cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_sentry_no_locals_v076.py -q
git add worker/src/utils/sentry_setup.py worker/tests/test_sentry_no_locals_v076.py
git commit -m "fix(security): Sentry 关闭栈帧局部变量捕获，防凭证随异常上报（crypto-H3）"
```

## Task 5: auth_node Api-Key 日志改尾 4 位掩码

**Files:**
- Modify: `worker/src/graphs/nodes/auth_node.py:81`（及 88/95 行同函数内完整响应结构打印收敛）
- Test: `worker/tests/test_auth_node_mask_v076.py`（新建）

**Interfaces:**
- Produces: `mask_api_key(key: str) -> str`——返回 `***` + 尾 4 位（key 长度 <4 时全掩码 `***`）。纯函数放 auth_node 模块级，日志点统一走它。

- [ ] **Step 1: 写失败测试**

```python
# worker/tests/test_auth_node_mask_v076.py
"""v0.76 T5(crypto-L1): auth_node 日志只出 Api-Key 尾 4 位掩码。"""
import logging
from graphs.nodes import auth_node as an


def test_mask_api_key_shape():
    assert an.mask_api_key("AKIAIOSFODNN7EXAMPLE") == "***MPLE"
    assert an.mask_api_key("abc") == "***"
    assert an.mask_api_key("") == "***"


def test_log_line_never_contains_key_prefix(caplog):
    key = "AKIAIOSFODNN7EXAMPLE"
    caplog.set_level(logging.INFO)
    an.logger.info("鉴权校验: Api-Key=%s", an.mask_api_key(key))
    assert key[:10] not in caplog.text
    assert "***MPLE" in caplog.text
```

- [ ] **Step 2: 跑失败**（`mask_api_key` 不存在）。

- [ ] **Step 3: 实现**：auth_node 模块级新增：

```python
def mask_api_key(key: str) -> str:
    """T5(crypto-L1): 日志掩码——只出尾 4 位；过短全掩码。"""
    return f"***{key[-4:]}" if key and len(key) >= 4 else "***"
```

81 行 `Api-Key={ozon_api_key[:10]}...` → `Api-Key={mask_api_key(ozon_api_key)}`。先读 70-100 行现状：88/95 行若打印完整响应 dict，改为只打印 `sorted(resp.keys())`（结构不打印值）。

- [ ] **Step 4: 跑过 + Commit**

```bash
git add worker/src/graphs/nodes/auth_node.py worker/tests/test_auth_node_mask_v076.py
git commit -m "fix(security): auth_node Api-Key 日志改尾4位掩码，收敛响应结构打印（crypto-L1）"
```

---

# PR-2 鉴权矩阵补齐（api-H2/H3/M2/M3/M4 + race-H1 + api-H1）

## Task 6: cancel_task 补 Bearer + 租户校验

**Files:**
- Modify: `worker/src/main.py:2238-2279`（旧路径）+ `main.py:2563` 附近（v1 别名透传处）
- Modify: `worker/src/utils/task_processor.py`（新增 `fetch_task_owner`）
- Test: `worker/tests/test_cancel_auth_v076.py`（新建）

**Interfaces:**
- Consumes: `_task_status_guard(request, task_row)`（main.py:2127，现成——TASK_STATUS_AUTH 应急门/401/404 语义全复用）。
- Produces: `task_processor.fetch_task_owner(task_id: str) -> Optional[dict]`——返回 `{"tenant_id": ..., "status": ...}` 或 None。

- [ ] **Step 1: 写失败测试**

```python
# worker/tests/test_cancel_auth_v076.py
"""v0.76 T6(api-H2): cancel_task 必须鉴权+租户校验。修复前：匿名 200 跨租户取消。"""
import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture()
def client(monkeypatch):
    c = TestClient(app, raise_server_exceptions=False)
    return c


def test_cancel_without_token_401(monkeypatch):
    monkeypatch.setattr("main.task_processor.fetch_task_owner", lambda tid: {"tenant_id": "9", "status": "pending"})
    r = client.post("/api/v1/cancel_task/tid-1")
    assert r.status_code == 401
    assert r.json()["detail"] == "Token is required"


def test_cancel_cross_tenant_404(monkeypatch):
    monkeypatch.setattr("main.task_processor.fetch_task_owner", lambda tid: {"tenant_id": "victim-tenant", "status": "pending"})
    monkeypatch.setattr("main._verify_analytics_token", lambda t: None)
    monkeypatch.setattr("services.tenant_service.resolve_tenant", lambda tok: "attacker-tenant")
    r = client.post("/api/v1/cancel_task/tid-1", headers={"Authorization": "Bearer sk-attacker"})
    assert r.status_code == 404


def test_cancel_owner_ok(monkeypatch):
    monkeypatch.setattr("main.task_processor.fetch_task_owner", lambda tid: {"tenant_id": "own-tenant", "status": "pending"})
    monkeypatch.setattr("main._verify_analytics_token", lambda t: None)
    monkeypatch.setattr("services.tenant_service.resolve_tenant", lambda tok: "own-tenant")
    monkeypatch.setattr("main.task_processor.cancel_task", lambda tid: True)
    r = client.post("/api/v1/cancel_task/tid-1", headers={"Authorization": "Bearer sk-owner"})
    assert r.status_code == 200


def test_cancel_missing_row_404(monkeypatch):
    monkeypatch.setattr("main.task_processor.fetch_task_owner", lambda tid: None)
    r = client.post("/api/v1/cancel_task/tid-404")
    assert r.status_code == 404
```

- [ ] **Step 2: 跑失败**。Expected: 前 3 用例 FAIL（现返回 200）。

- [ ] **Step 3: 实现**

`task_processor.py` 新增（放 `cancel_task` 旁）：

```python
async def fetch_task_owner(task_id: str) -> Optional[dict]:
    """T6(api-H2): 取任务归属供取消前鉴权（tenant_id + status）。不存在 → None。"""
    with get_session() as sess:  # 以文件内现有 session 获取方式为准（get_session/engine.begin 同步）
        row = sess.execute(
            text("SELECT tenant_id, status FROM ozon_product_tasks WHERE id = CAST(:task_id AS uuid)"),
            {"task_id": task_id},
        ).mappings().first()
    return dict(row) if row else None
```

> ⚠️ id 列类型以 `storage/database/shared/model.py` 实际定义为准（若是 text 主键去掉 CAST；uuid 型非法 task_id 要 catch `DataError` 返回 None——先读后写）。

`main.py` 2249：

```python
@app.post("/cancel_task/{task_id}", responses={...})
async def http_cancel_task(task_id: str, request: Request):
    """T6(api-H2): 补 Bearer+租户校验（语义与 task_status 同源：TASK_STATUS_AUTH=0 应急关、
    跨租户 404 不泄漏存在性、老数据无租户宽容）。"""
    task_row = await task_processor.fetch_task_owner(task_id)
    if not task_row:
        raise HTTPException(status_code=404, detail="task not found")
    _task_status_guard(request, task_row)
    success = await task_processor.cancel_task(task_id)
    ...  # 原返回逻辑不动
```

- [ ] **Step 4: 跑过 + Commit**

```bash
cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_cancel_auth_v076.py -q
git add worker/src/main.py worker/src/utils/task_processor.py worker/tests/test_cancel_auth_v076.py
git commit -m "fix(security): cancel_task 补 Bearer+租户校验，跨租户 404（api-H2）"
```

## Task 7: task_statistics 鉴权 + 租户强制

**Files:**
- Modify: `worker/src/main.py:2386-2419`（http_task_statistics）
- Test: `worker/tests/test_task_statistics_auth_v076.py`（新建）

**行为定义（保持 MCP 工具兼容）**：Bearer 必填；query `tenant_id` 缺省=查自己；`tenant_id` ≠ 自己 → 仅 admin（`resolve_analytics_scope(token)["is_admin"]`）放行，否则 403。

- [ ] **Step 1: 写失败测试**

```python
# worker/tests/test_task_statistics_auth_v076.py
"""v0.76 T7(api-H3): task_statistics 鉴权 + 非 admin 不可查他人租户。"""
import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr("main._verify_analytics_token", lambda t: None)
    monkeypatch.setattr("services.tenant_service.resolve_tenant", lambda tok: "my-tenant")
    monkeypatch.setattr("main.task_processor.get_task_statistics", lambda tenant: {"total": 1})
    return TestClient(app)


def test_no_token_401(monkeypatch):
    r = TestClient(app).get("/api/v1/task_statistics")
    assert r.status_code == 401


def test_own_tenant_ok(client):
    r = client.get("/api/v1/task_statistics", headers={"Authorization": "Bearer sk-me"})
    assert r.status_code == 200


def test_other_tenant_forced_to_own_for_non_admin(client, monkeypatch):
    import main as m
    seen = {}
    monkeypatch.setattr(m.task_processor, "get_task_statistics", lambda tenant: seen.update(t=tenant) or {"total": 1})
    r = client.get("/api/v1/task_statistics?tenant_id=victim", headers={"Authorization": "Bearer sk-me"})
    assert r.status_code in (200, 403)
    if r.status_code == 200:
        assert seen["t"] == "my-tenant"     # 静默回落自己租户（不回显他人数据）


def test_admin_can_query_other(client, monkeypatch):
    monkeypatch.setattr("main.resolve_analytics_scope", lambda tok: {"tenant_id": "my-tenant", "is_admin": True})
    seen = {}
    monkeypatch.setattr("main.task_processor.get_task_statistics", lambda tenant: seen.update(t=tenant) or {"total": 1})
    r = client.get("/api/v1/task_statistics?tenant_id=victim", headers={"Authorization": "Bearer sk-admin"})
    assert r.status_code == 200 and seen["t"] == "victim"
```

- [ ] **Step 2: 跑失败**。Expected: `test_no_token_401` FAIL（现 200）。

- [ ] **Step 3: 实现**（2394 函数体）：

```python
async def http_task_statistics(request: Request):
    # T7(api-H3): Bearer 必填；tenant_id 参数仅 admin 可跨租户，其余恒查自己
    _require_bearer(request)                      # Task 8 引入的共享 helper（本任务先内联，Task 8 合并收敛亦可）
    token = _extract_bearer_token(request)
    own_tenant = resolve_tenant(token)
    q_tenant = request.query_params.get("tenant_id") or ""
    tenant = own_tenant
    if q_tenant and q_tenant != own_tenant:
        scope = resolve_analytics_scope(token)
        if not scope.get("is_admin"):
            raise HTTPException(status_code=403, detail="admin only")
        tenant = q_tenant
    statistics = await task_processor.get_task_statistics(tenant)
```

- [ ] **Step 4: 跑过 + MCP 存量回归（`tests/test_mcp_server.py` 19 用例含 get_task_statistics 整形）+ Commit**

```bash
git add worker/src/main.py worker/tests/test_task_statistics_auth_v076.py
git commit -m "fix(security): task_statistics 补鉴权，非 admin 恒查自身租户（api-H3）"
```

## Task 8: 共享 `_require_bearer` + progress 端点接入

**Files:**
- Modify: `worker/src/main.py`（`_task_status_guard` 前新增 helper；`/progress/{run_id}` 1737-1775 接入）
- Test: `worker/tests/test_require_bearer_v076.py`（新建）

**Interfaces:**
- Produces: `_require_bearer(request: Request) -> str`（返回 clean token；缺 401；无效走 `_verify_analytics_token` 原样 401/503）。Task 7/9/10 复用（若 T7 已内联，本任务把 T7 的三行收敛为调用本 helper）。

- [ ] **Step 1: 写失败测试**

```python
# worker/tests/test_require_bearer_v076.py
"""v0.76 T8(api-M2): _require_bearer 共享 helper + /progress 鉴权。"""
import pytest
from fastapi.testclient import TestClient
from main import app, _require_bearer
from fastapi import HTTPException


def test_require_bearer_missing_401():
    class R:  headers = {}
    with pytest.raises(HTTPException) as e:
        _require_bearer(R())
    assert e.value.status_code == 401


def test_require_bearer_strips_sk(monkeypatch):
    monkeypatch.setattr("main._verify_analytics_token", lambda t: None)
    class R:  headers = {"Authorization": "Bearer sk-abc123"}
    assert _require_bearer(R()) == "abc123"


def test_progress_requires_token(monkeypatch):
    monkeypatch.setattr("main._verify_analytics_token", lambda t: None)
    c = TestClient(app)
    r = c.get("/progress/run-xyz")
    assert r.status_code == 401
```

- [ ] **Step 2: 跑失败**。

- [ ] **Step 3: 实现**

```python
def _require_bearer(request: Request) -> str:
    """T8: Bearer 提取+有效性校验共享入口（progress/task_statistics/store-health/logistics-quote 用）。
    缺失 401 "Token is required"；有效性走 _verify_analytics_token（401/503 原样透传）。"""
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    clean = token.replace("sk-", "", 1) if token.startswith("sk-") else token
    _verify_analytics_token(clean)
    return clean
```

`/progress/{run_id}` 函数体首行加 `_ = _require_bearer(request)`（需给函数签名补 `request: Request` 参数）。

- [ ] **Step 4: 跑过 + Commit**

```bash
git add worker/src/main.py worker/tests/test_require_bearer_v076.py
git commit -m "fix(security): 新增 _require_bearer 共享鉴权并接入 /progress（api-M2）"
```

## Task 9: store/health 鉴权 + 凭证支持 header 传递

**Files:**
- Modify: `worker/src/main.py:1427-1473`
- Test: `worker/tests/test_store_health_auth_v076.py`（新建）

**行为**：Bearer 必填；`client_id`/`api_key` 优先读 `X-Ozon-Client-Id`/`X-Ozon-Api-Key` header，缺省回落 query（向后兼容，注释标注 query 传凭证已弃用）；Ozon 错误只回状态码类别不回原文。

- [ ] **Step 1: 写失败测试**——三个用例：无 Bearer 401；header 传凭证时 `client.get("/api/v1/store/health").prepare()` 后服务端收到的 query 不含 api_key（用 monkeypatch 假 Ozon client 捕获入参）；Ozon 抛错时响应 detail 不含 Ozon 原文 message。

```python
# worker/tests/test_store_health_auth_v076.py
"""v0.76 T9(api-M3): store/health 鉴权 + 凭证 header 化 + 错误不回显原文。"""
import pytest
from fastapi.testclient import TestClient
from main import app


def test_no_bearer_401():
    r = TestClient(app).get("/api/v1/store/health?client_id=1&api_key=k")
    assert r.status_code == 401


def test_credentials_from_header(monkeypatch):
    monkeypatch.setattr("main._verify_analytics_token", lambda t: None)
    seen = {}
    def fake_check(client_id, api_key):
        seen.update(cid=client_id, ak=api_key)
        return {"healthy": True}
    monkeypatch.setattr("main._check_store_health", fake_check)   # 以真实内部函数名为准，实现时先读
    r = TestClient(app).get("/api/v1/store/health",
                            headers={"Authorization": "Bearer sk-x",
                                     "X-Ozon-Client-Id": "123", "X-Ozon-Api-Key": "AK-secret"})
    assert r.status_code == 200
    assert seen == {"cid": "123", "ak": "AK-secret"}


def test_ozon_error_not_echoed(monkeypatch):
    monkeypatch.setattr("main._verify_analytics_token", lambda t: None)
    def boom(cid, ak):
        raise RuntimeError("Ozon API error: Client-Id header value should be positive integer")
    monkeypatch.setattr("main._check_store_health", boom)
    r = TestClient(app).get("/api/v1/store/health",
                            headers={"Authorization": "Bearer sk-x",
                                     "X-Ozon-Client-Id": "1", "X-Ozon-Api-Key": "k"})
    assert r.status_code == 502
    assert "positive integer" not in r.text
```

- [ ] **Step 2: 跑失败 → Step 3: 实现**（先读 1427-1473 真实结构再套：

```python
    _require_bearer(request)
    client_id = request.headers.get("X-Ozon-Client-Id") or request.query_params.get("client_id") or ""
    api_key = request.headers.get("X-Ozon-Api-Key") or request.query_params.get("api_key") or ""
    # T9(api-M3): query 传凭证已弃用（header 优先）——反代/访问日志留痕面
    ...  # 此处原有健康检查逻辑保持不动，仅凭证取值来源替换为上行两行
    except Exception as exc:
        logger.warning("store/health upstream fail: %s", str(exc)[:120])
        raise HTTPException(status_code=502, detail="upstream store health check failed")
```

）**→ Step 4: 跑过 + Commit**

```bash
git add worker/src/main.py worker/tests/test_store_health_auth_v076.py
git commit -m "fix(security): store/health 补鉴权，凭证支持 header 传递且错误不回显 Ozon 原文（api-M3）"
```

## Task 10: logistics/quote token 必填 + 限流

**Files:**
- Modify: `worker/src/main.py:2470-2490`
- Test: `worker/tests/test_logistics_quote_auth_v076.py`（新建）

- [ ] **Step 1: 失败测试**：无 token POST `/api/v1/logistics/quote` → 401（现 200 返回费率）；带 token → 200；同 token 30 秒内 11 次 → 第 11 次 429。

- [ ] **Step 2: 跑失败 → Step 3: 实现**：

```python
    _require_bearer(request)            # T10(api-M4): 匿名拉费率面收口
    allowed, _ = rate_limiter.check(f"logistics:{clean_token}")
    if not allowed:
        raise HTTPException(status_code=429, detail="rate limited")
```

（限流键加前缀避免与提交限流额度互相挤占。）

- [ ] **Step 4: 跑过 + Commit** `fix(security): logistics/quote 补鉴权与限流（api-M4）`

## Task 11: submit_task 校验 token status

**Files:**
- Modify: `worker/src/services/tenant_service.py:75-96`（resolve_tenant）
- Test: `worker/tests/test_token_status_gate_v076.py`（新建）

**行为**：`tokens` 表行带 `status` 字段时，`status != 1` → 401 `"token_invalid or account_inactive"`（与 auth/verify 同文案）；行无该字段（schema 容错/本地 mock）→ 放行。注意 `_tenant_cache` 60s TTL——封禁最长 60s 延迟生效（docstring 写明，可接受）。

- [ ] **Step 1: 写失败测试**

```python
# worker/tests/test_token_status_gate_v076.py
"""v0.76 T11(race-H1): resolve_tenant 必须消费 tokens.status——封禁/过期 token 不得提交任务。"""
import pytest
from fastapi import HTTPException
import services.tenant_service as ts


class _Row(dict): pass


def _fake_supabase(row):
    class T:
        def select(self, cols): return self
        def eq(self, *a, **k): return self
        def is_(self, *a, **k): return self
        def limit(self, n): return self
        def execute(self):
            class R: data = [row] if row else []
            return R()
    class SB:
        def table(self, name): return T()
    return SB()


def test_status_disabled_rejected(monkeypatch):
    monkeypatch.setattr(ts, "get_supabase", lambda: _fake_supabase({"user_id": 7, "status": 2}))
    with pytest.raises(HTTPException) as e:
        ts.resolve_tenant("sk-banned")
    assert e.value.status_code == 401


def test_status_null_ok(monkeypatch):
    monkeypatch.setattr(ts, "get_supabase", lambda: _fake_supabase({"user_id": 7, "status": None}))
    assert ts.resolve_tenant("sk-ok") == "7"


def test_status_active_ok(monkeypatch):
    monkeypatch.setattr(ts, "get_supabase", lambda: _fake_supabase({"user_id": 7, "status": 1}))
    assert ts.resolve_tenant("sk-ok") == "7"
```

- [ ] **Step 2: 跑失败**。Expected: `test_status_disabled_rejected` FAIL（现返回 "7"）。

- [ ] **Step 3: 实现**：select 列改 `"user_id, status"`；行取出后：

```python
        row = rows.data[0]
        if not row.get("user_id"):
            raise HTTPException(status_code=401, detail="token_invalid or account_inactive")
        status = row.get("status")
        # T11(race-H1): status 语义与 auth/verify 对齐（1=active；None=schema 容错放行）。
        # 封禁时效受 _tenant_cache 60s TTL 影响属已知权衡。
        if status is not None and int(status) != 1:
            raise HTTPException(status_code=401, detail="token_invalid or account_inactive")
        user_id = str(row["user_id"])
```

同时删掉 `main.py:1558-1560` `_check_mxou_balance` 里 select 的 `status` 列（读而不用，避免下一个审计再报）。**存量测试**：`grep -rn "select(\"user_id\")" worker/tests | grep -i tenant` 找 mock 断言 select 参数的用例并同步。

- [ ] **Step 4: 跑过（含 `tests/test_tenant_guard_phase3.py` 8 用例）+ Commit**

```bash
git add worker/src/services/tenant_service.py worker/src/main.py worker/tests/test_token_status_gate_v076.py
git commit -m "fix(security): resolve_tenant 消费 tokens.status，封禁 token 不可提交任务（race-H1）"
```

## Task 12: /node_run 有状态节点黑名单

**Files:**
- Modify: `worker/src/main.py:1071-1080`（http_node_run 入口）
- Test: `worker/tests/test_node_run_blocklist_v076.py`（新建）

- [ ] **Step 1: 失败测试**

```python
# worker/tests/test_node_run_blocklist_v076.py
"""v0.76 T12(api-H1): /node_run 拒绝持久化副作用节点（全局学习表投毒面）。"""
import pytest
from fastapi.testclient import TestClient
from main import app


def test_learning_record_denied(monkeypatch):
    monkeypatch.setattr("main._authenticate_token", lambda t: None)
    r = TestClient(app).post("/node_run/learning_record",
                             json={"token": "sk-x"}, headers={"Authorization": "Bearer sk-x"})
    assert r.status_code == 403
    assert "stateful" in r.json()["detail"]


def test_pure_node_still_allowed(monkeypatch):
    monkeypatch.setattr("main._authenticate_token", lambda t: None)
    # 以图内纯计算节点名为准（读 graphs/graph.py 选一个无持久化的，如 pricing）
    r = TestClient(app).post("/node_run/pricing",
                             json={"token": "sk-x"}, headers={"Authorization": "Bearer sk-x"})
    assert r.status_code != 403
```

- [ ] **Step 2: 跑失败 → Step 3: 实现**（http_node_run 鉴权后、执行前）：

```python
# T12(api-H1): 有持久化副作用的节点禁止经 /node_run 触发——learning_record 会以
# 调用方可控的 moderation_status/user_id 写全局共享 category_mapping（W11），
# 属跨租户投毒面。新增有状态节点时必须同步维护本清单。
_NODE_RUN_DENIED = frozenset({"learning_record"})
...  # 此处为 http_node_run 原有鉴权/装载逻辑，保持不动
    if node_id in _NODE_RUN_DENIED:
        raise HTTPException(status_code=403, detail=f"node '{node_id}' is stateful and not runnable via /node_run")
```

实现时 grep `graphs/graph.py` 的 `add_node(` 全量名单，凡写库/写学习表/发外部请求的节点逐个评估并入清单（learning_record 确认在内）。

- [ ] **Step 4: 跑过 + Commit** `fix(security): /node_run 黑名单有状态节点，封学习表投毒面（api-H1）`

---

# PR-3 SSRF 统一收敛（inj-C1/H1/H2/I1）

## Task 13: 新建 `utils/secure_fetch.py`（唯一安全抓取入口）

**Files:**
- Create: `worker/src/utils/secure_fetch.py`
- Test: `worker/tests/test_secure_fetch_v076.py`（新建）

**Interfaces:**
- Produces:
  - `UnsafeUrlError(ValueError)`
  - `assert_safe_remote_url(url: str, allowed_host_suffixes: Optional[Iterable[str]] = None) -> None`
  - `safe_fetch(url: str, method: str = "get", timeout: float = 10.0, max_redirects: int = 3, allowed_host_suffixes: Optional[Iterable[str]] = None, **kwargs) -> requests.Response`

- [ ] **Step 1: 写失败测试**

```python
# worker/tests/test_secure_fetch_v076.py
"""v0.76 T13(inj-C1): SSRF 防线核心——解析 IP 校验 + 域名精确匹配 + 逐跳重定向复核。"""
import pytest
from utils.secure_fetch import assert_safe_remote_url, UnsafeUrlError


def _fake_dns(monkeypatch, mapping):
    import utils.secure_fetch as sf
    monkeypatch.setattr(sf.socket, "getaddrinfo",
                        lambda host, port, *a, **k: [(2, 1, 6, "", (mapping[host], port))] if host in mapping
                        else [(2, 1, 6, "", ("93.184.216.34", port))])


def test_loopback_blocked():
    with pytest.raises(UnsafeUrlError):
        assert_safe_remote_url("http://127.0.0.1:8080/x")


def test_metadata_ip_blocked(monkeypatch):
    _fake_dns(monkeypatch, {"evil.example": "169.254.169.254"})
    with pytest.raises(UnsafeUrlError):
        assert_safe_remote_url("http://evil.example/latest/meta-data/")


def test_private_v4_and_v6_blocked(monkeypatch):
    _fake_dns(monkeypatch, {"a.example": "10.1.2.3", "b.example": "fd00::1"})
    for u in ("http://a.example/x", "http://[fd00::1]/x"):
        with pytest.raises(UnsafeUrlError):
            assert_safe_remote_url(u)


def test_scheme_restricted():
    with pytest.raises(UnsafeUrlError):
        assert_safe_remote_url("file:///etc/passwd")


def test_allowlist_exact_host_not_substring(monkeypatch):
    # 域名白名单按 hostname 精确/后缀匹配——query 垫片失效（inj-H1 根因）
    with pytest.raises(UnsafeUrlError):
        assert_safe_remote_url("http://127.0.0.1:8080/admin?pad=alicdn.com/img/x.jpg",
                               allowed_host_suffixes=("alicdn.com",))
    _fake_dns(monkeypatch, {"img.alicdn.com": "93.184.216.34"})
    assert_safe_remote_url("https://img.alicdn.com/img/x.jpg",
                           allowed_host_suffixes=("alicdn.com",))   # 子域后缀匹配通过


def test_redirect_hop_revalidated(monkeypatch):
    import utils.secure_fetch as sf
    _fake_dns(monkeypatch, {"jump.example": "93.184.216.34"})
    class FakeResp:
        status_code = 302
        headers = {"Location": "http://169.254.169.254/latest/meta-data/"}
    monkeypatch.setattr(sf.requests, "request", lambda *a, **k: FakeResp())
    with pytest.raises(UnsafeUrlError):
        sf.safe_fetch("http://jump.example/start")


def test_public_ip_ok(monkeypatch):
    _fake_dns(monkeypatch, {"ok.example": "93.184.216.34"})
    assert_safe_remote_url("http://ok.example/x")
```

- [ ] **Step 2: 跑失败**（模块不存在）。

- [ ] **Step 3: 实现**

```python
# worker/src/utils/secure_fetch.py
# Engine: worker | Python 3.12+ | 语言: Python
# Run: 由 worker 内图片抓取/探测链路调用（T14-T16 接线）
# Deps: requests（worker 现有依赖）
"""SSRF 防线唯一入口（v0.76 security-remediation T13，审计 inj-C1/H1/H2）。

三层防线：
1. scheme 白名单 http/https；hostname 必须存在。
2. getaddrinfo 解析 hostname 的**全部**地址，任一命中保留/内网/链路本地段即拒
   （防 DNS rebinding 的解析侧；TOCTOU 残余风险接受——worker 无凭据型内网服务可达性差异）。
3. 可选 allowed_host_suffixes：hostname 精确或「.suffix」后缀匹配（绝不做子串 in，
   那会被 `?pad=alicdn.com` 垫片绕过——审计实证）。

safe_fetch 手动跟随重定向（allow_redirects=False），每跳重新过 1-3 全套校验。
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Iterable, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16",
    "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",
    "::1/128", "fc00::/7", "fe80::/10", "::ffff:0:0/96",
))


class UnsafeUrlError(ValueError):
    """URL 未通过安全校验（内网地址/非法 scheme/白名单外域名）。"""


def _ip_is_blocked(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True  # 解析不出合法 IP = 不放行
    return any(ip in net for net in _BLOCKED_NETS) or ip.is_reserved or ip.is_multicast


def _host_is_safe(host: str) -> bool:
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    return not any(_ip_is_blocked(info[4][0]) for info in infos)


def _host_in_allowlist(hostname: str, suffixes: Iterable[str]) -> bool:
    h = hostname.lower()
    for s in suffixes:
        s = s.lower().lstrip(".")
        if h == s or h.endswith("." + s):
            return True
    return False


def assert_safe_remote_url(url: str, allowed_host_suffixes: Optional[Iterable[str]] = None) -> None:
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"scheme not allowed: {parsed.scheme!r}")
    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("missing hostname")
    if allowed_host_suffixes is not None and not _host_in_allowlist(hostname, allowed_host_suffixes):
        raise UnsafeUrlError(f"host not in allowlist: {hostname}")
    if hostname in ("localhost",) or hostname.endswith(".localhost"):
        raise UnsafeUrlError("localhost blocked")
    if not _host_is_safe(hostname):
        raise UnsafeUrlError(f"host resolves to blocked address: {hostname}")


def safe_fetch(url: str, method: str = "get", timeout: float = 10.0,
               max_redirects: int = 3, allowed_host_suffixes: Optional[Iterable[str]] = None,
               **kwargs) -> requests.Response:
    """安全抓取：每跳（含重定向 Location）重新过全套校验。"""
    current = str(url).strip()
    for hop in range(max_redirects + 1):
        assert_safe_remote_url(current, allowed_host_suffixes=allowed_host_suffixes)
        resp = requests.request(method.upper(), current, timeout=timeout,
                                allow_redirects=False, **kwargs)
        if resp.is_redirect or resp.is_permanent_redirect:
            loc = resp.headers.get("Location", "")
            if not loc:
                return resp
            from urllib.parse import urljoin
            current = urljoin(current, loc)
            continue
        return resp
    raise UnsafeUrlError(f"too many redirects (> {max_redirects})")
```

- [ ] **Step 4: 跑过 + Commit**

```bash
git add worker/src/utils/secure_fetch.py worker/tests/test_secure_fetch_v076.py
git commit -m "feat(security): 新增 utils/secure_fetch SSRF 防线唯一入口（解析IP校验+域名精确匹配+逐跳复核）"
```

## Task 14: 草稿镜像链接入 safe_fetch（inj-C1 主体）

**Files:**
- Modify: `worker/src/services/draft_image_mirror.py:40-57`（`_mirror_one` 的 `requests.get` → `safe_fetch`）
- Test: `worker/tests/test_mirror_ssrf_v076.py`（新建）

- [ ] **Step 1: 写失败测试**（复刻审计探针 1 形态：本地 http.server 起在 127.0.0.1 随机端口，`_mirror_one` 应返回 None 且 server 收不到请求——改为不真起 server，直接断言抛 UnsafeUrlError 被 _mirror_one 吞掉返回 None）

```python
# worker/tests/test_mirror_ssrf_v076.py
"""v0.76 T14(inj-C1): 草稿镜像链内网 URL 必须被拒——不发起任何请求。"""
from services import draft_image_mirror as dim


def test_mirror_rejects_loopback(monkeypatch):
    called = {"n": 0}
    import services.draft_image_mirror as m
    real_request = m.requests.get if hasattr(m, "requests") else None
    # safe_fetch 内部才发请求；此处断言 requests 层零触达
    import utils.secure_fetch as sf
    monkeypatch.setattr(sf.requests, "request",
                        lambda *a, **k: called.update(n=called["n"] + 1) or (_ for _ in ()).throw(AssertionError("network touched")))
    assert dim._mirror_one("http://127.0.0.1:18477/ssrf-poc") is None
    assert called["n"] == 0


def test_mirror_allows_public(monkeypatch):
    from services import draft_image_mirror as m
    class FakeResp:
        status_code = 200
        content = b"img"
    monkeypatch.setattr("utils.secure_fetch.requests.request", lambda *a, **k: FakeResp())
    monkeypatch.setattr(m, "cos_upload_bytes", lambda data, key, content_type=None: f"https://cos/{key}")
    out = m._mirror_one("https://img.example.com/a.jpg")
    assert out and out.startswith("https://cos/draft-images/")
```

- [ ] **Step 2: 跑失败 → Step 3: 实现**：`_mirror_one` 内 `resp = requests.get(url.strip(), timeout=DOWNLOAD_TIMEOUT, headers=headers)` 替换为：

```python
        from utils.secure_fetch import safe_fetch
        resp = safe_fetch(url.strip(), timeout=DOWNLOAD_TIMEOUT, headers=headers)
```

文件顶部 import requests 保留（except 分支用）。`except Exception` 分支已有，UnsafeUrlError 是 ValueError 子类自然被吞并 warn——把 warn 文案带上 `type(exc).__name__` 便于排查。

- [ ] **Step 4: 跑过 + 存量 drafts 测试回归（`tests/` 里 grep "mirror" 相关文件一并跑）+ Commit**

```bash
git add worker/src/services/draft_image_mirror.py worker/tests/test_mirror_ssrf_v076.py
git commit -m "fix(security): 草稿镜像链接入 safe_fetch，封全读 SSRF+COS 外带链（inj-C1）"
```

## Task 15: image_url_guard 白名单精确化（inj-H1）

**Files:**
- Modify: `worker/src/utils/image_url_guard.py:43-48`
- Test: `worker/tests/test_image_url_guard_v076.py`（新建）

- [ ] **Step 1: 失败测试**

```python
# worker/tests/test_image_url_guard_v076.py
"""v0.76 T15(inj-H1): 图床白名单按 hostname 精确/后缀匹配——query 垫片失效。"""
from utils.image_url_guard import is_product_image_candidate


def test_query_pad_bypass_blocked():
    assert not is_product_image_candidate("http://127.0.0.1:8080/admin?pad=alicdn.com/img/ibank/x.jpg")
    assert not is_product_image_candidate("http://evil.example/pad=1688.com/x.jpg")


def test_real_bed_domains_pass():
    assert is_product_image_candidate("https://img.alicdn.com/imgextra/x.jpg")
    assert is_product_image_candidate("https://cbu01.alicdn.com/img/ibank/y.jpg")
    assert is_product_image_candidate("https://img.1688.com/kf/z.jpg")


def test_lookalike_domain_blocked():
    assert not is_product_image_candidate("https://alicdn.com.evil.example/x.jpg")
    assert not is_product_image_candidate("https://notalicdn.com/x.jpg")
```

- [ ] **Step 2: 跑失败 → Step 3: 实现**（43-48 的 `any(dom in lowered ...)` 替换为 hostname 匹配；其余逻辑——webp 拒绝、`_THUMBNAIL_PATTERN`——保留在 lowered 上不变）：

```python
    # T15(inj-H1): 白名单按 hostname 精确/后缀匹配——子串 in 会被 query 垫片绕过（审计实证）
    from urllib.parse import urlparse
    try:
        host = (urlparse(lowered).hostname or "").lower()
    except ValueError:
        return False
    if not any(host == d or host.endswith("." + d) for d in (
        "alicdn.com", "1688.com",
        "taobaocdn.com",
        "pddpic.com", "yangkeduo.com", "pinduoduo.com",
    )):
        return False
```

- [ ] **Step 4: 跑过（同文件已有存量用例必须全绿）+ Commit** `fix(security): image_url_guard 白名单改 hostname 精确匹配（inj-H1）`

## Task 16: 其余图片探测点接入 safe_fetch（inj-H2）

**Files:**
- Modify: `worker/src/graphs/nodes/ozon_validate_node.py:577`（`requests.head` → safe_fetch(method="head")）
- Modify: `worker/src/utils/image_quality_evaluator.py:18-25`（`check_url_alive` GET → safe_fetch）
- Test: `worker/tests/test_probe_points_ssrf_v076.py`（新建）

- [ ] **Step 1: 失败测试**：monkeypatch `utils.secure_fetch.requests.request` 为计数器，分别调 `check_url_alive("http://127.0.0.1:1/x")` 与 validate 节点的探测 helper（先读 577 行所在函数签名定测试入口）——断言返回 False/跳过且 requests 零调用。

- [ ] **Step 2: 跑失败 → Step 3: 实现**（两处 `requests.head/get(...)` → `safe_fetch(url, method="head"/"get", timeout=...)`；探测语义「失败=False」保持，UnsafeUrlError 自然落入既有 except）。

- [ ] **Step 4: 全仓 grep 兜底 + 跑过 + Commit**

```bash
grep -rn "requests.get\|requests.head\|requests.request" worker/src --include="*.py" | grep -v secure_fetch | grep -v "ozon_client\|mxou_api\|supabase\|httpx"
```

Expected: 剩余命中逐个核对——凡 **URL 来自用户输入**（信封/草稿/请求参数）的 fetch 必须已接 safe_fetch；固定上游（Ozon seller API、MXOU、api.mxou.cn）不需要。核对结论写进 commit message。

```bash
git add worker/src/graphs/nodes/ozon_validate_node.py worker/src/utils/image_quality_evaluator.py worker/tests/test_probe_points_ssrf_v076.py
git commit -m "fix(security): validate/图片质量探测点接入 safe_fetch，内网探测面收口（inj-H2）"
```

## Task 17: 删除死代码 `utils/file/file.py`

**Files:**
- Delete: `worker/src/utils/file/file.py`（审计确认全仓零 import）
- Test: 无新测试；以 grep + 全量编译性冒烟为证

- [ ] **Step 1: 复核零引用**

```bash
grep -rn "utils.file\|from .file\|utils/file" worker/src worker/tests --include="*.py" | grep -v "utils/files\|logistics"
```

Expected: 零命中（仅 `__init__.py` 可能有的包声明——一并清理）。

- [ ] **Step 2: 删除 + Commit**

```bash
git rm worker/src/utils/file/file.py
git add -u worker/src/utils/file/
git commit -m "chore(security): 删除死代码 utils/file/file.py（含任意路径读/下载原语，inj-I1）"
```

---

# PR-4 本地/部署面（cicd-H1/M3/M6 + race-M4/M5 + cicd-M2）

## Task 18: 8902 tasks_server 鉴权 + CORS 收敛 + params 白名单

**Files:**
- Modify: `pounding-mcp/pounding_mcp/tasks_server.py:32-37,128-179`、`pounding-mcp/pounding_mcp/skill_runner.py:88-101`
- Test: `pounding-mcp/tests/test_tasks_server_auth.py`（新建，跑在 pounding-mcp 自身 venv）

**行为定义**：
- 启动时读 env `POUNDING_TASKS_TOKEN`；未设 → `secrets.token_urlsafe(24)` 生成并往 stderr 打一行 `TASKS_TOKEN=<t>`（harness/用户从输出取）。
- 所有 do_GET/do_POST（OPTIONS 与 `/health` 除外）校验 `Authorization: Bearer <t>`，否则 401 JSON。
- CORS：删除 `Access-Control-Allow-Origin: *` 整组头（同机消费方走非浏览器通道；浏览器侧属 harness 改造，登记联动项）。
- params 白名单：`skill_runner._build_argv` 只接受 `COLLECT_KINDS[kind]` 声明过的参数键，未知键丢弃并 stderr 提示。
- `_read_body` 拒绝 `Content-Length > 1_000_000`。

- [ ] **Step 1: 写失败测试**（http.client 打真实 server 或抽 `_check_auth`/`_filter_params` 纯函数测；推荐纯函数化）

```python
# pounding-mcp/tests/test_tasks_server_auth.py
"""v0.76 T18(cicd-H1): 8902 网关鉴权 + 参数白名单 + body 上限。"""
import pytest
import tasks_server  # 以包实际 import 路径为准（pounding_mcp.tasks_server）


def test_auth_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(tasks_server, "_TASKS_TOKEN", "right-token")
    assert tasks_server._check_auth({}) is False
    assert tasks_server._check_auth({"Authorization": "Bearer wrong"}) is False
    assert tasks_server._check_auth({"Authorization": "Bearer right-token"}) is True


def test_params_whitelist_drops_unknown(monkeypatch):
    allow = {"url", "target_count", "auto_submit"}
    out = tasks_server._filter_params({"url": "u", "auto_submit": True, "evil_flag": "x"}, allow)
    assert out == {"url": "u", "auto_submit": True}


def test_body_size_cap():
    assert tasks_server._body_size_ok(1_000_001) is False
    assert tasks_server._body_size_ok(999) is True
```

- [ ] **Step 2: 跑失败**（`cd pounding-mcp && .venv/bin/python -m pytest tests/test_tasks_server_auth.py -q`）。

- [ ] **Step 3: 实现**（按行为定义逐条落；`_build_argv` 白名单来源读 `tasks.py` 的 COLLECT_KINDS 定义，若当前无每-kind 参数声明，则建模块级 `ALLOWED_PARAM_KEYS: dict[str, set]`，键与 tasks.py 各 kind 实际消费的 CLI flag 一一对应——实现时先 `grep -n "add_parser\|add_argument" ../skill/scripts/cli.py` 提取各子命令 flag 集合再写白名单）。

- [ ] **Step 4: 跑过 + pounding-mcp 全量回归 + Commit**

```bash
cd pounding-mcp && .venv/bin/python -m pytest tests/ -q
git add pounding-mcp/pounding_mcp/tasks_server.py pounding-mcp/pounding_mcp/skill_runner.py pounding-mcp/tests/test_tasks_server_auth.py
git commit -m "fix(security): 8902 任务网关补 Bearer 鉴权、去 CORS *、params 白名单与 body 上限（cicd-H1）"
```

> **联动登记**：pounding-harness 侧需读取 `TASKS_TOKEN` 注入前端调用（独立仓库，发版前检查清单加一条；本仓库不动）。

## Task 19: 部署重启默认不复活 failed 任务

**Files:**
- Modify: `worker/src/main.py:556-560`（默认值翻转）
- Modify: `deploy/.env.example`（补条目）
- Modify: `deploy/docker-compose.yml`（worker environment 透传）
- Test: `worker/tests/test_revive_default_v076.py`（新建）

**行为变更（发版说明必写）**：`SKIP_FAILED_REVIVE` 默认语义翻转——默认**不**复活 failed；要旧行为显式设 `SKIP_FAILED_REVIVE=0`。

- [ ] **Step 1: 失败测试**（纯逻辑：monkeypatch env 断言 `_skip_failed` 默认 True、`SKIP_FAILED_REVIVE=0` 时 False——先读 548-566 现状把判定抽成 `_revive_failed_enabled() -> bool` 纯函数再测）

- [ ] **Step 2: 跑失败 → Step 3: 实现**：

```python
def _revive_failed_enabled() -> bool:
    """T19(race-M4): 部署重启默认不复活 failed 任务（retry_count 归零会烧用户额度重复上架）。
    应急恢复旧行为：SKIP_FAILED_REVIVE=0。"""
    return os.environ.get("SKIP_FAILED_REVIVE", "").strip() == "0"
```

调用点 `_skip_failed = not _revive_failed_enabled()`。`deploy/.env.example` 追加：

```
# 部署重启是否复活 failed 任务重跑（默认不复活；设 0 恢复旧行为——会烧用户额度重复上架）
# SKIP_FAILED_REVIVE=0
```

compose worker 服务 environment 块加 `SKIP_FAILED_REVIVE=${SKIP_FAILED_REVIVE:-}`。

- [ ] **Step 4: 跑过 + compose hygiene 测试（`tests/test_deploy_compose_hygiene.py`）+ Commit**

```bash
git add worker/src/main.py worker/tests/test_revive_default_v076.py deploy/.env.example deploy/docker-compose.yml
git commit -m "fix(security): 部署重启默认不复活 failed 任务，SKIP_FAILED_REVIVE 语义翻转（race-M4）"
```

## Task 20: test/e2e compose PG 绑回环 + hygiene 断言补齐

**Files:**
- Modify: `deploy/docker-compose.test.yml:12`、`deploy/docker-compose.e2e.yml:13`（`"5433:5432"` → `"127.0.0.1:5433:5432"`）
- Modify: `worker/tests/test_deploy_compose_hygiene.py:62-73`（补断言）
- Test: 即 hygiene 测试本身

- [ ] **Step 1: 失败断言**（hygiene 测试加：

```python
def test_test_e2e_compose_pg_binds_loopback():
    """T20(cicd-M6): 测试栈 PG 不许绑非回环——09-11 事故通道的暴露方向镜像。"""
    for name in ("docker-compose.test.yml", "docker-compose.e2e.yml"):
        text = (DEPLOY_DIR / name).read_text()
        for line in text.splitlines():
            if "5433:5432" in line:
                assert line.strip().startswith("- \"127.0.0.1:5433:5432\""), f"{name}: {line.strip()}"
```

）
- [ ] **Step 2: 跑失败 → Step 3: 改两份 compose 的 ports 行 → Step 4: 跑过 + Commit**

```bash
git add deploy/docker-compose.test.yml deploy/docker-compose.e2e.yml worker/tests/test_deploy_compose_hygiene.py
git commit -m "fix(security): test/e2e compose PG 绑 127.0.0.1，hygiene 测试锁定（cicd-M6）"
```

## Task 21: 限流器后置 + 过期键清扫

**Files:**
- Modify: `worker/src/main.py:236-256`（RateLimiter.check 加清扫）、限流调用点次序核对（1506/1974/2698/2774）
- Test: `worker/tests/test_rate_limiter_v076.py`（新建）

**行为**：`check()` 在 `len(self._requests) > 4096` 时先清扫「最后活跃 ≤ window_start」的键；submit_task（1974）等调用点必须在 `_authenticate_token` **之后**（实现时读 1974 前后 20 行核对次序，若已在鉴权后则只加清扫）。

- [ ] **Step 1: 失败测试**

```python
# worker/tests/test_rate_limiter_v076.py
"""v0.76 T21(race-M5): 限流器字典有界——4096 键上限触发过期清扫，未认证洪水不再无界吃内存。"""
import time
from main import RateLimiter


def test_purge_when_over_cap():
    rl = RateLimiter(max_per_minute=10)
    old = time.time() - 3600
    for i in range(5000):
        rl._requests[f"old-{i}"] = [old]
    allowed, _ = rl.check("fresh-1")
    assert allowed
    assert len(rl._requests) <= 4097          # 清扫发生
    assert "old-0" not in rl._requests


def test_active_keys_survive_purge():
    rl = RateLimiter(max_per_minute=10)
    now = time.time()
    for i in range(5000):
        rl._requests[f"act-{i}"] = [now]
    rl.check("fresh-1")
    assert any(k.startswith("act-") for k in list(rl._requests)[:100])  # 活跃键不误清（抽样）
```

- [ ] **Step 2: 跑失败 → Step 3: 实现**（check 开头加：

```python
        if len(self._requests) > 4096:  # T21(race-M5): 有界化——未认证洪水下的过期键清扫
            stale = [k for k, ts in self._requests.items() if not ts or ts[-1] <= window_start]
            for k in stale:
                del self._requests[k]
```

）+ 核对四个调用点次序并在 commit message 写明核对结论。**Step 4: 跑过 + Commit** `fix(security): 限流器字典有界化 + 调用点鉴权后置核对（race-M5）`

## Task 22: CSV 公式注入中和（worker + webui）

**Files:**
- Create: `worker/src/utils/csv_safety.py`
- Modify: `worker/src/services/draft_service.py:359-408`（export_drafts_csv 每个文本单元格过中和）
- Modify: `webui/src/components/DiscoveryPanel.tsx:44-60`（前端拼 CSV 处）
- Test: `worker/tests/test_csv_safety_v076.py`（新建）

**Interfaces:**
- Produces: `neutralize_csv_cell(value: str) -> str`——`value` 以 `= + - @ \t \r` 开头 → 前缀 `'`；其余原样。webui 侧同语义 `safeCsvCell`。

- [ ] **Step 1: 失败测试**

```python
# worker/tests/test_csv_safety_v076.py
"""v0.76 T22(cicd-M2): CSV 导出公式注入中和。"""
from utils.csv_safety import neutralize_csv_cell as n


def test_formula_prefixes_neutralized():
    for p in ("=cmd|'/c calc'!A1", "+1", "-1", "@SUM(1)", "\t=1"):
        assert n(p).startswith("'")
    assert n("=HYPERLINK(\"http://evil\",\"x\")").startswith("'=")


def test_normal_text_untouched():
    assert n("留香珠 200ml") == "留香珠 200ml"
    assert n("") == ""
    assert n("x=y") == "x=y"          # 非 首 字符不中和
```

- [ ] **Step 2: 跑失败 → Step 3: 实现**：

```python
# worker/src/utils/csv_safety.py
"""CSV 公式注入中和（v0.76 T22，OWASP CSV Injection 口径）。
导出侧统一入口：凡用户可控文本（标题/供应商/notes/竞品词）写单元格前必过。"""
_CSV_DANGEROUS_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def neutralize_csv_cell(value):
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(_CSV_DANGEROUS_PREFIX):
        return "'" + value
    return value
```

`export_drafts_csv` 里 `writer.writerow([...])` 的每个来自 `draft.title/supplier/notes/...` 的字符串包 `neutralize_csv_cell(...)`（数字列不包）。webui `DiscoveryPanel` 的 cell 拼装处加同语义 `safeCsvCell`（ts 内联 5 行即可）。

- [ ] **Step 4: 跑过 + drafts 存量导出用例 + tsc/build + Commit**

```bash
git add worker/src/utils/csv_safety.py worker/src/services/draft_service.py worker/tests/test_csv_safety_v076.py webui/src/components/DiscoveryPanel.tsx
git commit -m "fix(security): CSV 导出公式注入中和，worker+webui 同口径（cicd-M2）"
```

## Task 23: cd.yml tag 名插值改 env 间接引用

**Files:**
- Modify: `.github/workflows/cd.yml:171-187,199-226`（两处 `VERSION="${{ steps.version.outputs.version }}"`）
- Test: 无自动测试；以 actionlint（如有）+ 人工 diff 审查

- [ ] **Step 1: 实现**（与同文件 version-check job 的 env 间接引用对齐）：

```yaml
      - name: cos-deploy
        env:
          CD_VERSION: ${{ steps.version.outputs.version }}   # T23(cicd-M3): 表达式只在 env 展开一次，不进 shell 二次插值
        run: |
          VERSION="${CD_VERSION}"
          ...  # 以下 cos-deploy 原有步骤保持不动，仅 VERSION 取值方式替换
```

（202 行同法。）顺手 grep 全部 workflow：

```bash
grep -rn '\${{.*}}' .github/workflows/*.yml | grep -E '^\s*[^#]*run:' 
```

Expected: run: 块内无直接 `${{ }}` 残留（env: 块内的属正确形态）。

- [ ] **Step 2: Commit** `fix(security): cd.yml tag 名经 env 间接引用，封 shell 表达式注入（cicd-M3）`

---

# PR-5 加固排期项（crypto-M2/M1 + race-L1/L2/L3 + inj-L1 + cicd-M4）

## Task 24: 主密钥 KDF 升级 v2（PBKDF2，向后兼容解密 v1）

**Files:**
- Modify: `worker/src/utils/credential_cipher.py:47-56`（derive_key）+ 加密封装（新密文走 `v2:` 前缀）
- Test: `worker/tests/test_kdf_v2_v076.py`（新建）

**行为**：`v2:` 信封 = `PBKDF2-HMAC-SHA256(passphrase, salt=sha256(b"ozon-worker-credential-kdf-v2"), 600_000, 32)`；解密按前缀路由（无前缀=legacy v1 原逻辑）。32 字节随机 key（base64/hex 形态）直接用不进 KDF（检测：解码后 32 字节）。DEPLOY.md 补一行「推荐 32 字节随机 key」。

- [ ] **Step 1: 失败测试**：`derive_key_v2("password123") != sha256("password123")`；v2 加密→解密 roundtrip；v1 旧密文仍可解；跨租户 aad 语义不变（复用既有 aad 测试形态）。
- [ ] **Step 2: 跑失败 → Step 3: 实现 → Step 4: 跑过（含 `tests/test_credential_cipher*.py` 存量）+ Commit**

```bash
git add worker/src/utils/credential_cipher.py worker/tests/test_kdf_v2_v076.py docs/DEPLOY.md
git commit -m "fix(security): 主密钥 KDF 升级 PBKDF2-600k（v2 信封，兼容 v1 解密）（crypto-M2）"
```

## Task 25: 备份上传拒明文

**Files:**
- Modify: `deploy/backup-upload-cos.sh:55-66`（循环内：`.sql`（非 `.gpg`）跳过上传，除非 env `ALLOW_PLAINTEXT_BACKUP_UPLOAD=1`；跳过时 stderr 打原因）
- Test: 人工——本地以假文件矩阵跑脚本 dry-run 分支（脚本加 `DRY_RUN=1` 分支打印不执行，两行改动）

- [ ] **Step 1: 实现**（循环体内首行）：

```bash
    case "$f" in
      *.gpg) ;;
      *)
        if [ "${ALLOW_PLAINTEXT_BACKUP_UPLOAD:-0}" != "1" ]; then
          echo "⏭️ 跳过明文备份 $f（设 ALLOW_PLAINTEXT_BACKUP_UPLOAD=1 强制上传）" >&2
          continue
        fi ;;
    esac
```

- [ ] **Step 2: Commit** `fix(security): 备份上传默认拒明文 dump（crypto-M1/cicd-M1）`

## Task 26: resubmit 对齐余额预检 + 409

**Files:**
- Modify: `worker/src/main.py:2293-2373`（补 `_check_mxou_balance` 调用与 `IntegrityError` → 409，形态照抄 2097-2103）
- Test: `worker/tests/test_resubmit_align_v076.py`（新建；两个用例：低余额 402、并发重复 409——mock 余额函数与 session execute 抛 IntegrityError）

- [ ] **Step 1: 失败测试 → Step 2: 实现（照抄 submit_task 同款两段）→ Step 3: 跑过 + Commit** `fix(security): resubmit 补余额预检与并发 409 对齐（race-L1）`

## Task 27: purchase_cost 非正数提交闸

**Files:**
- Modify: `worker/src/utils/draft_sanity.py`（新增检查项；先读该文件现有检查形态与其错误码返回结构）
- Test: `worker/tests/test_draft_sanity_cost_v076.py`（新建）

**行为**：非跟卖信封 `purchase_cost` 存在且 `<= 0` → 拦截（错误文案「采购成本必须大于 0」+ 现有 sanity 错误通道，不入管线）；跟卖（`follow_sell`）与缺失值不拦。

- [ ] **Step 1: 失败测试（3 用例：负数拦/0 拦/follow 豁免）→ Step 2: 实现 → Step 3: 跑过 + 存量 draft_sanity 用例 + Commit** `fix(security): 采购成本非正数提交闸（race-L2）`

## Task 28: template set_default 单事务

**Files:**
- Modify: `worker/src/services/template_service.py:244-256`（clear+set 合并进一个 `get_engine().begin()`；UniqueViolation 捕获转 409）
- Test: `worker/tests/test_template_default_atomic_v076.py`（新建；并发 20 次 set_default 断言：恒 1 个 default、无 500——复用审计探针 5 形态，本地 PG）

- [ ] **Step 1: 失败测试 → Step 2: 实现 → Step 3: 跑过 + Commit** `fix(security): 模板默认切换单事务化，并发 409 不 500（race-L3）`

## Task 29: ILIKE 通配符转义

**Files:**
- Modify: `worker/src/services/analytics_service.py:44-48`、`worker/src/services/queries_service.py:126,166`、`worker/src/routes/seo_keywords_routes.py`
- Create: `worker/src/utils/like_escape.py`（`escape_like(q: str) -> str`：`\` → `\\`、`%` → `\%`、`_` → `\_`；SQL 侧加 `ESCAPE '\'`）
- Test: `worker/tests/test_like_escape_v076.py`（新建）

- [ ] **Step 1: 失败测试（`escape_like("100%_x") == "100\\%\\_x"`；含 `%` 的查询不再全表扫——用本地 PG 对 mappings/lookup 语义做行为断言或纯函数断言即可）→ Step 2: 实现（三处 `f"%{q}%"` → `f"%{escape_like(q)}%"` + text SQL 补 `ESCAPE '\'`）→ Step 3: 跑过 + Commit** `fix(security): ILIKE 通配符转义（inj-L1）`

## Task 30: CI actions pin SHA + 下载物校验

**Files:**
- Modify: `.github/workflows/ci.yml:22,48,87,254,256`、`cd.yml:130,194-197`、`build-skill.yml:270-276`、`skill-distribute.yml:68-73`
- Test: 人工 diff + push 后首个 CI run 绿

- [ ] **Step 1: 解析各 action 当前 commit SHA**（联网步骤，执行时跑）：

```bash
for r in actions/checkout gitleaks/gitleaks-action softprops/action-gh-release; do
  gh api "repos/$r/commits/main" --jq ".sha"
done
```

- [ ] **Step 2: 全部 `uses:` 改 `owner/action@<full-sha> # vX.Y` 注释保留版本号**；gitleaks `curl|bash` 改为先下载固定 commit 的 install.sh 到 /tmp、校验非空再执行；coscli 下载后 `sha256sum -c`（校验值从 cd.yml 变量 `COSCLI_SHA256` 读——执行时先手动下载一次算出并写入）。
- [ ] **Step 3: Commit** `fix(security): CI actions pin 到 commit SHA，下载物加校验和（cicd-M4）`

## Task 31: gitleaks 自定义规则补非标形态（crypto-M3）

审计实测：`password = "..."` 赋值与无关键字高熵 blob 能漏过默认规则；ratchet 只锁 15 个已知指纹不防新形态。

**Files:**
- Modify: `.gitleaks.toml`（追加自定义规则组）
- Modify: `worker/tests/test_leak_guard_in_tree.py`（新指纹注册 + 合成样本验证用例）
- Test: 本机 gitleaks 实扫 + leak_guard 测试

- [ ] **Step 1: 失败验证**（先证明缺口存在，落进测试）：

```bash
cat > /tmp/probe-leak.py <<'EOF'
password = "Tr0ub4dour&3"
EOF
gitleaks detect --no-git --source /tmp/probe-leak.py --config .gitleaks.toml 2>&1 | tail -3
```

Expected: 0 leaks（缺口实锤）→ 把该样本形态写成 leak_guard 测试断言「修复后必须命中」。

- [ ] **Step 2: 写失败测试**（leak_guard 增用例：合成 `password = "high-entropy-value-123"` 文本必须被扫描器命中——测试内调 gitleaks 子进程，跳过条件 `shutil.which("gitleaks") is None`）

- [ ] **Step 3: 实现**：`.gitleaks.toml` 追加两条自定义规则：

```toml
[[rules]]
id = "ozon-custom-password-assign"
description = "密码/口令赋值（含上下文关键字）"
regex = '''(?i)(password|passwd|pwd|secret|passphrase)["']?\s*[:=]\s*["'][^"']{8,}["']'''
entropy = 3.0
keywords = ["password", "passwd", "pwd", "secret", "passphrase"]

[[rules]]
id = "ozon-custom-high-entropy-blob"
description = "无关键字高熵长串启发式（32+ 位混合大小写数字）"
regex = '''["'][A-Za-z0-9+/_-]{32,}={0,2}["']'''
entropy = 4.2
```

随后**全仓实跑一遍**新规则，把既有误报（如 base64 图片样例、编译产物指纹）逐个加进 `.gitleaks.toml` allowlist 并在 leak_guard `KNOWN_REMAINING` 同步登记——allowlist 每条必须带注释说明豁免原因（纪律同 CONVENTIONS.md）。

- [ ] **Step 4: 跑过 + Commit**

```bash
gitleaks detect --no-git -v   # 全树验证 0 命中且新规则生效
git add .gitleaks.toml worker/tests/test_leak_guard_in_tree.py
git commit -m "fix(security): gitleaks 自定义规则补密码赋值与高熵串形态（crypto-M3）"
```

## Task 32: COS 升级链签名（cicd-H2，信任根收口）

**Files:**
- Modify: `deploy/cos-update.sh:75-136`（manifest 签名校验 + 指定版本路径也强制走 manifest + 缓存 JSON 校验）
- Modify: `skill/scripts/lib/updater.py:36`（同公钥校验客户端 skill 更新 manifest）
- Modify: `.github/workflows/cd.yml:199-226`（打包 job 生成签名）
- Test: `worker/tests/test_cos_update_verify_v076.py`（新建——把校验函数抽成可单测形态；shell 侧以本地 dry-run 验证）

**前置（一次性，人工执行并登记）**：`minisign -G -p cos-update.pub -s cos-update.sec`（或 openssl ed25519 等价物）；**公钥**提交进 `deploy/cos-update.pub`；**私钥**进 GitHub repo secret `COS_UPDATE_SIGN_KEY` + 离线冷备。指纹登记进 `docs/audit/`。

**行为定义**：
- CI：打包 job 对 `manifest.json` 产出 `manifest.sig`（minisign -S），与包同传 COS。
- `cos-update.sh`：启动即 fetch `manifest.json` + `manifest.sig`，`minisign -V -p deploy/cos-update.pub -x manifest.sig -m manifest.json` 失败立即退出（exit 3）；**指定版本路径同样先取 manifest**（从 manifest 的版本表拿 SHA256——封死「指定版本跳过校验」）；缓存 JSON 的 sha256 也登记进 manifest，下载后校验再 `docker compose cp`。
- 逃生门：`COS_UPDATE_SKIP_VERIFY=1` 仅 warn+继续（应急，日志必留痕）。

- [ ] **Step 1: 写失败测试**（把 sha256+签名校验序列抽成 `deploy/verify_manifest.sh <pub> <manifest> <sig> <expected_version>`，Python 测试调它：篡改 manifest 一字节 → exit 非 0；正确签名 → exit 0。测试内用 minisign 以临时 keypair 签名——跳过条件 `shutil.which("minisign") is None`）

- [ ] **Step 2: 跑失败 → Step 3: 实现**（cos-update.sh 三处：头部校验函数、主流程与指定版本分支都先走 manifest、缓存 JSON 下载后校验；updater.py 同语义 Python 实现，公钥硬编码常量）→ **Step 4: 本地 dry-run 验证**（伪造 manifest+sig 走通正/反两路）→ **Step 5: Commit**

```bash
git add deploy/cos-update.sh deploy/cos-update.pub deploy/verify_manifest.sh skill/scripts/lib/updater.py .github/workflows/cd.yml worker/tests/test_cos_update_verify_v076.py
git commit -m "fix(security): COS 升级链 manifest 签名校验，指定版本与缓存 JSON 强制过验（cicd-H2）"
```

---

# PR-6 文档收口

## Task 33: 文档与发版物料

**Files:**
- Modify: `CHANGELOG.md`（0.76.0 安全修复节：行为变更清单 = T1 明文字段消失 / T7 statistics 鉴权 / T11 status 校验 / T19 复活默认翻转 / T18 8902 需 token）
- Modify: `AGENTS.md`（顶部「最近更新」块 + 高频坑补 SKIP_FAILED_REVIVE 翻转 + 鉴权矩阵变动）
- Modify: `docs/API-OVERVIEW.md`（变更记录节：受影响端点与新鉴权要求）
- Modify: `docs/MCP-SERVER.md`（cancel_task/statistics 工具鉴权行为 + 8902 token 联动说明）

- [ ] **Step 1: 按上列逐个落文档（内容从各任务 commit message 汇总，勿新造口径）**
- [ ] **Step 2: 全量回归**（发版 gate 预演）

```bash
cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q
cd ../skill && .venv314/bin/python -m pytest tests/ -q
cd ../pounding-mcp && .venv/bin/python -m pytest tests/ -q
cd .. && bash scripts/ci.sh --quick
```

Expected: 全绿；基线对比开工冒烟无回归（worker 新增约 25 个测试）。

- [ ] **Step 3: Commit** `docs(security): 0.76.0 安全修复批次文档收口`

- [ ] **Step 4: 合入**：六个 PR 全绿后按 WORKFLOW.md 流程 dev→main PR、main 打 tag。**发版说明必提**：T1/T7/T11/T19/T18 五项行为变更 + 8902 token 对 harness 的联动改造项。

---

## 落地后遗留登记（写入 CHANGELOG defer 节，本批不做）

- 明文 token 列 DB 侧退役与存量轮换审计（api-C1 的存储根；读侧已断）
- `payload` 列级加密（crypto-H2 存储根；出口已脱敏）
- race-M2：priority 用户可控 + 认领无 per-tenant 公平性——单租户可满级 priority 饿死全平台（priority 开放是 v0.75 有意设计 BL-25；解法=per-tenant running/pending 计数上限，涉认领 SQL 改造，单独立项）
- race-M3：`/run` `/stream_run` 同步端点游离在 MAX_CONCURRENT 信号量与 SKU 去重之外（端点已标 DEPRECATED；退役排期，勿再接新调用方）
- race-L4：`_handle_failure_sync` retry_count 读-改-写非原子（仅在 stale 双跑场景可达，L-4 理论级；与 race-M5 场景关联，观察）
- store/health 凭证 query 传参彻底移除（T9 已 header 化，query 回落待客户端全量迁移后删）
- api-L5：限流键 raw/clean token 不一致（跨端点族 2 倍名义配额）——T21 已核调用点次序，键归一随限流器重构一并做
- cicd-L4：本地 `scripts/ci.sh` worker 测试失败仅警告不置 FAILED——本地绿≠CI 绿（随下次 ci.sh 改造批修）
- Sentry M-4 指纹前缀（8 位明文前缀改纯 hash）、webui localStorage token → HttpOnly cookie、ofelia digest pin、多副本硬闸升级（`_warn_if_multi_worker` 告警改阻断）
- pounding-harness 联动：8902 `TASKS_TOKEN` 注入（T18 发版前置检查项）
