#!/usr/bin/env python3
"""P0-1 AK 凭证反复获取根治单测（2026-09-09 审计靶点一）。

用户实机症状：AK 一失效，图搜/搜索/详情每个 CLI 命令各自弹浏览器重取，反复不断。
静态实锤的四层根因（docs/PLAN-race-duplication-audit-v1.md §2.4 靶点一）：
  1. 读写位不相交——写侧 ak_callback._save_ak 落 SKILL_ROOT/.1688-AK，读侧
     get_ak_from_file 只看 CWD 相对/老 workspace 位 → 刷新结果读取方看不到；
  2. 掩码毒化——ak_callback 回调 result 只回 display 掩码（code[:4]+"****"+code[-4:]），
     _try_refresh_ak 把掩码 set_ali_1688_ak 写进 settings.json（用户实机 settings 里
     eFhV****MDA= 的来源），settings 非空假健康（check 显示 ✅ 但 AK 不可用）；
  3. 刷新无冷却且进程间无记忆——AK 一失效每个命令各弹一次浏览器；
  4. ak_exp 存了不用——AK 尾部 14 位到期标识从不做预判，只走「请求报错→被动刷新」。

修复契约（本文件锁定）：
  - config_store 是 AK 文件存储唯一入口（resolve/read/write_through）；
  - get_active_ak 跳过不可解析的掩码候选（文件位优先，settings 兜底）；
  - _try_refresh_ak 跨进程 600s 冷却（.refresh_claim.json O_EXCL 原子占位），且
    绝不把 display 掩码写进任何存储；
  - _ensure_ak_fresh 到期前 600s 主动刷新（受同一冷却约束），已过期且冷却中 → 快速
    报 AkAuthError（人话指引），不再逐调用弹浏览器。

运行：
    cd skill && .venv314/bin/python -m pytest tests/test_ak_store_unification.py -q
"""
from __future__ import annotations

import base64
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.lib import config_store
from scripts.lib.ak_1688_client import (
    AkAuthError,
    AkConfigError,
    _ensure_ak_fresh,
    _extract_ak_keys,
    _try_refresh_ak,
    get_active_ak,
    get_ak_from_file,
    parse_ak_expiry,
)


FAR_FUTURE = "29991231235959"
EXPIRED = "20200101000000"


def _make_ak(expiry: str = FAR_FUTURE, key: str = "k" * 36) -> str:
    """构造 base64(urlsafe) AK：36 字节密钥 + 14 位到期标识（用户逆向实测格式）。"""
    return base64.urlsafe_b64encode((key + expiry).encode()).decode().rstrip("=")


