import os
import json
import logging
from typing import Dict, Any, List, Optional
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context
from graphs.state import ErrorHandlerInput, ErrorHandlerOutput
from utils.progress_logger import ProgressLogger

logger = logging.getLogger(__name__)

def error_handler_node(
    state: ErrorHandlerInput, 
    config: RunnableConfig, 
    runtime: Runtime[Context]
) -> ErrorHandlerOutput:
    """[保留待用] 错误分类与修复建议节点。

    当前未注册到 graph.py 中（所有错误恢复走 validation_retry_loop）。
    保留此节点供未来需要人工可读错误分析时接入。
    """
    desc: 分类错误类型并返回修复建议
    integrations: 无
    """
    ctx = runtime.context
    
    # 添加进度日志
    progress = ProgressLogger()
    progress.log_node_start("error_handler_node", "错误处理节点")
    progress.log_node_action("正在分析错误类型并生成修复建议...")
    
    # 获取商品ID和采购信息
    product_id = state.product_id
    errors = state.errors
    purchase_url = state.purchase_url
    purchase_cost = state.purchase_cost
    sku_id = state.sku_id
    profit_estimation = state.profit_estimation
    
    logger.info(f"开始处理错误: product_id={product_id}, errors={len(errors)}个")
    
    # 分类错误类型
    error_type: str = "other"
    error_summary: str = ""
    fix_suggestions: List[str] = []
    
    try:
        if not errors:
            logger.warning("errors列表为空，无错误需要处理")
            return ErrorHandlerOutput(
                product_id=product_id,
                error_type="none",
                error_summary="无错误",
                fix_suggestions=[],
                purchase_url=purchase_url,
                purchase_cost=purchase_cost,
                sku_id=sku_id,
                profit_estimation=profit_estimation,
                stages={"error_handler": "no_errors"}
            )
        
        # 分析错误类型
        category_errors = []
        attribute_errors = []
        image_errors = []
        price_errors = []
        other_errors = []
        
        for error in errors:
            error_code = error.get("code", "")
            error_message = error.get("message", "")
            
            # ✅ 增强：识别标签格式错误（hashtag/tags）
            if "标签" in error_message or "hashtag" in error_message.lower() or "tag" in error_message.lower():
                attribute_errors.append(error)
                logger.warning(f"识别标签格式错误：{error_message}")
            
            # ✅ 增强：识别尺寸重量错误（weight/dimensions）
            elif "尺寸" in error_message or "重量" in error_message or "weight" in error_message.lower() or "dimensions" in error_message.lower():
                attribute_errors.append(error)
                logger.warning(f"识别尺寸重量错误：{error_message}")
            
            # ✅ 增强：识别图片顺序错误（primary_image/images）
            elif "图片" in error_message or "primary_image" in error_message.lower() or "image" in error_code.lower() or "photo" in error_code.lower():
                image_errors.append(error)
                logger.warning(f"识别图片错误：{error_message}")
            
            # ✅ 增强：识别材料属性错误（材料/dictionary）
            elif "材料" in error_message or "dictionary" in error_message.lower() or "属性值不正确" in error_message or "attribute" in error_code.lower() or "dictionary" in error_code.lower():
                attribute_errors.append(error)
                logger.warning(f"识别材料属性错误：{error_message}")
            
            elif "category" in error_code.lower():
                category_errors.append(error)
            elif "price" in error_code.lower() or "currency" in error_code.lower():
                price_errors.append(error)
            else:
                other_errors.append(error)
        
        # 确定主要错误类型（按数量最多的分类）
        error_counts = {
            "category": len(category_errors),
            "attribute": len(attribute_errors),
            "image": len(image_errors),
            "price": len(price_errors),
            "other": len(other_errors)
        }
        error_type = max(error_counts.items(), key=lambda x: x[1])[0]
        
        # 生成错误摘要
        if category_errors:
            error_summary += f"类目错误{len(category_errors)}个: "
            for err in category_errors[:3]:  # 只展示前3个
                error_summary += f"{err.get('message', '')}, "
        
        if attribute_errors:
            error_summary += f"属性错误{len(attribute_errors)}个: "
            for err in attribute_errors[:3]:
                error_summary += f"{err.get('message', '')}, "
        
        if image_errors:
            error_summary += f"图片错误{len(image_errors)}个: "
            for err in image_errors[:3]:
                error_summary += f"{err.get('message', '')}, "
        
        if price_errors:
            error_summary += f"价格错误{len(price_errors)}个: "
            for err in price_errors[:3]:
                error_summary += f"{err.get('message', '')}, "
        
        if other_errors:
            error_summary += f"其他错误{len(other_errors)}个: "
            for err in other_errors[:3]:
                error_summary += f"{err.get('message', '')}, "
        
        # 生成修复建议
        if category_errors:
            fix_suggestions.append("建议重新查询类目ID，使用更准确的类目关键词")
            fix_suggestions.append("检查类目映射表，确认类目ID是否正确")
        
        if attribute_errors:
            # ✅ 增强：针对性修复建议
            if any("标签" in err.get("message", "") for err in attribute_errors):
                fix_suggestions.append("标签格式错误：建议只使用字母、数字、#、下划线，空格分隔")
                fix_suggestions.append("主题标签格式：每个以#开头，空格分隔（如 #时尚 #便携）")
            
            if any("尺寸" in err.get("message", "") or "重量" in err.get("message", "") for err in attribute_errors):
                fix_suggestions.append("尺寸重量缺失：建议填写货物的长度、宽度、高度和重量")
                fix_suggestions.append("单位规范：重量单位克（g），尺寸单位毫米（mm）")
            
            if any("材料" in err.get("message", "") for err in attribute_errors):
                fix_suggestions.append("材料属性不正确：建议从Ozon属性列表中选择dictionary_value_id")
                fix_suggestions.append("检查attributes_learning_node字典查询逻辑，确保材料值在Ozon列表中")
            
            # 常规属性错误建议
            fix_suggestions.append("建议重新映射属性，检查dictionary_value_id是否正确")
            fix_suggestions.append("补充缺失的必需属性，确保所有required属性都已填写")
        
        if image_errors:
            # ✅ 增强：图片顺序针对性建议
            if any("图片" in err.get("message", "") for err in image_errors):
                fix_suggestions.append("图片顺序错误：主图=第一张，白底图=最后一张，多维白底图=倒数第二张")
                fix_suggestions.append("检查primary_image和images数组顺序，确保符合Ozon规范")
            
            # 常规图片错误建议
            fix_suggestions.append("检查图片URL是否有效，确保图片可访问")
            fix_suggestions.append("检查图片格式和尺寸，确保符合Ozon规范")
        
        if price_errors:
            fix_suggestions.append("检查价格和货币类型是否匹配店铺设置")
            fix_suggestions.append("检查价格计算公式，确保价格合理")
        
        if other_errors:
            fix_suggestions.append("查看详细错误信息，根据具体错误进行修复")
        
        logger.info(f"错误分类完成: error_type={error_type}, suggestions={len(fix_suggestions)}条")
        
        return ErrorHandlerOutput(
            product_id=product_id,
            error_type=error_type,
            error_summary=error_summary.strip(),
            fix_suggestions=fix_suggestions,
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            stages={"error_handler": "processed"}
        )
        
    except Exception as e:
        logger.error(f"错误处理节点异常: {str(e)}")
        return ErrorHandlerOutput(
            product_id=product_id,
            error_type="other",
            error_summary=f"错误处理异常: {str(e)}",
            fix_suggestions=["请联系技术支持处理异常"],
            purchase_url=purchase_url,
            purchase_cost=purchase_cost,
            sku_id=sku_id,
            profit_estimation=profit_estimation,
            stages={"error_handler": "failed"}
        )