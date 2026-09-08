#!/usr/bin/env python3
"""从 FastAPI app 的 OpenAPI 生成 docs/API-REFERENCE.md + openapi.json 快照。

用法（仓库根或 worker/ 下均可）：
    python worker/scripts/gen_api_docs.py            # 重生成 API-REFERENCE.md + 两份 openapi.json 快照
    python worker/scripts/gen_api_docs.py --check    # 只比对，漂移则 exit 1（CI Step 5d）

数据源是进程内 ``app.openapi()``（无需起服务、无需 PG）。输出刻意不含时间戳——
同一份代码必须生成逐字节相同的产物，否则 --check 会误报。

示例列的数据源优先级：schema 的 ``examples[0]`` / ``example``（Pydantic
``json_schema_extra``）> 字段 ``default`` > 按类型合成的占位值（只填 required 字段）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_SRC = REPO_ROOT / "worker" / "src"
DEFAULT_MD = REPO_ROOT / "docs" / "API-REFERENCE.md"
DEFAULT_SNAPSHOTS = (
    REPO_ROOT / "api-integration" / "openapi.json",
    REPO_ROOT / "webui" / "src" / "imports" / "openapi.json",
)
V1_PREFIX = "/api/v1"
METHOD_ORDER = {"get": 0, "post": 1, "put": 2, "patch": 3, "delete": 4}
MAX_EXAMPLE_DEPTH = 5


def load_spec() -> dict[str, Any]:
    # 导入 main 会初始化 Sentry / 日志；文档生成不需要这些副作用。
    os.environ.setdefault("SENTRY_DSN", "")
    os.environ["SENTRY_DSN"] = ""
    os.environ.setdefault("LOG_LEVEL", "ERROR")
    sys.path.insert(0, str(WORKER_SRC))
    import logging

    logging.disable(logging.WARNING)
    from main import app  # noqa: E402

    spec = app.openapi()
    # 多方法 api_route（如 newapi 代理 /api/{path}）的 operationId 由 set 迭代顺序决定，
    # 跨进程随 hash 种子漂移；按实际方法名归一，保证产物逐字节可复现。
    for item in spec.get("paths", {}).values():
        for method, op in item.items():
            if method in METHOD_ORDER and isinstance(op, dict) and op.get("operationId"):
                op["operationId"] = re.sub(r"_(get|post|put|patch|delete|head|options)$", f"_{method}", op["operationId"])
    return spec


# ── schema 工具 ────────────────────────────────────────────────


def ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def resolve(schema: dict[str, Any] | bool | None, components: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    while "$ref" in schema:
        schema = components.get(ref_name(schema["$ref"]), {})
    return schema


def type_label(schema: dict[str, Any] | bool | None, components: dict[str, Any]) -> str:
    if not isinstance(schema, dict):
        return "any"  # OpenAPI 3.1 允许布尔 schema（additionalProperties: true）
    if "$ref" in schema:
        name = ref_name(schema["$ref"])
        return f"[{name}](#schema-{name.lower()})"
    for key in ("anyOf", "oneOf"):
        if key in schema:
            parts = [type_label(s, components) for s in schema[key]]
            parts = [p for p in parts if p != "null"] + (["null"] if any(p == "null" for p in parts) else [])
            return " \\| ".join(parts)
    if "allOf" in schema:
        return " & ".join(type_label(s, components) for s in schema["allOf"])
    t = schema.get("type")
    if t == "array":
        return f"list[{type_label(schema.get('items'), components)}]"
    if t == "null":
        return "null"
    if "enum" in schema:
        return "enum(" + ", ".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"]) + ")"
    if t == "object" and "additionalProperties" in schema:
        return f"dict[str, {type_label(schema['additionalProperties'], components)}]"
    if t == "object" and schema.get("title") and "properties" in schema:
        return f"{schema['title']}（内联）"
    if not t:
        return "any"
    fmt = schema.get("format")
    return f"{t}({fmt})" if fmt else str(t)


def synth_example(schema: dict[str, Any] | None, components: dict[str, Any], depth: int = 0, seen: frozenset[str] = frozenset()) -> Any:
    """按 schema 合成最小示例。显式 examples/example/default 优先；object 只填 required 字段。"""
    if not isinstance(schema, dict) or depth > MAX_EXAMPLE_DEPTH:
        return None
    if "$ref" in schema:
        name = ref_name(schema["$ref"])
        if name in seen:
            return {}
        return synth_example(components.get(name, {}), components, depth + 1, seen | {name})
    if schema.get("examples"):
        return schema["examples"][0]
    if "example" in schema:
        return schema["example"]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            for sub in schema[key]:
                if sub.get("type") != "null":
                    return synth_example(sub, components, depth + 1, seen)
            return None
    if "allOf" in schema:
        merged: dict[str, Any] = {}
        for sub in schema["allOf"]:
            v = synth_example(sub, components, depth + 1, seen)
            if isinstance(v, dict):
                merged.update(v)
        return merged
    if "enum" in schema:
        return schema["enum"][0]
    t = schema.get("type")
    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        required = schema.get("required") or []
        keys = required if required else list(props)[:3]
        return {k: synth_example(props[k], components, depth + 1, seen) for k in keys if k in props}
    if t == "array":
        item = synth_example(schema.get("items"), components, depth + 1, seen)
        return [item] if item is not None else []
    if t == "integer":
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return False
    if t == "string":
        fmt = schema.get("format")
        return {"date-time": "2026-01-01T00:00:00Z", "date": "2026-01-01", "uuid": "00000000-0000-0000-0000-000000000000"}.get(fmt, "string")
    return None


def json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def md_cell(text: Any) -> str:
    s = str(text or "").replace("\n", " ").replace("|", "\\|").strip()
    return s


# ── 路径分组 ───────────────────────────────────────────────────


def canonical(path: str, all_paths: set[str]) -> tuple[str, str | None]:
    """返回 (规范路径, 兼容别名)。/api/v1/x 与 /x 同时存在时以前者为准。"""
    if path.startswith(V1_PREFIX):
        bare = path[len(V1_PREFIX):] or "/"
        return path, (bare if bare in all_paths else None)
    v1 = V1_PREFIX + path
    if v1 in all_paths:
        return v1, path  # 由 v1 条目渲染，此处标记为别名（调用方跳过）
    return path, None


def group_of(path: str) -> str:
    rel = path[len(V1_PREFIX):] if path.startswith(V1_PREFIX) else path
    seg = rel.strip("/").split("/")[0] if rel.strip("/") else "root"
    return seg


# ── 渲染 ───────────────────────────────────────────────────────


def render_operation(method: str, path: str, alias: str | None, op: dict[str, Any], components: dict[str, Any]) -> list[str]:
    out: list[str] = []
    title = f"`{method.upper()} {path}`"
    if op.get("deprecated"):
        title += " ⚠️ DEPRECATED"
    out.append(f"### {title}")
    summary = op.get("summary") or ""
    desc = (op.get("description") or "").strip()
    first_para = desc.split("\n\n")[0].strip() if desc else ""
    if summary and first_para and summary.strip() != first_para:
        out.append(f"{summary} — {first_para}")
    elif summary or first_para:
        out.append(summary or first_para)
    if alias:
        out.append(f"> 兼容别名：`{method.upper()} {alias}`（旧裸路径，语义相同）")

    params = op.get("parameters") or []
    if params:
        out.append("")
        out.append("**参数**")
        out.append("")
        out.append("| 名称 | 位置 | 类型 | 必填 | 说明 |")
        out.append("|---|---|---|---|---|")
        for p in params:
            out.append(
                f"| `{p['name']}` | {p.get('in')} | {type_label(p.get('schema'), components)} | "
                f"{'✓' if p.get('required') else ''} | {md_cell(p.get('description'))} |"
            )

    body = op.get("requestBody")
    if body:
        content = body.get("content", {})
        media, mschema = next(iter(content.items()), ("application/json", {}))
        schema = mschema.get("schema", {})
        out.append("")
        label = type_label(schema, components) if schema else "any"
        req = "必填" if body.get("required") else "可选"
        out.append(f"**请求体**（{media}，{req}）：{label}")
        example = synth_example(schema, components)
        if example is not None:
            out.append("")
            out.append(json_block(example))

    responses = op.get("responses") or {}
    if responses:
        out.append("")
        out.append("**响应**")
        out.append("")
        out.append("| 状态码 | 说明 | Schema |")
        out.append("|---|---|---|")
        for code in sorted(responses, key=lambda c: (not c.isdigit(), c)):
            r = responses[code]
            schema = next(iter(r.get("content", {}).values()), {}).get("schema")
            out.append(f"| {code} | {md_cell(r.get('description'))} | {type_label(schema, components) if schema else '—'} |")
        # 只对显式声明了 examples 的响应 schema 渲染示例，避免文档膨胀
        ok = responses.get("200") or responses.get("201")
        if ok:
            schema = next(iter(ok.get("content", {}).values()), {}).get("schema")
            resolved = resolve(schema, components) if schema else {}
            if resolved.get("examples") or "example" in resolved:
                out.append("")
                out.append("响应示例：")
                out.append("")
                out.append(json_block(synth_example(schema, components)))
    out.append("")
    return out


def render_schema(name: str, schema: dict[str, Any], components: dict[str, Any]) -> list[str]:
    out = [f"### {name} <a id=\"schema-{name.lower()}\"></a>"]
    if schema.get("description"):
        out.append(md_cell(schema["description"]))
    if "enum" in schema:
        out.append("枚举：" + ", ".join(f"`{json.dumps(v, ensure_ascii=False)}`" for v in schema["enum"]))
        out.append("")
        return out
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    if props:
        out.append("")
        out.append("| 字段 | 类型 | 必填 | 说明 |")
        out.append("|---|---|---|---|")
        for pname, pschema in props.items():
            desc = pschema.get("description", "")
            if "default" in pschema and pschema["default"] is not None:
                desc = f"{desc}（默认 `{json.dumps(pschema['default'], ensure_ascii=False)}`）".strip()
            out.append(f"| `{pname}` | {type_label(pschema, components)} | {'✓' if pname in required else ''} | {md_cell(desc)} |")
    if schema.get("examples"):
        out.append("")
        out.append("示例：")
        out.append("")
        out.append(json_block(schema["examples"][0]))
    out.append("")
    return out


def render_markdown(spec: dict[str, Any], version: str) -> str:
    components = spec.get("components", {}).get("schemas", {})
    paths: dict[str, Any] = spec.get("paths", {})
    all_paths = set(paths)

    groups: dict[str, list[tuple[str, str, str | None, dict[str, Any]]]] = {}
    for raw_path, item in paths.items():
        canon, alias = canonical(raw_path, all_paths)
        if canon != raw_path:
            continue  # 裸路径由对应 /api/v1 条目渲染并标注别名
        for method, op in item.items():
            if method not in METHOD_ORDER:
                continue
            groups.setdefault(group_of(canon), []).append((canon, method, alias, op))

    lines: list[str] = []
    lines.append("# Ozon Worker API 参考（自动生成）")
    lines.append("")
    lines.append(
        f"> 由 `worker/scripts/gen_api_docs.py` 从 FastAPI `app.openapi()` 生成 · 对应 v{version} · "
        f"{len(paths)} 个 path / {len(components)} 个 schema · **勿手改**（CI Step 5d 校验漂移）。"
    )
    lines.append("> 对外约定（Base URL / 鉴权 / 限流 / 错误信封 / 分页 / 版本策略）见 `docs/API-OVERVIEW.md`；"
                 "MCP 面见 `docs/MCP-SERVER.md`；交互式 Swagger `GET /docs`。")
    lines.append("")
    lines.append("规范路径为 `/api/v1/...`；带「兼容别名」的端点同时挂在旧裸路径，语义一致。"
                 "示例 JSON 只填 required 字段（schema 声明了 `examples` 的按声明渲染）。")
    lines.append("")
    lines.append("## 目录")
    lines.append("")
    for g in sorted(groups):
        n = len(groups[g])
        lines.append(f"- [{g}](#{g.lower().replace('_', '-')}) （{n}）")
    lines.append("")

    for g in sorted(groups):
        lines.append(f"## {g}")
        lines.append("")
        for canon, method, alias, op in sorted(groups[g], key=lambda x: (x[0], METHOD_ORDER[x[1]])):
            lines.extend(render_operation(method, canon, alias, op, components))

    lines.append("## Schemas")
    lines.append("")
    for name in sorted(components):
        lines.extend(render_schema(name, components[name], components))
    return "\n".join(lines).rstrip() + "\n"


def render_snapshot(spec: dict[str, Any]) -> str:
    return json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# ── CLI ────────────────────────────────────────────────────────


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_MD, help="API-REFERENCE.md 输出路径")
    ap.add_argument("--snapshot", type=Path, nargs="*", default=list(DEFAULT_SNAPSHOTS), help="openapi.json 快照输出路径（可多份）")
    ap.add_argument("--no-snapshot", action="store_true", help="不写快照")
    ap.add_argument("--check", action="store_true", help="只比对不写入，漂移则 exit 1")
    args = ap.parse_args(argv)

    version = (REPO_ROOT / "VERSION").read_text().strip()
    spec = load_spec()
    targets: list[tuple[Path, str]] = [(args.out, render_markdown(spec, version))]
    if not args.no_snapshot:
        snap = render_snapshot(spec)
        targets += [(p, snap) for p in args.snapshot]

    if args.check:
        drift = [p for p, content in targets if not p.exists() or p.read_text() != content]
        if drift:
            print("API 文档/快照与代码不一致，请运行 `python worker/scripts/gen_api_docs.py` 并提交：")
            for p in drift:
                print(f"  - {_rel(p)}")
            return 1
        print(f"API 文档与快照一致（{len(spec.get('paths', {}))} paths）")
        return 0

    for p, content in targets:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        print(f"wrote {_rel(p)} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
