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


def test_reexec_anchors_real_install_dir():
    src = _SCRIPT.read_text(encoding="utf-8")
    # exec 行必须透传真实安装目录，否则新进程把 $TMP_DIR 当 ROOT_DIR
    # （备份/VERSION/整包解压全落 tmp、.env 读不到致 compose 保护静默跳过——终审 must-fix 1）
    assert 'COS_UPDATE_REAL_SCRIPT_DIR="$SCRIPT_DIR"' in src, "exec 未透传真实 SCRIPT_DIR"
    # 头部回正块：execed 时以真实目录重算 SCRIPT_DIR/ROOT_DIR，且必须先于 BACKUP_DIR 派生
    anchor = 'SCRIPT_DIR="$COS_UPDATE_REAL_SCRIPT_DIR"'
    assert anchor in src, "缺头部路径回正块"
    assert src.index(anchor) < src.index("BACKUP_DIR="), "路径回正块必须在 BACKUP_DIR 派生之前"
    # 回滚重建按旧版本号打 tag（镜像元数据不谎报新版本）
    assert 'export VERSION="$LOCAL_VERSION"' in src, "rollback 缺旧版本号 tag 导出"


def test_version_stripped_and_exported():
    src = _SCRIPT.read_text(encoding="utf-8")
    assert 'VERSION="${VERSION#v}"' in src, "manifest 路径 VERSION 剥 v"
    assert 'LOCAL_VERSION="${LOCAL_VERSION#v}"' in src, "本地 VERSION 剥 v"
    assert "\nexport VERSION" in src or "export VERSION\n" in src, "compose build 前须 export VERSION"


def test_cd_packages_docs():
    src = _CD.read_text(encoding="utf-8")
    assert re.search(r"deploy\s+worker\s+webui\s+docs\b", src), \
        "部署包必须含 docs/（runbook 服务器可见）"


def test_export_version_precedes_main_build_and_cleanup_excludes_running():
    src = _SCRIPT.read_text(encoding="utf-8")
    # 主流程 build（带 tail 管道的那条）必须在主流程 export VERSION（独立行）之后；
    # rollback 函数内的 export 不算数——用「独立行 export VERSION」精确定位主流程导出。
    assert src.index("\nexport VERSION\n") < \
        src.index("docker compose build --no-cache 2>&1 | tail -3"), \
        "主流程 build 必须在 export VERSION 之后（镜像 tag 同源）"
    # 清理段排除当前 VERSION 在用镜像（export 后无 latest tag，旧 grep -v latest 误伤在用镜像）
    assert '_exclude="latest"' in src and "grep -Ev" in src, \
        "镜像清理未排除当前版本 tag"
