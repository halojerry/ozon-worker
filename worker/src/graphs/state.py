"""工作流状态定义 - Ozon电商自动化系统"""
import os
import operator  # ✅ 导入operator，用于Annotated的合并策略
from typing import Optional, List, Dict, Any, Annotated  # ✅ 导入Annotated
from pydantic import BaseModel, Field


def _overwrite_str(old: str, new: str) -> str:
    """字符串覆盖reducer：后写入的值覆盖先写入的（last-write-wins）。
    v0.8.0: None保护，不覆盖已有值"""
    return new if new is not None else old


# ==================== 全局状态定义 ====================
class GlobalState(BaseModel):
    """全局状态 - 包含所有处理过程中的数据"""
    # 用户传入的配置
    token: str = Field(default="", description="api.mxou.cn的API Key")
    ozon_client_id: str = Field(default="", description="Ozon Client-Id")
    ozon_api_key: str = Field(default="", description="Ozon Api-Key")
    
    # 进度追踪（全局共享计数器）- ✅ 修改：使用Annotated允许多节点并发更新（取最大值）
    progress_counter: Annotated[int, lambda a, b: max(a, b)] = Field(default=0, description="全局进度计数器（用于计算进度百分比，取最大值避免冲突）")
    run_id: str = Field(default="", description="当前工作流 run_id（用于日志和进度查询）")
    
    # 循环修复机制（验证失败退回修复）
    retry_count: int = Field(default=0, description="验证失败重试次数（最多3次）")
    assembly_retry_count: int = Field(default=0, description="组装阶段类目匹配重试次数（最多2次）")
    error_type: str = Field(default="", description="错误类型分类（标签格式/尺寸重量/图片顺序/材料属性）")
    
    # Supabase配置（必须通过环境变量传入，无默认值）
    supabase_url: str = Field(
        default_factory=lambda: os.getenv("SUPABASE_URL", ""),
        description="Supabase URL（必须通过环境变量 SUPABASE_URL 传入）"
    )
    supabase_key: str = Field(
        default_factory=lambda: os.getenv("SUPABASE_KEY", ""),
        description="Supabase service_role key（必须通过环境变量 SUPABASE_KEY 传入）"
    )
    
    # 产品数据
    envelope: Dict[str, Any] = Field(default_factory=dict, description="产品数据envelope")
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品草稿数据")
    source: Optional[Dict[str, Any]] = Field(default=None, description="产品来源数据")
    extensions: Optional[Dict[str, Any]] = Field(default=None, description="扩展配置")
    
    # 参考图数据（关键：用于图片生成）
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（Phase1参考图）")
    clean_ref: Optional[str] = Field(default=None, description="Phase1提取的干净产品参考图URL（Phase2参考图）")
    
    # 认证信息
    user_id: str = Field(default="", description="用户ID")
    token_id: str = Field(default="", description="Token ID")
    balance: float = Field(default=0.0, description="用户余额")
    currency_code: str = Field(default="", description="店铺货币类型（CNY或RUB，关键：决定价格货币）")
    
    # 任务信息
    task_id: str = Field(default="", description="任务ID")
    status: str = Field(default="", description="任务状态")
    
    # 跟卖竞品信息（follow_sell_import_node 提取，供下游使用）
    competitor_name: str = Field(default="", description="跟卖竞品的俄语标题")
    competitor_price: str = Field(default="", description="跟卖竞品的Ozon售价")
    
    # 类目信息
    category: Optional[Dict[str, Any]] = Field(default=None, description="类目信息")
    description_category_id: str = Field(default="", description="描述类目ID")
    type_id: str = Field(default="", description="类型ID")
    
    # 属性映射
    attributes_schema: List[Dict[str, Any]] = Field(default_factory=list, description="属性schema")
    llm_attributes: List[Dict[str, Any]] = Field(default_factory=list, description="LLM生成的属性")
    final_attributes: List[Dict[str, Any]] = Field(default_factory=list, description="最终属性列表")
    
    # 图片结果
    phase1_images: Dict[str, str] = Field(default_factory=dict, description="Phase1图片URLs")
    phase2_images: Dict[str, str] = Field(default_factory=dict, description="Phase2图片URLs")
    
    # 场景生成结果（LLM生成的3个场景）
    scene_context_1: str = Field(default="", description="场景1的使用场景描述（LLM生成）")
    scene_context_2: str = Field(default="", description="场景2的使用场景描述（LLM生成）")
    scene_context_3: str = Field(default="", description="场景3的使用场景描述（LLM生成）")
    all_images: Dict[str, str] = Field(default_factory=dict, description="所有图片URLs")
    
    # 价格信息
    pricing_info: Dict[str, Any] = Field(default_factory=dict, description="价格计算结果")
    
    # Ozon上传结果
    product_id: Optional[str] = Field(default=None, description="Ozon商品ID（第一个变体）")
    ozon_task_id: str = Field(default="", description="Ozon上传任务ID（临时，ozon_upload设置，ozon_status读取后替换为真实product_id）")
    product_ids: List[str] = Field(default_factory=list, description="所有变体的Ozon商品ID列表")
    upload_status: str = Field(default="", description="Ozon上传状态（success/failed/pending/timeout）")
    
    # ✅ 新增：验证相关字段（条件路径函数依赖）
    validation_errors: List[str] = Field(default_factory=list, description="验证错误列表（仅当前节点产生）")
    is_valid: bool = Field(default=True, description="是否验证通过")
    errors: List[Dict[str, Any]] = Field(default_factory=list, description="Ozon API返回的结构化错误数组")
    auto_fixed: bool = Field(default=False, description="是否已自动修复")
    
    # 错误信息（last-write-wins：后写入的覆盖先写入的）
    error_message: Annotated[str, _overwrite_str] = Field(default="", description="错误信息")
    error_code: str = Field(default="", description="错误代码")
    failed_stage: Annotated[str, operator.add] = Field(default="", description="失败的节点名称（允许多个节点并发更新）")
    stages: Annotated[Dict[str, str], lambda a, b: {**a, **b}] = Field(default_factory=dict, description="处理阶段状态（允许多个节点并发更新，字典合并）")
    
    # 视频信息
    video_url: str = Field(default="", description="视频URL")
    
    # ✅ 新增：变体商品支持
    item_id: str = Field(default="", description="1688商品ID（用于变体绑定）")
    variants: List[Dict[str, Any]] = Field(default_factory=list, description="变体SKU列表（相同item_id的不同sku_id）")
    variant_primary_images: List[str] = Field(default_factory=list, description="已生成的变体主图列表（对应每个variant）")
    # ✅ 个体图片字段（生图节点输出 → prepare_ozon_upload 消费）
    main_image: Optional[str] = Field(default=None, description="主营销图URL")
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度展示图URL")
    scene_1_image: Optional[str] = Field(default=None, description="场景图1 URL")
    scene_2_image: Optional[str] = Field(default=None, description="场景图2 URL")
    scene_3_image: Optional[str] = Field(default=None, description="场景图3 URL")
    detail_image: Optional[str] = Field(default=None, description="详情图URL")
    social_proof_image: Optional[str] = Field(default=None, description="社交证明图URL")
    comparison_image: Optional[str] = Field(default=None, description="对比图URL")
    ozon_payloads: List[Dict[str, Any]] = Field(default_factory=list, description="多个Ozon payload列表（多SKU变体）")
    uploaded_products: List[Dict[str, Any]] = Field(default_factory=list, description="已上传的商品列表（包含sku_id、task_id等）")


