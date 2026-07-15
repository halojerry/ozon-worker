"""
错误分类器：识别Ozon验证错误的类型并返回修复路径
"""
import logging
logger = logging.getLogger(__name__)

def classify_validation_error(error_message: str, validation_errors: list) -> tuple[str, str]:
    """
    分类Ozon验证错误，返回错误类型和修复路径
    
    Args:
        error_message: 错误消息字符串
        validation_errors: 验证错误列表
        
    Returns:
        tuple[str, str]: (error_type, fix_path)
        - error_type: 错误类型分类（标签格式/尺寸重量/图片顺序/材料属性/其他）
        - fix_path: 修复路径（retry_detail_gen/retry_prepare_ozon_upload/retry_attributes_learning/error_handler）
    """
    
    # 合并所有错误信息（方便识别）
    all_errors_text = error_message + " " + " ".join(validation_errors)
    
    # ✅ 识别标签格式错误（标签/hashtag）
    if any(keyword in all_errors_text.lower() for keyword in ["标签", "hashtag", "tag", "主题标签", "标签格式", "字母数字"]):
        error_type = "标签格式错误"
        fix_path = "retry_detail_gen"  # 退回detail_gen节点重新生成description
        logger.warning(f"识别错误类型：{error_type}，修复路径：{fix_path}")
        return error_type, fix_path
    
    # ✅ 识别尺寸重量错误（尺寸/重量/weight/dimensions）
    elif any(keyword in all_errors_text.lower() for keyword in ["尺寸", "重量", "weight", "dimensions", "长度", "宽度", "高度", "未填充"]):
        error_type = "尺寸重量缺失"
        fix_path = "retry_prepare_ozon_upload"  # 退回prepare_ozon_upload节点重新填充
        logger.warning(f"识别错误类型：{error_type}，修复路径：{fix_path}")
        return error_type, fix_path
    
    # ✅ 识别图片顺序错误（图片顺序/primary_image/images）
    elif any(keyword in all_errors_text.lower() for keyword in ["图片", "image", "photo", "primary_image", "顺序", "主图"]):
        error_type = "图片顺序错误"
        fix_path = "retry_prepare_ozon_upload"  # 退回prepare_ozon_upload节点重新排序
        logger.warning(f"识别错误类型：{error_type}，修复路径：{fix_path}")
        return error_type, fix_path
    
    # ✅ 识别材料属性错误（材料/dictionary/属性值）
    elif any(keyword in all_errors_text.lower() for keyword in ["材料", "dictionary", "属性值", "不正确", "列表中选择"]):
        error_type = "材料属性不正确"
        fix_path = "retry_attributes_learning"  # 退回attributes_learning节点重新查询
        logger.warning(f"识别错误类型：{error_type}，修复路径：{fix_path}")
        return error_type, fix_path
    
    # ✅ 其他错误类型（未知错误）
    else:
        error_type = "其他错误"
        fix_path = "error_handler"  # 直接进入错误处理
        logger.error(f"未知错误类型：{error_message}，进入错误处理")
        return error_type, fix_path


def get_retry_count(state) -> int:
    """获取重试次数（从GlobalState）"""
    retry_count = state.retry_count if hasattr(state, 'retry_count') else 0
    return retry_count


def increment_retry_count(state) -> int:
    """增加重试次数"""
    retry_count = get_retry_count(state) + 1
    logger.info(f"重试次数增加：{retry_count-1} → {retry_count}")
    return retry_count