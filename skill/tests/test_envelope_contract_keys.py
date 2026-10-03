# -*- coding: utf-8 -*-
"""skill 侧信封契约 parity 门禁（W2 治理）。

权威 = worker `api/envelope_contract.py`（经 `api-integration/envelope-keys.json`
机器可读导出）。本测试断言 skill 实写 extensions 键集 ⊆ 权威键集——skill 新增
extensions 键而未在 worker 契约登记时，在 skill 的 CI 就红（不用等 worker 侧兜底）。

worker 侧完整行为校验在 `worker/tests/test_envelope_contract.py`（含 validate_envelope
行为矩阵与 provenance 反向闸）；两侧用同一套写面提取正则（登记在 worker 测试）。

运行: cd skill && .venv314/bin/python -m pytest tests/test_envelope_contract_keys.py -q
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SKILL_ROOT.parent
JSON_PATH = REPO_ROOT / "api-integration" / "envelope-keys.json"

PRODUCER_FILES = [
    SKILL_ROOT / "scripts" / "cloud_probe.py",
    SKILL_ROOT / "scripts" / "lib" / "ozon_discovery.py",
    SKILL_ROOT / "scripts" / "lib" / "ozon_widget.py",
    SKILL_ROOT / "scripts" / "cli.py",
]


def _extract_writes(src: str) -> set[str]:
    """与 worker/tests/test_envelope_contract.py 同一套登记形态（改这里必须同步那边）。"""
    keys: set[str] = set()
    keys |= set(re.findall(r"(?<![a-z_0-9])extensions\[[\'\"]([a-z_0-9]+)[\'\"]\]\s*=", src))
    # 容器名 "extensions" 本身不算键；链式 setdefault("extensions",…)["key"] 单独收
    # （行内其他 ["key"] 子脚本是取值源，不收）
    for line in src.splitlines():
        if "extensions" in line and "setdefault" in line:
            m = re.search(r"setdefault\([\'\"]([a-z_0-9]+)[\'\"]", line)
            if m and m.group(1) != "extensions":
                keys.add(m.group(1))
    keys |= set(re.findall(
        r"setdefault\([\'\"]extensions[\'\"][^)]*\)\[[\'\"]([a-z_0-9]+)[\'\"]\]",
        src,
    ))
    m = re.search(r"_INJECTABLE_EXT_KEYS\s*=\s*\(([^)]*)\)", src, re.S)
    if m:
        keys |= set(re.findall(r"[\'\"]([a-z_0-9]+)[\'\"]", m.group(1)))
    return keys


def test_contract_json_exists():
    assert JSON_PATH.exists(), (
        "api-integration/envelope-keys.json 不存在——本测试要求全仓 checkout"
        "（跑 python worker/scripts/gen_contract_docs.py 生成）"
    )


def test_skill_extension_writes_subset_of_contract():
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    known = set(data.get("extensions", {}))
    missing_files = [str(p) for p in PRODUCER_FILES if not p.exists()]
    assert not missing_files, f"skill 生产者源文件缺失: {missing_files}"
    src = "\n".join(p.read_text(encoding="utf-8") for p in PRODUCER_FILES)
    writes = _extract_writes(src)
    assert writes, "extensions 写面提取为空——正则形态失效（与 worker 侧同步修）"
    unknown = sorted(writes - known)
    assert not unknown, (
        f"skill 实写 extensions 键未登记进 worker 契约: {unknown}——"
        "在 worker/src/api/envelope_contract.py 加字段（含来源/描述）→ "
        "跑 python worker/scripts/gen_contract_docs.py → 同步提交"
    )


def test_contract_json_authority_pointer():
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    assert "envelope_contract.py" in data.get("_authority", ""), "JSON 权威指针异常"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
