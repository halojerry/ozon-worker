# pounding-mcp

把 ozon-worker 的 skill CLI（19 个命令）包装成 MCP 工具，供 DeepSeek Harness（dsh）的 Agent 调用。

> 薄封装：业务逻辑（CDP 采集 / 选品引擎 / 上架组装）全在 `../skill/`，这里只做「参数映射 CLI + 调 subprocess + 返回 JSON」。

## 目录

```
pounding-mcp/
├── pyproject.toml            FastMCP 依赖 + 入口
├── cordis.patch.yml          挂载到 dsh 的 patch 配置示例
├── pounding_mcp/
│   ├── server.py             FastMCP 工厂 + 19 个工具
│   └── skill_runner.py       run_skill_command 薄封装
└── tests/
    └── test_smoke.py         冒烟测试
```

## 快速开始

```bash
# 1. 安装（建议独立 venv）
cd pounding-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. 配置 skill 路径（环境变量）
export OZON_SKILL_DIR=/Volumes/os/dev/ozon-worker/skill
export OZON_SKILL_PYTHON=/Volumes/os/dev/ozon-worker/skill/.venv314/bin/python3

# 3. 跑冒烟测试
python -m pytest tests/ -v

# 4. 手动验证单个工具
pounding-mcp   # 启动 MCP stdio 服务
```

## 挂载到 dsh

参照官方 `examples/mcp-memory`，把 `cordis.patch.yml` 的 `insert` 段合并到
`$DSH_HOME/profiles/web/cordis.patch.yml`，或临时：

```bash
dsh web --patch "$PWD/cordis.patch.yml"
```

工具在 dsh 中可见为 `mcp__pounding__<tool>`（如 `mcp__pounding__graph`）。

## 安全分级（审批在 dsh 侧）

本 server **不做审批**——它是独立进程，拿不到 dsh 的 `ctx.approval`。
审批 answerer 由 **dsh-web-app 自带**（`@deepseek-ai/dsh-host-apiproxy`，`cordis.patch.yml:105-106` 以 `api-gateway` id 挂载），非本 server 职责。
三级安全门控（read/write/destructive）由 dsh 侧的 `tools/pre-execute` 钩子实现，
依据 `docs/ozonharness/MCP-TOOLS.md` §七 的 SAFETY_MAP。

## 工具清单（19 个）

只读：`check` `list_stores` `search` `probe` `image_search` `category` `seller` `queries` `query`
写：`set_store` `set_token` `set_ak` `get_ak` `graph` `update`
动态（提交类 flag 触发写）：`search(auto_submit)` `follow` `discover` `discover_multi`
破坏性：`cleanup`

详见 `../docs/ozonharness/MCP-TOOLS.md`。