# ==================== 图输入输出定义 ====================
class GraphInput(BaseModel):
    """工作流输入 - 用户传入的数据"""
    # 必需字段
    token: str = Field(..., description="api.mxou.cn的API Key")
    ozon_client_id: str = Field(..., description="Ozon Client-Id")
    ozon_api_key: str = Field(..., description="Ozon Api-Key")
    envelope: Dict[str, Any] = Field(..., description="产品数据envelope")
    
    # ✅ 已删除：supabase_url和supabase_key字段（用户不需要知道平台Supabase配置）
    # 所有用户数据统一存储到平台的Supabase实例，通过环境变量配置（SUPABASE_URL和SUPABASE_KEY）


class GraphOutput(BaseModel):
    """工作流输出 - 返回给用户的结果"""
    task_id: str = Field(..., description="任务ID")
    product_id: Optional[str] = Field(default=None, description="Ozon商品ID")
    
    # ✅ 新增：采购信息
    purchase_url: str = Field(default="", description="采购链接（1688）")
    purchase_cost: str = Field(default="", description="采购成本（CNY）")
    sku_id: str = Field(default="", description="1688 SKU_ID")
    
    # ✅ 新增：利润预估
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估明细")
    
    # ✅ 新增：多SKU变体输出
    variant_count: int = Field(default=0, description="变体SKU数量")
    uploaded_products: List[Dict[str, Any]] = Field(default_factory=list, description="已上传商品列表（包含sku_id、task_id、product_id等）")
    
    # 已有字段
    all_images: Dict[str, str] = Field(default_factory=dict, description="生成的图片URLs")
    final_attributes: List[Dict[str, Any]] = Field(default_factory=list, description="属性列表")
    pricing_info: Dict[str, Any] = Field(default_factory=dict, description="价格信息")
    error_message: str = Field(default="", description="错误信息")
    stages: Dict[str, str] = Field(default_factory=dict, description="处理阶段状态")


# ==================== 认证节点 ====================
class AuthInput(BaseModel):
    """认证节点输入"""
    token: str = Field(..., description="api.mxou.cn的API Key")
    ozon_client_id: str = Field(..., description="Ozon Client-Id")
    ozon_api_key: str = Field(..., description="Ozon Api-Key")
    envelope: Optional[Dict[str, Any]] = Field(default=None, description="产品数据envelope")


