import os
import json
import re
import logging
from typing import Dict, Any, List, Optional
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state import OzonValidateInput, OzonValidateOutput
# ✅ v0.69 Wave3: 数值属性清洗唯一入口 + 尺寸契约硬边界（唯一事实源，与 normalizer 同源）
from utils.attr_numeric_sanitize import is_numeric_attr_type, sanitize_numeric_attr_value
from utils.cos_uploader import is_cos_url
from utils.weight_dimension_normalizer import OZON_DIM_BOUNDS_MM

logger = logging.getLogger(__name__)


# ── ✅ v0.69 Wave4 T2.1: 标题-类目词面一致性（DESCRIPTION_DECLINE 本地预检）──
# 店铺扫描 DESCRIPTION_DECLINE 37 例（最高频）：「标题与类目不符」类 decline 靠
# 本地词面检查提前拦。阈值宁松勿严：标题与 RU 类目路径只要有 ≥4 字符公共西里尔词
# 就放行，只拦零交集；UPDATE/跟卖（带 product_id）豁免——对齐本节点类目必填豁免
# 逻辑（v0.64.0 C3/N6）。「命中重生成一次」不做在 validate（validate 不调 LLM）：
# retry 子图 DESCRIPTION_DECLINE→error_repair_llm 已承担重生成，本地拦截的价值是
# 快速失败 + 错误信息带类目名，上游一次性修。
_CYR_TOKEN_RE = re.compile(r"[а-яё]+")
_MIN_COMMON_WORD_LEN = 4  # ≥4 字符的西里尔词才计入公共词（短词多是 для/и 噪音）
_MIN_COMMON_PREFIX = 4    # 共同前缀 ≥4 视为同词根（кружка↔кружки 词形变化）


def _cyr_words(text: str) -> set:
    """文本中的小写西里尔词集合（按非西里尔字符切分，≥2 字符）。"""
    return {w for w in _CYR_TOKEN_RE.findall(str(text or "").lower()) if len(w) >= 2}


def common_cyr_words(title: str, category_path: str) -> set:
    """标题 × 类目路径的公共西里尔词（纯函数，可单测）。

    公共词 = 双方各含一个 ≥4 字符西里尔词，二者相等或共同前缀 ≥
    _MIN_COMMON_PREFIX（俄语名词单复数/格变化兜底）。返回公共词集合（空=零交集）。
    """
    title_words = _cyr_words(title)
    path_words = _cyr_words(category_path)
    if not title_words or not path_words:
        return set()
    common: set = set()
    for tw in title_words:
        if len(tw) < _MIN_COMMON_WORD_LEN:
            continue
        for pw in path_words:
            if len(pw) < _MIN_COMMON_WORD_LEN:
                continue
            if tw == pw or tw[:_MIN_COMMON_PREFIX] == pw[:_MIN_COMMON_PREFIX]:
                common.add(tw if len(tw) <= len(pw) else pw)
                break
    return common


def _fetch_ru_category_path(description_category_id, type_id) -> str:
    """dc+tp → RU 类目 full_path（PG 直查）。

    任何异常/树缺行 → ""（调用方跳过一致性检查）：validate 是上传前在线阶段，
    PG 短暂不可用不应把一致性预检变成硬故障（宁松勿严）。
    """
    try:
        from storage.database.db import get_session
        from sqlalchemy import text as _sql_text
        with get_session() as _s:
            _row = _s.execute(_sql_text(
                "SELECT full_path FROM category_tree_nodes "
                "WHERE description_category_id=:cid AND type_id=:tid AND language='RU' LIMIT 1"
            ), {"cid": int(description_category_id), "tid": int(type_id)}).fetchone()
        return str(_row[0] or "") if _row else ""
    except Exception as _e:
        logger.debug(f"RU 类目路径查询失败（跳过标题一致性检查）: {_e}")
        return ""


