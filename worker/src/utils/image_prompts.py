"""生图提示词配置加载（v0.15 热加载：改文件即生效，无需重建镜像/重启容器）

配置来源: {APP_WORKSPACE_PATH}/config/image_prompts.json
- 每次调用现读磁盘（无缓存）→ 宿主机改文件后，下一次生图自动用新提示词
- 文件缺失 / JSON 损坏 / 渲染失败 → 回退到模块级默认提示词（与 v0.14 硬编码逐字一致），
  绝不抛异常阻断生图节点
- 提示词为 Jinja2 模板，占位符: {{title}} / {{scene_context}}
"""
import json
import logging
import os

from jinja2 import Template

logger = logging.getLogger(__name__)

# 默认提示词（与 config/image_prompts.json 默认内容逐字一致，文件缺失/损坏时兜底）
_DEFAULT_PROMPTS = {
    "main": "产品：{{title}}。生成该产品的电商营销主图。要求：创意营销风格、可包含场景化背景、突出产品卖点、适合Ozon平台商品卡首图展示、高清细节、适合俄罗斯电商平台展示、符合俄罗斯人民审美。画面中严禁出现任何水印、logo、价格、运费、促销、二维码、物流信息、退换货说明、地址、电话、联系方式、店铺名称等字样。",
    "white_bg": "产品：{{title}}。去除产品背景，生成纯白底产品图。严格要求：纯白背景(#FFFFFF)、纯产品摄影、高清细节、严格禁止任何文字/水印/标签/数字/品牌logo/二维码/联系方式/价格信息/中文字符/英文字符/促销标语、非信息图/非营销海报、专业电商产品摄影风格、产品必须与参考图一致。画面中严禁出现任何文字、数字、水印、logo、价格、运费、促销、二维码、物流信息、退换货说明、地址、电话、联系方式、店铺名称等字样（俄语/中文/英文均禁止）。",
    "multi_angle": "产品：{{title}}。生成该产品的多角度实物展示图。严格要求：纯白背景(#FFFFFF)、纯产品摄影、展示产品正面/侧面/背面不同角度、高清细节清晰、无任何文字/标签/参数/logo、无水印、非信息图/非营销海报、专业电商产品摄影风格。画面中严禁出现任何文字、数字、水印、logo、价格、运费、促销、二维码、物流信息、退换货说明、地址、电话、联系方式、店铺名称等字样（俄语/中文/英文均禁止）。",
    "scene_1": "生成产品电商场景图。要求：展示产品在{{scene_context}}场景中的使用效果，展示产品在特殊场景中的独特应用，吸引消费者兴趣，适合俄罗斯电商平台展示，适合俄罗斯消费者审美。画面中严禁出现任何水印、logo、价格、运费、促销、二维码、物流信息、退换货说明、地址、电话、联系方式、店铺名称等字样。",
    "scene_2": "生成产品电商场景图。要求：展示产品在{{scene_context}}场景中的使用效果，展示产品在特殊场景中的独特应用，吸引消费者兴趣，适合俄罗斯电商平台展示，适合俄罗斯消费者审美。画面中严禁出现任何水印、logo、价格、运费、促销、二维码、物流信息、退换货说明、地址、电话、联系方式、店铺名称等字样。",
    "scene_3": "生成产品电商场景图。要求：展示产品在{{scene_context}}场景中的使用效果，展示产品在特殊场景中的独特应用，吸引消费者兴趣，适合俄罗斯电商平台展示，适合俄罗斯消费者审美。画面中严禁出现任何水印、logo、价格、运费、促销、二维码、物流信息、退换货说明、地址、电话、联系方式、店铺名称等字样（俄语/中文/英文均禁止）。",
    "comparison": "生成产品对比电商展示图。突出产品优势，吸引消费者购买。适合俄罗斯电商平台展示，符合俄罗斯人民审美。画面中严禁出现任何水印、logo、价格、运费、促销、二维码、物流信息、退换货说明、地址、电话、联系方式、店铺名称等字样（俄语/中文/英文均禁止）。",
    "detail": "生成产品电商详情展示图。要求：清晰展示产品细节和材质，高清晰度，适合俄罗斯电商平台展示，符合俄罗斯人民审美。画面中严禁出现任何水印、logo、价格、运费、促销、二维码、物流信息、退换货说明、地址、电话、联系方式、店铺名称等字样（俄语/中文/英文均禁止）。",
    "social_proof": "生成产品好评如潮展示图。要求：展示大量五星好评、高评分、买家满意等正面评价信息，营造热销爆款氛围，增加消费者信任感和购买欲望，适合俄罗斯电商平台展示，适合俄罗斯消费者审美。画面中严禁出现任何水印、logo、价格、运费、促销、二维码、物流信息、退换货说明、地址、电话、联系方式、店铺名称等字样（俄语/中文/英文均禁止）。",
    "variant_white_bg": "去除产品背景，生成纯白底图：要求：纯白背景（#FFFFFF），无文字水印，专业产品摄影风格。画面中严禁出现任何文字、数字、水印、logo、价格、运费、促销、二维码、物流信息、退换货说明、地址、电话、联系方式、店铺名称等字样（俄语/中文/英文均禁止）。",
}


def _load_prompt_config() -> dict:
    """从 {APP_WORKSPACE_PATH}/config/image_prompts.json 读提示词（每次现读，热加载）。

    失败返回空 dict（由 get_image_prompt 回退默认提示词）。
    """
    workspace = os.getenv("APP_WORKSPACE_PATH") or os.getcwd()
    cfg_path = os.path.join(workspace, "config", "image_prompts.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as fd:
            data = json.load(fd)
        if isinstance(data, dict):
            return data
        logger.warning("生图提示词配置格式错误(%s): 期望 dict，实际 %s", cfg_path, type(data).__name__)
    except FileNotFoundError:
        logger.warning("生图提示词配置文件不存在(%s)，使用默认提示词", cfg_path)
    except Exception as e:
        logger.warning("生图提示词配置加载失败(%s): %s，使用默认提示词", cfg_path, e)
    return {}


def get_image_prompt(key: str, **kwargs) -> str:
    """取指定 key 的生图提示词（Jinja2 渲染 {{title}}/{{scene_context}} 等占位符）。

    优先级: image_prompts.json 中的模板 > 模块级默认模板；
    渲染失败回退默认模板原文（不传占位符变量时保留占位符，避免拼进畸形文本）。
    """
    template = _load_prompt_config().get(key) or _DEFAULT_PROMPTS.get(key, "")
    if not template:
        logger.warning("生图提示词 key 不存在且无默认值: %s", key)
        return ""
    try:
        return Template(template).render(**kwargs)
    except Exception as e:
        logger.warning("生图提示词渲染失败(key=%s): %s，回退默认模板", key, e)
        return _DEFAULT_PROMPTS.get(key, template)