class AuthOutput(BaseModel):
    """认证节点输出"""
    # ✅ 新增：进度追踪（关键：用于计算进度百分比）
    progress_counter: int = Field(default=1, description="节点计数器（更新为1）")
    
    user_id: str = Field(default="", description="用户ID")
    token_id: str = Field(default="", description="Token ID")
    balance: float = Field(default=0.0, description="用户余额")
    supabase_url: str = Field(default="", description="Supabase URL")
    supabase_key: str = Field(default="", description="Supabase key")
    ozon_client_id: str = Field(default="", description="Ozon Client-Id")
    ozon_api_key: str = Field(default="", description="Ozon Api-Key")
    currency_code: str = Field(default="", description="店铺货币类型（CNY或RUB，关键：决定价格货币）")
    
    # 从envelope提取的数据（关键：传递给下游节点）
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品草稿数据")
    source: Optional[Dict[str, Any]] = Field(default=None, description="产品来源数据")
    extensions: Optional[Dict[str, Any]] = Field(default=None, description="扩展配置")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（Phase1参考图）")
    
    # 错误信息
    error_code: str = Field(default="", description="错误代码")
    error_message: str = Field(default="", description="错误信息")


# ==================== 数据摄入节点 ====================
class IngestInput(BaseModel):
    """数据摄入节点输入"""
    envelope: Dict[str, Any] = Field(..., description="产品数据envelope")
    user_id: str = Field(..., description="用户ID")
    supabase_url: str = Field(..., description="Supabase URL")
    supabase_key: str = Field(..., description="Supabase key")
    currency_code: str = Field(default="", description="店铺货币类型（从auth_node传递）")  # 关键：传递currency_code


class IngestOutput(BaseModel):
    """数据摄入节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=2, description="节点计数器（更新为2）")
    
    task_id: str = Field(..., description="任务ID")
    status: str = Field(default="accepted", description="任务状态")
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品草稿数据")
    source: Optional[Dict[str, Any]] = Field(default=None, description="产品来源数据")
    extensions: Optional[Dict[str, Any]] = Field(default=None, description="扩展配置")
    currency_code: str = Field(default="", description="店铺货币类型（从auth_node传递）")  # 关键：传递currency_code
    variants: List[Dict[str, Any]] = Field(default_factory=list, description="变体SKU列表（多SKU商品）")
    item_id: str = Field(default="", description="1688商品ID（用于变体绑定）")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（传递给Phase2节点作为参考图回退）")


# ==================== 类目查找节点 ====================
class CategoryLookupInput(BaseModel):
    """类目查找节点输入"""
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品草稿数据")
    source: Optional[Dict[str, Any]] = Field(default=None, description="产品来源数据")
    extensions: Optional[Dict[str, Any]] = Field(default=None, description="扩展配置")
    supabase_url: str = Field(..., description="Supabase URL")
    supabase_key: str = Field(..., description="Supabase key")
    ozon_client_id: str = Field(default="", description="Ozon Client-Id")
    ozon_api_key: str = Field(default="", description="Ozon Api-Key")
    task_id: str = Field(..., description="任务ID")
    currency_code: str = Field(default="", description="店铺货币类型（从auth_node传递）")  # 关键：传递currency_code
    token: str = Field(default="", description="api.mxou.cn的API Key（用于LLM调用）")  # 关键：LLM调用使用用户token


class CategoryLookupOutput(BaseModel):
    """类目查找节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=3, description="节点计数器（更新为3）")
    
    # 类目信息
    category: Optional[Dict[str, Any]] = Field(default=None, description="类目信息")
    description_category_id: str = Field(default="", description="描述类目ID")
    type_id: str = Field(default="", description="类型ID")
    
    # 关键：传递draft/source/extensions给下游节点
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品草稿数据")
    source: Optional[Dict[str, Any]] = Field(default=None, description="产品来源数据")
    extensions: Optional[Dict[str, Any]] = Field(default=None, description="扩展配置")
    currency_code: str = Field(default="", description="店铺货币类型（从auth_node传递）")  # 关键：传递currency_code
    
    # 错误信息
    error_message: str = Field(default="", description="错误信息")
    failed_stage: str = Field(default="category_lookup", description="失败的节点名称")
    blocked: bool = Field(default=False, description="是否被阻断")


# ==================== 价格计算节点 ====================
class PricingInput(BaseModel):
    """价格计算节点输入"""
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品草稿数据")
    extensions: Optional[Dict[str, Any]] = Field(default=None, description="扩展配置")
    supabase_url: str = Field(..., description="Supabase URL")
    supabase_key: str = Field(..., description="Supabase key")
    currency_code: str = Field(default="", description="店铺货币类型（CNY或RUB）")  # 关键：从auth_node传递
    ozon_client_id: str = Field(default="", description="Ozon Client-Id（用于fallback查询店铺货币）")  # 关键：fallback查询
    ozon_api_key: str = Field(default="", description="Ozon Api-Key（用于fallback查询店铺货币）")  # 关键：fallback查询


