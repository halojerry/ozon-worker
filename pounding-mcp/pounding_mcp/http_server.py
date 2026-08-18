"""HTTP 模式入口 —— 本地网关：让 webui（手动 GUI）能通过 HTTP 调 skill。

双向驱动的关键：与 stdio 模式（给 dsh agent 对话驱动）并行，
HTTP 模式暴露同一套 19 工具，webui 通过 HTTP 调用 = 手动驱动。

用法：
    python -m pounding_mcp.http_server          # 默认 127.0.0.1:8901
    VAULT 相关配置沿用 OZON_SKILL_DIR / VAULT_DIR 环境变量。
"""

from __future__ import annotations

from .server import mcp


def main() -> None:
    import asyncio
    asyncio.run(
        mcp.run_http_async(
            transport="streamable-http",
            host="127.0.0.1",
            port=8901,
        )
    )


if __name__ == "__main__":
    main()