def ozon_validate_node(
    state: OzonValidateInput, 
    config: RunnableConfig, 
    runtime: Runtime[Context]
) -> OzonValidateOutput:
    """
    title: Ozon上传预检测节点
    desc: 上传前检测Ozon payload是否符合规范，提前发现错误并修复
    integrations: Ozon API
    """
    ctx = runtime.context
    
    # 获取Ozon payload和采购信息
    ozon_payload = state.ozon_payload
    purchase_url = state.purchase_url
    purchase_cost = state.purchase_cost
    sku_id = state.sku_id
    profit_estimation = state.profit_estimation
    
    # 获取Ozon API配置
    ozon_client_id = state.ozon_client_id
    ozon_api_key = state.ozon_api_key
    attributes_schema = state.attributes_schema if state.attributes_schema else []
    
    # ✅ 关键修复：构建字典属性ID集合（用于校验dictionary_value_id）
    dict_attr_ids: set = set()
    for schema_attr in attributes_schema:
        if isinstance(schema_attr, dict):
            schema_attr_id = schema_attr.get("id")
            schema_dict_id = schema_attr.get("dictionary_id", 0)
            if schema_attr_id and schema_dict_id:
                try:
                    if int(schema_dict_id) > 0:
                        dict_attr_ids.add(int(schema_attr_id))
                except (ValueError, TypeError):
                    pass
    logger.info(f"✅ 字典属性校验：共{len(dict_attr_ids)}个字典类型属性需要校验dictionary_value_id")
    
    # ✅ 构建属性类型映射（用于值类型校验）
    attr_type_map: dict = {}
    for schema_attr in attributes_schema:
        if isinstance(schema_attr, dict):
            aid = schema_attr.get("id")
            atype = schema_attr.get("type", "")
            if aid and atype:
                attr_type_map[int(aid)] = atype

    # ✅ v0.69 Wave3: 必填属性清单（schema is_required）——预检时逐 item 对照
    # payload attributes，缺失的列 attr_id+name（与尺寸/数值错误同批一次列全）
    required_schema_attrs: List[Dict[str, Any]] = [
        sa for sa in attributes_schema
        if isinstance(sa, dict) and sa.get("is_required") and sa.get("id") is not None
    ]
    
    logger.info(f"开始Ozon上传预检测: payload包含{len(ozon_payload.get('items', []))}个商品")
    
    validation_errors: List[str] = []
    auto_fixed: bool = False
    _ru_path_cache: dict = {}  # (dc,tp) → RU full_path（T2.1 一致性检查，进程内缓存）
    
    try:
        # Step 1: 验证payload结构
        items = ozon_payload.get("items", [])
        if not items:
            validation_errors.append("payload缺少items字段或items为空")
            return OzonValidateOutput(
                ozon_payload=ozon_payload,
                ordered_images=state.ordered_images,
                purchase_url=purchase_url,
                purchase_cost=purchase_cost,
                sku_id=sku_id,
                profit_estimation=profit_estimation,
                validation_errors=validation_errors,
                auto_fixed=False,
                error_message="Payload结构验证失败",
                is_valid=False,
                stages={"ozon_validate": "failed"}
            )
        
        # Step 2: 验证每个item的必需字段
        for i, item in enumerate(items):
            item_errors: List[str] = []
            
            # 验证name（俄语标题）
            name = item.get("name", "")
            if not name:
                item_errors.append(f"item[{i}].name缺失（俄语标题）")
            
            # 验证offer_id（1688 SKU_ID）
            offer_id = item.get("offer_id", "")
            if not offer_id:
                item_errors.append(f"item[{i}].offer_id缺失（1688 SKU_ID）")
            
            # barcode 可选 — Ozon允许空barcode（平台自动分配），不报错
            barcode = item.get("barcode", "")
            if not barcode:
                logger.info(f"item[{i}].barcode为空（Ozon将自动分配）")
            
            # 验证description_category_id和type_id
            description_category_id = item.get("description_category_id", "")
            type_id = item.get("type_id", "")
            # v0.64.0 C3(N6): 带 product_id 的 item 是 UPDATE 模式（跟卖 import-by-sku /
            # 编辑更新，Ozon 按 product_id 更新已有卡，无需类目）——类目缺失不报错。
            # 此前无条件报「类目缺失」→ 进 retry 对一张本不需类目的卡盲修烧 3 轮。
            if not description_category_id or not type_id:
                _has_pid = bool(item.get("product_id"))
                if _has_pid:
                    logger.warning(
                        f"item[{i}] 类目缺失但带 product_id（UPDATE 模式，走更新已有卡）"
                        f"— 豁免类目必填校验"
                    )
                else:
                    item_errors.append(f"item[{i}].description_category_id或type_id缺失（类目信息不完整）")
            
            # 验证price和old_price
            price = item.get("price", "")
            old_price = item.get("old_price", "")
            if not price:
                item_errors.append(f"item[{i}].price缺失（价格）")
            
            # 验证vat（自动修复，不计入错误）
            vat = item.get("vat", "")
            if vat != "0":
                logger.info(f"item[{i}].vat自动修复: {vat} → '0'")
                item["vat"] = "0"
                auto_fixed = True
            
            # 验证weight_unit和dimension_unit（自动修复，不计入错误）
            weight_unit = item.get("weight_unit", "")
            dimension_unit = item.get("dimension_unit", "")
            if weight_unit != "g":
                logger.info(f"item[{i}].weight_unit自动修复: {weight_unit} → 'g'")
                item["weight_unit"] = "g"
                auto_fixed = True
            
            if dimension_unit != "mm":
                logger.info(f"item[{i}].dimension_unit自动修复: {dimension_unit} → 'mm'")
                item["dimension_unit"] = "mm"
                auto_fixed = True
            
            # ✅ 尺寸/密度合理性检查（拦截INCORRECT_DENSITY根因：cm→mm二次转换）
            weight_g = item.get("weight", 0)
            depth = item.get("depth", 0)
            width = item.get("width", 0)
            height = item.get("height", 0)
            if weight_g > 0 and depth > 0 and width > 0 and height > 0:
                volume_m3 = (depth * width * height) / 1e9
                density = (weight_g / 1000.0) / volume_m3 if volume_m3 > 0 else 0
                max_dim = max(depth, width, height)
                # 密度极低（< 1.0 kg/m³）说明尺寸被错误放大（典型的cm→mm二次转换）
                if density < 1.0:
                    # ✅ 自修复：如果max_dim > 500mm，尝试缩小10倍
                    if max_dim > 500:
                        old_d, old_w, old_h = depth, width, height
                        depth = max(10, int(depth / 10))
                        width = max(10, int(width / 10))
                        height = max(10, int(height / 10))
                        item["depth"] = depth
                        item["width"] = width
                        item["height"] = height
                        auto_fixed = True
                        new_vol = (depth * width * height) / 1e9
                        new_dens = (weight_g / 1000.0) / new_vol if new_vol > 0 else 0
                        logger.warning(
                            f"🔧 密度自修复: {density:.2f}→{new_dens:.1f} kg/m³, "
                            f"尺寸 {old_d}×{old_w}×{old_h} → {depth}×{width}×{height}mm"
                        )
                    else:
                        item_errors.append(
                            f"item[{i}]密度异常({density:.2f}kg/m³): {weight_g}g, {depth}×{width}×{height}mm"
                        )
                        logger.error(f"❌ 密度异常: {density:.2f}kg/m³ (max_dim={max_dim}mm)")
                # 任一维度超过2000mm（2米）也很可疑
                if max_dim > 2000:
                    item_errors.append(
                        f"item[{i}]尺寸异常大(max={max_dim}mm): {depth}×{width}×{height}mm "
                        f"→ 可能是cm→mm单位错误"
                    )

            # ✅ v0.69 Wave3: Ozon 契约尺寸硬边界（防御第二道，与 normalizer 同源
            # OZON_DIM_BOUNDS_MM）——normalizer 已在源头 clamp，这里覆盖绕过
            # normalizer 的路径（retry 重建 payload/手工数据等）。越界只报错不改写
            # （修复走 repair/prepare clamp），错误与数值/必填同批一次列全不 fail-first。
            for _dim_key, (_lo, _hi) in OZON_DIM_BOUNDS_MM.items():
                _raw_dim = item.get(_dim_key if _dim_key != "length" else "depth", 0)
                try:
                    _actual_dim = int(float(str(_raw_dim))) if _raw_dim else 0
                except (ValueError, TypeError):
                    _actual_dim = 0
                if _actual_dim > 0 and not (_lo <= _actual_dim <= _hi):
                    _dim_label = "length(depth)" if _dim_key == "length" else _dim_key
                    item_errors.append(
                        f"item[{i}]尺寸超出Ozon契约边界: {_dim_label}={_actual_dim}mm "
                        f"允许[{_lo}, {_hi}]mm"
                    )
                    logger.error(
                        f"❌ item[{i}]尺寸越界: {_dim_key}={_actual_dim}mm "
                        f"边界[{_lo},{_hi}]mm"
                    )

            images = item.get("images", [])
            primary_image = item.get("primary_image", "")
            if not images and not primary_image:
                item_errors.append(f"item[{i}].images缺失（至少需要1张图片或primary_image）")
            
            # 验证attributes
            attributes = item.get("attributes", [])
            if not attributes:
                logger.warning(f"item[{i}].attributes为空（可能缺少属性映射）")
            
            # ✅ 关键修复：校验字典类型属性是否有有效的dictionary_value_id
            # ✅ v0.69 Wave3: 循环不再被 dict_attr_ids 非空门槛——数值型属性
            # （dictionary_id=0）的坏值同样要在本批列出
            for attr in attributes:
                if not isinstance(attr, dict):
                    continue
                attr_id = attr.get("id")
                if attr_id is None:
                    continue
                try:
                    attr_id_int: int = int(attr_id)
                except (ValueError, TypeError):
                    continue

                if attr_id_int in dict_attr_ids:
                    attr_values = attr.get("values", [])
                    for v in attr_values:
                        if not isinstance(v, dict):
                            continue
                        dict_val_id = v.get("dictionary_value_id", 0)
                        try:
                            dict_val_id_int: int = int(dict_val_id) if dict_val_id else 0
                        except (ValueError, TypeError):
                            dict_val_id_int = 0
                        if dict_val_id_int <= 0:
                            item_errors.append(
                                f"item[{i}].attributes: 字典属性(id={attr_id_int})缺少有效的dictionary_value_id"
                            )
                            logger.error(f"❌ 字典属性校验失败: attr_id={attr_id_int}, dictionary_value_id={dict_val_id}")

                # ✅ v0.69 Wave3 值类型校验：数值型属性（Integer/Decimal/Number…，
                # 大小写不敏感）必须可解析为数字——复用 attr_numeric_sanitize 唯一
                # 入口（"1,5"/"30包" 可解析放行，清洗归 prepare；完全无数字才报错）
                attr_type = attr_type_map.get(attr_id_int)
                if is_numeric_attr_type(attr_type):
                    for v in attr.get("values", []):
                        if not isinstance(v, dict):
                            continue
                        raw_val = v.get("value")
                        val = str(raw_val) if raw_val is not None else ""
                        if not val:
                            continue
                        cleaned, _reason = sanitize_numeric_attr_value(attr_id_int, val, attr_type)
                        if cleaned is None:
                            item_errors.append(
                                f"item[{i}].attributes: 数值属性(id={attr_id_int})值无法解析为数字: '{val}'"
                            )
                            logger.error(
                                f"❌ 数值属性坏值: attr_id={attr_id_int}, value='{val}'"
                            )
            
            # ✅ v0.69 Wave3: 必填属性对照（schema is_required vs payload attributes）
            # ——缺失的列 attr_id+name（语义对齐 revalidate_node 同名检查）；
            # 与尺寸/数值错误同批 extend 返回，一次列全不 fail-first
            if required_schema_attrs:
                _present_attr_ids: set = set()
                for attr in attributes:
                    if isinstance(attr, dict) and attr.get("id") is not None:
                        try:
                            _present_attr_ids.add(int(attr.get("id")))
                        except (ValueError, TypeError):
                            pass
                for _schema_attr in required_schema_attrs:
                    try:
                        _req_id = int(_schema_attr.get("id"))
                    except (ValueError, TypeError):
                        continue
                    if _req_id not in _present_attr_ids:
                        _req_name = _schema_attr.get("name", f"id={_req_id}")
                        item_errors.append(
                            f"item[{i}]必填属性缺失: {_req_name} (id={_req_id})"
                        )
                        logger.error(f"❌ item[{i}]必填属性缺失: {_req_name} (id={_req_id})")

            # ✅ v0.69 Wave4 T2.1: 标题-类目词面一致性（DESCRIPTION_DECLINE 本地预检）
            # 只对 CREATE 生效——UPDATE/跟卖（带 product_id）豁免，对齐上方类目必填
            # 豁免逻辑；RU 路径缺失（树缺行/PG 异常）→ 跳过（宁松勿严）。
            # RU 路径按 (dc,tp) 进程内缓存，多变体不重复查 PG。
            if description_category_id and type_id and not item.get("product_id"):
                _ru_cache_key = (str(description_category_id), str(type_id))
                _ru_path = _ru_path_cache.get(_ru_cache_key)
                if _ru_path is None:
                    _ru_path = _fetch_ru_category_path(description_category_id, type_id)
                    _ru_path_cache[_ru_cache_key] = _ru_path
                if _ru_path:
                    _consistency_name = item.get("name", "")
                    if _consistency_name and not common_cyr_words(_consistency_name, _ru_path):
                        item_errors.append(
                            f"item[{i}]标题与类目不一致（Ozon DESCRIPTION_DECLINE 风险）: "
                            f"标题「{str(_consistency_name)[:60]}」与类目「{_ru_path[:80]}」"
                            f"无公共西里尔词（≥{_MIN_COMMON_WORD_LEN}字符）"
                        )
                        logger.error(
                            f"❌ item[{i}]标题与类目零交集: {_consistency_name[:60]} × {_ru_path[:80]}"
                        )

            # ✅ 关键修复：本地内容预检 — 检测拉丁字母/中文字符
            # 这些问题会被Ozon审核标记为DESCRIPTION_DECLINE等错误
            _cyrillic_re = re.compile(r'[а-яА-ЯёЁ]')
            _latin_re = re.compile(r'[a-zA-Z]')
            _chinese_re = re.compile(r'[\u4e00-\u9fff]')

            # 检查name字段（产品名称）— 必须含西里尔，禁止纯拉丁/中文
            item_name = item.get("name", "")
            if item_name:
                if _latin_re.search(item_name) and not _cyrillic_re.search(item_name):
                    item_errors.append(f"item[{i}].name含拉丁字母（Ozon要求俄语名称）: {item_name[:60]}")
                    logger.error(f"❌ item[{i}]名称含拉丁字母: {item_name[:80]}")
                if _chinese_re.search(item_name):
                    item_errors.append(f"item[{i}].name含中文字符（Ozon要求俄语名称）: {item_name[:60]}")
                    logger.error(f"❌ item[{i}]名称含中文字符: {item_name[:80]}")

            # 检查description字段（商品简介）
            description = item.get("description", "")
            if description:
                # v0.29.2 FIX: 规格表 HTML(<table class="ozon-spec"><caption> 等)
                # 标签本身是拉丁字符 —— 直接检测必然误报"描述含拉丁字母"。
                # 先剔除 HTML 标签再检测正文(正文已由 prepare 净化, 应纯俄语)。
                _desc_text = re.sub(r'<[^>]+>', ' ', description)
                # 拉丁文检测：不管是否混合西里尔，有拉丁文就报错
                if _latin_re.search(_desc_text):
                    # 提取拉丁文片段用于日志
                    _latin_fragments = re.findall(r'[a-zA-Z]{2,}', _desc_text)
                    item_errors.append(f"item[{i}].description含拉丁字母（Ozon要求纯俄语描述）: {', '.join(_latin_fragments[:3])}")
                    logger.error(f"❌ item[{i}]描述含拉丁字母: {_desc_text[:80]}...")
                if _chinese_re.search(_desc_text):
                    item_errors.append(f"item[{i}].description含中文字符（Ozon要求俄语描述）")
                    logger.error(f"❌ item[{i}]描述含中文字符: {_desc_text[:80]}...")

            # 检查所有属性值 — 拉丁字母检测（关键属性）+ 中文字符检测（所有属性）
            for attr in attributes:
                if not isinstance(attr, dict):
                    continue
                attr_id_val = attr.get("id")
                if attr_id_val is None:
                    continue
                try:
                    attr_id_int_check = int(attr_id_val)
                except (ValueError, TypeError):
                    continue

                attr_values_list = attr.get("values", [])
                for av in attr_values_list:
                    if not isinstance(av, dict):
                        continue
                    av_val = av.get("value", "")
                    if not av_val or not isinstance(av_val, str):
                        continue

                    # 拉丁字母检测：关键属性（4191描述, 4180标题）
                    # v0.64.0: 9048(Название модели) 移出本检测 —— 型号是标识符，
                    # 纯拉丁合法（iPhone 15 / v0.60 防并卡前缀 `item_id~sha1` 均纯拉丁），
                    # 本地误报触发无意义 retry（Sentry POUDING_OZON-C2 23× 实证）
                    if attr_id_int_check in (4191, 4180):
                        if _latin_re.search(av_val) and not _cyrillic_re.search(av_val):
                            item_errors.append(
                                f"item[{i}].attributes: 属性{attr_id_int_check}值为纯拉丁字母: {str(av_val)[:60]}"
                            )
                            logger.error(f"❌ 属性{attr_id_int_check}纯拉丁字母: {str(av_val)[:80]}")

                    # 中文字符检测：所有属性值（Ozon禁止中文/日文字符）
                    # ✅ v0.69 Wave4: 数值型属性的可解析值豁免——Wave3 契约明文
                    # 「'30包' 可解析放行，清洗归 prepare」。此前中文检查因 extend
                    # 缺陷是死代码，两者冲突不可见；修复后按 Wave3 契约对齐：
                    # 数值属性值可解析 → 不报中文（下游必清洗）；不可解析坏值
                    # 仍报（中文+数值双错，进 retry 修）。
                    if _chinese_re.search(av_val):
                        _num_type = attr_type_map.get(attr_id_int_check)
                        _flag_chinese = True
                        if is_numeric_attr_type(_num_type):
                            _cleaned, _ = sanitize_numeric_attr_value(
                                attr_id_int_check, av_val, _num_type)
                            _flag_chinese = _cleaned is None
                        if _flag_chinese:
                            item_errors.append(
                                f"item[{i}].attributes: 属性{attr_id_int_check}含中文字符: {str(av_val)[:60]}"
                            )
                            logger.error(f"❌ 属性{attr_id_int_check}含中文字符: {str(av_val)[:80]}")

            # ✅ v0.69 Wave4 T1 缺陷修复: extend 挪到该 item 全部检查之后。
            # 此前 extend 在必填属性检查后即执行，其后的 拉丁/中文/危化品/图片检查
            # append 到旧 item_errors 但不再 extend——错误实际进不了 validation_errors
            # （critical_errors 关键词表却含对应词），「本地能拦的没拦住、全靠 Ozon 事后拒」。
            validation_errors.extend(item_errors)

        # Step 3: 变体颜色差异检查（多变体场景下，颜色必须不同才能合并）
        COLOR_ATTR_IDS: set = {10096, 10097, 10098, 10099}
        if len(items) > 1:
            variant_colors: List[tuple] = []  # (item_index, color_value, dict_value_id)
            for i, item in enumerate(items):
                attributes = item.get("attributes", [])
                for attr in attributes:
                    if not isinstance(attr, dict):
                        continue
                    try:
                        attr_id_int = int(attr.get("id", 0))
                    except (ValueError, TypeError):
                        continue
                    if attr_id_int in COLOR_ATTR_IDS:
                        attr_values = attr.get("values", [])
                        for v in attr_values:
                            if isinstance(v, dict):
                                color_val = v.get("value", "")
                                dict_val_id = v.get("dictionary_value_id", 0)
                                if color_val:
                                    variant_colors.append((i, color_val, dict_val_id))
                                break
                        break
            
            if len(variant_colors) >= 2:
                # 检查是否所有变体颜色相同
                unique_colors: set = set()
                for _, cv, dvid in variant_colors:
                    # 用 (value, dict_value_id) 组合来判断唯一性
                    unique_colors.add((cv.strip().lower(), int(dvid) if dvid else 0))
                
                if len(unique_colors) <= 1:
                    color_details: str = "; ".join([f"变体{idx}: {cv}(dict_id={dvid})" for idx, cv, dvid in variant_colors])
                    validation_errors.append(
                        f"多变体颜色相同：{len(items)}个变体颜色无差异（{color_details}），"
                        f"Ozon将无法合并变体。请确保每个变体有不同的颜色属性值。"
                    )
                    logger.error(f"❌ 变体颜色无差异: {color_details}")
                else:
                    logger.info(f"✅ 变体颜色差异检查通过: {len(unique_colors)}种不同颜色")
            else:
                logger.warning(f"⚠️ 多变体({len(items)}个)但未检测到颜色属性，可能影响变体合并")
        
        # Step 3.5: 增强预检 — 图片URL可达性 + 危化品扫描
        FIRE_HAZARD_KEYWORDS_RU = [
            "зажигалка", "зажигалки", "спички", "спичка",
            "огнемет", "взрывчат", "оружие", "пистолет",
        ]
        FIRE_HAZARD_KEYWORDS_CN = [
            "打火机", "火柴", "点火器", "炸药", "武器", "手枪",
        ]
        
        for i, item in enumerate(items):
            # 危化品扫描
            item_name = item.get("name", "")
            item_desc = item.get("description", "")
            combined = (item_name + " " + item_desc).lower()
            
            hazard_matches = []
            for kw in FIRE_HAZARD_KEYWORDS_RU:
                if kw in combined:
                    hazard_matches.append(kw)
            for kw in FIRE_HAZARD_KEYWORDS_CN:
                if kw in combined:
                    hazard_matches.append(kw)
            
            if hazard_matches:
                logger.warning(f"⚠️ item[{i}]检测到危化品关键词: {hazard_matches}，标记不可修复")
                # ✅ v0.69 Wave4 T1: 直接挂 validation_errors——此前 append 到上个 item
                # 循环遗留的 item_errors（stale 引用），从未 extend → 错误被丢弃。
                validation_errors.append(
                    f"item[{i}]检测到危化品/火险品关键词: {hazard_matches}，"
                    f"此类商品需特殊认证才能上架Ozon"
                )

            # 图片URL可达性检查（抽样：主图+前3张）
            images = item.get("images", [])[:3]
            primary = item.get("primary_image", "")
            sample_urls = [primary] + images if primary else images

            failed_urls = []
            for url in sample_urls:
                if not url:
                    continue
                try:
                    import requests as req
                    head_resp = req.head(url, timeout=5, allow_redirects=True)
                    if head_resp.status_code >= 400:
                        failed_urls.append(url[:60])
                except Exception as _probe_e:
                    # ✅ v0.69 Wave4 T1 异常安全: 网络失败（超时/DNS/SSL/代理）≠ 图片失效。
                    # 本检查进 errors 且判 critical——validate 是上传前在线阶段，
                    # 把网络抖动当「全部图片不可达」会大面积误拦正常任务（修复 extend
                    # 缺陷后该错误真实生效，必须同步兜底）。降级 warning 放行，由 Ozon
                    # 抓图侧兜底；只有 HTTP ≥400 明确失效才计失败。
                    logger.warning(
                        f"⚠️ item[{i}]图片可达性探测异常（降级 warning 不拦截）: "
                        f"{url[:60]}: {_probe_e}"
                    )

            if len(failed_urls) == len(sample_urls) and sample_urls:
                validation_errors.append(
                    f"item[{i}]所有图片URL不可访问（{len(failed_urls)}/{len(sample_urls)}），"
                    f"Ozon将无法下载图片"
                )
            elif failed_urls:
                logger.warning(f"⚠️ item[{i}]部分图片不可访问: {len(failed_urls)}/{len(sample_urls)}")

            # ✅ v0.69 镜像闸（第二道防线）：图片全外链（零 COS 托管）→ 硬错误。
            # 上面的可达性探测在「提交时点」抽样，防不住「此刻可达、Ozon 异步抓图
            # 时已失效/防盗链」的时间窗——declined IMAGE_ERROR 实证：外链卡
            # images=0 被拒，同批 COS 卡 approved。静态判定不依赖时点，直传外链
            # 一律拦在上传前（绕过 draft 通道的直连 submit_task 信封由此兜住）。
            _all_imgs = (
                ([str(item.get("primary_image"))] if item.get("primary_image") else [])
                + [str(u) for u in (item.get("images") or []) if u]
            )
            if _all_imgs and not any(is_cos_url(u) for u in _all_imgs):
                validation_errors.append(
                    f"item[{i}]图片全外链（{len(_all_imgs)} 张均非 COS 托管）——"
                    f"Ozon 下载外链失败为已知必拒项 IMAGE_ERROR，须先镜像至 COS 再上传"
                )
        
        # ✅ 本地预检完成（属性/文本/图片/危化品）。
        # 注：Ozon /v1/product/validate API 不存在（返回404），所有检查均为本地执行。
        # 本地检查覆盖范围：属性完整性、文本合规、图片可达性、危化品识别。
        # 无法预检的项目：Ozon ML 模型（体积重量对比）、图片内容审核。
        # ✅ v0.69 Wave3: 新增关键词「超出」（尺寸契约边界）/「无法解析」（数值坏值）
        # ——两类错误与必填缺失（「缺失」）同样属 Ozon 必拒项，必须判 critical
        # ✅ v0.69 Wave4: 新增关键词「标题与类目不一致」（T2.1 DESCRIPTION_DECLINE
        # 本地预检）——零交集标题×类目是 Ozon 事后必拒项，必须判 critical 拦在上传前。
        # ✅ v0.69 镜像闸: 新增关键词「全外链」——Ozon 抓外链失败=必拒（IMAGE_ERROR
        # declined 实证），与「不可访问」同级的上传前硬拦。
        critical_errors = [err for err in validation_errors if any(kw in err for kw in ["缺失", "为空", "格式错误", "变体颜色", "拉丁字母", "非俄语", "中文字符", "危化品", "不可访问", "全外链", "超出", "无法解析", "标题与类目不一致"])]
        if critical_errors:
            logger.error(f"Ozon预检测发现严重错误: {len(critical_errors)}个")
            return OzonValidateOutput(
                ozon_payload=ozon_payload,
                ordered_images=state.ordered_images,
                purchase_url=purchase_url,
                purchase_cost=purchase_cost,
                sku_id=sku_id,
                profit_estimation=profit_estimation,
                validation_errors=validation_errors,
                auto_fixed=auto_fixed,
                error_message=f"Payload验证失败: {len(critical_errors)}个严重错误",
                is_valid=False,
                stages={"ozon_validate": "failed"}
            )
        
        # Step 4: 如果只修复了vat/unit等字段，返回成功（不追加到validation_errors）
        if auto_fixed:
            logger.info(f"Ozon预检测自动修复了vat/unit字段（不计入错误）")
        
        logger.info(f"Ozon预检测完成: 发现{len(validation_errors)}个警告，已修复{auto_fixed}")
        
        return OzonValidateOutput(
            ozon_payload=ozon_payload,
            ordered_images=state.ordered_images,
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            validation_errors=validation_errors,
            auto_fixed=auto_fixed,
            error_message="",
            is_valid=True,
            stages={"ozon_validate": "success"}
        )
        
    except Exception as e:
        logger.error(f"Ozon预检测异常: {str(e)}")
        return OzonValidateOutput(
            ozon_payload=ozon_payload,
            ordered_images=state.ordered_images,
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            validation_errors=[f"预检测异常: {str(e)}"],
            auto_fixed=False,
            error_message=str(e),
            is_valid=False,
            stages={"ozon_validate": "failed"}
        )