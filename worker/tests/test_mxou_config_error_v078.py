"""批D fix/mxou-downgrade-visibility-v1（v0.78）：配置类生图错误响亮化 + ledger 成败 + 生图入口日志。

生产取证 I5（docs/PLAN-image-source-hardening-v1.md §0）：
- 「价格尚未由管理员配置 / model_not_found / 无可用渠道」类**配置性错误**被当普通
  失败重试后静默降级（48h 69 次 400 / 54 次降级，无任何告警），主槽最多 ~15 真实 POST；
- mxou_call_ledger 只记调用不记成败（有记录会被误判「已生效」）；
- 生图节点入口静默 return（draft/token 缺失无日志）。

覆盖：
1. MxouModelConfigError：body 特征命中 → 该模型零重试零降级直接抛；
   call_mxou_image_api 编排层响亮告警（logger.error + 每模型每小时去重
   capture_task_event）后跳过该模型继续 fallback 链；链耗尽且终态为配置错误 → 上抛。
2. 回归：非配置类失败的重试/降级行为逐字保持（banana 兜底可用性不变）。
3. ledger：record_call 返回 ledger_id；finish_call 回写 outcome/duration_ms（容错）；
   mxou_api 每模型段回写 ok/failed/config_error。
4. 幂等迁移：mxou_call_ledger 加 outcome/duration_ms 列（IF NOT EXISTS，跑两遍不炸）。
5. gen 节点：入口静默分支有日志；main 槽陈旧模型文案修为实际模型名；主槽
   node-level 循环对 MxouModelConfigError 立即 break。

运行（纯 mock，无 PG、无网络）：
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest \\
        tests/test_mxou_config_error_v078.py -q
"""
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

# ═══ 公共桩 ═══

_LEDGER_MODULE = "services.mxou_ledger_service"

_BODY_CONFIG = "400: 模型价格尚未由管理员配置，请联系管理员"  # newapi 未配价 400 形态
_BODY_ORDINARY = "upstream error: do request failed"  # 普通失败（须保持重试+降级）


class _R:
    """fake requests.Response"""

    def __init__(self, code=200, j=None, text=""):
        self.status_code = code
        self._j = j or {}
        self.text = text

    def json(self):
        return self._j


