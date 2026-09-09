# W1-W8 云端部署问题修复实施计划 v1

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 根治 2026-09-09 服务器部署报告的 W1/W2/W3/W4/W5/W8 六个代码级问题（W6 人工清理、W7 生产数据复核不在本计划），使全量预热→导出→上 COS 闭环真正可跑。

**Architecture:** 三条独立文件面并行施工：①warm 脚本面（SQL cast + 导出语义 + 死节点跳过表）；②init_data/查询面（同款 cast）+ cos-update/cd.yml 部署面（自举 + 版本传导 + 打包 docs）；③学习面（LearningRecordInput 补字段）+ 文档面。根因取证见 AGENTS.md v0.73 块与记忆 `sqlalchemy-jsonb-cast-trap` / `w1-w8-cos-deploy-root-causes`。

**Tech Stack:** Python 3.12 / SQLAlchemy 2.0 / pytest（PG 集成直连探测守卫）/ bash / GitHub Actions

## Global Constraints（每个 task 隐含遵守）

- **并行会话共享工作树**：subagent **一律不做任何 git add/commit**（主会话统一逐文件提交，防 index 竞争与误提交他人 WIP）。
- **动文件前先 `ls -la <file>` 看 mtime**：若比本计划创建时间（2026-09-09 23:40）新且非本任务所改，停下来在报告中说明。**严禁触碰** `worker/scripts/refresh_category_tree.py`、`worker/tests/test_refresh_category_tree.py`、`worker/uv.lock`（另一会话 WIP）。
- 测试命令（必须用 skill venv 的 python，系统 python 无 pytest）：
  - 带 PG：`cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/<file> -q`
  - 纯 mock：去掉 PGDATABASE_URL。
- PG 集成测试 skip 守卫**用直连探测**（照抄 `worker/tests/conftest.py` 的 5433 直连模式），勿读 env 判存。
- lint：`cd worker && ../skill/.venv314/bin/python -m ruff check <改动的文件> --select E,F,W --ignore E501`。
- 不 bump VERSION（随未 tag 的 0.73.0 同车发）；不改 API schema（无需跑 gen_api_docs，主会话终验仍会跑 `--check` 兜底）。
- commit 由主会话按任务批次做，格式 `<type>(<scope>): 中文描述`。

---

### Task 1: warm_category_cache.py —— CAST 写库 + --export-from-pg + 400 死节点跳过表

**Files:**
- Modify: `worker/scripts/warm_category_cache.py`（全文 652 行；改动点见下）
- Test: `worker/tests/test_warm_cache_pg_write_v073.py`（新建，PG 守卫）

**Interfaces（Produces，后续任务/主会话依赖）：**
- `_call_ozon_api_status(endpoint, payload, timeout=30) -> tuple[Optional[dict], int]`
- `_fetch_attribute_schema_with_status(dc, tid) -> tuple[list[dict], int]`
- `ensure_dead_nodes_table(session)` / `load_dead_nodes(session) -> set[tuple[int,int]]` / `mark_dead_node(session, dc: int, tid: int, reason: str)`
- `export_from_pg() -> None`（读 PG 双表流式写 `SCHEMAS_FILE`/`DICT_VALUES_FILE`）
- CLI：新增 `--export-from-pg`（无需 Ozon 凭证；与 --export-only/--pg-only/--import-only/--coverage/--all/--limit/--offset/--force 组合时报错退出 2）
- `collect_coverage()` 返回 dict 新增键 `dead_excluded: int`；total 不含死节点
- 死节点表 DDL（幂等，warm 自建）：`warm_dead_nodes(description_category_id BIGINT, type_id BIGINT, reason TEXT, created_at BIGINT, PRIMARY KEY(description_category_id, type_id))`

- [ ] **Step 1: 写失败测试（PG 守卫 + 纯 mock 混合）**

