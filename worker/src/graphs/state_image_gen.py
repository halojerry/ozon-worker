# 图片生成节点State定义
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field


# ========== 10个图片节点的Input/Output定义 ==========

# Phase1节点（2个）
class WhiteBgInput(BaseModel):
    """白底图生成节点输入"""
    # ✅ 新增：进度追踪（从GlobalState传递）
    progress_counter: int = Field(default=0, description="全局进度计数器（从GlobalState传递）")
    
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn token")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（参考图）")
    image_gen_plan: Optional[Dict[str, int]] = Field(default=None, description="T7b: image_gen_plan（type→count；plan 无该 slot → 节点跳过，不调生图 API）")

class WhiteBgOutput(BaseModel):
    """白底图生成节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=8, description="节点计数器（Phase1第1个节点，更新为8）")
    
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL")


class MultiAngleInput(BaseModel):
    """多角度图生成节点输入"""
    # ✅ 新增：进度追踪（从GlobalState传递）
    progress_counter: int = Field(default=0, description="全局进度计数器（从GlobalState传递）")
    
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn token")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（参考图）")
    image_gen_plan: Optional[Dict[str, int]] = Field(default=None, description="T7b: image_gen_plan（type→count；plan 无该 slot → 节点跳过，不调生图 API）")

class MultiAngleOutput(BaseModel):
    """多角度图生成节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=9, description="节点计数器（Phase1第2个节点，更新为9）")
    
    multi_angle_image: Optional[str] = Field(default=None, description="多角度图URL")


# Phase2节点（8个）
class MainImageInput(BaseModel):
    """主图生成节点输入"""
    # ✅ 新增：进度追踪（从GlobalState传递）
    progress_counter: int = Field(default=0, description="全局进度计数器（从GlobalState传递）")
    
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn token")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（Phase1失败时回退参考图）")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度图URL（Phase1）")
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL（Phase1）")
    variants: list = Field(default_factory=list, description="变体SKU列表（非空时跳过主图生成，由variant_primary_loop处理）")
    image_gen_plan: Optional[Dict[str, int]] = Field(default=None, description="T7b: image_gen_plan（type→count；plan 无该 slot → 节点跳过，不调生图 API）")

class MainImageOutput(BaseModel):
    """主图生成节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=10, description="节点计数器（Phase2第1个节点，更新为10）")
    
    main_image: Optional[str] = Field(default=None, description="主图URL")


class MultiInfoInput(BaseModel):
    """多信息图生成节点输入"""
    # ✅ 新增：进度追踪（从GlobalState传递）
    progress_counter: int = Field(default=0, description="全局进度计数器（从GlobalState传递）")
    
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn token")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（Phase1失败时回退参考图）")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度图URL（Phase1）")
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL（Phase1）")
    image_gen_plan: Optional[Dict[str, int]] = Field(default=None, description="T7b: image_gen_plan（type→count；plan 无该 slot → 节点跳过，不调生图 API）")

class MultiInfoOutput(BaseModel):
    """多信息图生成节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=11, description="节点计数器（Phase2第2个节点，更新为11）")
    
    multi_info_image: Optional[str] = Field(default=None, description="多信息图URL")


class DetailImageInput(BaseModel):
    """详情图生成节点输入"""
    # ✅ 新增：进度追踪（从GlobalState传递）
    progress_counter: int = Field(default=0, description="全局进度计数器（从GlobalState传递）")
    
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn token")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（Phase1失败时回退参考图）")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度图URL（Phase1）")
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL（Phase1）")
    image_gen_plan: Optional[Dict[str, int]] = Field(default=None, description="T7b: image_gen_plan（type→count；plan 无该 slot → 节点跳过，不调生图 API）")

class DetailImageOutput(BaseModel):
    """详情图生成节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=13, description="节点计数器（Phase2第4个节点，更新为13）")
    
    detail_image: Optional[str] = Field(default=None, description="详情图URL")


class SocialProofInput(BaseModel):
    """社交证明图生成节点输入"""
    # ✅ 新增：进度追踪（从GlobalState传递）
    progress_counter: int = Field(default=0, description="全局进度计数器（从GlobalState传递）")
    
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn token")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（Phase1失败时回退参考图）")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度图URL（Phase1）")
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL（Phase1）")
    image_gen_plan: Optional[Dict[str, int]] = Field(default=None, description="T7b: image_gen_plan（type→count；plan 无该 slot → 节点跳过，不调生图 API）")

class SocialProofOutput(BaseModel):
    """社交证明图生成节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=14, description="节点计数器（Phase2第5个节点，更新为14）")
    
    social_proof_image: Optional[str] = Field(default=None, description="社交证明图URL")