class PricingOutput(BaseModel):
    """价格计算节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=4, description="节点计数器（更新为4）")
    
    pricing_info: Dict[str, Any] = Field(default_factory=dict, description="价格计算结果")
    price: str = Field(default="", description="最终价格")
    old_price: str = Field(default="", description="原价")
    error_message: str = Field(default="", description="错误信息")
    failed_stage: str = Field(default="pricing", description="失败的节点名称")


# ==================== 属性获取节点 ====================
class AttributesFetchInput(BaseModel):
    """属性获取节点输入"""
    description_category_id: str = Field(..., description="描述类目ID")
    type_id: str = Field(default="", description="类型ID")
    ozon_client_id: str = Field(default="", description="Ozon Client-Id")
    ozon_api_key: str = Field(default="", description="Ozon Api-Key")
    supabase_url: str = Field(..., description="Supabase URL")
    supabase_key: str = Field(..., description="Supabase key")
    task_id: str = Field(..., description="任务ID")
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品草稿数据（用于提取关键词）")  # 关键：新增字段


class AttributesFetchOutput(BaseModel):
    """属性获取节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=5, description="节点计数器（更新为5）")
    
    attributes_schema: List[Dict[str, Any]] = Field(default_factory=list, description="属性schema")
    learned_attributes: Dict[str, Any] = Field(default_factory=dict, description="已学习的属性映射")
    dictionary_values: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict, description="字典值列表（attribute_id -> [{id, value}]）")  # 关键：新增字段
    ozon_source: str = Field(default="", description="数据来源")
    error_message: str = Field(default="", description="错误信息")
    failed_stage: str = Field(default="attributes_fetch", description="失败的节点名称")


# ==================== 属性LLM映射节点 ====================
class AttributesLLMInput(BaseModel):
    """属性LLM映射节点输入"""
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品草稿数据")
    attributes_schema: List[Dict[str, Any]] = Field(default_factory=list, description="属性schema")
    learned_attributes: Dict[str, Any] = Field(default_factory=dict, description="已学习的属性映射")
    dictionary_values: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict, description="字典值列表（attribute_id -> [{id, value}]）")  # 关键：新增字段
    token: str = Field(default="", description="api.mxou.cn的API Key")
    description_category_id: str = Field(default="", description="描述类目ID")
    ozon_client_id: str = Field(default="", description="Ozon卖家客户端ID")
    ozon_api_key: str = Field(default="", description="Ozon卖家API密钥")
    type_id: str = Field(default="", description="Ozon类型ID")


class AttributesLLMOutput(BaseModel):
    """属性LLM映射节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=6, description="节点计数器（更新为6）")
    
    llm_attributes: List[Dict[str, Any]] = Field(default_factory=list, description="LLM生成的属性")
    llm_count: int = Field(default=0, description="LLM映射数量")
    error_message: str = Field(default="", description="错误信息")
    failed_stage: str = Field(default="attributes_llm", description="失败的节点名称")


# ==================== 属性学习节点 ====================
class AttributesLearningInput(BaseModel):
    """属性学习节点输入"""
    llm_attributes: List[Dict[str, Any]] = Field(..., description="LLM生成的属性")
    attributes_schema: List[Dict[str, Any]] = Field(..., description="属性schema")
    description_category_id: str = Field(..., description="描述类目ID")
    type_id: str = Field(default="", description="类型ID")
    ozon_client_id: str = Field(default="", description="Ozon Client-Id")
    ozon_api_key: str = Field(default="", description="Ozon Api-Key")
    supabase_url: str = Field(..., description="Supabase URL")
    supabase_key: str = Field(..., description="Supabase key")
    task_id: str = Field(..., description="任务ID")
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品草稿数据")  # ← 统一为Optional


class AttributesLearningOutput(BaseModel):
    """属性学习节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=7, description="节点计数器（更新为7）")
    
    final_attributes: List[Dict[str, Any]] = Field(default_factory=list, description="最终属性列表")
    enrich_count: int = Field(default=0, description="字典查询成功数量")
    llm_count: int = Field(default=0, description="LLM映射数量")
    error_message: str = Field(default="", description="错误信息")
    failed_stage: str = Field(default="attributes_learning", description="失败的节点名称")


# ==================== 图片生成Phase1节点 ====================
class ImageGenPhase1Input(BaseModel):
    """图片生成Phase1节点输入"""
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品草稿数据")  # ← 统一为Optional
    token: str = Field(default="", description="api.mxou.cn的API Key")
    envelope: Optional[Dict[str, Any]] = Field(default=None, description="产品envelope")  # ← 统一为Optional


class ImageGenPhase1Output(BaseModel):
    """图片生成Phase1节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=8, description="节点计数器（更新为8）")
    
    phase1_images: Dict[str, str] = Field(default_factory=dict, description="Phase1图片URLs")
    white_bg_url: str = Field(default="", description="白底图URL")
    multi_angle_url: str = Field(default="", description="多角度图URL")
    clean_ref: List[str] = Field(default_factory=list, description="干净参考图片")
    error_message: str = Field(default="", description="错误信息")
    failed_stage: str = Field(default="image_gen_phase1", description="失败的节点名称")


# ==================== 图片生成Phase2节点 ====================
class ImageGenPhase2Input(BaseModel):
    """图片生成Phase2节点输入"""
    phase1_images: Dict[str, str] = Field(default_factory=dict, description="Phase1图片URLs")
    clean_ref: List[str] = Field(default_factory=list, description="干净参考图片")
    token: str = Field(default="", description="api.mxou.cn的API Key")
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品草稿数据")  # ← 统一为Optional
    envelope: Optional[Dict[str, Any]] = Field(default=None, description="产品envelope")  # ← 统一为Optional


class ImageGenPhase2Output(BaseModel):
    """图片生成Phase2节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=12, description="节点计数器（更新为12，因为Phase2包含4个节点）")
    
    phase2_images: Dict[str, str] = Field(default_factory=dict, description="Phase2图片URLs")
    all_images: Dict[str, str] = Field(default_factory=dict, description="所有图片URLs")
    error_message: str = Field(default="", description="错误信息")
    failed_stage: str = Field(default="image_gen_phase2", description="失败的节点名称")