```python
"""v0.73 W1/W2/W3: warm 写库 CAST / --export-from-pg / 400 死节点跳过 回归。

PG 用例守卫用直连探测（勿读 env 判存——import main 会注入容器风格 URL）。
"""
import json
import os
import re
import sys
import time
import pathlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest

_PG_URL = os.environ.get(
    "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon"
)


def _pg_ready() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(_PG_URL, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


pg = pytest.mark.skipif(not _pg_ready(), reason="local PG 5433 不可达")

warm = pytest.importorskip("warm_category_cache")

# ── 纯 mock：静态锁死「bind 名紧跟 ::cast」禁入 ──

def test_warm_sql_has_no_bare_bind_cast():
    src = pathlib.Path(warm.__file__).read_text(encoding="utf-8")
    import re
    hits = re.findall(r":[A-Za-z_]\w*::[a-zA-Z]+", src)
    assert hits == [], f"发现裸 bind cast（SQLAlchemy 不识别，必炸 syntax error）: {hits}"


def test_export_from_pg_flag_exists():
    parser = warm.build_arg_parser()
    opts = {a.option_strings[0] for a in parser._actions if a.option_strings}
    assert "--export-from-pg" in opts


def test_export_from_pg_rejects_conflicting_flags(capsys):
    with pytest.raises(SystemExit) as ei:
        warm.parse_args(["--export-from-pg", "--force"])
    assert ei.value.code == 2
    with pytest.raises(SystemExit):
        warm.parse_args(["--export-from-pg", "--limit", "5"])


# ── PG 用例 ──

@pytest.fixture()
def _pg_cleanup():
    yield
    from sqlalchemy import create_engine, text
    eng = create_engine(_PG_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "DELETE FROM attribute_cache WHERE description_category_id < 0"))
        conn.execute(text(
            "DELETE FROM dictionary_value_cache WHERE description_category_id < 0"))
        conn.execute(text(
            "DELETE FROM warm_dead_nodes WHERE description_category_id < 0"))


@pg
def test_write_node_to_pg_no_syntax_error(_pg_cleanup):
    """W1 核心：写库路径不再报 syntax error（此前 :schema::jsonb 100% 炸）。"""
    from sqlalchemy import create_engine, text
    from storage.database.db import get_session
    # warm 模块 get_session 读 worker 自己的 db 配置——测试进程已设 PYTHONPATH=src，
    # storage.db 读 PGDATABASE_URL env；缺省时显式注入。
    os.environ.setdefault("PGDATABASE_URL", _PG_URL)
    warm._write_node_to_pg(
        dc=-999001, tid=-999001,
        schema=[{"id": 85, "name": "品牌"}],
        dict_values={"85:-999001:-999001": [{"id": 1, "value": "x"}]},
        now=int(time.time()),
    )
    s = get_session()
    try:
        row = s.execute(text(
            "SELECT attributes_schema FROM attribute_cache "
            "WHERE description_category_id=-999001 AND type_id=-999001"
        )).fetchone()
        assert row is not None
        assert row[0][0]["name"] == "品牌"
    finally:
        s.close()


@pg
def test_export_from_pg_streams_both_tables(_pg_cleanup, tmp_path, monkeypatch):
    """W2 核心：export-from-pg 从 PG 读缓存导出（此前 export-only 只导本次 API 拉取）。"""
    os.environ.setdefault("PGDATABASE_URL", _PG_URL)
    from sqlalchemy import create_engine, text
    eng = create_engine(_PG_URL)
    with eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO attribute_cache VALUES (-999002,-999002,'ZH_HANS',"
            "CAST(:s AS jsonb), 9999999999, 0) ON CONFLICT DO NOTHING"),
            {"s": json.dumps([{"id": 1}])})
        conn.execute(text(
            "INSERT INTO dictionary_value_cache VALUES "
            "(-999002,-999002,-999002,'ZH_HANS',CAST(:v AS jsonb), 9999999999, 0) "
            "ON CONFLICT DO NOTHING"), {"v": json.dumps([{"id": 2}])})
    monkeypatch.setattr(warm, "SCHEMAS_FILE", str(tmp_path / "s.json"))
    monkeypatch.setattr(warm, "DICT_VALUES_FILE", str(tmp_path / "d.json"))
    warm.export_from_pg()
    schemas = json.loads((tmp_path / "s.json").read_text())
    dicts = json.loads((tmp_path / "d.json").read_text())
    assert "-999002:-999002" in schemas
    assert "-999002:-999002:-999002" in dicts


@pg
def test_mark_and_load_dead_nodes_and_coverage(_pg_cleanup):
    """W3 核心：400 死节点进表被跳过，coverage 分母剔除（100% 恢复可达）。"""
    os.environ.setdefault("PGDATABASE_URL", _PG_URL)
    from storage.database.db import get_session
    s = get_session()
    try:
        warm.ensure_dead_nodes_table(s)
        warm.mark_dead_node(s, -999003, -999003, "400 category not found")
        dead = warm.load_dead_nodes(s)
        assert (-999003, -999003) in dead
        rep = warm.collect_coverage()
        assert rep["dead_excluded"] >= 1
    finally:
        s.close()


@pg
def test_schema_fetch_400_marks_dead(monkeypatch):
    """fetch 返回 400 → (schema,400) 状态外露（主循环据此 mark dead）。"""
    class _Resp:
        status_code = 400
        text = '{"error":"category not found"}'
        def json(self):
            return {}
    monkeypatch.setattr(warm._session, "post",
                        lambda *a, **k: _Resp(), raising=True)
    schema, status = warm._fetch_attribute_schema_with_status(1, 2)
    assert status == 400 and schema == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_warm_cache_pg_write_v073.py -q`
