"""主工作流编排 - Ozon电商自动化系统（并行图片生成版本）"""
import logging
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime.context import Context

logger = logging.getLogger(__name__)

# 导入全局状态和图输入输出
from graphs.state import (
    GlobalState,
    GraphInput,
    GraphOutput
)

# 导入核心节点
from graphs.nodes.auth_node import auth_node
from graphs.nodes.ingest_node import ingest_node
from graphs.nodes.follow_sell_import_node import follow_sell_import_node  # 🆕 跟卖导入节点
from graphs.nodes.assemble_ozon_product_node import assemble_ozon_product_node  # 统一商品组装（替代 4 节点管线）
from graphs.nodes.scene_generation_llm_node import scene_generation_llm_node  # 场景生成LLM节点
from graphs.nodes.pricing_node import pricing_node
from graphs.nodes.prepare_ozon_upload_node import prepare_ozon_upload_node  # 数据准备节点
from graphs.nodes.ozon_upload_node import ozon_upload_node
from graphs.nodes.ozon_validate_node import ozon_validate_node  # 预检测节点
from graphs.nodes.check_quota_node import check_quota_node  # 配额检查节点
from graphs.nodes.ozon_status_node import ozon_status_node  # 状态轮询节点

# ✅ 新增导入：validation_retry_wrapper节点 + learning_record节点
from graphs.nodes.validation_retry_wrapper_node import validation_retry_wrapper_node  # 验证循环修复包装器节点（调用子图）
from graphs.nodes.learning_record_node import learning_record_node  # 学习记录节点（上传成功后记录学习数据）

# 导入图片生成节点（Phase1和Phase2）
from graphs.nodes.variant_primary_loop_node import variant_primary_loop_node  # wrapper节点：调用variant_primary_loop子图

# 导入10个独立图片生成节点
from graphs.nodes.white_bg_gen_node import white_bg_gen_node
from graphs.nodes.multi_angle_gen_node import multi_angle_gen_node
from graphs.nodes.main_image_gen_node import main_image_gen_node
from graphs.nodes.multi_info_gen_node import multi_info_gen_node
from graphs.nodes.detail_gen_node import detail_gen_node
from graphs.nodes.social_proof_gen_node import social_proof_gen_node
from graphs.nodes.scene_1_gen_node import scene_1_gen_node
from graphs.nodes.scene_2_gen_node import scene_2_gen_node
from graphs.nodes.scene_3_gen_node import scene_3_gen_node
from graphs.nodes.comparison_gen_node import comparison_gen_node


# 创建状态图（指定图的输入和输出）
builder = StateGraph(
    GlobalState,
    input_schema=GraphInput,
    output_schema=GraphOutput
)


# ==================== 添加节点 ====================

# Phase 1: 认证 + 数据摄入
builder.add_node("auth", auth_node)
builder.add_node("ingest", ingest_node)
builder.add_node("follow_sell_import", follow_sell_import_node)  # 🆕 跟卖导入

# Phase 2: 类目查找 → 定价（串行：定价先执行，组装节点需要定价信息）
builder.add_node("pricing", pricing_node)
builder.add_node("assemble_ozon_product", assemble_ozon_product_node, metadata={"type": "agent", "llm_cfg": "config/product_assembly_cfg.json"})

# Phase 3.5: 场景生成（使用LLM生成3个场景）
builder.add_node("scene_generation_llm", scene_generation_llm_node, metadata={"type": "agent", "llm_cfg": "config/scene_generation_llm_cfg.json"})

# Phase 4: 图片生成 - 两阶段并行流程
# Phase1（2节点并行）：white_bg + multi_angle，使用输入的原始产品图片作为参考
# Phase2（8节点并行）：营销图片，直接使用Phase1生成的图片作为参考（逻辑内联）
builder.add_node("white_bg_gen", white_bg_gen_node)
builder.add_node("multi_angle_gen", multi_angle_gen_node)
builder.add_node("variant_primary_loop", variant_primary_loop_node, metadata={"type": "looparray"})  # ✅ 多SKU路径：循环生成所有变体主图
builder.add_node("main_image_gen", main_image_gen_node)  # ✅ 单SKU路径：生成单张主图
builder.add_node("multi_info_gen", multi_info_gen_node)
builder.add_node("detail_gen", detail_gen_node)
builder.add_node("social_proof_gen", social_proof_gen_node)
builder.add_node("scene_1_gen", scene_1_gen_node)
builder.add_node("scene_2_gen", scene_2_gen_node)
builder.add_node("scene_3_gen", scene_3_gen_node)
builder.add_node("comparison_gen", comparison_gen_node)

