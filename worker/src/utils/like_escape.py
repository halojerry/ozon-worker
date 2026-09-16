r"""ILIKE 通配符转义唯一入口（v0.76 inj-L1）。

用户搜索词里的 `%`/`_`/`\` 是 SQL LIKE/ILIKE 模式通配符——原样拼进
`%{q}%` 时，`q=%` 等价全表扫（行为混淆 + 资源消耗）。本模块把用户可控
输入转义为字面量语义，配套 SQL 侧 `ESCAPE '\'` 声明转义符。

用法（两 SQL 形态）：
- text() 裸 SQL：`ILIKE :kw ESCAPE '\\'`（SQL 文本内单引号包反斜杠）+
  `params["kw"] = f"%{escape_like(q)}%"`
- ORM：`col.ilike(escape_like(q), escape="\\")`

接线纪律：只接用户可控输入（HTTP q/keyword 等请求参数）；固定常量/
内部拼的模式不动。接线点盘点见
`.superpowers/sdd/PLAN-security-remediation-v1/task-29-report.md`。
"""


def escape_like(q: str) -> str:
    r"""转义 LIKE/ILIKE 通配符为字面量：`\`→`\\`、`%`→`\%`、`_`→`\_`。

    顺序硬约束：反斜杠必须最先转义——否则后插入的 `\%`/`\_` 里的 `\`
    会被二次翻倍，或用户原有 `\` 被当成后续转义符吞掉。
    非 str 输入按原样返回（调用方已在路由层 strip/判空）。
    """
    if not isinstance(q, str) or not q:
        return q
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
