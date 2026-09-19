"""deploy/docker-compose.yml 卫生守卫（v0.75 部署加固防复发闸）。

锁定的不变式全部来自真实事故——改 compose 删掉任何一项会让本测试红：
- v0.72 40G 盘事故：json-file 日志无上限无限增长 → 全部 service 必须封顶
  （当时修复漏了 postgres，2026-09-11 补）；
- 2026-09-11 I/O 雪崩 9h 事故：PG 全默认参数 + 全服务零资源限制 + 宿主端口
  5433 与开发惯例撞车（测试套件直连生产库 18h 的通道）→ postgres 必须带
  调参 command / 关键容器必须 mem_limit / 发布端口不得为 5433。
- 2026-09-19 config 挂空事故：compose 栈从后来被删除的 worktree 目录起 →
  bind mount 源路径不存在 → Docker 静默挂空目录盖住镜像内 /app/config →
  worker「健康地空跑」→ worker healthcheck 必须含 config 存在性哨兵
  （unhealthy 可见化，不配 autoheal/自动重启）。
背景见 docs/audit/2026-09-11-io-avalanche.md。
"""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml"

# 调参键只需存在（具体数值允许按机器规格调整，不锁死）
_REQUIRED_PG_PARAMS = (
    "shared_buffers=",
    "max_wal_size=",
    "checkpoint_completion_target=",
    "effective_io_concurrency=",
)


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())


@pytest.fixture(scope="module")
def services(compose) -> dict:
    return compose["services"]


def test_all_services_log_capped(services):
    """json-file 必须封顶（v0.72 事故 + 2026-09-11 postgres 补漏）。"""
    for name, svc in services.items():
        opts = (svc.get("logging") or {}).get("options") or {}
        assert opts.get("max-size"), f"service {name} 缺 logging.options.max-size"
        assert opts.get("max-file"), f"service {name} 缺 logging.options.max-file"


def test_postgres_tuned(services):
    """postgres 必须带调参 command（全默认参数是 I/O 雪崩的放大器）。"""
    command = " ".join(services["postgres"].get("command", [])) if isinstance(
        services["postgres"].get("command"), list
    ) else str(services["postgres"].get("command", ""))
    for key in _REQUIRED_PG_PARAMS:
        assert key in command, f"postgres command 缺调参项 {key}"


def test_resource_limits_present(services):
    """关键容器必须有 mem_limit（无上限时单容器可吃满全机触发 OOM/雪崩）。"""
    for name in ("postgres", "worker", "backup-scheduler"):
        assert services[name].get("mem_limit"), f"service {name} 缺 mem_limit"
    assert services["postgres"].get("shm_size"), "postgres 缺 shm_size"


def test_no_dev_port_collision(services):
    """宿主发布端口不得为 5433（与本地开发惯例撞车=测试直连生产库通道）。"""
    for name, svc in services.items():
        for pub in svc.get("ports", []) or []:
            # 形如 "127.0.0.1:15433:5432" → 宿主端口是倒数第二段
            parts = str(pub).rsplit(":", 2)
            if len(parts) == 3:
                host_port = parts[0].split(":")[-1] if ":" in parts[0] else parts[0]
                assert host_port != "5433", (
                    f"service {name} 宿主端口 5433 与开发惯例撞车"
                    "（localhost:5433=本地 PG；详见 2026-09-11 事故报告）"
                )


def test_pg_healthcheck_follows_env(services):
    """pg_isready 用户/库名跟随容器 env，不硬编码。"""
    test_cmd = str(services["postgres"]["healthcheck"]["test"])
    assert "${POSTGRES_USER:-postgres}" in test_cmd, "healthcheck 硬编码了用户名"
    assert "${POSTGRES_DB:-ozon}" in test_cmd, "healthcheck 硬编码了库名"


# ── 2026-09-19 config 挂空事故第三层防线（0.78.0 批D）───────────────────────
# 前两层在 worker 内（FileNotFoundError 永久错误化 + 启动 _assert_critical_configs
# report-not-block）；本测试锁第三层：容器 healthcheck 感知 config 挂空。