Expected: FAIL——`--export-from-pg` 不存在 / `_fetch_attribute_schema_with_status` AttributeError / 写库用例报 syntax error。

- [ ] **Step 3: 实现（按顺序改 5 处）**

3a. `_call_ozon_api_status` + 状态化 schema 拉取（`_call_ozon_api` 保持原签名，内部委托）：

```python
def _call_ozon_api_status(endpoint: str, payload: dict, timeout: int = 30,
                         _retries: int = 0) -> tuple[Optional[dict], int]:
    """v0.73 W3: 带状态码版本——400/404=类目永久失效（供死节点表），与瞬态故障区分。"""
    headers = {
        "Client-Id": OZON_CLIENT_ID,
        "Api-Key": OZON_API_KEY,
        "Content-Type": "application/json",
    }
    url = f"https://api-seller.ozon.ru{endpoint}"
    try:
        resp = _session.post(url, json=payload, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.json(), 200
        if resp.status_code == 429 and _retries < MAX_429_RETRIES:
            wait = 5 * (2 ** _retries)
            logger.warning(f"   ⚠️ 限流 (429)，指数退避 {wait}s...")
            time.sleep(wait)
            return _call_ozon_api_status(endpoint, payload, timeout, _retries + 1)
        logger.warning(f"   ⚠️ API {endpoint} 返回 {resp.status_code}: {resp.text[:200]}")
        return None, resp.status_code
    except Exception as e:
        logger.warning(f"   ⚠️ API {endpoint} 异常: {e}")
        return None, 0


def _call_ozon_api(endpoint, payload, timeout=30, _retries=0):
    data, _status = _call_ozon_api_status(endpoint, payload, timeout, _retries)
    return data


def _fetch_attribute_schema_with_status(dc: int, type_id: int) -> tuple[list[dict], int]:
    data, status = _call_ozon_api_status("/v1/description-category/attribute", {
        "description_category_id": dc, "type_id": type_id, "language": "ZH_HANS",
    })
    if data:
        return data.get("result", []), status
    return [], status
```

（原 `fetch_attribute_schema` 改为 `_fetch_attribute_schema_with_status(dc,tid)[0]` 委托，签名不变。）

3b. W1 CAST（4 处，:215/:228/:267/:289）：`:schema::jsonb` → `CAST(:schema AS jsonb)`；`:vals::jsonb` → `CAST(:vals AS jsonb)`。

3c. 死节点表三函数 + main 循环接线：