class Scene1Input(BaseModel):
    """场景图1生成节点输入"""
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn token")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（Phase1失败时回退参考图）")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度图URL（Phase1）")
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL（Phase1）")
    scene_context_1: str = Field(default="", description="场景1的使用场景描述（LLM生成）")
    image_gen_plan: Optional[Dict[str, int]] = Field(default=None, description="T7b: image_gen_plan（type→count；plan 无该 slot → 节点跳过，不调生图 API）")

class Scene1Output(BaseModel):
    """场景图1生成节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=15, description="节点计数器（Phase2第6个节点，更新为15）")
    
    scene_1_image: Optional[str] = Field(default=None, description="场景图1 URL")


class Scene2Input(BaseModel):
    """场景图2生成节点输入"""
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn token")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（Phase1失败时回退参考图）")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度图URL（Phase1）")
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL（Phase1）")
    scene_context_2: str = Field(default="", description="场景2的使用场景描述（LLM生成）")
    image_gen_plan: Optional[Dict[str, int]] = Field(default=None, description="T7b: image_gen_plan（type→count；plan 无该 slot → 节点跳过，不调生图 API）")

class Scene2Output(BaseModel):
    """场景图2生成节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=16, description="节点计数器（Phase2第7个节点，更新为16）")
    
    scene_2_image: Optional[str] = Field(default=None, description="场景图2 URL")


class Scene3Input(BaseModel):
    """场景图3生成节点输入"""
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn token")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（Phase1失败时回退参考图）")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度图URL（Phase1）")
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL（Phase1）")
    scene_context_3: str = Field(default="", description="场景3的使用场景描述（LLM生成）")
    image_gen_plan: Optional[Dict[str, int]] = Field(default=None, description="T7b: image_gen_plan（type→count；plan 无该 slot → 节点跳过，不调生图 API）")

class Scene3Output(BaseModel):
    """场景图3生成节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=17, description="节点计数器（Phase2第8个节点，更新为17）")
    
    scene_3_image: Optional[str] = Field(default=None, description="场景图3 URL")


class ComparisonInput(BaseModel):
    """对比图生成节点输入"""
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn token")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（Phase1失败时回退参考图）")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度图URL（Phase1）")
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL（Phase1）")
    image_gen_plan: Optional[Dict[str, int]] = Field(default=None, description="T7b: image_gen_plan（type→count；plan 无该 slot → 节点跳过，不调生图 API）")

class ComparisonOutput(BaseModel):
    """对比图生成节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=18, description="节点计数器（Phase2第9个节点，更新为18）")
    
    comparison_image: Optional[str] = Field(default=None, description="对比图URL")


# ========== 子图State定义 ==========

class ImageGenSubgraphState(BaseModel):
    """图片生成子图状态"""
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn token")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")
    ozon_client_id: str = Field(default="", description="Ozon Client-Id")
    ozon_api_key: str = Field(default="", description="Ozon Api-Key")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（参考图）")
    variants: list = Field(default_factory=list, description="变体SKU列表")
    scene_context_1: str = Field(default="", description="场景1描述")
    scene_context_2: str = Field(default="", description="场景2描述")
    scene_context_3: str = Field(default="", description="场景3描述")
    
    # 图片结果（每个节点返回独立字段）
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度图URL")
    main_image: Optional[str] = Field(default=None, description="主图URL")
    multi_info_image: Optional[str] = Field(default=None, description="多信息图URL")
    detail_image: Optional[str] = Field(default=None, description="详情图URL")
    social_proof_image: Optional[str] = Field(default=None, description="社交证明图URL")
    scene_1_image: Optional[str] = Field(default=None, description="场景图1 URL")
    scene_2_image: Optional[str] = Field(default=None, description="场景图2 URL")
    scene_3_image: Optional[str] = Field(default=None, description="场景图3 URL")
    comparison_image: Optional[str] = Field(default=None, description="对比图URL")
    
    # 失败图片列表（避免并行节点冲突）
    failed_images: List[str] = Field(default_factory=list, description="失败的图片节点列表")


class ImageGenSubgraphInput(BaseModel):
    """图片生成子图输入"""
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品数据")
    token: str = Field(default="", description="api.mxou.cn token")
    visual_vars: Optional[Dict[str, str]] = Field(default=None, description="19 个视觉变量（visual_vars_llm 生成）")
    ozon_client_id: str = Field(default="", description="Ozon Client-Id")
    ozon_api_key: str = Field(default="", description="Ozon Api-Key")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（参考图）")
    variants: list = Field(default_factory=list, description="变体SKU列表")
    scene_context_1: str = Field(default="", description="场景1描述")
    scene_context_2: str = Field(default="", description="场景2描述")
    scene_context_3: str = Field(default="", description="场景3描述")


class ImageGenSubgraphOutput(BaseModel):
    """图片生成子图输出"""
    all_images: Dict[str, str] = Field(default_factory=dict, description="所有生成的图片URL")
    failed_images: List[str] = Field(default_factory=list, description="失败的图片节点列表")