# ==================== Ozon上传数据准备节点 ====================
class PrepareOzonUploadInput(BaseModel):
    """Ozon上传数据准备节点输入"""
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品草稿数据")
    source: Optional[Dict[str, Any]] = Field(default=None, description="产品来源数据（采购信息）")  # ✅ 新增：采购来源信息
    pricing_info: Dict[str, Any] = Field(default_factory=dict, description="价格计算结果")
    description_category_id: str = Field(default="", description="Ozon类目ID")
    type_id: str = Field(default="", description="Ozon类型ID")
    final_attributes: List[Dict[str, Any]] = Field(default_factory=list, description="最终属性列表")
    attributes_schema: List[Dict[str, Any]] = Field(default_factory=list, description="属性Schema（用于校验字典属性）")
    
    # Phase1图片
    white_bg_image: Optional[str] = Field(default=None, description="白底图URL")
    multi_angle_image: Optional[str] = Field(default=None, description="多角度展示图URL")
    
    # Phase2图片
    main_image: Optional[str] = Field(default=None, description="主营销图URL")
    multi_info_image: Optional[str] = Field(default=None, description="多信息图URL")
    detail_image: Optional[str] = Field(default=None, description="详情图URL")
    social_proof_image: Optional[str] = Field(default=None, description="社交证明图URL")
    scene_1_image: Optional[str] = Field(default=None, description="场景图1 URL")
    scene_2_image: Optional[str] = Field(default=None, description="场景图2 URL")
    scene_3_image: Optional[str] = Field(default=None, description="场景图3 URL")
    comparison_image: Optional[str] = Field(default=None, description="对比图URL")
    
    # ✅ 多SKU变体数据
    variants: List[Dict[str, Any]] = Field(default_factory=list, description="变体SKU列表")
    variant_primary_images: List[str] = Field(default_factory=list, description="已生成的变体主图列表")
    original_images: List[str] = Field(default_factory=list, description="原始产品图片URL列表（来自envelope，用于Ozon上传避免AI营销图违规）"
    )
    product_id: Optional[str] = Field(default=None, description="Ozon商品ID（跟卖更新模式需要）")
    token: str = Field(default="", description="api.mxou.cn的API Key（用于LLM翻译调用）")  # 关键：LLM翻译使用用户token
    dictionary_values: Dict[str, List[Dict[str, Any]]] = Field(
        default_factory=dict,
        description="Ozon属性字典值缓存（来自attributes_fetch_node，key=attribute_id字符串, value=字典值列表[{id,value,info}...]）"
    )


class PrepareOzonUploadOutput(BaseModel):
    """Ozon上传数据准备节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=19, description="节点计数器（更新为19）")
    
    ozon_payload: Dict[str, Any] = Field(default_factory=dict, description="组装好的Ozon上传payload（单SKU）")
    ozon_payloads: List[Dict[str, Any]] = Field(default_factory=list, description="组装好的Ozon上传payload列表（多SKU变体）")
    ordered_images: List[str] = Field(default_factory=list, description="按顺序排列的图片URL列表")
    
    # ✅ 新增：采购信息和利润预估
    purchase_url: str = Field(default="", description="采购链接（1688链接）")
    purchase_cost: str = Field(default="", description="采购成本（CNY）")
    sku_id: str = Field(default="", description="1688 SKU_ID")
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估明细")
    
    validation_errors: List[str] = Field(default_factory=list, description="验证错误列表")
    error_message: str = Field(default="", description="错误信息")
    failed_stage: str = Field(default="prepare_ozon_upload", description="失败的节点名称")


# ==================== Ozon上传节点 ====================
class OzonUploadInput(BaseModel):
    """Ozon上传节点输入（简化版，接收准备好的payload）"""
    ozon_payload: Dict[str, Any] = Field(default_factory=dict, description="组装好的Ozon上传payload")
    ordered_images: List[str] = Field(default_factory=list, description="按顺序排列的图片URL列表")
    ozon_client_id: str = Field(default="", description="Ozon Client-Id")
    ozon_api_key: str = Field(default="", description="Ozon Api-Key")
    currency_code: str = Field(default="", description="店铺货币类型（CNY/RUB）")
    
    # ✅ 新增：采购信息（从prepare_ozon_upload_node传递）
    purchase_url: str = Field(default="", description="采购链接（1688）")
    purchase_cost: str = Field(default="", description="采购成本（CNY）")
    sku_id: str = Field(default="", description="1688 SKU_ID")
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估明细")
    
    # ✅ 新增：ozon_validate_node传递的错误信息（用于检查严重错误）
    error_message: str = Field(default="", description="ozon_validate_node返回的错误信息")
    validation_errors: List[str] = Field(default_factory=list, description="验证错误列表")


class OzonUploadOutput(BaseModel):
    """Ozon上传节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=20, description="节点计数器（更新为20）")

    product_id: Optional[str] = Field(default=None, description="Ozon商品ID（第一个变体，向后兼容）")
    ozon_task_id: str = Field(default="", description="Ozon上传任务ID（ozon_upload设置，ozon_status读取）")
    product_ids: List[str] = Field(default_factory=list, description="所有变体的Ozon商品ID列表")
    upload_status: str = Field(default="", description="上传状态（success/failed）")
    
    # ✅ 新增：采购信息（传递到GraphOutput）
    purchase_url: str = Field(default="", description="采购链接（1688）")
    purchase_cost: str = Field(default="", description="采购成本（CNY）")
    sku_id: str = Field(default="", description="1688 SKU_ID")
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估明细")
    
    # ✅ 新增：错误信息（传递到下游节点）
    error_message: str = Field(default="", description="错误信息")
    validation_errors: List[str] = Field(default_factory=list, description="验证错误列表")
    failed_stage: str = Field(default="ozon_upload", description="失败的节点名称")
    stages: Dict[str, str] = Field(default_factory=dict, description="节点状态标记")