@pytest.fixture()
def ak_env(tmp_path, monkeypatch):
    """全隔离：SKILL_ROOT / settings.json / home / cwd 都指向临时目录。"""
    skill_root = tmp_path / "skill"
    home = tmp_path / "home"
    cwd = tmp_path / "cwd"
    for d in (skill_root, home, cwd):
        d.mkdir()
    monkeypatch.setattr(config_store, "SKILL_ROOT", skill_root)
    monkeypatch.setattr(config_store, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.chdir(cwd)
    return {"skill_root": skill_root, "home": home, "cwd": cwd, "tmp": tmp_path}


def _store_dir(env) -> Path:
    return env["skill_root"] / ".1688-AK"


def _write_store_file(dirpath: Path, ak: str) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / ".ak_store.json"
    p.write_text(json.dumps({"ak": ak}), encoding="utf-8")
    return p


# ── 1. 存储统一：读写位收敛 ──


def test_read_finds_ak_at_writer_location(ak_env):
    """写侧（SKILL_ROOT/.1688-AK）落的文件，读侧必须能看到——CWD 无关。
    回归：旧 get_ak_from_file 的四个读位与写位完全不相交。"""
    _write_store_file(_store_dir(ak_env), "A" * 64)
    assert get_ak_from_file() == "A" * 64


def test_read_legacy_positions_still_work(ak_env):
    """旧读位（CWD 相对 / .openclaw / sourcing-inquiry）保持兼容，顺序不回退。"""
    legacy = ak_env["home"] / ".openclaw" / "workspace" / ".1688-AK"
    _write_store_file(legacy, "B" * 64)
    assert get_ak_from_file() == "B" * 64


def test_writer_location_wins_over_stale_legacy(ak_env):
    """新旧两处同时有文件：写侧新位优先（旧文件不再遮蔽刷新结果）。"""
    _write_store_file(ak_env["home"] / ".openclaw" / "workspace" / ".1688-AK", "OLD" * 22)
    _write_store_file(_store_dir(ak_env), "NEW" * 22)
    assert get_ak_from_file().startswith("NEW")


def test_write_through_updates_existing_legacy_only(ak_env):
    """write_ak_store_file 写穿「已存在」的旧读位；不存在的旧位不建目录。"""
    legacy = ak_env["home"] / ".openclaw" / "workspace" / ".1688-AK"
    _write_store_file(legacy, "OLD" * 22)
    never_dir = ak_env["skill_root"].parent / "1688-sourcing-inquiry-0.1.0" / "workspace"

    path = config_store.write_ak_store_file("C" * 64)

    assert path == _store_dir(ak_env) / ".ak_store.json"
    assert json.loads(legacy.joinpath(".ak_store.json").read_text())["ak"] == "C" * 64
    assert not never_dir.exists()


# ── 2. 掩码免疫：settings 脱敏占位值不再冒充真值 ──


def test_get_active_ak_skips_masked_settings_placeholder(ak_env):
    """settings 存有掩码毒值（eFhV****MDA= 形态）+ 文件有真值 → 取文件真值。"""
    config_store.set_ali_1688_ak("ABCD****WXYZ")
    _write_store_file(_store_dir(ak_env), "D" * 64)
    assert get_active_ak() == "D" * 64


def test_get_active_ak_masked_everywhere_raises(ak_env):
    """两处都只有掩码/缺失 → 人话报错，而非把掩码喂给签名函数炸出天书。"""
    config_store.set_ali_1688_ak("ABCD****WXYZ")
    with pytest.raises(AkConfigError):
        get_active_ak()


def test_refresh_does_not_poison_settings_with_mask(ak_env, monkeypatch):
    """回归核心：自动刷新后 settings.json 必须保持真值，绝不被 display 掩码覆盖。
    （旧 _try_refresh_ak 拿 result['ak'] 即掩码 set_ali_1688_ak——用户实机毒源。）"""
    real_new_ak = _make_ak()
    old_real = _make_ak(expiry=EXPIRED)
    config_store.set_ali_1688_ak(old_real)

    def fake_browser(**_kw):
        # 模拟 ak_callback 回调处理器：真值落文件，result 只回掩码
        config_store.write_ak_store_file(real_new_ak)
        config_store.set_ali_1688_ak(real_new_ak)
        return {"success": True, "ak": real_new_ak[:4] + "****" + real_new_ak[-4:]}

    monkeypatch.setattr("scripts.lib.ak_callback.get_ak_via_browser", fake_browser)
    assert _try_refresh_ak() is True
    assert config_store.get_ali_1688_ak() == real_new_ak
    assert "****" not in config_store.get_ali_1688_ak()


# ── 3. 跨进程刷新冷却 ──


def test_refresh_cooldown_blocks_second_browser_call(ak_env, monkeypatch):
    """同进程连续两次刷新：第二次命中冷却，浏览器只弹一次。"""
    calls = []
    real_ak = _make_ak()

    def fake_browser(**_kw):
        calls.append(1)
        config_store.write_ak_store_file(real_ak)
        return {"success": True, "ak": real_ak[:4] + "****" + real_ak[-4:]}

    monkeypatch.setattr("scripts.lib.ak_callback.get_ak_via_browser", fake_browser)
    assert _try_refresh_ak() is True
    assert _try_refresh_ak() is False
    assert len(calls) == 1


def test_cooldown_claim_is_cross_process(ak_env, monkeypatch):
    """另一进程刚刷新过（claim 文件新鲜）→ 本进程不再弹浏览器。"""
    claim = _store_dir(ak_env) / ".refresh_claim.json"
    claim.parent.mkdir(parents=True, exist_ok=True)
    claim.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
    called = []
    monkeypatch.setattr(
        "scripts.lib.ak_callback.get_ak_via_browser",
        lambda **_kw: called.append(1) or {"success": False},
    )
    assert _try_refresh_ak() is False
    assert called == []


# ── 4. ak_exp 到期预判 ──


def test_parse_ak_expiry_14_digit_marker():
    ak = _make_ak("20260915203000")
    assert parse_ak_expiry(ak) == datetime(2026, 9, 15, 20, 30, 0)


def test_parse_ak_expiry_tolerates_plain_and_garbage():
    assert parse_ak_expiry("someid:somesecret") is None
    assert parse_ak_expiry("not-a-valid-ak!!") is None
    assert parse_ak_expiry(_make_ak("notadigit12345")) is None


def test_extract_ak_keys_still_works_on_new_format():
    ak = _make_ak()
    secret, _ak_id = _extract_ak_keys(ak)[1], _extract_ak_keys(ak)[0]
    assert secret == "k" * 32


def test_ensure_fresh_passes_when_far_from_expiry(ak_env, monkeypatch):
    """距到期还有 2 小时：直接放行，零浏览器活动。"""
    _write_store_file(_store_dir(ak_env), _make_ak())
    called = []
    monkeypatch.setattr(
        "scripts.lib.ak_callback.get_ak_via_browser",
        lambda **_kw: called.append(1) or {"success": False},
    )
    assert _ensure_ak_fresh() == _make_ak()
    assert called == []


def test_ensure_fresh_proactively_refreshes_in_window(ak_env, monkeypatch):
    """临期（<600s）：主动刷新一次并改用新 AK——不再「先挨一次失败再刷」。"""
    old_ak = _make_ak(expiry=time.strftime("%Y%m%d%H%M%S", time.localtime(time.time() + 60)))
    _write_store_file(_store_dir(ak_env), old_ak)
    new_ak = _make_ak()
    calls = []

    def fake_browser(**_kw):
        calls.append(1)
        config_store.write_ak_store_file(new_ak)
        return {"success": True, "ak": new_ak[:4] + "****" + new_ak[-4:]}

    monkeypatch.setattr("scripts.lib.ak_callback.get_ak_via_browser", fake_browser)
    got = _ensure_ak_fresh()
    assert calls == [1]
    assert got == new_ak


def test_ensure_fresh_expired_with_cooldown_fails_fast(ak_env, monkeypatch):
    """已过期 + 冷却被其他进程占用：快速报 AkAuthError（人话指引），不弹浏览器。"""
    _write_store_file(_store_dir(ak_env), _make_ak(expiry=EXPIRED))
    claim = _store_dir(ak_env) / ".refresh_claim.json"
    claim.parent.mkdir(parents=True, exist_ok=True)
    claim.write_text(json.dumps({"ts": time.time()}), encoding="utf-8")
    called = []
    monkeypatch.setattr(
        "scripts.lib.ak_callback.get_ak_via_browser",
        lambda **_kw: called.append(1) or {"success": False},
    )
    with pytest.raises(AkAuthError):
        _ensure_ak_fresh()
    assert called == []