```python
DEAD_NODES_DDL = """
CREATE TABLE IF NOT EXISTS warm_dead_nodes (
    description_category_id BIGINT NOT NULL,
    type_id BIGINT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (description_category_id, type_id)
)"""
# v0.73 W3: Ozon 已删类目（400 category not found）永久跳过——此前每次预热恒计失败，
# coverage 分母含死节点 → 100% 数学不可达（服务器被迫把看护阈值调 7350）。

def ensure_dead_nodes_table(session):
    from sqlalchemy import text
    session.execute(text(DEAD_NODES_DDL))
    session.commit()


def load_dead_nodes(session) -> set:
    from sqlalchemy import text
    rows = session.execute(text(
        "SELECT description_category_id, type_id FROM warm_dead_nodes")).fetchall()
    return {(int(r[0]), int(r[1])) for r in rows}


def mark_dead_node(session, dc: int, tid: int, reason: str):
    from sqlalchemy import text
    session.execute(text(
        "INSERT INTO warm_dead_nodes (description_category_id, type_id, reason, created_at) "
        "VALUES (:dc, :tid, :reason, :now) ON CONFLICT DO NOTHING"),
        {"dc": dc, "tid": tid, "reason": reason[:200], "now": int(time.time())})
    session.commit()
```

main() 循环改造：起步 `ensure_dead_nodes_table` + `dead_set = load_dead_nodes(...)`；循环内节点若 `(dc,tid) in dead_set` → `skipped_dead += 1; continue`（打点日志每 100 个）；schema 拉取改用 `_fetch_attribute_schema_with_status`，`status in (400, 404) and not schema` → `mark_dead_node` + `dead += 1`（不计入 failed）。结束行打印 `跳过死节点 {skipped_dead}`。

3d. `export_from_pg`（W2）：

```python
def export_from_pg() -> None:
    """v0.73 W2: --export-from-pg——从 PG 缓存导出 JSON（不调 Ozon API、无需凭证）。

    此前 --export-only 是「边拉边导」语义（v1.1 OOM 修复引入），预热完成后
    单独跑只会导出本次进程内 API 拉取的部分；runbook「预热→导出→上 COS」
    需要的是把 PG 里已预热的数据落 JSON，本函数补上这一环（流式，内存 O(单行)）。
    """
    from storage.database.db import get_session
    from sqlalchemy import text
    os.makedirs(ASSETS_DIR, exist_ok=True)
    now = int(time.time())
    session = get_session()
    try:
        w = _JsonStreamWriter(SCHEMAS_FILE)
        rows = session.execute(text(
            "SELECT description_category_id, type_id, attributes_schema "
            "FROM attribute_cache WHERE language='ZH_HANS' AND expires_at > :now "
            "ORDER BY description_category_id, type_id"), {"now": now}).mappings()
        n = 0
        while chunk := rows.fetchmany(500):
            for r in chunk:
                w.write(f"{int(r['description_category_id'])}:{int(r['type_id'])}",
                        r["attributes_schema"])
                n += 1
        w.close()
        logger.info(f"✅ [export-from-pg] 属性 schema: {SCHEMAS_FILE} ({n} 个类目)")

        w2 = _JsonStreamWriter(DICT_VALUES_FILE)
        rows = session.execute(text(
            "SELECT attribute_id, description_category_id, type_id, values_data "
            "FROM dictionary_value_cache WHERE language='ZH_HANS' AND expires_at > :now "
            "ORDER BY attribute_id, description_category_id, type_id"), {"now": now}).mappings()
        n = 0
        while chunk := rows.fetchmany(500):
            for r in chunk:
                w2.write(f"{int(r['attribute_id'])}:{int(r['description_category_id'])}:"
                         f"{int(r['type_id'])}", r["values_data"])
                n += 1
        w2.close()
        logger.info(f"✅ [export-from-pg] 字典值: {DICT_VALUES_FILE} ({n} 个条目)")
    finally:
        session.close()
```

3e. argparse/main 接线：`--export-from-pg` 参数 + `parse_args` 冲突校验（与 --export-only/--pg-only/--import-only/--coverage/--all/--limit/--offset/--force 任一同给即 `parser.error`）；main() 里**放在凭证检查之前**（同 --coverage；顺手把 --import-only 分支也上移到凭证检查之前——现有代码顺序使 --import-only 无凭证会误退出，与 :45 注释矛盾）。