# ==================== Ozon预检测节点 ====================
class OzonValidateInput(BaseModel):
    """Ozon预检测节点输入"""
    ozon_payload: Dict[str, Any] = Field(..., description="Ozon商品上传payload")
    ordered_images: List[str] = Field(default_factory=list, description="排序后的图片列表")
    ozon_client_id: str = Field(..., description="Ozon店铺ID")
    ozon_api_key: str = Field(..., description="Ozon API密钥")
    attributes_schema: List[Dict[str, Any]] = Field(default_factory=list, description="属性Schema（用于校验字典属性）")
    
    # ✅ 新增：采购信息（传递到下游）
    purchase_url: str = Field(default="", description="采购链接（1688）")
    purchase_cost: str = Field(default="", description="采购成本（CNY）")
    sku_id: str = Field(default="", description="1688 SKU_ID")
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估明细")

    # 条件分支路径函数需要访问的字段（ozon_validate节点输出后合并到GlobalState）
    validation_errors: List[str] = Field(default_factory=list, description="验证错误列表")
    is_valid: bool = Field(default=True, description="是否验证通过")
    error_message: str = Field(default="", description="错误信息")


class OzonValidateOutput(BaseModel):
    """Ozon预检测节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=21, description="节点计数器（更新为21）")
    
    ozon_payload: Dict[str, Any] = Field(..., description="修复后的Ozon payload")
    ordered_images: List[str] = Field(default_factory=list, description="排序后的图片列表")
    validation_errors: List[str] = Field(default_factory=list, description="验证错误列表")
    is_valid: bool = Field(default=True, description="是否验证通过")
    auto_fixed: bool = Field(default=False, description="是否已自动修复vat/unit等字段")
    
    # ✅ 新增：采购信息（传递到下游）
    purchase_url: str = Field(default="", description="采购链接（1688）")
    purchase_cost: str = Field(default="", description="采购成本（CNY）")
    sku_id: str = Field(default="", description="1688 SKU_ID")
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估明细")
    
    error_message: str = Field(default="", description="错误信息")
    
    # ✅ 新增：循环修复相关字段
    retry_count: int = Field(default=0, description="验证失败重试次数")
    error_type: str = Field(default="", description="错误类型分类")
    stages: Dict[str, Any] = Field(default_factory=dict, description="阶段状态标记")


# ==================== Ozon状态轮询节点 ====================
class OzonStatusInput(BaseModel):
    """Ozon状态轮询节点输入"""
    product_id: Optional[str] = Field(default=None, description="Ozon商品ID（可选，上传失败时为None）")
    ozon_client_id: str = Field(..., description="Ozon Client-Id")
    ozon_api_key: str = Field(..., description="Ozon Api-Key")
    
    # ✅ 新增：采购信息（传递到GraphOutput）
    purchase_url: str = Field(default="", description="采购链接（1688）")
    purchase_cost: str = Field(default="", description="采购成本（CNY）")
    sku_id: str = Field(default="", description="1688 SKU_ID")
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估明细")

    # 条件分支路径函数需要访问的字段（ozon_status节点输出后合并到GlobalState）
    status: str = Field(default="", description="Ozon商品状态")
    errors: List[Dict[str, Any]] = Field(default_factory=list, description="Ozon API返回的错误列表")
    error_message: str = Field(default="", description="错误信息")
    upload_status: str = Field(default="", description="上传状态")
    stages: Dict[str, str] = Field(default_factory=dict, description="各阶段执行状态")


class OzonStatusOutput(BaseModel):
    """Ozon状态轮询节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=22, description="节点计数器（更新为22）")
    
    status: str = Field(default="", description="商品状态（processed/failed/blocked/pending_moderation/timeout）")
    upload_status: str = Field(default="", description="上传状态（success/failed/pending/timeout）")
    product_id: Optional[str] = Field(default=None, description="Ozon真实商品ID（从API获取）")
    task_id: str = Field(default="", description="Ozon任务ID")
    
    # ✅ 新增：采购信息（传递到GraphOutput）
    purchase_url: str = Field(default="", description="采购链接（1688）")
    purchase_cost: str = Field(default="", description="采购成本（CNY）")
    sku_id: str = Field(default="", description="1688 SKU_ID")
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估明细")
    
    errors: List[Dict[str, Any]] = Field(default_factory=list, description="Ozon API返回的结构化错误数组")
    error_message: str = Field(default="", description="错误信息")
    error_code: str = Field(default="", description="错误代码（如VARIANT_NOT_MERGED、VARIANT_MODERATE_REJECTED、VARIANT_UPLOAD_FAILED）")
    failed_stage: str = Field(default="ozon_status", description="失败的节点名称")


