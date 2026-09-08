"""T14b: 草稿单字段 AI 重新生成服务（只读，不写回草稿）。

复用 `utils.mxou_api.call_mxou_chat_api`（LLM 调用）+ 现有翻译路径配方
（prepare_ozon_upload_node 标题「核心词+属性+场景」公式 / 描述净化规则、
utils.title_sanitizer 的西里尔/拉丁/中文正则）——**不新建模型客户端**。

- field ∈ {title, description, attributes, tags}；**不含 brand**（品牌强制
  Нет бренда 约定，见 AGENTS.md）。
- 失败契约：返回 None（调用方转 422），**绝不返回含中文/拉丁残留的值**。
"""

import json
import logging
import re
from typing import Optional

from utils.mxou_api import call_mxou_chat_api
from utils.title_formula import build_title_formula_prompt, parse_title_formula_keywords  # v0.59 标题公式唯一入口
# v0.70 预组装：estimate 服务（纯读派生）+ 类目树搜索单例。模块级 import 便于测试
# monkeypatch；两者均只依赖 utils/storage，与本模块无循环导入。
from services.estimate_service import estimate_from_envelope
from utils.ozon_category_query import get_category_query

logger = logging.getLogger(__name__)

# 支持的字段（不含 brand——品牌强制 Нет бренда 约定，不提供 AI 生成）
AI_FIELDS = frozenset({"title", "description", "attributes", "tags"})

# ── 校验正则（与 utils/title_sanitizer.py 同源）──
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RE = re.compile(r"[a-zA-Z]{2,}")  # 连续 2+ 拉丁 = 残留


def extract_current_value(field: str, payload: dict) -> Optional[str]:
    """从 draft 信封 payload 提取字段当前值；缺失/空 → None。

    - attributes: dict {中文属性名: 值} → 行格式（每行 "名: 值"）
    - tags: draft.tags 优先；无则从 attributes 中键名含「标签」的值提取
    """
    draft = payload.get("draft") or {}
    if field == "title":
        return str(draft.get("title") or "").strip() or None
    if field == "description":
        return str(draft.get("description") or "").strip() or None
    if field == "tags":
        tags = str(draft.get("tags") or "").strip()
        if tags:
            return tags
        attrs = draft.get("attributes") or {}
        for key, value in attrs.items():
            if "标签" in str(key):
                text = str(value or "").strip()
                if text:
                    return text
        return None
    if field == "attributes":
        attrs = draft.get("attributes") or {}
        lines = [
            f"{key}: {value}" for key, value in attrs.items()
            if str(value or "").strip()
        ]
        return "\n".join(lines) if lines else None
    return None


def _build_prompt(field: str, current_value: str, traffic_keywords: Optional[list] = None) -> tuple[str, str]:
    """按字段构建 (system_prompt, user_prompt)——复用现有翻译路径配方。"""
    if field == "title":
        # 与 prepare_ozon_upload_node 标题分支同配方：共享模块 utils/title_formula
        # （核心词+属性+场景公式 + 流量词建议行；traffic_keywords 已 parse 过滤）
        sys_prompt = build_title_formula_prompt(
            "zh", parse_title_formula_keywords(traffic_keywords or []),
        )
    elif field == "description":
        # 与 prepare_ozon_upload_node 描述翻译同配方（净化规则）
        sys_prompt = (
            "你是一个专业翻译，专门翻译Ozon俄罗斯电商平台的产品描述。将给定的中文描述翻译成俄语。\n"
            "严格规则：\n"
            "1. 100%西里尔字母，移除所有拉丁字母和中文字符\n"
            "2. 移除营销词汇：爆款、热销、新品、促销、跨境、亚马逊、best、hot、sale、new、premium、top、free\n"
            "3. 移除联系方式：网址、电话、邮箱\n"
            "4. 移除品牌名称引用\n"
            "5. 只返回俄语描述文本，不要添加任何解释或前缀"
        )
    elif field == "attributes":
        sys_prompt = (
            "你是Ozon俄罗斯电商平台的产品属性专家。将给定的中文产品属性（每行「属性名: 值」）"
            "翻译为俄语并输出为 JSON 对象。\n"
            "严格规则：\n"
            "1. 只输出一个 JSON 对象，键为俄语属性名，值为俄语属性值（不要 Markdown 代码块、不要多余文本）\n"
            "2. 100%西里尔字母，禁止拉丁字母和中文（品牌值填 \"Нет бренда\"）\n"
            "3. 保留属性数量，不要合并或删除\n"
            "示例：\n"
            "- 输入：\"颜色: 白色\\n材质: 塑料\"\n"
            "- 输出：{\"Цвет\": \"Белый\", \"Материал\": \"Пластик\"}"
        )
    elif field == "tags":
        sys_prompt = (
            "你是Ozon俄罗斯电商平台的主题标签专家。根据给定的中文产品信息生成俄语主题标签。\n"
            "严格规则：\n"
            "1. 生成 3-8 个俄语主题标签，逗号分隔，不要 # 号\n"
            "2. 100%西里尔字母，禁止拉丁字母和中文\n"
            "3. 只返回标签文本，不要解释"
        )
    else:  # pragma: no cover — AI_FIELDS 门已挡
        raise ValueError(f"unknown field: {field}")
    return sys_prompt, f"Translate to Russian: {current_value}"


