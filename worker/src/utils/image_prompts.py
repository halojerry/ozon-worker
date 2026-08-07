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
    "main": "产品：{{title}}。这是一张Ozon平台的电商营销主图。画面以产品为视觉中心，占比不低于60%。背景是自然融入的场景环境，柔和自然光打在产品上，清晰呈现材质纹理与细节。整体调性简洁高级，适合俄罗斯消费者的审美偏好。画面中不得出现任何水印、logo、品牌标识、价格标签、运费信息、促销文案、二维码、物流信息、退换货说明、地址、电话、联系方式或店铺名称。",
    "social_proof": "产品：{{title}}。这是一张电商买家好评展示图，用于激发消费者信任和购买欲望。画面以视觉化方式呈现产品的高口碑评价，可包含星级评分、真实使用场景暗示、群体口碑传播氛围。整体调性温暖可信，营造热销爆款的紧迫感。画面中不得出现任何水印、logo、价格、运费、促销、二维码、物流信息、退换货说明、地址、电话、联系方式或店铺名称。",
    "white_bg": "产品：{{title}}。纯白背景(#FFFFFF)电商产品摄影。产品居中完整展示，无阴影无倒影无渐变，产品与参考图一致。禁止任何文字、数字、水印、logo、价格、运费、促销、二维码、电话号码、地址、店铺名称（俄语/中文/英文均禁止）。",
    "multi_angle": "产品：{{title}}。纯白背景(#FFFFFF)多角度产品展示。展示正面、45度侧面、背面三个角度，等大排列，无透视变形，高清细节。禁止任何文字、数字、水印、logo、价格、运费、促销、二维码、电话号码、地址、店铺名称（俄语/中文/英文均禁止）。",
    "scene_1": "产品：{{title}}。场景图：产品在{{scene_context}}中实际使用的画面。产品清晰可见，场景真实自然。禁止任何水印、logo、价格、运费、促销、二维码、电话号码、地址、店铺名称（俄语/中文/英文均禁止）。",
    "scene_2": "产品：{{title}}。场景图：产品在{{scene_context}}中的近距离特写。浅景深，背景虚化，产品主体清晰锐利。禁止任何水印、logo、价格、运费、促销、二维码、电话号码、地址、店铺名称（俄语/中文/英文均禁止）。",
    "scene_3": "产品：{{title}}。场景图：产品在{{scene_context}}环境中的宽幅展示，适合商品卡轮播。禁止任何水印、logo、价格、运费、促销、二维码、电话号码、地址、店铺名称（俄语/中文/英文均禁止）。",
    "comparison": "产品：{{title}}。左右分栏对比展示图，突出产品优势与差异化卖点。禁止任何水印、logo、价格、运费、促销、二维码、电话号码、地址、店铺名称（俄语/中文/英文均禁止）。",
    "detail": "产品：{{title}}。微距细节特写图。展示产品材质肌理、接缝工艺、表面处理，1-3个局部放大，高清细节。禁止任何文字、数字、水印、logo、价格、运费、促销、二维码、电话号码、地址、店铺名称（俄语/中文/英文均禁止）。",
    "variant_white_bg": "产品：{{title}}。纯白背景(#FFFFFF)产品摄影。产品居中，无阴影无倒影，产品与参考图一致。禁止任何文字、数字、水印、logo、价格、运费、促销、二维码、电话号码、地址、店铺名称（俄语/中文/英文均禁止）。",
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