# ==================== 错误处理节点 ====================
class ErrorHandlerInput(BaseModel):
    """错误处理节点输入"""
    product_id: Optional[str] = Field(default=None, description="Ozon商品ID")
    error_message: str = Field(..., description="错误信息")
    errors: List[Dict[str, Any]] = Field(default_factory=list, description="错误列表")
    failed_stage: str = Field(default="", description="失败的节点名称")
    
    # ✅ 新增：采购信息（传递到GraphOutput）
    purchase_url: str = Field(default="", description="采购链接（1688）")
    purchase_cost: str = Field(default="", description="采购成本（CNY）")
    sku_id: str = Field(default="", description="1688 SKU_ID")
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估明细")


class ErrorHandlerOutput(BaseModel):
    """错误处理节点输出"""
    # ✅ 新增：进度追踪
    progress_counter: int = Field(default=23, description="节点计数器（更新为23）")
    
    error_type: str = Field(default="", description="错误类型（category/attribute/image/price/other）")
    error_detail: str = Field(default="", description="错误详情")
    suggested_fix: str = Field(default="", description="建议修复方案")
    
    # ✅ 新增：采购信息（传递到GraphOutput）
    purchase_url: str = Field(default="", description="采购链接（1688）")
    purchase_cost: str = Field(default="", description="采购成本（CNY）")
    sku_id: str = Field(default="", description="1688 SKU_ID")
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估明细")


# ==================== 视频生成节点 ====================
class VideoGenInput(BaseModel):
    """视频生成节点输入"""
    all_images: Dict[str, str] = Field(..., description="所有图片URLs")
    mxou_token: str = Field(..., description="api.mxou.cn的API Key")
    ozon_client_id: str = Field(default="", description="Ozon Client-Id")
    ozon_api_key: str = Field(default="", description="Ozon Api-Key")
    task_id: str = Field(..., description="任务ID")


class VideoGenOutput(BaseModel):
    """视频生成节点输出"""
    video_url: Optional[str] = Field(default=None, description="视频URL")


# ==================== 变体循环节点 ====================
class VariantLoopInput(BaseModel):
    """变体循环输入（用于variant_primary_loop子图）"""
    variants: List[Dict[str, Any]] = Field(default_factory=list, description="变体SKU列表")
    variant_primary_images: List[str] = Field(default_factory=list, description="已生成的变体主图列表")
    current_variant_index: int = Field(default=0, description="当前循环到的variant索引")
    
    # Phase1生成的图片（作为辅助参考）
    white_bg_image: str = Field(default="", description="白底图")
    multi_angle_image: str = Field(default="", description="多角度展示图")
    draft: Dict[str, Any] = Field(default_factory=dict, description="产品数据")


class VariantLoopState(BaseModel):
    """变体循环状态（用于variant_primary_loop子图）"""
    variants: List[Dict[str, Any]] = Field(default_factory=list, description="变体SKU列表")
    variant_primary_images: List[str] = Field(default_factory=list, description="已生成的变体主图列表")
    current_variant_index: int = Field(default=0, description="当前循环到的variant索引")
    
    # Phase1生成的图片（作为辅助参考）
    white_bg_image: str = Field(default="", description="白底图")
    multi_angle_image: str = Field(default="", description="多角度展示图")
    draft: Dict[str, Any] = Field(default_factory=dict, description="产品数据")


class VariantLoopOutput(BaseModel):
    """变体循环输出（用于variant_primary_loop子图）"""
    variant_primary_images: List[str] = Field(default_factory=list, description="已生成的变体主图列表")
    current_variant_index: int = Field(default=0, description="当前循环到的variant索引")
    stages: Dict[str, str] = Field(default_factory=dict, description="节点执行状态")
    error_message: str = Field(default="", description="错误信息")


class VariantPrimaryLoopOutput(BaseModel):
    """变体主图循环节点输出（用于variant_primary_loop_node）"""
    variant_primary_images: List[str] = Field(default_factory=list, description="已生成的所有变体主图列表")


# ==================== 场景生成LLM节点 ====================
class SceneGenerationInput(BaseModel):
    """场景生成LLM节点输入"""
    draft: Dict[str, Any] = Field(default_factory=dict, description="产品数据（包含title、description、category等）")
    description_category_id: str = Field(default="", description="Ozon类目ID")
    type_id: str = Field(default="", description="Ozon类型ID")
    token: str = Field(default="", description="api.mxou.cn的API Key（用于LLM调用）")  # 关键：LLM调用使用用户token


