"""v0.73 W4/W5: cos-update.sh 自举 + VERSION 传导 / cd.yml 打包 docs 不变量。"""
import pathlib
import re
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
    for marker in ("BACKUP_PATH=", 'tar -xzf "$TMP_DIR/$PKG" -C "$ROOT_DIR"'):
        assert src.index(marker) > boot, f"自举必须先于 {marker.strip()}"


def test_version_stripped_and_exported():
    src = _SCRIPT.read_text(encoding="utf-8")
    assert 'VERSION="${VERSION#v}"' in src, "manifest 路径 VERSION 剥 v"
    assert 'LOCAL_VERSION="${LOCAL_VERSION#v}"' in src, "本地 VERSION 剥 v"
    assert "\nexport VERSION" in src or "export VERSION\n" in src, "compose build 前须 export VERSION"


def test_cd_packages_docs():
    src = _CD.read_text(encoding="utf-8")
    assert re.search(r"deploy\s+worker\s+webui\s+docs\b", src), \
        "部署包必须含 docs/（runbook 服务器可见）"
