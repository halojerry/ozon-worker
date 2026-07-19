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
            
            # 验证images（✅ 多SKU变体item可能只有primary_image，images为空）
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
            
            validation_errors.extend(item_errors)
            
            # ✅ 关键修复：本地内容预检 — 检测拉丁字母描述和英文属性值
            # 这些问题会被Ozon审核标记为"商品描述完全是拉丁字母"等错误
            _cyrillic_re = re.compile(r'[а-яА-ЯёЁ]')
            _latin_re = re.compile(r'[a-zA-Z]')
            
            # 检查description字段（商品简介）
            description = item.get("description", "")
            if description and _latin_re.search(description) and not _cyrillic_re.search(description):
                item_errors.append(f"item[{i}].description完全是拉丁字母（Ozon要求俄语描述）")
                logger.error(f"❌ item[{i}]描述完全是拉丁字母: {description[:80]}...")
            
            # 检查关键属性值是否为俄语
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
                
                # 检查4191(描述)和9048(产品名)必须含西里尔字母
                if attr_id_int_check in (4191, 9048, 4180):
                    attr_values_list = attr.get("values", [])
                    for av in attr_values_list:
                        if not isinstance(av, dict):
                            continue
                        av_val = av.get("value", "")
                        if av_val and _latin_re.search(av_val) and not _cyrillic_re.search(av_val):
                            item_errors.append(
                                f"item[{i}].attributes: 属性{attr_id_int_check}值为纯拉丁字母: {str(av_val)[:60]}"
                            )
                            logger.error(f"❌ 属性{attr_id_int_check}纯拉丁字母: {str(av_val)[:80]}")
        
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
        
        # Step 4: 检查payload中的属性是否有无效字典值
        # （prepare_ozon_upload_node已跳过未匹配的字典属性，所以payload中不应该有dict_id<=0的字典属性）
        # 只检查payload本身的属性，不再依赖validation_errors
        critical_errors = [err for err in validation_errors if "缺失" in err or "为空" in err or "格式错误" in err or "变体颜色" in err or "拉丁字母" in err or "非俄语" in err]
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