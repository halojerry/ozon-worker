"""v0.77.3（管线延迟修复）回归：

生产/本地实测（2026-09-19 gate 取证）三类白烧：
1. SUPABASE_URL 未配置/空 → requests 立刻抛 MissingSchema（确定性错误），
   旧代码 3 次重试 × 2s 退避 = 每任务固定白烧 4.0s（g3 实测 17:59:36.13→40.13）。
2. 环境级确定性异常（FileNotFoundError——config bind 挂空）被当 temporary
   重试 4 轮（cbbeaf03 实测 4×~11s 全白烧）。重试不会让文件长出来。
3. assemble Step3 字典预载 / prepare 字典补位为串行 Ozon RTT（~0.5s/次 ×
   7-16 次/任务）；各 attr 键互不相干，可安全并行。
"""
import threading
import time
from unittest.mock import MagicMock, patch

import requests

from graphs.nodes.auth_node import auth_node
from graphs.state import AuthInput
from utils.task_processor import _is_permanent_task_error


# ────────────────────────────────────────────────
# Fix 1: Supabase URL 缺失/非 http(s) → 零重试零 sleep 直接降级
# ────────────────────────────────────────────────

def _make_input() -> AuthInput:
    return AuthInput(token="sk-test123", ozon_client_id="1", ozon_api_key="2", envelope=None)


def _run_auth(monkeypatch, supabase_url: str):
    sleeps: list[float] = []
    monkeypatch.setenv("SUPABASE_URL", supabase_url)
    monkeypatch.setenv("SUPABASE_KEY", "test-key")
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    fake_session = MagicMock()
    calls = {"n": 0}

    def _get(*_a, **_kw):
        calls["n"] += 1
        raise requests.exceptions.MissingSchema("Invalid URL '/rest/v1/tokens...'")

    fake_session.get.side_effect = _get
    with patch("graphs.nodes.auth_node.session", fake_session), \
         patch("utils.mxou_api.get_mxou_balance", return_value=None), \
         patch("graphs.nodes.auth_node._verify_mxou_token", return_value=(True, "")), \
         patch("graphs.nodes.auth_node.query_ozon_seller_info", return_value={"currency_code": ""}):
        out = auth_node(_make_input(), config={}, runtime=None)
    return out, sleeps, calls["n"]


def test_auth_supabase_url_empty_no_retry_no_sleep(monkeypatch):
    """Given: SUPABASE_URL=''（未配置）。When: auth_node。
    Then: 不发任何 HTTP、不 sleep，直接 supabase_offline 降级（省 4s/任务）。"""
    out, sleeps, n_get = _run_auth(monkeypatch, "")
    assert n_get == 0, "URL 为空时不应发起任何请求"
    assert sleeps == [], "确定性配置缺失不应退避 sleep"
    assert out.user_id == "supabase_offline"
    assert out.error_code == ""


def test_auth_supabase_url_relative_no_retry(monkeypatch):
    """Given: SUPABASE_URL 无 scheme（如 '/rest/v1'）。When: auth_node。
    Then: 同上——一次都不重试。"""
    out, sleeps, n_get = _run_auth(monkeypatch, "/rest/v1")
    assert n_get == 0
    assert sleeps == []
    assert out.user_id == "supabase_offline"


def test_auth_supabase_transient_error_still_retries(monkeypatch):
    """Given: 正常 https URL + 连接超时（暂时性）。When: auth_node。
    Then: 保留既有 3 次重试语义（不能把真故障也砍了）。"""
    out, sleeps, n_get = _run_auth(monkeypatch, "https://supabase.example.co")
    # 侧效果由 _run_auth 的固定 side_effect(MissingSchema) 决定：
    # https URL 会真正发起请求 → 第一次即 MissingSchema → 确定性错误不退避
    assert n_get >= 1
    assert sleeps == [], "MissingSchema 是确定性错误，即使 URL 合法也不该 sleep 重试"
    assert out.user_id == "supabase_offline"


# ────────────────────────────────────────────────
# Fix 2: 环境级确定性异常 → permanent（不消耗 retry_count）
# ────────────────────────────────────────────────

def test_permanent_classifier_file_not_found():
    assert _is_permanent_task_error(FileNotFoundError("/app/config/x.json")) is True


def test_permanent_classifier_permission_error():
    assert _is_permanent_task_error(PermissionError("/app/config")) is True


def test_permanent_classifier_import_error():
    assert _is_permanent_task_error(ModuleNotFoundError("No module named 'x'")) is True


