"""SSTI 加固：jinja2 渲染唯一沙箱入口（Mimosa 7×high 修复，v0.81）。

背景：Mimosa L3 扫描标记 7 处「模板注入（SSTI）」——全部是
``jinja2.Template(<字符串>).render(数据)`` 形态，模板串来自
worker/config/*.json（运维 bind-mount 热加载）或模块常量，渲染变量是商品
数据（数据作变量是正确用法；扫描器 flag 的是「模板串非字面量」）。
真实风险：config 被注入恶意模板（``{{ ''.__class__.__mro__ }}`` 类 payload）
时，jinja2 默认环境可沿属性链访问敏感对象并拼进 LLM prompt。

修复：全仓统一 ``SandboxedEnvironment``——对良性模板渲染结果与默认环境
逐字一致；恶意模板访问不安全属性（``_`` 开头 / 不可信调用）时抛
``jinja2.exceptions.SecurityError``，不泄漏任何对象图。

使用纪律（新增/改动任何 prompt/模板渲染路径前必读）：
- **新增模板渲染必须走本模块** ``render_safe`` / ``render_safe_mapping``，
  禁止再直接 ``from jinja2 import Template``。
- autoescape 恒 False：prompt 是纯文本不是 HTML，开 autoescape 会把
  ``'``/``&``/``<`` 转义破坏 prompt 文本。
- 回退语义归调用方：本模块不吞沙箱异常——SecurityError 记 warning 后原样
  上抛，由调用点既有 try/except 走各自回退（默认模板 / 下一匹配层等）；
  无回退保护的调用点（scene/visual_vars 节点直渲）则与现状模板语法错误
  （TemplateSyntaxError）同为 fail-closed——恶意 config 宁可任务失败也
  不把渲染结果发给 LLM。
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)

# 模块级单例：Environment 的渲染是线程安全的（jinja2 文档保证），全仓共享零开销。
# autoescape=False 恒定——prompt 是纯文本不是 HTML（见模块 docstring）。
SANDBOX_ENV = SandboxedEnvironment(autoescape=False)

_WARN_SNIPPET = 120  # 告警里模板串截断长度（config 内容非机密，但别刷屏）


def render_safe(template_str: str, **variables: Any) -> str:
    """沙箱渲染模板（kwargs 形参版），替代 ``Template(t).render(**kw)``。

    良性模板：渲染结果与 ``jinja2.Template(...).render(...)`` 逐字一致。
    恶意模板：SecurityError 记 warning 后原样上抛——回退由调用方既有
    except 处理（见模块 docstring「回退语义归调用方」）。
    """
    return _render(template_str, variables)


def render_safe_mapping(template_str: str, variables: Mapping[str, Any]) -> str:
    """沙箱渲染模板（dict 形参版），替代 ``Template(t).render(vars_dict)``。"""
    return _render(template_str, dict(variables))


def _render(template_str: str, variables: dict[str, Any]) -> str:
    try:
        return SANDBOX_ENV.from_string(template_str).render(**variables)
    except SecurityError as e:
        logger.warning(
            "SSTI 沙箱拦截：模板访问不安全属性(%s)，template 前缀=%r",
            e,
            (template_str or "")[:_WARN_SNIPPET],
        )
        raise