3f. `collect_coverage` 剔除死节点：查 `warm_dead_nodes`（表不存在则视空），`total_pairs -= dead_pairs`，返回新增 `"dead_excluded": len(dead_pairs & original_total)`；`format_coverage_report` 加一行 `已剔除失效类目(400): N`。

- [ ] **Step 4: 跑测试确认通过 + lint**

Run: `cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_warm_cache_pg_write_v073.py -q` → 全 PASS
Run: `cd worker && ../skill/.venv314/bin/python -m ruff check scripts/warm_category_cache.py tests/test_warm_cache_pg_write_v073.py --select E,F,W --ignore E501` → 0 违例
回归：`... -m pytest tests/test_attribute_cache_writeback_v069.py tests/test_dict_cache_three_bucket.py -q`（warm 既有消费方）→ PASS

---

### Task 2: init_data.py + ozon_category_query.py 同款 CAST（含 /mappings/lookup 端点复活）

**Files:**
- Modify: `worker/scripts/init_data.py:418`（`:schema::jsonb` → `CAST(:schema AS jsonb)`）、`:454`（`:vals::jsonb` → `CAST(:vals AS jsonb)`）
- Modify: `worker/src/utils/ozon_category_query.py:1369`（`:kw::text[]` → `CAST(:kw AS text[])`）
- Test: `worker/tests/test_bare_bind_cast_ban_v073.py`（新建）+ PG 行为用例（同文件）

**Interfaces:** 无对外新接口；`get_category_mapping_by_keywords(source_keywords: list[str], min_overlap=1, top_k=10) -> list[dict]` 语义从「恒 [] 」恢复为「真查询」。

- [ ] **Step 1: 写失败测试**

```python
"""v0.73 W1 补漏: init_data / mappings_lookup 的裸 bind cast 修复回归。"""
import os
import re
import sys
import pathlib
import pytest

from sqlalchemy import text

_ROOT = pathlib.Path(__file__).resolve().parents[1]

_PG_URL = os.environ.get(
    "PGDATABASE_URL", "postgresql://postgres:localdev123@localhost:5433/ozon")


def _pg_ready():
    try:
        import psycopg2
        psycopg2.connect(_PG_URL, connect_timeout=3).close()
        return True
    except Exception:
        return False


pg = pytest.mark.skipif(not _pg_ready(), reason="local PG 5433 不可达")


def test_init_data_and_query_have_no_bare_bind_cast():
    for rel in ("scripts/init_data.py", "src/utils/ozon_category_query.py"):
        src = (_ROOT / rel).read_text(encoding="utf-8")
        hits = re.findall(r":[A-Za-z_]\w*::[a-zA-Z]+", src)
        assert hits == [], f"{rel} 发现裸 bind cast: {hits}"


@pg
def test_category_mapping_keyword_lookup_actually_queries():
    """端点此前恒空：SQLAlchemy 不识别 :kw::text[] → 异常被 except 吞掉 return []。"""
    os.environ.setdefault("PGDATABASE_URL", _PG_URL)
    sys.path.insert(0, str(_ROOT / "src"))
    from storage.database.db import get_session
    from storage.database.shared.model import CategoryMapping
    from utils.ozon_category_query import OzonCategoryQuery

    s = get_session()
    try:
        row = CategoryMapping(
            source_category_leaf="__probe_cast_v073__",
            source_category_path="__probe_cast_v073__",
            source_keywords=["__probecastkw__"],
            description_category_id=-999011, type_id=-999011,
            category_path_zh="", category_path_ru="",
            confidence=0.9, success_count=1, fail_count=0,
            source="learned_approved", is_active=True,
        )
        s.add(row)
        s.commit()
        hits = OzonCategoryQuery().get_category_mapping_by_keywords(
            ["__probecastkw__"], top_k=5)
        assert hits, "keyword lookup 仍返回空——CAST 修复未生效"
        assert hits[0]["source_category_leaf"] == "__probe_cast_v073__"
    finally:
        s.rollback()
        s.execute(text("DELETE FROM category_mapping WHERE source_category_leaf='__probe_cast_v073__'"))
        s.commit()
        s.close()
```