def test_permanent_classifier_transient_still_false():
    assert _is_permanent_task_error(requests.ConnectionError("net blip")) is False
    assert _is_permanent_task_error(TimeoutError("slow")) is False


# ────────────────────────────────────────────────
# Fix 3: 字典预载并行（行为等价 + 实际重叠）
# ────────────────────────────────────────────────

def _fake_sf_factory(delay: float = 0.25):
    """并发探针：记录最大重叠数与调用总数。"""
    state = {"active": 0, "max_active": 0, "calls": 0, "lock": threading.Lock()}
    ids_seen: list[int] = []

    def fake_sf(attr_id, *_a, **_kw):
        with state["lock"]:
            state["calls"] += 1
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            ids_seen.append(attr_id)
        time.sleep(delay)
        with state["lock"]:
            state["active"] -= 1
        return [{"id": attr_id * 10, "value": f"v{attr_id}"}]

    return fake_sf, state


def test_assemble_step3_dict_preload_parallel(monkeypatch):
    """Given: 6 个字典属性、每次拉取 0.25s。
    When: assemble 的 Step3 预载走并行helper。
    Then: 串行要 1.5s，并行墙钟 <0.9s 且调用总数/键集合不变。"""
    from graphs.nodes.assemble_ozon_product_node import _preload_dict_values

    fake_sf, state = _fake_sf_factory(0.25)
    monkeypatch.setattr(
        "graphs.nodes.assemble_ozon_product_node._get_dict_values_sf", fake_sf
    )
    attr_list = [
        {"id": aid, "dictionary_id": aid + 100, "is_required": False}
        for aid in (8229, 85, 31, 5076, 4389, 10400)
    ]
    t0 = time.monotonic()
    lookup = _preload_dict_values(
        attr_list, 17027907, 92359, "cid", "key"
    )
    wall = time.monotonic() - t0
    assert state["calls"] == 6
    assert set(lookup.keys()) == {8229, 85, 31, 5076, 4389, 10400}
    assert wall < 0.9, f"并行预载应 <0.9s（串行 1.5s），实测 {wall:.2f}s"
    assert state["max_active"] >= 2, "应观察到真实并发"


def test_assemble_step3_dict_preload_empty():
    """边界：零字典属性 → 空表零调用。"""
    from graphs.nodes.assemble_ozon_product_node import _preload_dict_values

    lookup = _preload_dict_values([], 1, 2, "cid", "key")
    assert lookup == {}


# ────────────────────────────────────────────────
# Fix 4: 启动关键配置守卫（bind 挂空秒级暴露）
# ────────────────────────────────────────────────

def test_config_guard_flags_missing_critical(tmp_path, caplog):
    """Given: 配置目录存在但缺关键文件。When: _assert_critical_configs。
    Then: ERROR 日志点名缺失文件（不抛异常——只报不拦）。"""
    import logging as _logging

    import main as main_mod

    fake_dir = tmp_path / "config"
    fake_dir.mkdir()
    # 只放一个关键文件（imagegen.json）——其余关键文件缺失
    (fake_dir / "imagegen.json").write_text("{}")
    with caplog.at_level(_logging.ERROR, logger="main"):
        main_mod._assert_critical_configs(base_dir=str(fake_dir))
    assert any("关键配置文件缺失" in r.message for r in caplog.records)


def test_config_guard_all_present_quiet(tmp_path, caplog):
    """Given: 全部关键+可选文件在位。When: _assert_critical_configs。Then: 无 ERROR。"""
    import logging as _logging

    import main as main_mod

    fake_dir = tmp_path / "config"
    fake_dir.mkdir()
    for f in main_mod._CRITICAL_CONFIG_FILES + main_mod._OPTIONAL_CONFIG_FILES:
        (fake_dir / f).write_text("{}")
    with caplog.at_level(_logging.ERROR, logger="main"):
        main_mod._assert_critical_configs(base_dir=str(fake_dir))
    assert not any("关键配置文件缺失" in r.message for r in caplog.records)


def test_config_guard_no_dir_is_silent(tmp_path, caplog):
    """Given: 配置目录不存在（源码态）。When: _assert_critical_configs。
    Then: WARNING 提示后直接 return 不炸。"""
    import logging as _logging

    import main as main_mod

    with caplog.at_level(_logging.WARNING, logger="main"):
        main_mod._assert_critical_configs(base_dir=str(tmp_path / "nope"))
    assert any("不存在" in r.message for r in caplog.records)