class _ScriptedSession:
    """按脚本逐次返回响应并计数 POST 的 fake session。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.post_calls = []  # [(url, payload)]

    def post(self, url, **kw):
        self.post_calls.append((url, kw.get("json") or {}))
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


def _ok_image(url="http://img/ok.png"):
    return _R(200, {"status": "succeeded", "results": [{"url": url}]})


def _config_400():
    return _R(400, {}, text=_BODY_CONFIG)


def _ordinary_400():
    return _R(400, {}, text=_BODY_ORDINARY)


@pytest.fixture(autouse=True)
def _mxou_env(monkeypatch):
    """余额放行 + 限流放行 + 零等待 + 告警去重表清空（用例间隔离）。"""
    import utils.mxou_api as mxou_api

    monkeypatch.setattr(mxou_api, "get_mxou_balance", lambda t: 100.0)
    monkeypatch.setattr(mxou_api, "mxou_acquire", lambda t: None)
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    monkeypatch.setattr(mxou_api, "_MODEL_CONFIG_ALERT_TS", {})
    yield


def _install_ledger(monkeypatch, ledger_id=101):
    """注入 fake 台账服务：record_call 返回固定 id；finish_call 记录调用。"""
    finished = []

    def _record_call(**kwargs):
        return ledger_id

    def _finish_call(ledger_id_, *, outcome, duration_ms=None):
        finished.append({"id": ledger_id_, "outcome": outcome, "duration_ms": duration_ms})

    mod = types.ModuleType(_LEDGER_MODULE)
    mod.record_call = _record_call
    mod.finish_call = _finish_call
    monkeypatch.setitem(sys.modules, _LEDGER_MODULE, mod)
    return finished


# ═══ 1. 配置类错误判定 ═══

def test_config_error_body_detection():
    from utils.mxou_api import _is_model_config_error_body

    assert _is_model_config_error_body(_BODY_CONFIG) is True
    assert _is_model_config_error_body("400: 该模型未配价") is True
    assert _is_model_config_error_body('{"error":{"code":"model_not_found"}}') is True
    assert _is_model_config_error_body("No available channel for model gpt-image-2.5") is True
    assert _is_model_config_error_body("无可用渠道") is True
    assert _is_model_config_error_body(_BODY_ORDINARY) is False
    assert _is_model_config_error_body("") is False
    assert _is_model_config_error_body(None) is False


def test_config_error_exception_carries_model_and_body():
    from utils.mxou_api import MxouModelConfigError

    exc = MxouModelConfigError(model="gpt-image-2.5", body=_BODY_CONFIG)
    assert exc.model == "gpt-image-2.5"
    assert "价格尚未由管理员配置" in str(exc)


# ═══ 2. 单模型快停：零重试 ═══

def test_model_config_error_zero_retry(monkeypatch):
    """400 + 未配价 body → MxouModelConfigError 直接抛，max_retries=2 仍只 1 次 POST。"""
    import utils.mxou_api as mxou_api

    _install_ledger(monkeypatch)
    session = _ScriptedSession([_config_400()])
    monkeypatch.setattr(mxou_api, "_get_session", lambda: session)

    with pytest.raises(mxou_api.MxouModelConfigError) as ei:
        mxou_api._call_image_with_model(
            token="tok", prompt="p", ref_images=None, aspect_ratio="3:4",
            timeout=5, max_retries=2, model="gpt-image-2.5",
        )
    assert ei.value.model == "gpt-image-2.5"
    assert len(session.post_calls) == 1, "配置类错误必须零重试（1 POST 即快停）"


def test_ordinary_400_still_retries(monkeypatch):
    """回归：普通 400 body → 现有重试行为逐字保持（max_retries=2 → 3 POST）。"""
    import utils.mxou_api as mxou_api

    _install_ledger(monkeypatch)
    session = _ScriptedSession([_ordinary_400()])
    monkeypatch.setattr(mxou_api, "_get_session", lambda: session)

    out = mxou_api._call_image_with_model(
        token="tok", prompt="p", ref_images=None, aspect_ratio="3:4",
        timeout=5, max_retries=2, model="gpt-image-2.5",
    )
    assert out is None
    assert len(session.post_calls) == 3, "普通失败保持既有重试次数（1+2）"


# ═══ 3. 编排层：跳过坏模型继续链 + 终态上抛 + 响亮告警 ═══

def test_primary_config_error_falls_through_to_working_fallback(monkeypatch):
    """主模型配置错 → 1 POST 快停，fallback 链继续，fast 成功 → 返回 URL（共 2 POST）。"""
    import utils.mxou_api as mxou_api

    _install_ledger(monkeypatch)
    session = _ScriptedSession([_config_400(), _ok_image()])
    monkeypatch.setattr(mxou_api, "_get_session", lambda: session)

    url = mxou_api.call_mxou_image_api(
        token="tok", prompt="p", model="gpt-image-2.5", timeout=5,
    )
    assert url == "http://img/ok.png"
    models = [p["model"] for _, p in session.post_calls]
    assert models == ["gpt-image-2.5", "nano-banana-fast"], (
        f"主模型 1 POST + 链内首个可用模型即返回，实际 {models}"
    )


def test_all_models_config_error_raises_after_single_post_each(monkeypatch):
    """三级全配置错 → 每模型 1 POST（共 3）后上抛 MxouModelConfigError（终态快停）。"""
    import utils.mxou_api as mxou_api

    _install_ledger(monkeypatch)
    session = _ScriptedSession([_config_400()])
    monkeypatch.setattr(mxou_api, "_get_session", lambda: session)

    with pytest.raises(mxou_api.MxouModelConfigError):
        mxou_api.call_mxou_image_api(
            token="tok", prompt="p", model="gpt-image-2.5", timeout=5,
        )
    models = [p["model"] for _, p in session.post_calls]
    assert models == ["gpt-image-2.5", "nano-banana-fast", "nano-banana-2-lite"]
    assert len(session.post_calls) == 3, (
        f"配置错误全链必须每模型仅 1 POST（旧行为 ~15），实际 {len(session.post_calls)}"
    )


def test_ordinary_failure_chain_regression(monkeypatch):
    """回归：全链普通失败 → 不抛、返回 None，重试/降级次数逐字保持
    （主 2 + fast 2 + lite 2 = 6 POST，max_retries=1）。"""
    import utils.mxou_api as mxou_api

    _install_ledger(monkeypatch)
    session = _ScriptedSession([_ordinary_400()])
    monkeypatch.setattr(mxou_api, "_get_session", lambda: session)

    out = mxou_api.call_mxou_image_api(
        token="tok", prompt="p", model="gpt-image-2.5", timeout=5, max_retries=1,
    )
    assert out is None
    assert len(session.post_calls) == 6, (
        f"普通失败保持既有 POST 次数 2+2+2，实际 {len(session.post_calls)}"
    )


def test_mixed_ordinary_and_config_chain(monkeypatch):
    """混合：主模型普通失败（重试保持）→ fast 配置错（1 POST 跳过）→ lite 成功。"""
    import utils.mxou_api as mxou_api

    _install_ledger(monkeypatch)
    session = _ScriptedSession(
        [_ordinary_400(), _ordinary_400(), _config_400(), _ok_image("http://img/lite.png")]
    )
    monkeypatch.setattr(mxou_api, "_get_session", lambda: session)

    url = mxou_api.call_mxou_image_api(
        token="tok", prompt="p", model="gpt-image-2.5", timeout=5, max_retries=1,
    )
    assert url == "http://img/lite.png"
    models = [p["model"] for _, p in session.post_calls]
    assert models == [
        "gpt-image-2.5", "gpt-image-2.5",  # 普通 400 重试 2 次（保持）
        "nano-banana-fast",                 # 配置错 1 POST 快停
        "nano-banana-2-lite",               # 成功
    ]


def test_config_error_loud_log_and_deduped_event(monkeypatch, caplog):
    """配置错 → logger.error 响亮 + capture_task_event('image_model_config_error')；
    同模型 1 小时内第二次只记日志不重复发事件。"""
    import logging

    import utils.mxou_api as mxou_api

    events = []
    import utils.sentry_setup as sentry_setup
    monkeypatch.setattr(sentry_setup, "capture_task_event",
                        lambda *a, **k: events.append((a, k)))

    session = _ScriptedSession([_config_400()])
    monkeypatch.setattr(mxou_api, "_get_session", lambda: session)

    with caplog.at_level(logging.ERROR, logger="utils.mxou_api"):
        with pytest.raises(mxou_api.MxouModelConfigError):
            mxou_api.call_mxou_image_api(
                token="tok", prompt="p", model="nano-banana-2-lite", timeout=5,
            )
        # 第二次（链内无 fallback → 直接上抛）：事件去重
        with pytest.raises(mxou_api.MxouModelConfigError):
            mxou_api.call_mxou_image_api(
                token="tok", prompt="p", model="nano-banana-2-lite", timeout=5,
            )

    assert any("配置" in r.message and r.levelno == logging.ERROR
               for r in caplog.records), "必须 logger.error 响亮（进 Sentry）"
    assert len(events) == 1, "同模型每小时事件必须去重（2 次错误只发 1 条）"
    args, kwargs = events[0]
    assert args[0] == "image_model_config_error"
    assert kwargs.get("level") == "error"
    assert kwargs.get("model") == "nano-banana-2-lite"


# ═══ 4. ledger 成败 ═══

def test_record_mxou_call_returns_ledger_id(monkeypatch):
    import utils.mxou_api as mxou_api

    _install_ledger(monkeypatch, ledger_id=77)
    assert mxou_api._record_mxou_call("tok", "image_gen:m1", model="m1") == 77


def test_record_mxou_call_returns_none_on_ledger_failure(monkeypatch):
    """台账服务炸 → 返回 None，绝不影响业务（红线回归）。"""
    import utils.mxou_api as mxou_api

    def _boom(**kwargs):
        raise RuntimeError("ledger down")

    mod = types.ModuleType(_LEDGER_MODULE)
    mod.record_call = _boom
    mod.finish_call = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, _LEDGER_MODULE, mod)
    assert mxou_api._record_mxou_call("tok", "chat") is None


def test_image_wrapper_finishes_outcome(monkeypatch):
    """每模型段回写：成功 ok / 普通失败 failed / 配置错 config_error。"""
    import utils.mxou_api as mxou_api

    finished = _install_ledger(monkeypatch, ledger_id=9)

    # 成功
    session = _ScriptedSession([_ok_image()])
    monkeypatch.setattr(mxou_api, "_get_session", lambda: session)
    assert mxou_api.call_mxou_image_api(token="t", prompt="p", model="gpt-image-2.5") is not None
    assert finished[-1]["id"] == 9
    assert finished[-1]["outcome"] == "ok"
    assert isinstance(finished[-1]["duration_ms"], int) and finished[-1]["duration_ms"] >= 0

    # 普通失败（含降级耗尽）
    session = _ScriptedSession([_ordinary_400()])
    monkeypatch.setattr(mxou_api, "_get_session", lambda: session)
    assert mxou_api.call_mxou_image_api(
        token="t", prompt="p", model="gpt-image-2.5", timeout=5, max_retries=1) is None
    assert finished[-1]["outcome"] == "failed"

    # 配置错
    session = _ScriptedSession([_config_400()])
    monkeypatch.setattr(mxou_api, "_get_session", lambda: session)
    with pytest.raises(mxou_api.MxouModelConfigError):
        mxou_api.call_mxou_image_api(token="t", prompt="p", model="nano-banana-2-lite", timeout=5)
    assert finished[-1]["outcome"] == "config_error"


def test_chat_finishes_outcome(monkeypatch):
    """chat：成功 → ok；普通 4xx → failed（POST 返回/异常处回写）。"""
    import utils.mxou_api as mxou_api

    finished = _install_ledger(monkeypatch, ledger_id=5)

    session = _ScriptedSession([_R(200, {"choices": [{"message": {"content": "答"}}]})])
    monkeypatch.setattr(mxou_api, "_get_session", lambda: session)
    out = mxou_api.call_mxou_chat_api(token="t", system_prompt="s", user_prompt="u")
    assert out == "答"
    assert finished[-1]["outcome"] == "ok"

    session = _ScriptedSession([_ordinary_400()])
    monkeypatch.setattr(mxou_api, "_get_session", lambda: session)
    out = mxou_api.call_mxou_chat_api(token="t", system_prompt="s", user_prompt="u")
    assert out is None
    assert finished[-1]["outcome"] == "failed"


# ═══ 5. mxou_ledger_service.finish_call + record_call 返回 id ═══

class _FakeResult:
    def __init__(self, scalar=None, rowcount=1):
        self._scalar = scalar
        self.rowcount = rowcount

    def scalar(self):
        return self._scalar

    def fetchone(self):
        return None


class _FakeConn:
    def __init__(self, executed, fail_on=None, scalar=None, rowcount=1):
        self.executed = executed
        self._fail_on = fail_on
        self._scalar = scalar
        self._rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        sql_str = str(sql)
        if self._fail_on and self._fail_on in sql_str:
            raise RuntimeError(f"模拟 DB 失败: {self._fail_on}")
        self.executed.append((sql_str, dict(params or {})))
        return _FakeResult(scalar=self._scalar, rowcount=self._rowcount)

    def commit(self):
        pass


class _FakeEngine:
    def __init__(self, fail_on=None, scalar=None, rowcount=1):
        self.executed = []
        self._fail_on = fail_on
        self._scalar = scalar
        self._rowcount = rowcount

    def _conn(self):
        return _FakeConn(self.executed, fail_on=self._fail_on,
                         scalar=self._scalar, rowcount=self._rowcount)

    def connect(self):
        return self._conn()

    def begin(self):
        return self._conn()


def test_record_call_returns_inserted_id():
    from services import mxou_ledger_service as ml

    eng = _FakeEngine(scalar=42)
    with mock.patch.object(ml, "get_engine", return_value=eng):
        lid = ml.record_call(tenant_id="28", token_fp="fp", endpoint="chat", model="m")
    assert lid == 42, "record_call 必须返回 ledger_id（供 finish_call 回写）"
    sql_str, params = eng.executed[0]
    assert "INSERT INTO mxou_call_ledger" in sql_str
    assert params["outcome"] == "pending", "新行以 pending 起步，回写后变终态"


def test_record_call_returns_none_on_db_error():
    from services import mxou_ledger_service as ml

    eng = _FakeEngine(fail_on="INSERT INTO mxou_call_ledger")
    with mock.patch.object(ml, "get_engine", return_value=eng):
        assert ml.record_call(tenant_id=None, token_fp="fp", endpoint="chat") is None


def test_finish_call_updates_outcome_and_duration():
    from services import mxou_ledger_service as ml

    eng = _FakeEngine(rowcount=1)
    with mock.patch.object(ml, "get_engine", return_value=eng):
        ml.finish_call(42, outcome="config_error", duration_ms=1234)
    sql_str, params = eng.executed[0]
    assert "UPDATE mxou_call_ledger" in sql_str
    assert "outcome" in sql_str and "duration_ms" in sql_str
    assert params == {"outcome": "config_error", "duration_ms": 1234, "id": 42}


def test_finish_call_tolerant_missing_row_and_bad_id():
    """容错：行不存在（rowcount=0）/ ledger_id=None → 吞掉不抛。"""
    from services import mxou_ledger_service as ml

    eng = _FakeEngine(rowcount=0)
    with mock.patch.object(ml, "get_engine", return_value=eng):
        ml.finish_call(404, outcome="ok", duration_ms=5)  # 不抛即过
    ml.finish_call(None, outcome="ok")  # 无 id → 直接跳过（不碰 DB）


def test_finish_call_tolerant_db_error():
    from services import mxou_ledger_service as ml

    eng = _FakeEngine(fail_on="UPDATE mxou_call_ledger")
    with mock.patch.object(ml, "get_engine", return_value=eng):
        ml.finish_call(1, outcome="failed", duration_ms=1)  # 不抛即过


def test_finish_call_rejects_unknown_outcome():
    from services import mxou_ledger_service as ml

    eng = _FakeEngine()
    with mock.patch.object(ml, "get_engine", return_value=eng):
        ml.finish_call(1, outcome="bogus")
    assert eng.executed == [], "未知 outcome 必须拒绝写库（白名单 ok/failed/config_error）"


# ═══ 6. 幂等迁移 + 表结构 ═══

def test_ledger_outcome_columns_in_model():
    """model.py MxouCallLedger 必须声明 outcome（默认 pending）+ duration_ms（可空）。"""
    from storage.database.shared.model import MxouCallLedger

    cols = {c.name: c for c in MxouCallLedger.__table__.columns}
    assert "outcome" in cols and "duration_ms" in cols
    assert cols["outcome"].nullable is False
    assert cols["duration_ms"].nullable is True


def _run_migration_twice():
    import init_data as init_mod

    engines = [_FakeEngine() for _ in range(2)]
    for eng in engines:
        init_mod.migrate_ledger_outcome_v078(eng)
    return engines


def test_migration_outcome_ddl_idempotent():
    """迁移 DDL：ADD COLUMN IF NOT EXISTS outcome/duration_ms + 历史行回填 ok + 登记；跑两遍不炸。"""
    engines = _run_migration_twice()
    for eng in engines:
        sqls = [" ".join(s.split()) for s, _ in eng.executed]
        assert any(
            "ALTER TABLE mxou_call_ledger ADD COLUMN IF NOT EXISTS outcome" in s for s in sqls
        ), "缺幂等 ADD COLUMN outcome"
        assert any(
            "ALTER TABLE mxou_call_ledger ADD COLUMN IF NOT EXISTS duration_ms" in s
            for s in sqls
        ), "缺幂等 ADD COLUMN duration_ms"
        assert any(
            "UPDATE mxou_call_ledger SET outcome = 'ok'" in s for s in sqls
        ), "缺历史行回填（'ok' 保持历史可读，简报拍板口径）"
        reg = [(s, p) for s, p in eng.executed if "schema_migrations" in s]
        assert reg, "迁移执行成功后必须登记版本"
        assert reg[0][1]["version"] == "v078_ledger_outcome"


def test_migration_failure_propagates():
    """结构性 DDL 失败要向上抛（H9 fail-fast，对齐 v0772 ledger model 迁移语义）。"""

    class _BoomConn:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *a):
            return False

    class _Boom:
        def connect(self):
            return _BoomConn()

    import init_data as init_mod
    with pytest.raises(RuntimeError):
        init_mod.migrate_ledger_outcome_v078(_Boom())


def test_migration_wired_into_create_tables():
    """create_tables 必须接线 migrate_ledger_outcome_v078(engine)（防漏挂）。"""
    import inspect

    import init_data as init_mod

    src = inspect.getsource(init_mod.create_tables)
    assert "migrate_ledger_outcome_v078(engine)" in src, "create_tables 未接线 outcome 迁移"


# ═══ 7. gen 节点：入口日志 + 陈旧文案 + 主槽快停 ═══

class _ProgressStub:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


_ENTRY_CASES = [
    ("main_image_gen_node", "main_image_gen_node", "MainImageInput", "MainImageOutput", "main_image"),
    ("social_proof_gen_node", "social_proof_gen_node", "SocialProofInput", "SocialProofOutput", "social_proof_image"),
    ("comparison_gen_node", "comparison_gen_node", "ComparisonInput", "ComparisonOutput", "comparison_image"),
    ("detail_gen_node", "detail_gen_node", "DetailImageInput", "DetailImageOutput", "detail_image"),
    ("scene_1_gen_node", "scene_1_gen_node", "Scene1Input", "Scene1Output", "scene_1_image"),
    ("scene_2_gen_node", "scene_2_gen_node", "Scene2Input", "Scene2Output", "scene_2_image"),
    ("scene_3_gen_node", "scene_3_gen_node", "Scene3Input", "Scene3Output", "scene_3_image"),
]


@pytest.mark.parametrize("mod_name,fn_name,input_cls,output_cls,out_field", _ENTRY_CASES)
def test_gen_node_entry_missing_draft_token_logs(monkeypatch, caplog, mod_name, fn_name, input_cls, output_cls, out_field):
    """入口 draft/token 缺失不再静默：必须有 INFO 日志说明原因。"""
    import importlib
    import logging

    import graphs.nodes.main_image_gen_node  # noqa: F401 确保子包可导入
    mod = importlib.import_module(f"graphs.nodes.{mod_name}")
    from graphs import state_image_gen as sig

    monkeypatch.setattr(mod, "ProgressLogger", _ProgressStub)
    monkeypatch.setattr(mod, "slot_enabled", lambda *a, **k: True)

    state = getattr(sig, input_cls)(draft=None, token="")
    with caplog.at_level(logging.INFO):
        out = getattr(mod, fn_name)(state, {}, mock.Mock())

    assert getattr(out, out_field) is None
    entry_logs = [r for r in caplog.records
                  if r.levelno == logging.INFO and ("draft" in r.getMessage() or "token" in r.getMessage())]
    assert entry_logs, f"{fn_name} 入口 draft/token 缺失必须有日志（含原因），实际无"


def test_main_node_stale_model_text_uses_actual_model(monkeypatch, caplog):
    """主槽降级文案不得硬编码 gpt-image-2，须引用实际节点模型名变量。"""
    import logging

    import graphs.nodes.main_image_gen_node as mod
    from graphs.state_image_gen import MainImageInput

    calls = []

    def _fake_call(*a, **k):
        calls.append(k.get("model"))
        return None  # 主模型与所有降级都「普通失败」

    monkeypatch.setattr(mod, "call_mxou_image_api", _fake_call)
    monkeypatch.setattr(mod, "slot_enabled", lambda *a, **k: True)
    monkeypatch.setattr(mod, "get_image", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_task_id_from_config", lambda *a, **k: None)
    monkeypatch.setattr(mod, "assemble_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(mod, "merge_visual_vars", lambda *a, **k: {})
    monkeypatch.setattr(mod, "resolve_color_preset", lambda *a, **k: "")
    monkeypatch.setattr(mod, "get_image_model", lambda *a, **k: "vision-model-x")
    monkeypatch.setattr(mod, "filter_reference_images", lambda imgs, allow_competitor=False: list(imgs or []))

    state = MainImageInput(
        draft={"title": "测试商品"}, token="tok",
        original_images=["http://img/1.png"], white_bg_image=None, multi_angle_image=None,
    )
    with caplog.at_level(logging.WARNING, logger="graphs.nodes.main_image_gen_node"):
        mod.main_image_gen_node(state, {}, mock.Mock())

    assert calls == ["vision-model-x", "nano-banana-fast", "nano-banana-2-lite"]
    stale = [r for r in caplog.records if "gpt-image-2 " in r.getMessage()]
    assert not stale, f"降级文案仍硬编码 gpt-image-2: {[r.getMessage() for r in stale]}"
    assert any("vision-model-x" in r.getMessage() and "nano-banana-fast" in r.getMessage()
               for r in caplog.records), "降级文案必须带实际主模型名"


def test_main_node_breaks_loop_on_config_error(monkeypatch):
    """node-level 降级循环：call 抛 MxouModelConfigError → 立即 break（不重试坏模型）。"""
    import graphs.nodes.main_image_gen_node as mod
    from graphs.state_image_gen import MainImageInput
    import utils.mxou_api as mxou_api

    calls = []

    def _fake_call(*a, **k):
        calls.append(k.get("model"))
        if k.get("model") == "nano-banana-fast":
            # fast 也配置错（其链内 lite 同错）→ 编排层链耗尽上抛
            raise mxou_api.MxouModelConfigError(
                model="nano-banana-fast", body=_BODY_CONFIG)
        return None  # 主模型普通失败

    monkeypatch.setattr(mod, "call_mxou_image_api", _fake_call)
    monkeypatch.setattr(mod, "slot_enabled", lambda *a, **k: True)
    monkeypatch.setattr(mod, "get_image", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_task_id_from_config", lambda *a, **k: None)
    monkeypatch.setattr(mod, "assemble_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(mod, "merge_visual_vars", lambda *a, **k: {})
    monkeypatch.setattr(mod, "resolve_color_preset", lambda *a, **k: "")
    monkeypatch.setattr(mod, "get_image_model", lambda *a, **k: "gpt-image-2.5")
    monkeypatch.setattr(mod, "filter_reference_images", lambda imgs, allow_competitor=False: list(imgs or []))

    state = MainImageInput(
        draft={"title": "测试商品"}, token="tok",
        original_images=["http://img/1.png"], white_bg_image=None, multi_angle_image=None,
    )
    out = mod.main_image_gen_node(state, {}, mock.Mock())  # 不抛（节点兜住，任务不被推向失败）
    assert out.main_image is None
    assert calls == ["gpt-image-2.5", "nano-banana-fast"], (
        f"配置错后必须 break 跳过 nano-banana-2-lite，实际调用 {calls}"
    )


def test_main_node_primary_all_config_error_returns_none(monkeypatch):
    """主调用整链配置错上抛 → 节点兜住返回 None（不把任务推向失败）。"""
    import graphs.nodes.main_image_gen_node as mod
    from graphs.state_image_gen import MainImageInput
    import utils.mxou_api as mxou_api

    def _fake_call(*a, **k):
        raise mxou_api.MxouModelConfigError(model=k.get("model"), body=_BODY_CONFIG)

    monkeypatch.setattr(mod, "call_mxou_image_api", _fake_call)
    monkeypatch.setattr(mod, "slot_enabled", lambda *a, **k: True)
    monkeypatch.setattr(mod, "get_image", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_task_id_from_config", lambda *a, **k: None)
    monkeypatch.setattr(mod, "assemble_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(mod, "merge_visual_vars", lambda *a, **k: {})
    monkeypatch.setattr(mod, "resolve_color_preset", lambda *a, **k: "")
    monkeypatch.setattr(mod, "get_image_model", lambda *a, **k: "gpt-image-2.5")
    monkeypatch.setattr(mod, "filter_reference_images", lambda imgs, allow_competitor=False: list(imgs or []))

    state = MainImageInput(
        draft={"title": "测试商品"}, token="tok",
        original_images=["http://img/1.png"], white_bg_image=None, multi_angle_image=None,
    )
    out = mod.main_image_gen_node(state, {}, mock.Mock())
    assert out.main_image is None