（注意：`text` 需在文件头 `from sqlalchemy import text`；CategoryMapping 模型字段名以 `worker/src/storage/database/shared/model.py` 实际定义为准，写测试前先读该模型，必填字段缺失会 INSERT 失败——这是唯一允许的"看实物微调测试夹具"。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_bare_bind_cast_ban_v073.py -q`
Expected: FAIL——静态用例报 3 处 hit；行为用例 `hits` 为空。

- [ ] **Step 3: 实现**——三处文本替换（init_data:418/454、ozon_category_query:1369），其余零改动。

- [ ] **Step 4: 跑测试确认通过 + lint** + 回归 `tests/test_category_key_v071.py tests/test_commission_resolver.py -q`（ozon_category_query 既有消费方）→ PASS；ruff 两文件 0 违例。

---

### Task 3: cos-update.sh 自举与版本传导 + cd.yml 打包 docs/

**Files:**
- Modify: `deploy/cos-update.sh`（W4 自举 + W5 export VERSION/剥 v）
- Modify: `.github/workflows/cd.yml`（cos-deploy 打包清单加 `docs`）
- Test: `worker/tests/test_cos_update_invariants_v073.py`（新建，纯文本断言 + bash -n）

**Interfaces:** 无代码接口；行为契约——①旧脚本跑新包：下载后、备份/解压/构建**之前**，若包内 `deploy/cos-update.sh` 与当前脚本不同且 `COS_UPDATE_EXECED!=1` → `exec env COS_UPDATE_EXECED=1 bash <tmp新版>` 重跑全流程；②manifest 与指定版本两条路径的 VERSION 一律剥 v；compose build 前 `export VERSION`（build arg 与镜像 tag 同源，tag 从 latest 变为具体版本）；③本地 VERSION 文件比较前同样剥 v（兼容服务器现存 `v0.72.0` 格式）。

- [ ] **Step 1: 写失败测试**

```python
"""v0.73 W4/W5: cos-update.sh 自举 + VERSION 传导 / cd.yml 打包 docs 不变量。"""
import pathlib
import subprocess

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "deploy" / "cos-update.sh"
_CD = _ROOT / ".github" / "workflows" / "cd.yml"


