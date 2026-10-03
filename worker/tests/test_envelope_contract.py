# -*- coding: utf-8 -*-
"""信封契约 parity 门禁（W2 治理重写——替代 v0.63 的 5-token 存在性测试）。

契约权威 = `worker/src/api/envelope_contract.py`（EnvelopeExtensions extra="forbid"）。
本文件锁定四件事：
  1. 模型键 ↔ api-integration/envelope-keys.json 零漂移（gen_contract_docs 产物）
  2. validate_envelope 行为：全量真实信封放行 / 未知键点名拒绝 / legacy 扁平放行 /
     draft.ozon_category 窄校验
  3. skill 实写键集 ⊆ 模型键集（**不 skip**——CI checkout 全仓，skill 源必在；
     写面提取用三条已登记的正则，新写法不在覆盖面时靠 provenance 反向闸兜底）
  4. provenance 反向闸：每个非 legacy 模型键必须能在 skill 或 worker 源码中找到
     字面出处（防 JSON/模型腐化成无人认领的键清单）

运行: cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_envelope_contract.py -q
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.envelope_contract import (
    EXTENSION_KEY_ORIGINS,
    EnvelopeExtensions,
    EnvelopeRoot,
    envelope_strict_enabled,
    validate_envelope,
)

TEST_DIR = Path(__file__).resolve().parent
REPO_ROOT = TEST_DIR.parent.parent
WORKER_SRC = REPO_ROOT / "worker" / "src"
SKILL_SCRIPTS = REPO_ROOT / "skill" / "scripts"
JSON_PATH = REPO_ROOT / "api-integration" / "envelope-keys.json"

SKILL_PRODUCER_FILES = [
    SKILL_SCRIPTS / "cloud_probe.py",
    SKILL_SCRIPTS / "lib" / "ozon_discovery.py",
    SKILL_SCRIPTS / "lib" / "ozon_widget.py",
    SKILL_SCRIPTS / "cli.py",
]


def _read_skill_sources() -> str:
    missing = [str(p) for p in SKILL_PRODUCER_FILES if not p.exists()]
    assert not missing, (
        f"skill 源码缺失: {missing}——本闸要求全仓 checkout（CI/本地均满足）。"
        "若在纯 worker 镜像上下文跑测试，属于环境错误，不是 skip 理由。"
    )
    return "\n".join(p.read_text(encoding="utf-8") for p in SKILL_PRODUCER_FILES)


def _extract_skill_extension_writes(skill_src: str) -> set[str]:
    """提取 skill 侧 extensions 写面（三条已登记形态；新写法须登记进此处）。"""
    keys: set[str] = set()
    # 形态① extensions["key"] = / extensions['key'] =（lookbehind 防吃
    # resolved_extensions["extensions"] 这类复合变量的尾巴）
    keys |= set(re.findall(r"(?<![a-z_0-9])extensions\[[\'\"]([a-z_0-9]+)[\'\"]\]\s*=", skill_src))
    # 形态② 行内含 extensions 的 setdefault 首参（容器名 "extensions" 本身不算键）
    for line in skill_src.splitlines():
        if "extensions" in line and "setdefault" in line:
            m = re.search(r"setdefault\([\'\"]([a-z_0-9]+)[\'\"]", line)
            if m and m.group(1) != "extensions":
                keys.add(m.group(1))
    # 形态②b 链式：X.setdefault("extensions", …)["key"] = …（cli 会话证据注入形态；
    # 刻意不收行内其他 ["key"] 子脚本——那是取值源，不是 extensions 写面）
    keys |= set(re.findall(
        r"setdefault\([\'\"]extensions[\'\"][^)]*\)\[[\'\"]([a-z_0-9]+)[\'\"]\]",
        skill_src,
    ))
    # 形态③ _INJECTABLE_EXT_KEYS 元组字面量
    m = re.search(r"_INJECTABLE_EXT_KEYS\s*=\s*\(([^)]*)\)", skill_src, re.S)
    if m:
        keys |= set(re.findall(r"[\'\"]([a-z_0-9]+)[\'\"]", m.group(1)))
    return keys


# ────────────────────────── 1. 模型 ↔ 生成物零漂移 ──────────────────────────

def test_extensions_model_matches_generated_json():
    assert JSON_PATH.exists(), "api-integration/envelope-keys.json 不存在——先跑 python worker/scripts/gen_contract_docs.py"
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    model_keys = set(EnvelopeExtensions.model_fields)
    assert set(data["extensions"]) == model_keys, (
        "envelope-keys.json 与模型漂移——跑 python worker/scripts/gen_contract_docs.py 后提交"
    )
    assert set(data["root_keys"]) == {"draft", "source", "extensions", "assets"}


def test_every_model_key_has_origin_and_description():
    fields = EnvelopeExtensions.model_fields
    assert set(fields) == set(EXTENSION_KEY_ORIGINS), "模型字段与来源表不同步"
    for key, field in fields.items():
        assert EXTENSION_KEY_ORIGINS[key] in ("skill-auto", "skill-injectable", "skill-collect", "worker", "legacy"), key
        assert field.description, f"{key} 缺 description（生成文档会用它）"


# ────────────────────────── 2. validate_envelope 行为 ──────────────────────────

def _full_realistic_envelope() -> dict:
    """30 键全量真实形态信封（golden——新增键时同步补这里）。"""
    return {
        "draft": {"item_id": "4767514314", "title": "测试商品", "images": ["https://cbu01.alicdn.com/img/x.jpg"],
                  "weight": 500, "dimensions": {"length": 100, "width": 100, "height": 50},
                  "purchase_cost": 10.0, "purchase_url": "https://detail.1688.com/offer/4767514314.html",
                  "ozon_category": {"source": "page", "namespace": "widget", "lang": "ZH_HANS",
                                     "category_path": "宠物/宠物用品", "description_category_id": "1", "type_id": "2"}},
        "source": {"purchase_url": "https://detail.1688.com/offer/4767514314.html", "purchase_cost": 10.0},
        "assets": {"image_urls": ["https://cbu01.alicdn.com/img/x.jpg"]},
        "extensions": {
            "ozon_client_id": "5381204", "mxou_token": "sk-x", "store_id": "s1",
            "shipping_provider": "TPL", "shipping_service": "Standard",
            "margin_rate": 1.5, "commission_rate": 0.12, "fx_buffer": 0.05,
            "margin_floor": 0.6, "margin_anchor": 2.0,
            "variable_cost_rate": 0.155, "promo_variable_cost_rate": 0.245,
            "traffic_keywords": ["товар"], "offer_id_prefix": "", "follow_type": "hand",
            "follow_sell": True, "competitor_weight_g": 480, "competitor_dimensions_mm": {"length": 90},
            "competitor_ref_images": ["https://ir.ozone.ru/x.jpg"],
            "commission_segments": {"fbs": {"leq_1500": 12}, "fbo": {"leq_1500": 12}},
            "match_evidence": {"method": "aibuy", "confidence": 0.8, "trusted": True},
            "discovery_meta": {"ozon_product_id": "1", "blue_ocean_score": 80},
            "box_reviewed": False, "credential_id": "c1", "update_product_id": "",
            "update_offer_id": "", "image_regen": False, "currency_code": "CNY",
            "stock": 0, "warehouse_id": "",
        },
    }


def test_validate_accepts_full_realistic_envelope():
    assert validate_envelope(_full_realistic_envelope()) == []


def test_validate_rejects_unknown_extension_key_naming_it():
    env = _full_realistic_envelope()
    env["extensions"]["commision_rate"] = 0.1  # 拼写错误（真实漂移形态）
    errors = validate_envelope(env)
    assert errors, "未知 extensions 键必须被拒"
    assert any("commision_rate" in e for e in errors), f"错误必须点名键，实际: {errors}"


def test_validate_rejects_unknown_root_key():
    env = _full_realistic_envelope()
    env["extensions_note"] = "私加顶层键"
    errors = validate_envelope(env)
    assert errors and any("extensions_note" in e for e in errors)


def test_validate_accepts_legacy_flat_envelope():
    legacy = {"purchase_url": "https://detail.1688.com/offer/1.html", "purchase_cost": "9.9", "title": "x"}
    assert validate_envelope(legacy) == []


def test_validate_envelope_rejects_non_dict():
    assert validate_envelope(None) and validate_envelope({}) and validate_envelope([])


def test_ozon_category_spot_validation_names_path():
    env = _full_realistic_envelope()
    env["draft"]["ozon_category"] = {"source": "page", "category_path": ["应为", "字符串"]}
    errors = validate_envelope(env)
    assert errors and any("ozon_category.category_path" in e for e in errors)


def test_envelope_root_rejects_unknown_keys_directly():
    with pytest.raises(ValidationError):
        EnvelopeRoot.model_validate({"draft": {}, "extensions": {}, "bogus": 1})
    with pytest.raises(ValidationError):
        EnvelopeRoot.model_validate({"draft": {}, "extensions": {"nonsense_key": 1}})


def test_envelope_strict_env_toggle(monkeypatch):
    monkeypatch.setenv("ENVELOPE_STRICT", "0")
    assert envelope_strict_enabled() is False
    monkeypatch.setenv("ENVELOPE_STRICT", "1")
    assert envelope_strict_enabled() is True
    monkeypatch.delenv("ENVELOPE_STRICT")
    assert envelope_strict_enabled() is True, "缺省必须严格（fail-closed）"


# ────────────────────────── 3. skill 实写键集 ⊆ 模型键集（不 skip） ──────────────────────────

def test_skill_extension_writes_subset_of_model():
    skill_src = _read_skill_sources()
    writes = _extract_skill_extension_writes(skill_src)
    assert writes, "skill 写面提取为空——正则形态失效，检查 _extract_skill_extension_writes"
    model_keys = set(EnvelopeExtensions.model_fields)
    unknown = sorted(writes - model_keys)
    assert not unknown, (
        f"skill 实写 extensions 键未登记进契约模型: {unknown}——"
        "在 api/envelope_contract.py 加字段+来源 → 跑 gen_contract_docs.py"
    )


# ────────────────────────── 4. provenance 反向闸（防清单腐化） ──────────────────────────

def test_every_nonlegacy_model_key_has_literal_provenance():
    """非 legacy 键必须在 skill 或 worker 源码里有字面出处——防键清单变成无人认领的死清单。"""
    skill_src = _read_skill_sources()
    worker_src = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(WORKER_SRC.rglob("*.py"))
    )
    orphans = []
    for key, origin in EXTENSION_KEY_ORIGINS.items():
        if origin == "legacy":
            continue
        if origin.startswith("skill") and f'"{key}"' not in skill_src and f"'{key}'" not in skill_src:
            orphans.append(f"{key}（{origin}）：skill 源无字面出处")
        if origin == "worker" and f'"{key}"' not in worker_src and f"'{key}'" not in worker_src:
            orphans.append(f"{key}（worker）：worker 源无字面出处")
    assert not orphans, f"契约键失去出处（删键或修 origin）: {orphans}"


# ────────────────────────── 5. 保留：类目解析器结构契约 ──────────────────────────

def test_get_node_by_full_path_returns_shape():
    """解析器签名与方法存在（结构契约，v0.63 原测试保留）。"""
    from utils.ozon_category_query import OzonCategoryQuery
    assert callable(getattr(OzonCategoryQuery, "get_node_by_full_path", None))
    assert hasattr(OzonCategoryQuery, "get_types_under")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