# 哨兵文件：必须取自 main.py `_CRITICAL_CONFIG_FILES` 清单（与启动守卫同源）。
_CONFIG_SENTINEL = "/app/config/imagegen.json"

_WORKER_HC_PARAMS = {  # brief 拍板的参数纪律
    "interval": "30s",
    "timeout": "10s",
    "retries": 3,
}


def _healthcheck_cmd(hc: dict) -> str:
    """healthcheck.test → 纯命令串（list 形式剥掉前导 CMD/CMD-SHELL）。"""
    t = hc.get("test")
    if isinstance(t, list):
        return " ".join(str(x) for x in t[1:])
    return str(t or "")


def _duration_seconds(value) -> int:
    """compose duration 宽容解析：'30s'/'1m'/'90' → 秒数；解析失败返回 0。"""
    s = str(value or "").strip()
    for suffix, mult in (("ms", 0.001), ("s", 1), ("m", 60)):
        if s.endswith(suffix):
            try:
                return int(float(s[: -len(suffix)]) * mult)
            except ValueError:
                return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def test_worker_healthcheck_has_config_sentinel(services):
    """worker healthcheck 必须含 config 存在性哨兵（bind 挂空 → unhealthy 可见）。

    事故链（2026-09-19 gate 取证）：compose 栈从已删除 worktree 启动 → config
    bind 源路径不存在 → Docker 静默创建空目录盖住镜像内 /app/config → worker
    对 /api/v1/health 依旧 200「健康地空跑」，任务在 scene 节点 FileNotFoundError
    重试 4 轮烧 40s。哨兵文件 imagegen.json 在 `_CRITICAL_CONFIG_FILES` 清单内。
    """
    hc = services["worker"].get("healthcheck") or {}
    cmd = _healthcheck_cmd(hc)
    # 1) 既有健康端点检查不回归（v0.63.1 D8 统一 /api/v1/health）
    assert "/api/v1/health" in cmd, "worker healthcheck 缺 /api/v1/health 端点检查"
    assert "curl" in cmd, "worker healthcheck 应用镜像内 curl（runtime 层已安装）"
    # 2) config 存在性哨兵（本批主体）
    assert f"test -f {_CONFIG_SENTINEL}" in cmd, (
        f"worker healthcheck 缺 config 存在性哨兵 test -f {_CONFIG_SENTINEL}"
        "（bind 挂空必须 unhealthy 可见化；语义见 docs/DEPLOY.md 部署红线）"
    )
    # 3) `&&` 链式断言只在 shell 形式下成立——exec 形式会把 && 当字面参数传给
    #    curl 直接报错，锁 CMD-SHELL 防被「重构」坏
    t = hc.get("test")
    assert isinstance(t, list) and str(t[0]).upper() == "CMD-SHELL", (
        "worker healthcheck 双检查必须用 CMD-SHELL 形式（&& 链式在 exec 形式下失效）"
    )
    # 4) 参数纪律（interval/timeout/retries 定值 + start_period ≥60s 留足慢启动）
    for key, expected in _WORKER_HC_PARAMS.items():
        assert hc.get(key) == expected, f"worker healthcheck {key} 应为 {expected}"
    assert _duration_seconds(hc.get("start_period")) >= 60, (
        "worker healthcheck start_period 应 ≥60s（含 PG 就绪等待的慢启动窗口）"
    )


def test_worker_healthcheck_no_autoheal(services):
    """unhealthy 只做可见化，不配 autoheal/自动重启触发器。

    repair = 人工修 bind 源路径/重新部署——容器重启解决不了挂空的 bind mount
    （compose 会在 restart 时再次静默挂同一个空目录），盲目自愈只会掩盖事故。
    """
    for name, svc in services.items():
        labels = svc.get("labels") or {}
        offending = [k for k in labels if "autoheal" in str(k).lower()]
        assert not offending, f"service {name} 配了 autoheal 触发器 {offending}（不允许）"
