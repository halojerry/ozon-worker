#!/usr/bin/env python3
"""v0.83.1 N1（fix/rich-content-format-v083）：11254 Rich-контент JSON 格式重造。

gate v083 重跑实锤（REPORT-v083-rerun.md N1，P0）：旧 `build_rich_json` 产
`{"content":[{"widgetName":"raShowcase","type":"chess","blocks":[{"img":{...}}]}]}`
——缺根 `"version":0.3`、缺 `imgLink`/`widthMobile`/`heightMobile` → Ozon 全量拒
`invalid_rich_content_json`（Rich-контент JSON не соответствует шаблону），导致
**所有保留 11254 的 CREATE 卡无法过审**（0 completed 根因）。

本文件用**真卡黄金样本**锁定 Ozon 实收 schema：
  - 黄金样本逐字取自本店在线 approved 卡 11254 值
    （skill/data/audit_4718259_20260926.json 的 product 6457346280）；
  - 4 份官方发货模板（worker/assets/offer_description*.json）同为 version=0.3。

契约：
  N1-a 黄金样本通过 schema 校验；新输出结构与黄金样本**逐键等价**。
  N1-b 新输出恒含根 version=0.3 + roll/width_full + block 含 imgLink + img 七键。
  N1-c 回归锁：旧 v0.83 载荷（chess、无 version）**必须**被 schema 校验判 fail。
  N1-d 逃生门：RICH_CONTENT_FORMAT=v1 回滚旧格式；RICH_CONTENT_DISABLE=1 恒 None。

运行:
    cd worker && PYTHONPATH=src ../skill/.venv314/bin/python -m pytest tests/test_rich_content_format_v0831.py -q
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("GRSAI_API_KEY", "test-key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.content_enrich import (  # noqa: E402
    RICH_CONTENT_ATTR_ID,
    build_rich_json,
    rich_content_disabled,
    rich_content_format,
)

# ── 真卡黄金样本：逐字取自在线 approved 卡 product 6457346280 的 11254 值 ──
GOLDEN_APPROVED_ROLL = {
    "content": [
        {
            "widgetName": "raShowcase",
            "type": "roll",
            "blocks": [
                {
                    "imgLink": "",
                    "img": {
                        "src": "https://ir-20.ozone.ru/s3/multimedia-1-c/10920897696.jpg",
                        "srcMobile": "https://ir-20.ozone.ru/s3/multimedia-1-c/10920897696.jpg",
                        "alt": "",
                        "position": "width_full",
                        "positionMobile": "width_full",
                        "widthMobile": 900,
                        "heightMobile": 1200,
                    },
                }
            ],
        }
    ],
    "version": 0.3,
}

# 真卡另见块型（product 5837014560 = chess/to_the_edge 亦为合法在售结构；
# 说明被拒根因是缺根 version + 缺 img 必需字段，**不是** chess 本身）。
GOLDEN_APPROVED_CHESS = {
    "content": [
        {
            "widgetName": "raShowcase",
            "type": "chess",
            "blocks": [
                {
                    "img": {
                        "src": "https://ir-20.ozone.ru/s3/multimedia-1-6/8006474346.jpg",
                        "srcMobile": "https://ir-20.ozone.ru/s3/multimedia-1-6/8006474346.jpg",
                        "alt": "",
                        "position": "to_the_edge",
                        "positionMobile": "to_the_edge",
                        "widthMobile": 900,
                        "heightMobile": 1200,
                    },
                    "imgLink": "",
                    "title": {"items": [{"type": "text", "content": "Т"}], "size": "size4",
                              "align": "left", "color": "color1"},
                    "text": {"size": "size2", "align": "left", "color": "color1",
                             "items": [{"type": "text", "content": "Опис"}]},
                    "reverse": False,
                }
            ],
        }
    ],
    "version": 0.3,
}

_REQUIRED_IMG_KEYS = {
    "src", "srcMobile", "alt", "position", "positionMobile",
    "widthMobile", "heightMobile",
}
_ALLOWED_POSITIONS = {"width_full", "to_the_edge"}
_ALLOWED_WIDGET_TYPES = {"roll", "chess", "billboard", "tileXL"}


def _schema_errors(payload) -> list:
    """Ozon 实收 schema 校验（本文件契约的可执行形式，编码 N1 schema 小抄）。"""
    errs: list = []
    if not isinstance(payload, dict):
        return ["payload 非 dict"]
    if "version" not in payload:
        errs.append("缺根 version（Ozon invalid_rich_content_json 直接根因）")
    elif payload["version"] != 0.3:
        errs.append(f"根 version 非 0.3: {payload['version']!r}")
    content = payload.get("content")
    if not isinstance(content, list) or not content:
        errs.append("缺 content 数组/空")
        return errs
    for i, widget in enumerate(content):
        if not isinstance(widget, dict):
            errs.append(f"content[{i}] 非 dict")
            continue
        if widget.get("widgetName") != "raShowcase":
            errs.append(f"content[{i}].widgetName 非 raShowcase: {widget.get('widgetName')!r}")
        if widget.get("type") not in _ALLOWED_WIDGET_TYPES:
            errs.append(f"content[{i}].type 非法: {widget.get('type')!r}")
        blocks = widget.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            errs.append(f"content[{i}].blocks 缺/空")
            continue
        for j, block in enumerate(blocks):
            if not isinstance(block, dict):
                errs.append(f"content[{i}].blocks[{j}] 非 dict")
                continue
            if "imgLink" not in block:
                errs.append(f"content[{i}].blocks[{j}] 缺 imgLink")
            img = block.get("img")
            if not isinstance(img, dict):
                errs.append(f"content[{i}].blocks[{j}].img 缺/非 dict")
                continue
            missing = _REQUIRED_IMG_KEYS - set(img)
            if missing:
                errs.append(f"content[{i}].blocks[{j}].img 缺键 {sorted(missing)}")
            if img.get("position") not in _ALLOWED_POSITIONS:
                errs.append(f"content[{i}].blocks[{j}].img.position 非法: {img.get('position')!r}")
            for dim in ("widthMobile", "heightMobile"):
                val = img.get(dim)
                if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                    errs.append(f"content[{i}].blocks[{j}].img.{dim} 非正整数: {val!r}")
    return errs


# ═══════════════ N1-a：黄金样本 vs 新输出逐键等价 ═══════════════

def test_golden_approved_samples_pass_schema():
    """自检：两份真卡黄金样本都通过校验（校验器本身不失真）。"""
    assert _schema_errors(GOLDEN_APPROVED_ROLL) == []
    assert _schema_errors(GOLDEN_APPROVED_CHESS) == []


def test_new_output_matches_golden_roll_key_for_key():
    """新输出与真卡 roll 黄金样本逐键等价（结构同型，仅图片/尺寸值不同）。"""
    out = json.loads(build_rich_json(
        ["https://cos/1.jpg", "https://cos/2.jpg"], "Деревянные ложки"))
    assert _schema_errors(out) == [], _schema_errors(out)
    assert set(out.keys()) == set(GOLDEN_APPROVED_ROLL.keys())
    golden_widget = GOLDEN_APPROVED_ROLL["content"][0]
    widget = out["content"][0]
    assert set(widget.keys()) == set(golden_widget.keys())
    assert set(widget["blocks"][0].keys()) == set(golden_widget["blocks"][0].keys())
    assert set(widget["blocks"][0]["img"].keys()) == set(
        golden_widget["blocks"][0]["img"].keys())


# ═══════════════ N1-b：必需字段恒在 ═══════════════

def test_new_output_always_has_version_and_required_fields():
    for n in (2, 3, 4, 10):
        imgs = [f"https://cos/{i}.jpg" for i in range(n)]
        payload = json.loads(build_rich_json(imgs, "Товар"))
        assert payload["version"] == 0.3
        assert _schema_errors(payload) == [], _schema_errors(payload)
        assert len(payload["content"]) == min(n, 4)  # 每图一个 widget，前 4
        for widget in payload["content"]:
            assert widget["type"] == "roll"
            img = widget["blocks"][0]["img"]
            assert img["position"] == "width_full"
            assert img["positionMobile"] == "width_full"
            assert img["src"] == img["srcMobile"]


# ═══════════════ N1-c：回归锁（缺 version 即 fail）═══════════════

def test_regression_lock_old_v083_payload_fails_schema():
    """旧 v0.83 载荷（chess、无根 version、img 缺 3 键）必须被判 fail——
    这正是 Ozon 全量拒的具体形态，锁死不许回潮。"""
    old_payload = {
        "content": [{
            "widgetName": "raShowcase",
            "type": "chess",
            "blocks": [{
                "img": {
                    "src": "https://cos/1.jpg",
                    "srcMobile": "https://cos/1.jpg",
                    "alt": "Товар",
                    "position": "to_the_edge",
                    "positionMobile": "to_the_edge",
                }
            }],
        }]
    }
    errs = _schema_errors(old_payload)
    assert any("version" in e for e in errs), errs
    assert any("imgLink" in e for e in errs), errs
    assert any("widthMobile" in e for e in errs), errs


def test_regression_lock_missing_version_alone_fails():
    """单缺 version 即 fail（P0 直接根因的最小回归）。"""
    payload = json.loads(build_rich_json(["https://cos/1.jpg", "https://cos/2.jpg"], "Т"))
    del payload["version"]
    assert "缺根 version（Ozon invalid_rich_content_json 直接根因）" in _schema_errors(payload)


# ═══════════════ N1-d：逃生门 ═══════════════

def test_default_format_is_v2(monkeypatch):
    monkeypatch.delenv("RICH_CONTENT_FORMAT", raising=False)
    monkeypatch.delenv("RICH_CONTENT_DISABLE", raising=False)
    assert rich_content_format() == "v2"
    assert rich_content_disabled() is False
    assert _schema_errors(json.loads(
        build_rich_json(["https://c/1.jpg", "https://c/2.jpg"], "Т"))) == []


def test_legacy_format_escape_hatch(monkeypatch):
    """RICH_CONTENT_FORMAT=v1 → 回滚旧输出（chess、无 version），供紧急回滚。"""
    monkeypatch.setenv("RICH_CONTENT_FORMAT", "v1")
    payload = json.loads(build_rich_json(["https://c/1.jpg", "https://c/2.jpg"], "Т"))
    assert set(payload.keys()) == {"content"}
    widget = payload["content"][0]
    assert widget["type"] == "chess" and len(widget["blocks"]) == 2


def test_disable_kill_switch_returns_none(monkeypatch):
    """RICH_CONTENT_DISABLE=1 → 11254 整个跳过（宁缺毋滥，不挡整卡过审）。"""
    monkeypatch.setenv("RICH_CONTENT_DISABLE", "1")
    assert rich_content_disabled() is True
    assert build_rich_json(["https://c/1.jpg", "https://c/2.jpg"], "Т") is None


def test_disable_kill_switch_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("RICH_CONTENT_DISABLE", "1")
    monkeypatch.setenv("RICH_CONTENT_FORMAT", "v1")
    assert build_rich_json(["https://c/1.jpg", "https://c/2.jpg"], "Т") is None


# ═══════════════ prepare 出口恒填闸接线 ═══════════════

def _payload_item(images=None, attrs=None, name="Товар"):
    return {
        "name": name,
        "primary_image": (images or [None])[0],
        "images": list(images or []),
        "attributes": list(attrs or []),
    }


def test_prepare_exit_gate_emits_ozon_schema(monkeypatch):
    """_ensure_content_attrs_in_payload 出口的 11254 过 Ozon schema 校验。"""
    from graphs.nodes.prepare_ozon_upload_node import _ensure_content_attrs_in_payload

    monkeypatch.delenv("RICH_CONTENT_DISABLE", raising=False)
    monkeypatch.delenv("RICH_CONTENT_FORMAT", raising=False)
    payload = {"items": [_payload_item(
        images=["https://cos/1.jpg", "https://cos/2.jpg", "https://cos/3.jpg"])]}
    _ensure_content_attrs_in_payload(payload, title_ru="Ложки", draft_attrs={}, llm_desc_text="")
    attrs = {a["id"]: a for a in payload["items"][0]["attributes"]}
    rich_val = attrs[RICH_CONTENT_ATTR_ID]["values"][0]["value"]
    parsed = json.loads(rich_val)
    assert parsed["version"] == 0.3
    assert _schema_errors(parsed) == [], _schema_errors(parsed)


def test_prepare_exit_gate_skips_11254_when_disabled(monkeypatch):
    """RICH_CONTENT_DISABLE=1：跳过 11254，但 4191 照填（整卡不被 rich 拖住）。"""
    from graphs.nodes.prepare_ozon_upload_node import _ensure_content_attrs_in_payload
    from utils.content_enrich import ANNOTATION_ATTR_ID

    monkeypatch.setenv("RICH_CONTENT_DISABLE", "1")
    payload = {"items": [_payload_item(
        images=["https://cos/1.jpg", "https://cos/2.jpg"],
        name="Ложки, 50 шт.")]}
    _ensure_content_attrs_in_payload(payload, title_ru="Ложки", draft_attrs={}, llm_desc_text="")
    ids = [a["id"] for a in payload["items"][0]["attributes"]]
    assert RICH_CONTENT_ATTR_ID not in ids
    assert ANNOTATION_ATTR_ID in ids


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  ❌ {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