def _is_clean_russian(text: str) -> bool:
    """非空 + 含西里尔 + 无中文 + 无拉丁残留。"""
    return bool(_CYRILLIC_RE.search(text)) and not _CJK_RE.search(text) and not _LATIN_RE.search(text)


def _validate_attributes_json(text: str) -> Optional[str]:
    """attributes 结果必须是合法 JSON 对象，且所有键/值为干净俄语。"""
    cleaned = text.strip()
    # 剥离可能的 Markdown 代码块包裹（```json ... ```）
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        obj = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        logger.warning("attributes LLM 输出非 JSON: %r", text[:120])
        return None
    if not isinstance(obj, dict) or not obj:
        logger.warning("attributes LLM 输出非对象: %r", text[:120])
        return None
    for key, value in obj.items():
        if not _is_clean_russian(str(key)) or not _is_clean_russian(str(value)):
            logger.warning("attributes 含中文/拉丁残留: %r", text[:120])
            return None
    return json.dumps(obj, ensure_ascii=False)


def regenerate_field(field: str, current_value: str, token: str, traffic_keywords: Optional[list] = None) -> Optional[str]:
    """单字段 AI 重新生成：LLM → 校验（非空 RU 无中文/拉丁残留）→ 返回；失败 None。

    token: mxou API 密钥（复用 call_mxou_chat_api，不新建客户端）。
    traffic_keywords: 标题公式流量词（俄语，可选；title 分支注入 system_prompt）。
    """
    if field not in AI_FIELDS:
        raise ValueError(f"unknown field: {field}")
    text = str(current_value or "").strip()
    if not text:
        return None

    sys_prompt, user_prompt = _build_prompt(field, text, traffic_keywords)
    result = call_mxou_chat_api(
        token=token,
        system_prompt=sys_prompt,
        user_prompt=user_prompt,
        temperature=0.0,
        max_tokens=1500,  # deepseek-v4-flash reasoning 需要充足配额
    )
    if not result:
        logger.warning("draft_ai field=%s LLM 调用失败/空响应", field)
        return None

    result = result.strip()
    if field == "attributes":
        return _validate_attributes_json(result)
    if _is_clean_russian(result):
        return result
    logger.warning("draft_ai field=%s 结果含中文/拉丁残留或非俄语: %r", field, result[:120])
    return None


# ──────────────────────────────────────────────
# v0.70 采集箱一键预组装（整卡生成并写回 payload）
# ──────────────────────────────────────────────


def _has_cyrillic(text: str) -> bool:
    """是否含西里尔字母（预组装幂等判定：已译字段跳过，不重复烧 LLM）。"""
    return bool(_CYRILLIC_RE.search(str(text or "")))


def _has_cjk(text: str) -> bool:
    """是否含中文（类目建议搜索语言选择：ZH_HANS 树是 jieba 中文分词路径）。"""
    return bool(_CJK_RE.search(str(text or "")))


def _merge_ru_attributes(base: dict, generated: dict, protected_keys: set) -> dict:
    """RU 属性合并：generated 键并入 base 副本；protected_keys（既有 RU/中文键）不覆盖。

    纯函数（可单测「已有同键不覆盖」）；返回新 dict，不改 base。
    """
    merged = dict(base) if isinstance(base, dict) else {}
    for key, value in (generated or {}).items():
        if key in protected_keys:
            continue  # 已有同键不覆盖
        merged[key] = value
    return merged