def test_script_syntax_ok():
    r = subprocess.run(["bash", "-n", str(_SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_bootstrap_exec_guard_present():
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "COS_UPDATE_EXECED" in src, "缺自举防环 env"
    assert "exec env COS_UPDATE_EXECED=1" in src, "缺 exec 自举"
    # 自举必须发生在备份/解压/构建之前（tar -xzf 抽脚本在校验点之前）
    boot = src.index("COS_UPDATE_EXECED=1")
    for marker in (" BACKUP_PATH=", "tar -xzf \"$TMP_DIR/$PKG\" -C \"$ROOT_DIR\""):
        assert src.index(marker) > boot, f"自举必须先于 {marker.strip()}"


def test_version_stripped_and_exported():
    src = _SCRIPT.read_text(encoding="utf-8")
    assert src.count('${VERSION#v}') >= 2, "manifest/本地两处 VERSION 都要剥 v"
    assert "\nexport VERSION" in src or "export VERSION\n" in src, "compose build 前须 export VERSION"


def test_cd_packages_docs():
    src = _CD.read_text(encoding="utf-8")
    assert re.search(r"deploy\s+worker\s+webui\s+docs\b", src), \
        "部署包必须含 docs/（runbook 服务器可见）"
```

- [ ] **Step 2: 跑测试确认失败**（4 用例中除 syntax 外全 FAIL）

- [ ] **Step 3: 实现**

3a. cd.yml Package 步骤的 tar 命令改为 `deploy worker webui docs`（一行改动）。

3b. cos-update.sh：
- 读 manifest 分支 `:61` 取 VERSION 后加 `VERSION="${VERSION#v}"`；指定版本分支 `:53` 已剥 v 保持；`:72` 本地版本读入后加 `LOCAL_VERSION="${LOCAL_VERSION#v}"`。
- 下载完成后（`:93` sha 校验之后）、备份（`:137`）之前插入自举块：

```bash
# ── 3.5 v0.73 W4: 自举——包内脚本比当前新则 exec 新版重跑 ──
# 此前: 解压覆盖运行中的脚本 → bash 后续读到新旧混合字节（v0.64 升级
# 白费 1h 事故根因）。自举后所有变更性操作都在新版逻辑下执行。
if [ "${COS_UPDATE_EXECED:-0}" != "1" ]; then
  tar -xzf "$TMP_DIR/$PKG" -C "$TMP_DIR" deploy/cos-update.sh 2>/dev/null || true
  if [ -f "$TMP_DIR/deploy/cos-update.sh" ] && ! cmp -s "$TMP_DIR/deploy/cos-update.sh" "${BASH_SOURCE[0]}"; then
    log "检测到包内新版 cos-update.sh，自举重启以新版逻辑继续…"
    exec env COS_UPDATE_EXECED=1 bash "$TMP_DIR/deploy/cos-update.sh" "$@"
  fi
fi
```

  ⚠️ 注意自举重跑会重新下载包（多一次下载，换取单一路径简单可靠；exec 不触发旧脚本 EXIT trap，TMP 不被误删）。
- `:206` `docker compose build` 之前加 `export VERSION`（放「── 6. 优雅重建」段首，附注释：compose `${VERSION:-dev}` build arg + `${VERSION:-latest}` image tag 同源）。

- [ ] **Step 4: 跑测试确认通过** + `bash -n` 已含用例；手工 dry 阅一遍最终脚本改动 diff。

---

### Task 4: LearningRecordInput 补 moderation_status（学习闸 approved 分支复活）

**Files:**
- Modify: `worker/src/graphs/state.py`（`class LearningRecordInput`，:934-974 区域，在 `status` 字段后插入）
- Test: `worker/tests/test_learning_input_moderation_v073.py`（新建，纯 mock）

**Interfaces:** `LearningRecordInput.moderation_status: str = ""`（与 GlobalState:61 同名同型）。

- [ ] **Step 1: 写失败测试**

```python
"""v0.73 W8: LearningRecordInput 补 moderation_status——langgraph 按 Input 过滤
channel，此前未声明 → learning_record_node 里 approved 分支恒不可达，全靠
upload_status=success+product_id 兜底（ozon_status_node approved 时恰好都写）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graphs.state import LearningRecordInput
from graphs.nodes.learning_record_node import _is_real_upload_success


def test_input_declares_moderation_status():
    assert "moderation_status" in LearningRecordInput.model_fields
    assert LearningRecordInput().moderation_status == ""


def test_approved_branch_reachable_with_declared_field():
    class _S:
        moderation_status = "approved"
        status = "imported"
        upload_status = "success"
        product_id = "123"
    assert _is_real_upload_success(_S()) is True


def test_approved_branch_not_shadowed_by_default():
    """moderation_status 缺省（旧信封）时行为不变：走 status/upload_status 回退。"""
    class _S:
        moderation_status = ""
        status = "imported"
        upload_status = "success"
        product_id = "123"
    assert _is_real_upload_success(_S()) is True
```

- [ ] **Step 2: 跑测试确认失败**（`test_input_declares_moderation_status` FAIL：字段不存在）

- [ ] **Step 3: 实现**——state.py LearningRecordInput 的 `status` 字段（:944）后插入：

```python
    # ✅ v0.73 W8: 补声明——langgraph 按节点 Input 过滤 channel，此前 moderation_status
    # 未声明 → learning 侧 getattr 恒空，_is_real_upload_success 的 approved 分支
    # 恒不可达（全靠 upload_status=success+product_id 兜底）。与 GlobalState:61 同名同型。
    moderation_status: str = Field(default="", description="Ozon审核状态 (approved/pending/error)")
```

- [ ] **Step 4: 跑测试确认通过** + 回归 `tests/test_learning_record_gate.py -q`（既有学习闸用例）→ PASS；ruff 0 违例。

---

### Task 5: 文档收口 —— runbook 新流程 + CHANGELOG + AGENTS

**Files:**
- Modify: `docs/CACHE-WARM-RUNBOOK.md`（导出流程改为 export-from-pg；死节点表说明；覆盖率口径）
- Modify: `CHANGELOG.md`（0.73.0 未 tag 段追加本批条目——动前 `ls -la CHANGELOG.md` 看 mtime，若刚被并行会话改过，读最新内容再追加，勿覆盖）
- Modify: `AGENTS.md`（v0.73.0 顶部块加一节，同样先看 mtime）

**Interfaces:** 无。引用 Task A/C 定稿的契约词：`--export-from-pg`、`warm_dead_nodes` 表、`dead_excluded` 覆盖率键、`COS_UPDATE_EXECED`。

- [ ] **Step 1: 读 runbook 全文与 CHANGELOG 0.73.0 段、AGENTS 顶部块现状**
- [ ] **Step 2: runbook 更新**——「预热→导出→上 COS」流程改为：分片 `--pg-only` 预热 → **`--export-from-pg`**（秒级、零 API、无需凭证）→ 上 COS；--export-only 保留但标注「边拉边导，仅限预热同进程采集」；新增 warm_dead_nodes 小节（自动建表/幂等/coverage 已剔除/看护阈值可回到全量分母）；新增 cos-update 升级自会自动 `--import-only` 灌缓存的提醒。
- [ ] **Step 3: CHANGELOG 追加**（0.73.0 段，修复 bullet ×5：warm SQL cast×4+init_data×2+mappings_lookup 端点复活 / export-from-pg / 死节点跳过表 / cos-update 自举+VERSION / LearningRecordInput.moderation_status + cd.yml 打包 docs）
- [ ] **Step 4: AGENTS 顶部块追加**一节「W1-W8 部署修复（随 0.73.0 同车）」，含三行硬规则：改 warm/init_data 写 SQL 前必读 `sqlalchemy-jsonb-cast-trap`（bind 后禁 `::`，一律 CAST）；runbook 流程已换 export-from-pg；cos-update 已自举（旧脚本跑新包安全）。

---

## 主会话收口（subagent 全部完成后）

- [ ] 逐任务 review diff（对应计划验收点），确认无他人 WIP 文件混入
- [ ] 全量验证：`cd worker && PGDATABASE_URL="postgresql://postgres:localdev123@localhost:5433/ozon" PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/ -q`（跑前 `lsof -iTCP:5433 -sTCP:LISTEN` 核实库；若检测到另一会话正在跑全量回归，降级为任务 4 个新测试文件 + 既有相关文件）
- [ ] `../skill/.venv314/bin/python -m ruff check scripts/warm_category_cache.py scripts/init_data.py src/utils/ozon_category_query.py src/graphs/state.py tests/test_warm_cache_pg_write_v073.py tests/test_bare_bind_cast_ban_v073.py tests/test_cos_update_invariants_v073.py tests/test_learning_input_moderation_v073.py --select E,F,W --ignore E501`
- [ ] `../skill/.venv314/bin/python scripts/gen_api_docs.py --check`（兜底确认零 API 漂移）
- [ ] 逐文件提交 5 个 commit：
  1. `fix(worker): W1 bind cast 根治——init_data/mappings_lookup 三处 :x::jsonb|text[] 改 CAST + 端点复活回归`
  2. `fix(worker): warm 缓存三修——写库 CAST + --export-from-pg 从 PG 导出 + 400 死节点永久跳过表（coverage 分母剔除）`
  3. `fix(deploy): cos-update 自举防自我覆盖 + VERSION 剥v/export 传导 + 部署包收编 docs/`
  4. `fix(worker): LearningRecordInput 补 moderation_status——学习闸 approved 分支不再被 channel 过滤吞掉`
  5. `docs(plan): W1-W8 修复计划 + runbook export-from-pg 流程 + CHANGELOG/AGENTS 收口`
