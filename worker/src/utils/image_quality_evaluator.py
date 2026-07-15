"""图片质量评估工具 - 智能选择高质量产品主图

✅ 内存优化：使用with语句确保stream响应在任何情况下都能正确关闭
"""
import requests
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def evaluate_image_quality(image_urls: List[str], max_evaluation_count: int = 5) -> List[Dict[str, Any]]:
    """
    评估图片质量，返回按质量排序的图片列表
    
    Args:
        image_urls: 原始图片URL列表
        max_evaluation_count: 最多评估多少张图片（避免大量HEAD请求）
    
    Returns:
        按质量排序的图片列表（包含url、size、priority等字段）
    """
    image_metadata: List[Dict[str, Any]] = []
    
    # 只评估前N张图片（避免大量HEAD请求）
    evaluation_urls = image_urls[:max_evaluation_count]
    
    for img_url in evaluation_urls:
        try:
            # ✅ 内存优化：使用with语句确保连接在任何情况下都正确关闭
            # 修复：用GET + Range代替HEAD（部分CDN如1688 alicdn不支持HEAD请求）
            # Range: bytes=0-0 只下载1字节，既能获取header又不浪费带宽
            with requests.get(
                img_url,
                timeout=10,
                allow_redirects=True,
                stream=True,
                headers={"Range": "bytes=0-0"}
            ) as response:
                if response.status_code in (200, 206):
                    # Content-Range格式: bytes 0-0/12345 → 取total大小
                    content_range = response.headers.get('Content-Range', '')
                    if content_range and '/' in content_range:
                        content_length = int(content_range.split('/')[-1])
                    else:
                        content_length = int(response.headers.get('Content-Length', 0))
                    content_type = response.headers.get('Content-Type', '')
                    
                    # 计算优先级（综合评分）
                    priority_score = 0
                    
                    # 评分维度1：图片大小（假设大图片质量更高）
                    if content_length > 500000:  # >500KB
                        priority_score += 3
                    elif content_length > 200000:  # >200KB
                        priority_score += 2
                    elif content_length > 100000:  # >100KB
                        priority_score += 1
                    
                    # 评分维度2：图片格式（优先JPG/PNG）
                    if 'jpeg' in content_type or 'jpg' in content_type:
                        priority_score += 2  # JPG格式质量稳定
                    elif 'png' in content_type:
                        priority_score += 1  # PNG格式质量较好
                    elif 'gif' in content_type or 'webp' in content_type:
                        priority_score -= 1  # GIF/WebP可能包含动画
                    
                    image_metadata.append({
                        'url': img_url,
                        'size': content_length,
                        'content_type': content_type,
                        'priority': priority_score
                    })
                    
                    logger.info(f"图片质量评估: url={img_url[:100]}, size={content_length}, type={content_type}, priority={priority_score}")
        
        except Exception as e:
            logger.warning(f"图片质量评估失败: url={img_url}, error={str(e)}")
    
    # 按优先级排序（priority高的排在前面）
    image_metadata.sort(key=lambda x: (x['priority'], x['size']), reverse=True)
    
    return image_metadata