def _suggest_category(title_text: str) -> Optional[dict]:
    """类目建议（纯展示字段）：类目树搜索 top1 → {description_category_id, type_id, category_name}。

    ⚠️ 结果只写 draft.suggested_category，**绝不写 draft.ozon_category**——
    管线类目仲裁链（manual 权威/L0 学习/LLM 兜底）不能被预组装劫持。
    查不到/树不可用 → None（调用方保留旧建议）。
    """
    query_text = str(title_text or "").strip()
    if not query_text:
        return None
    try:
        language = "ZH_HANS" if _has_cjk(query_text) else "RU"
        rows = get_category_query().search_nodes(
            query_text, top_k=1, node_type="type", language=language)
    except Exception as exc:
        logger.warning("assemble 类目建议搜索失败（跳过）: %s", str(exc)[:200])
        return None
    if not rows:
        return None
    top = rows[0] if isinstance(rows[0], dict) else {}
    dc = str(top.get("description_category_id", "") or "")
    tp = str(top.get("type_id", "") or "")
    if not dc.isdigit() or not tp.isdigit():
        return None
    return {
        "description_category_id": dc,
        "type_id": tp,
        "category_name": str(top.get("node_name", "") or ""),
    }


def _get_cny_rub_rate() -> float:
    """CNY→RUB 汇率：PG 缓存优先，fallback 12.0（与 pricing_node._get_exchange_rate 同语义）。"""
    try:
        from utils.local_db_manager import LocalDBManager
        rate = LocalDBManager().get_exchange_rate("CNY", "RUB")
        if rate and float(rate) > 1:
            return float(rate)
    except Exception as exc:
        logger.debug("assemble 汇率查询失败，用默认 12.0: %s", str(exc)[:120])
    return 12.0


def _estimate_pricing(payload: dict) -> Optional[dict]:
    """三档预估价（RUB，纯展示字段）。

    estimate_from_envelope 缺省（无汇率）按 CNY 口径，这里显式 RUB + PG 汇率，
    让采集箱展示与 pricing_node 上架价同口径；失败/异常 → None。
    ⚠️ 管线不消费 draft.estimated_pricing——pricing_node 永远按成本+margin 重算。
    """
    try:
        result = estimate_from_envelope(
            payload, currency_code="RUB", exchange_rate=_get_cny_rub_rate())
    except Exception as exc:
        logger.warning("assemble 预估价失败（跳过）: %s", str(exc)[:200])
        return None
    if not isinstance(result, dict) or not result.get("price"):
        return None
    try:
        out = {
            "price": round(float(result["price"]), 2),
            "old_price": round(float(result.get("old_price") or 0), 2),
        }
        promo = result.get("promo_price")
        if promo is not None:
            out["promo_price"] = round(float(promo), 2)
    except (TypeError, ValueError):
        return None
    return out