# Phase 5: 数据准备 + Ozon上传 + 错误处理
builder.add_node("prepare_ozon_upload", prepare_ozon_upload_node, metadata={"type": "agent", "llm_cfg": "config/translate_russian_cfg.json"})  # 数据准备节点：整理图片顺序、组装payload、LLM俄语翻译
builder.add_node("ozon_validate", ozon_validate_node)  # 预检测节点：检测Ozon payload是否符合规范
builder.add_node("check_quota", check_quota_node)  # 配额检查节点：上传前检查店铺配额，配额不足阻断上传
builder.add_node("ozon_upload", ozon_upload_node)
builder.add_node("ozon_status", ozon_status_node)  # 状态轮询节点：上传后轮询Ozon商品状态


# ==================== 设置入口点 ====================
builder.set_entry_point("auth")


# ==================== 添加边（数据流转） ====================

# Phase 1-3: 串行处理
# 🆕 路由：跟卖 vs 1688 完整管线
def route_by_sell_type(state):
    """根据 envelope.extensions.follow_sell 决定管线"""
    extensions = state.envelope.get("extensions", {}) if state.envelope else {}
    if extensions.get("follow_sell"):
        logger.info("🔄 路由 → 跟卖管线")
        return "follow_sell"
    logger.info("📦 路由 → 1688 完整管线")
    return "full"

builder.add_conditional_edges(
    source="auth",
    path=route_by_sell_type,
    path_map={
        "follow_sell": "follow_sell_import",
        "full": "ingest",
    }
)

# 跟卖导入 → 走 AI 生图 + 上传管线（复用现有节点）
# import-by-sku 已复制类目+属性，后续生图+上传补充图片并 UPDATE 商品卡
def route_after_follow_sell_import(state):
    """跟卖导入后走简化管线：AI生图 → 上传UPDATE。
    不经过 pricing/assemble（已在 follow_sell_import 中处理）。"""
    if getattr(state, 'error_message', '') and 'ozon_product_id 为空' in str(state.error_message):
        return "END"
    logger.info("跟卖导入完成(product_id=%s)，走 AI 生图 → 上传管线", getattr(state, 'product_id', '?'))
    return "scene_generation_llm"

builder.add_conditional_edges(
    "follow_sell_import",
    route_after_follow_sell_import,
    {"scene_generation_llm": "scene_generation_llm", "END": END}
)
# 1688 管线：ingest → 定价
builder.add_edge("ingest", "pricing")

# 定价 → 商品组装（串行：组装需要定价信息）
# 跟卖产品：_assemble_follow_sell 轻量模式，复用竞品属性+类目
# 1688 产品：_build_items_deterministically 完整模式
builder.add_edge("pricing", "assemble_ozon_product")

# Phase 3.5: 场景生成（使用LLM生成3个场景描述）
builder.add_edge("assemble_ozon_product", "scene_generation_llm")
builder.add_edge("scene_generation_llm", "white_bg_gen")  # scene_generation_llm → Phase1开始
builder.add_edge("scene_generation_llm", "multi_angle_gen")  # scene_generation_llm → Phase1开始

# Phase 4: 图片生成 - 两阶段并行流程 + 多SKU条件分支
# Phase1（2节点并行）：使用输入的原始产品图片作为参考，生成白底图和多角度图
# （已移除）builder.add_edge("attributes_learning", "white_bg_gen")
# （已移除）builder.add_edge("attributes_learning", "multi_angle_gen")

# Phase2（7节点并行）：生成营销图（不包括主图，主图由variant_check分支处理）
# Phase2节点等待Phase1完成后再开始（white_bg_gen和multi_angle_gen）
builder.add_edge(["white_bg_gen", "multi_angle_gen"], "multi_info_gen")
builder.add_edge(["white_bg_gen", "multi_angle_gen"], "detail_gen")
builder.add_edge(["white_bg_gen", "multi_angle_gen"], "social_proof_gen")
builder.add_edge(["white_bg_gen", "multi_angle_gen"], "comparison_gen")
builder.add_edge(["white_bg_gen", "multi_angle_gen"], "scene_1_gen")
builder.add_edge(["white_bg_gen", "multi_angle_gen"], "scene_2_gen")
builder.add_edge(["white_bg_gen", "multi_angle_gen"], "scene_3_gen")

