"""deploy/docker-compose.yml 卫生守卫（v0.75 部署加固防复发闸）。

锁定的不变式全部来自真实事故——改 compose 删掉任何一项会让本测试红：
- v0.72 40G 盘事故：json-file 日志无上限无限增长 → 全部 service 必须封顶
  （当时修复漏了 postgres，2026-09-11 补）；
- 2026-09-11 I/O 雪崩 9h 事故：PG 全默认参数 + 全服务零资源限制 + 宿主端口
  5433 与开发惯例撞车（测试套件直连生产库 18h 的通道）→ postgres 必须带
  调参 command / 关键容器必须 mem_limit / 发布端口不得为 5433。
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