def assemble_draft(payload: dict, token: str) -> dict:
    """v0.70 采集箱一键预组装：LLM 生成整卡上架信息，就地修改并写回 payload。

    管线透传语义（改前必读）：prepare 节点检测到西里尔标题/描述 → 跳过翻译直接用；
    属性只补缺失不重算。所以本函数只写两类字段：
    - 透传字段（上架值=预填值）：draft.title（原值挪 title_source 留档）/
      description / ozon_attributes（RU 合并，同键不覆盖）/ tags；
    - 纯展示字段（管线不消费）：draft.suggested_category（类目建议——**绝不写
      draft.ozon_category**，防劫持 manual/L0/LLM 仲裁链）、draft.estimated_pricing
      （RUB 三档预估——**绝不写 draft.price/old_price**，pricing_node 永远重算）。

    幂等：已含西里尔的 title/description/tags 跳过（不重复烧 LLM）；
    ozon_attributes 已有内容（典型跟卖竞品 RU 属性）→ attributes 跳过（不混源）。
    空描述 → 用标题（留档中文优先）+属性摘要作材料合成 RU 描述。

    所有 LLM 调用复用 regenerate_field（fail-None → 该字段进 skipped，不阻断）；
    单字段 except Exception，KeyboardInterrupt 不吞。
    返回 {"assembled": [...], "skipped": [...], "suggested_category": ...,
    "estimated_pricing": ...}。
    """
    if not isinstance(payload, dict):
        payload = {}
    draft = payload.get("draft")
    if not isinstance(draft, dict):
        draft = {}
        payload["draft"] = draft
    extensions = payload.get("extensions")
    if not isinstance(extensions, dict):
        extensions = {}
    traffic_keywords = extensions.get("traffic_keywords")

    assembled: list[str] = []
    skipped: list[str] = []
    original_title = str(draft.get("title") or "").strip()

    # ── a. 标题：非空且无西里尔才生成；原值挪 title_source 留档 ──
    try:
        if not original_title or _has_cyrillic(original_title):
            skipped.append("title")
        else:
            ru_title = regenerate_field(
                "title", original_title, token, traffic_keywords=traffic_keywords)
            if ru_title:
                draft["title_source"] = original_title
                draft["title"] = ru_title
                assembled.append("title")
            else:
                skipped.append("title")
    except Exception as exc:
        logger.warning("assemble title 失败（跳过）: %s", str(exc)[:200])
        skipped.append("title")

    # ── b. 描述：非西里尔才生成；源为空 → 标题（留档中文优先）+属性摘要合成材料 ──
    try:
        desc_source = extract_current_value("description", payload)
        if desc_source and _has_cyrillic(desc_source):
            skipped.append("description")
        else:
            if not desc_source:
                title_material = str(draft.get("title_source") or original_title or "").strip()
                parts = [title_material]
                attr_lines = extract_current_value("attributes", payload)
                if attr_lines:
                    parts.append(attr_lines)
                desc_source = "\n".join(p for p in parts if p).strip()
            if desc_source:
                ru_desc = regenerate_field("description", desc_source, token)
                if ru_desc:
                    draft["description"] = ru_desc
                    assembled.append("description")
                else:
                    skipped.append("description")
            else:
                skipped.append("description")
    except Exception as exc:
        logger.warning("assemble description 失败（跳过）: %s", str(exc)[:200])
        skipped.append("description")

    # ── c. 属性：ozon_attributes 已有内容（跟卖竞品 RU 属性）→ 跳过不混源；
    #        否则中文源生成 RU，合并进 draft.ozon_attributes（同键不覆盖）──
    try:
        existing_ru = draft.get("ozon_attributes")
        attrs_source = extract_current_value("attributes", payload)
        if (isinstance(existing_ru, dict) and existing_ru) or not attrs_source:
            skipped.append("attributes")
        else:
            ru_attrs_raw = regenerate_field("attributes", attrs_source, token)
            ru_attrs = None
            if ru_attrs_raw:
                try:
                    parsed = json.loads(ru_attrs_raw)
                    ru_attrs = parsed if isinstance(parsed, dict) else None
                except (json.JSONDecodeError, TypeError):
                    ru_attrs = None
            if not ru_attrs:
                skipped.append("attributes")
            else:
                base = existing_ru if isinstance(existing_ru, dict) else {}
                src_attrs = draft.get("attributes")
                protected = set(base.keys())
                if isinstance(src_attrs, dict):
                    protected |= set(src_attrs.keys())
                draft["ozon_attributes"] = _merge_ru_attributes(base, ru_attrs, protected)
                assembled.append("attributes")
    except Exception as exc:
        logger.warning("assemble attributes 失败（跳过）: %s", str(exc)[:200])
        skipped.append("attributes")

    # ── d. tags：非空且无西里尔才生成 ──
    try:
        tags_source = extract_current_value("tags", payload)
        if not tags_source or _has_cyrillic(tags_source):
            skipped.append("tags")
        else:
            ru_tags = regenerate_field("tags", tags_source, token)
            if ru_tags:
                draft["tags"] = ru_tags
                assembled.append("tags")
            else:
                skipped.append("tags")
    except Exception as exc:
        logger.warning("assemble tags 失败（跳过）: %s", str(exc)[:200])
        skipped.append("tags")

    # ── 类目建议（展示字段）：优先中文标题查树（jieba 中文路径）；查空保留旧建议 ──
    old_suggestion = draft.get("suggested_category")
    suggested_category = old_suggestion if isinstance(old_suggestion, dict) else None
    try:
        query_title = original_title
        if not _has_cjk(query_title):
            source_title = str(draft.get("title_source") or "").strip()
            if _has_cjk(source_title):
                query_title = source_title  # 生成后的 RU 标题查中文树零命中，退回留档中文
        found = _suggest_category(query_title)
        if found is not None:
            draft["suggested_category"] = found
            suggested_category = found
    except Exception as exc:
        logger.warning("assemble 类目建议失败（保留旧值）: %s", str(exc)[:200])

    # ── 三档预估价（展示字段，RUB）──
    try:
        estimated = _estimate_pricing(payload)
    except Exception as exc:
        logger.warning("assemble 预估价失败（跳过）: %s", str(exc)[:200])
        estimated = None
    if estimated is not None:
        draft["estimated_pricing"] = estimated

    return {
        "assembled": assembled,
        "skipped": skipped,
        "suggested_category": suggested_category,
        "estimated_pricing": estimated,
    }