# ==================== 主图生成（简化架构）====================
# 主图生成节点直接根据variants判断是否需要生成多张主图（不使用variant_check_node）
builder.add_edge(["white_bg_gen", "multi_angle_gen"], "variant_primary_loop")  # 多SKU路径
builder.add_edge(["white_bg_gen", "multi_angle_gen"], "main_image_gen")  # 单SKU路径

# Phase2汇聚：主图（variant_primary_loop或main_image_gen） + 其他7个节点 → prepare_ozon_upload
builder.add_edge([
    "variant_primary_loop", "main_image_gen",  # 主图来源（根据variant_check分支）
    "multi_info_gen", "detail_gen", "social_proof_gen",
    "scene_1_gen", "scene_2_gen", "scene_3_gen", "comparison_gen"
], "prepare_ozon_upload")

# ==================== Phase 5: 数据准备 → 预检测 → 上传 → 状态轮询 → 错误处理 ====================
# 新增预检测节点：上传前检测Ozon payload是否符合规范
builder.add_edge("prepare_ozon_upload", "ozon_validate")

# ==================== 新增节点：validation_retry_wrapper + learning_record ====================
builder.add_node("validation_retry_wrapper", validation_retry_wrapper_node, metadata={"type": "loopcond"})  # ← 调用validation_retry_loop子图
builder.add_node("learning_record", learning_record_node)  # ← 上传成功后记录学习数据

# 上传节点：发送商品数据到Ozon
# ==================== Ozon预检测条件分支 + 配额检查 ====================
# 流程: ozon_validate → check_quota → ozon_upload
# 2 道防线: (1) validate 失败 → retry, (2) 配额不足 → retry

# 定义条件判断函数：根据ozon_validate_node的验证结果决定后续流程
def should_upload_after_validate(state):
    """
    title: 是否继续到配额检查
    desc: 根据ozon_validate_node的验证结果决定是否继续（配额检查），或进入修复循环
    """
    is_valid = state.is_valid if hasattr(state, 'is_valid') else True
    validation_errors = state.validation_errors if hasattr(state, 'validation_errors') else []
    
    if not is_valid or (isinstance(validation_errors, list) and len(validation_errors) > 0):
        logger.warning(f"ozon_validate验证失败: is_valid={is_valid}, errors={len(validation_errors) if isinstance(validation_errors, list) else 0}个")
        return "失败"
    
    logger.info("ozon_validate验证成功，进入配额检查")
    return "成功"

# ✅ 验证成功 → check_quota（先检查配额再上传）
builder.add_conditional_edges(
    source="ozon_validate",
    path=should_upload_after_validate,
    path_map={
        "成功": "check_quota",
        "失败": "validation_retry_wrapper",
    }
)

# ✅ 配额检查条件分支：通过 → 上传，阻断 → 进入修复循环
def should_upload_after_quota(state):
    """配额检查结果路由"""
    error_msg = getattr(state, 'error_message', '') or ''
    if '[QUOTA_BLOCKED]' in error_msg:
        logger.warning("配额不足阻断上传: %s", error_msg)
        return "阻断"
    logger.info("配额检查通过，继续上传")
    return "通过"

builder.add_conditional_edges(
    source="check_quota",
    path=should_upload_after_quota,
    path_map={
        "通过": "ozon_upload",
        "阻断": "validation_retry_wrapper",
    }
)

# ❌ 删除循环连接：validation_retry_wrapper → ozon_upload（子图内部已包含重新上传逻辑）
# builder.add_edge("validation_retry_wrapper", "ozon_upload")

# 状态轮询节点：上传后轮询Ozon商品状态（processed/failed/blocked）
builder.add_edge("ozon_upload", "ozon_status")