class SceneGenerationOutput(BaseModel):
    """场景生成LLM节点输出"""
    scene_context_1: str = Field(default="", description="场景1的使用场景描述（LLM生成）")
    scene_context_2: str = Field(default="", description="场景2的使用场景描述（LLM生成）")
    scene_context_3: str = Field(default="", description="场景3的使用场景描述（LLM生成）")
    error_message: str = Field(default="", description="错误信息")
    stages: Dict[str, Any] = Field(default_factory=dict, description="阶段信息")
    failed_stage: str = Field(default="video_gen", description="失败的节点名称")


# ==================== 验证循环修复包装器节点 ====================
class ValidationRetryWrapperInput(BaseModel):
    """验证循环修复包装器节点输入（调用validation_retry_loop子图）
    修复范围：属性、特征、类目、价格（不包含图片）"""
    ozon_payload: Dict[str, Any] = Field(..., description="Ozon商品上传payload")
    validation_errors: list = Field(default_factory=list, description="验证错误列表")
    errors: list = Field(default_factory=list, description="Ozon官方错误数组")
    error_message: str = Field(default="", description="错误信息")

    # 传递必要字段
    draft: Dict[str, Any] = Field(default_factory=dict, description="产品数据")
    token: str = Field(default="", description="API Token")
    ozon_client_id: str = Field(default="", description="Ozon Client ID")
    ozon_api_key: str = Field(default="", description="Ozon API Key")
    description_category_id: str = Field(default="", description="Ozon类目ID")
    type_id: str = Field(default="", description="Ozon类型ID")
    task_id: str = Field(default="", description="任务ID")

    # 业务字段（只包含属性/特征/类目/价格相关，不包含图片）
    purchase_url: str = Field(default="", description="采购链接")
    purchase_cost: str = Field(default="", description="采购成本")
    sku_id: str = Field(default="", description="SKU ID")
    profit_estimation: Dict[str, Any] = Field(default_factory=dict, description="利润预估")
    final_attributes: list = Field(default_factory=list, description="最终属性")
    attributes_schema: list = Field(default_factory=list, description="属性Schema")
    dictionary_values: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict, description="字典值数据")
    learned_attributes: Dict[str, Any] = Field(default_factory=dict, description="已学习的属性映射")
    pricing_info: Dict[str, Any] = Field(default_factory=dict, description="价格信息（用于价格修复）")

    # 条件分支路径函数需要访问的字段
    upload_status: str = Field(default="", description="上传状态（success/failed）")
    is_valid: bool = Field(default=True, description="修复后是否有效")
    product_id: Optional[str] = Field(default=None, description="Ozon商品ID（ozon_status已分配，用于靶向修复）")


class ValidationRetryWrapperOutput(BaseModel):
    """验证循环修复包装器节点输出"""
    progress_counter: int = Field(default=21, description="节点计数器（更新为21）")

    ozon_payload: Dict[str, Any] = Field(default_factory=dict, description="修复后的Ozon payload")
    validation_errors: list = Field(default_factory=list, description="最终验证错误")
    is_valid: bool = Field(default=False, description="最终验证结果")
    retry_count: int = Field(default=0, description="实际重试次数")
    error_message: str = Field(default="", description="最终错误信息")
    product_id: Optional[str] = Field(default=None, description="修复后的商品ID（如果重新上传成功）")
    upload_status: str = Field(default="", description="上传状态：success/failed/pending")


# ==================== 学习记录节点 ====================
class LearningRecordInput(BaseModel):
    """学习记录节点输入（上传成功后记录学习数据）"""
    description_category_id: str = Field(..., description="类目ID")  # ← 保持str类型（与GlobalState一致）
    final_attributes: List[Dict[str, Any]] = Field(default_factory=list, description="最终属性列表")
    attributes_schema: List[Dict[str, Any]] = Field(default_factory=list, description="属性Schema")
    draft: Optional[Dict[str, Any]] = Field(default=None, description="产品原始数据（用于提取中文源值）")
    # ✅ 修改为Optional：上传成功状态（从status字段判断）
    ozon_upload_success: Optional[bool] = Field(default=False, description="是否上传成功")
    # ✅ 新增：status字段（从ozon_status_node输出）
    status: Optional[str] = Field(default="", description="Ozon状态（processed/failed/blocked）")
    # ✅ 新增：upload_status字段（从validation_retry_wrapper传入，修复后成功状态）
    upload_status: Optional[str] = Field(default="", description="上传状态（success/failed/pending）")


class LearningRecordOutput(BaseModel):
    """学习记录节点输出"""
    progress_counter: int = Field(default=24, description="节点计数器（更新为24）")
    
    recorded_count: int = Field(..., description="记录的属性数量")


# ==================== 修复结果条件判断节点 ====================
class CondRepairResultInput(BaseModel):
    """修复结果条件判断节点输入（验证循环修复后判断是否需要记录学习数据）"""
    upload_status: str = Field(default="", description="上传状态：success/failed/pending")
    is_valid: bool = Field(default=False, description="最终验证结果")