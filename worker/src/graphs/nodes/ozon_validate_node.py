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

logger = logging.getLogger(__name__)

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
    
    logger.info(f"开始Ozon上传预检测: payload包含{len(ozon_payload.get('items', []))}个商品")
    
    validation_errors: List[str] = []
    auto_fixed: bool = False
    
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
            if not description_category_id or not type_id:
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
            images = item.get("images", [])
            primary_image = item.get("primary_image", "")
            if not images and not primary_image:
                item_errors.append(f"item[{i}].images缺失（至少需要1张图片或primary_image）")
            
            # 验证attributes
            attributes = item.get("attributes", [])
            if not attributes:
                logger.warning(f"item[{i}].attributes为空（可能缺少属性映射）")
            
            # ✅ 关键修复：校验字典类型属性是否有有效的dictionary_value_id
            if dict_attr_ids:
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
                    
                    # ✅ 值类型校验：Decimal属性不能是非数字字符串
                    attr_type = attr_type_map.get(attr_id_int)
                    if attr_type == "Decimal":
                        for v in attr.get("values", []):
                            val = str(v.get("value", ""))
                            if val:
                                try:
                                    float(val.replace(",", "."))
                                except ValueError:
                                    item_errors.append(
                                        f"item[{i}].attributes: Decimal属性(id={attr_id_int})值不是数字: '{val}'"
                                    )
            
            validation_errors.extend(item_errors)
            
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
                # 拉丁文检测：不管是否混合西里尔，有拉丁文就报错
                if _latin_re.search(description):
                    # 提取拉丁文片段用于日志
                    _latin_fragments = re.findall(r'[a-zA-Z]{2,}', description)
                    item_errors.append(f"item[{i}].description含拉丁字母（Ozon要求纯俄语描述）: {', '.join(_latin_fragments[:3])}")
                    logger.error(f"❌ item[{i}]描述含拉丁字母: {description[:80]}...")
                if _chinese_re.search(description):
                    item_errors.append(f"item[{i}].description含中文字符（Ozon要求俄语描述）")
                    logger.error(f"❌ item[{i}]描述含中文字符: {description[:80]}...")

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

                    # 拉丁字母检测：关键属性（4191描述, 9048产品名, 4180标题）
                    if attr_id_int_check in (4191, 9048, 4180):
                        if _latin_re.search(av_val) and not _cyrillic_re.search(av_val):
                            item_errors.append(
                                f"item[{i}].attributes: 属性{attr_id_int_check}值为纯拉丁字母: {str(av_val)[:60]}"
                            )
                            logger.error(f"❌ 属性{attr_id_int_check}纯拉丁字母: {str(av_val)[:80]}")

                    # 中文字符检测：所有属性值（Ozon禁止中文/日文字符）
                    if _chinese_re.search(av_val):
                        item_errors.append(
                            f"item[{i}].attributes: 属性{attr_id_int_check}含中文字符: {str(av_val)[:60]}"
                        )
                        logger.error(f"❌ 属性{attr_id_int_check}含中文字符: {str(av_val)[:80]}")
        
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
                item_errors.append(
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
                except Exception:
                    failed_urls.append(url[:60])
            
            if len(failed_urls) == len(sample_urls) and sample_urls:
                item_errors.append(
                    f"item[{i}]所有图片URL不可访问（{len(failed_urls)}/{len(sample_urls)}），"
                    f"Ozon将无法下载图片"
                )
            elif failed_urls:
                logger.warning(f"⚠️ item[{i}]部分图片不可访问: {len(failed_urls)}/{len(sample_urls)}")
        
        # ✅ 本地预检完成（属性/文本/图片/危化品）。
        # 注：Ozon /v1/product/validate API 不存在（返回404），所有检查均为本地执行。
        # 本地检查覆盖范围：属性完整性、文本合规、图片可达性、危化品识别。
        # 无法预检的项目：Ozon ML 模型（体积重量对比）、图片内容审核。
        critical_errors = [err for err in validation_errors if any(kw in err for kw in ["缺失", "为空", "格式错误", "变体颜色", "拉丁字母", "非俄语", "中文字符", "危化品", "不可访问"])]
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