# ==================== 错误处理分支（修改：添加修复循环）====================
# 定义条件判断函数：根据ozon_status_node的状态决定后续流程
def should_handle_error(state):
    """
    title: 是否需要错误处理
    desc: 根据Ozon商品状态和errors数组判断后续流程
    
    ✅ 修改：不使用累积的error_message判断（避免上游警告污染）
    ✅ pending/timeout状态直接结束，不浪费修复资源
    """
    # ✅ 从OzonStatusOutput获取status字段
    ozon_status_result = state.status if hasattr(state, 'status') else ""
    
    # ✅ 检查errors数组（Ozon API返回的结构化错误）
    errors = state.errors if hasattr(state, 'errors') else []
    if not isinstance(errors, list):
        errors = []
    
    # ✅ 检查product_id（判断是否已import成功）
    product_id = state.product_id if hasattr(state, 'product_id') else None
    
    # ✅ A6修复：timeout状态分情况处理
    if "timeout" in ozon_status_result:
        # 如果有moderate_status=declined，说明审核已拒绝，需要修复
        moderate_status = getattr(state, 'moderate_status', '') if hasattr(state, 'moderate_status') else ''
        if moderate_status == 'declined' or len(errors) > 0:
            logger.warning(f"Ozon审核超时但有错误/拒绝（moderate={moderate_status}, errors={len(errors)}），进入修复循环")
            return "失败"
        # 如果已有有效的 product_id（非"0"且不是task_id）且无错误，说明import成功
        if product_id and str(product_id) not in ("0", "None", ""):
            # 额外判断：product_id 不应该等于 upload 时的 task_id
            task_id_str = str(getattr(state, 'product_id', ''))
            logger.info(f"Ozon审核超时但已import成功(product_id={product_id})，审核异步进行，视为成功")
            return "成功"
        # 没有product_id说明import都没成功，需要修复
        logger.warning("Ozon超时且无product_id，import失败，进入修复循环")
        return "失败"
    
    # ✅ pending状态：有product_id视为成功（import完成，审核异步）
    if "pending" in ozon_status_result:
        if len(errors) > 0:
            logger.info(f"Ozon pending但有{len(errors)}个错误，进入修复循环")
            return "失败"
        if product_id and str(product_id) not in ("0", "None", ""):
            logger.info(f"Ozon pending但已import成功(product_id={product_id})，视为成功")
            return "成功"
        logger.info("Ozon pending且无有效product_id，进入修复循环")
        return "失败"
    
    # ✅ 如果errors数组非空，进入修复循环
    if len(errors) > 0:
        logger.warning(f"发现{len(errors)}个Ozon错误，进入修复循环")
        return "失败"
    
    # ✅ 如果status包含"成功"/"imported"/"approved"/"processed"/"active"，且errors为空，进入learning_record
    success_keywords = ("成功", "imported", "approved", "processed", "active")
    if any(kw in ozon_status_result for kw in success_keywords):
        logger.info("上传成功，进入learning_record（学习闭环）")
        return "成功"
    
    # ✅ 其他未知状态也进入修复循环
    return "失败"

# ✅ 修改条件分支：成功 → learning_record → END，失败 → validation_retry_wrapper（修复循环）
builder.add_conditional_edges(
    source="ozon_status",
    path=should_handle_error,
    path_map={
        "成功": "learning_record",  # ← 修改：成功后进入learning_record（学习闭环）
        "失败": "validation_retry_wrapper",  # ← 修改：失败后直接进入修复循环（不经过error_handler）
    }
)

# ✅ 新增：learning_record → END（学习闭环结束）
builder.add_edge("learning_record", END)

# ✅ 新增条件分支：validation_retry_wrapper修复后 → cond_repair_result → 成功/失败
def should_learn_after_repair(state):
    """
    title: 修复结果判断
    desc: 根据validation_retry_wrapper的修复结果决定是否记录学习数据。pending视为软成功（上传已提交，Ozon仍在处理）
    """
    upload_status = state.upload_status if state.upload_status else ""
    if upload_status in ("success", "pending"):
        logger.info(f"修复成功(upload_status={upload_status})，进入learning_record记录学习数据")
        return "成功"
    logger.warning(f"修复失败(upload_status={upload_status})，直接结束")
    return "失败"

builder.add_conditional_edges(
    source="validation_retry_wrapper",
    path=should_learn_after_repair,
    path_map={
        "成功": "learning_record",   # ✅ 修复成功 → learning_record（学习闭环）
        "失败": END                  # ✅ 修复失败 → END
    }
)


# ==================== 编译图 ====================
main_graph = builder.compile()


# ==================== 导出 ====================
__all__ = ["main_graph"]