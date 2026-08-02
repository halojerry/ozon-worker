"""主工作流编排 - Ozon电商自动化系统（并行图片生成版本）"""
import logging
from langgraph.graph import StateGraph, END

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
builder.add_node("assemble_ozon_product", assemble_ozon_product_node, metadata={"type": "agent", "llm_cfg": "config/category_match_v2_cfg.json"})

# Phase 3.5: 场景生成（使用LLM生成3个场景）
builder.add_node("scene_generation_llm", scene_generation_llm_node, metadata={"type": "agent", "llm_cfg": "config/scene_generation_llm_cfg.json"})

# Phase 4: 图片生成 - 两阶段并行流程
# Phase1（2节点并行）：white_bg + multi_angle，使用输入的原始产品图片作为参考
# Phase2（8节点并行）：营销图片，直接使用Phase1生成的图片作为参考（逻辑内联）
builder.add_node("white_bg_gen", white_bg_gen_node)
builder.add_node("multi_angle_gen", multi_angle_gen_node)
builder.add_node("variant_primary_loop", variant_primary_loop_node, metadata={"type": "looparray"})  # ✅ 多SKU路径：循环生成所有变体主图
builder.add_node("main_image_gen", main_image_gen_node)  # ✅ 单SKU路径：生成单张主图
builder.add_node("detail_gen", detail_gen_node)
builder.add_node("social_proof_gen", social_proof_gen_node)
builder.add_node("scene_1_gen", scene_1_gen_node)
builder.add_node("scene_2_gen", scene_2_gen_node)
builder.add_node("scene_3_gen", scene_3_gen_node)
builder.add_node("comparison_gen", comparison_gen_node)

# Phase 5: 数据准备 + Ozon上传 + 错误处理
builder.add_node("prepare_ozon_upload", prepare_ozon_upload_node, metadata={"type": "agent", "llm_cfg": "config/attributes_llm_cfg.json"})  # 数据准备节点：整理图片顺序、组装payload、LLM俄语翻译
builder.add_node("ozon_validate", ozon_validate_node)  # 预检测节点：检测Ozon payload是否符合规范
builder.add_node("check_quota", check_quota_node)  # 配额检查节点：上传前检查店铺配额，配额不足阻断上传
builder.add_node("ozon_upload", ozon_upload_node)
builder.add_node("ozon_status", ozon_status_node)  # 状态轮询节点：上传后轮询Ozon商品状态


# ==================== 设置入口点 ====================
builder.set_entry_point("auth")


# ==================== 添加边（数据流转） ====================

# ✅ v0.11: auth 失败时阻断管线，避免浪费 GPU/LLM 配额
def route_after_auth(state):
    """Token 验证失败 → END；否则 → check_quota"""
    error_code = getattr(state, 'error_code', '') or ''
    if error_code and error_code != 'AUTH_SUCCESS':
        logger.warning(f"⛔ Auth 失败({error_code})，阻断管线")
        return "END"
    return "check_quota"

builder.add_conditional_edges(
    source="auth",
    path=route_after_auth,
    path_map={"check_quota": "check_quota", "END": END}
)

# 🆕 路由：跟卖 vs 1688 完整管线（在配额检查通过后）
def route_by_sell_type(state):
    """根据 envelope.extensions.follow_sell 决定管线"""
    extensions = state.envelope.get("extensions", {}) if state.envelope else {}
    if extensions.get("follow_sell"):
        logger.info("🔄 路由 → 跟卖管线")
        return "follow_sell"
    logger.info("📦 路由 → 1688 完整管线")
    return "full"


def route_after_early_quota(state):
    """配额检查后路由：通过→继续管线，阻断→直接结束"""
    error_msg = getattr(state, 'error_message', '') or ''
    if '[QUOTA_BLOCKED]' in error_msg:
        logger.warning("⛔ 店铺配额已满，阻断管线（不浪费后续 GPU/LLM 额度）")
        return "blocked"
    return route_by_sell_type(state)


builder.add_conditional_edges(
    source="check_quota",
    path=route_after_early_quota,
    path_map={
        "follow_sell": "follow_sell_import",
        "full": "ingest",
        "blocked": END,
    }
)

# ✅ v4: 跟卖导入 → pricing → assemble（统一定价管线）
# 跟卖和 1688 两条路径在 pricing 节点汇合，使用同一套定价公式
def route_after_follow_sell_import(state):
    """跟卖导入后走统一定价管线：pricing → assemble → 生图 → 上传"""
    error_msg = getattr(state, 'error_message', '') or ''
    # 致命错误：ozon_product_id 为空或类目解析全部失败
    if 'ozon_product_id 为空' in error_msg:
        logger.error("⛔ 跟卖阻断: ozon_product_id 为空")
        return "END"
    if '类目解析失败' in error_msg:
        logger.error("⛔ 跟卖阻断: 类目解析全部失败，走 retry loop")
        return "retry"
    logger.info("跟卖导入完成(product_id=%s)，走统一定价管线", getattr(state, 'product_id', '?'))
    return "pricing"

builder.add_conditional_edges(
    "follow_sell_import",
    route_after_follow_sell_import,
    {"pricing": "pricing", "retry": "validation_retry_wrapper", "END": END}
)
# 1688 管线：ingest → 定价
builder.add_edge("ingest", "pricing")

# 定价 → 商品组装（串行：组装需要定价信息）
# 跟卖产品：_assemble_follow_sell 轻量模式，复用竞品属性+类目
# 1688 产品：_build_items_deterministically 完整模式
# ⚠️ v0.14 P1-4: 定价失败（[PRICING_FAILED]）→ 阻断管线，不兜底 1000 上架
def route_after_pricing(state):
    """定价后路由：定价失败 → 阻断，正常 → assemble"""
    error_msg = getattr(state, 'error_message', '') or ''
    if '[PRICING_FAILED]' in error_msg:
        logger.error("⛔ 定价失败，阻断管线（不兜底价格上架）: %s", error_msg[:120])
        return "END"
    return "assemble"

builder.add_conditional_edges(
    "pricing",
    route_after_pricing,
    {"assemble": "assemble_ozon_product", "END": END}
)

# Phase 3.5: 场景生成（使用LLM生成3个场景描述）
# ✅ v5: 类目匹配低置信度时阻断，不上架错误类目
def route_after_assemble(state):
    """
    title: 类目匹配质量检查
    desc: 类目匹配置信度过低或无有效候选时，阻止继续上架
    """
    error_msg = getattr(state, 'error_message', '') or ''
    if error_msg and ("类目匹配失败" in str(error_msg) or "无有效候选" in str(error_msg)):
        logger.warning(f"🛑 类目匹配阻断: {error_msg}")
        return "失败"
    match_conf = getattr(state, 'match_confidence', 1.0) or 1.0
    if match_conf < 0.3:
        logger.warning(f"🛑 类目匹配置信度过低({match_conf})，阻断上架")
        return "失败"
    return "成功"

builder.add_conditional_edges(
    source="assemble_ozon_product",
    path=route_after_assemble,
    path_map={
        "成功": "scene_generation_llm",
        "失败": END,
    }
)
builder.add_edge("scene_generation_llm", "white_bg_gen")  # scene_generation_llm → Phase1开始
builder.add_edge("scene_generation_llm", "multi_angle_gen")  # scene_generation_llm → Phase1开始

# Phase 4: 图片生成 - 两阶段并行流程 + 多SKU条件分支
# Phase1（2节点并行）：使用输入的原始产品图片作为参考，生成白底图和多角度图
# （已移除）builder.add_edge("attributes_learning", "white_bg_gen")
# （已移除）builder.add_edge("attributes_learning", "multi_angle_gen")

	# Phase2（6节点并行）：生成营销图（不包括主图，主图由variant_check分支处理）
# ✅ v0.11: 节点内部检查 variants 数量 — 单SKU时 variant_primary_loop 跳过, 多SKU时 main_image_gen 跳过
# Phase2节点等待Phase1完成后再开始（white_bg_gen和multi_angle_gen）
# ✅ v0.11: multi_info_gen 已移除（Ozon 禁止附加图含文字/广告，输出从未被 IMG_ORDER 使用）
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

# Phase2汇聚：主图 + 其他7个节点 → prepare_ozon_upload
builder.add_edge([
    "variant_primary_loop", "main_image_gen",
    "detail_gen", "social_proof_gen",
    "scene_1_gen", "scene_2_gen", "scene_3_gen", "comparison_gen"
], "prepare_ozon_upload")

# ==================== Phase 5: 数据准备 → 预检测 → 上传 → 状态轮询 → 错误处理 ====================
# 预检测节点：上传前检测Ozon payload是否符合规范
builder.add_edge("prepare_ozon_upload", "ozon_validate")

# ==================== 新增节点：validation_retry_wrapper + learning_record ====================
builder.add_node("validation_retry_wrapper", validation_retry_wrapper_node, metadata={"type": "loopcond"})  # ← 调用validation_retry_loop子图
builder.add_node("learning_record", learning_record_node)  # ← 上传成功后记录学习数据

# ✅ P0 修复：配额已在 auth 后检查，ozon_validate 直接到 ozon_upload
# 定义条件判断函数：根据ozon_validate_node的验证结果决定后续流程
def should_upload_after_validate(state):
    """
    title: 是否继续到上传
    desc: 根据ozon_validate_node的验证结果决定是否继续上传，或进入修复循环
    """
    is_valid = state.is_valid if hasattr(state, 'is_valid') else True
    validation_errors = state.validation_errors if hasattr(state, 'validation_errors') else []

    if not is_valid or (isinstance(validation_errors, list) and len(validation_errors) > 0):
        logger.warning(f"ozon_validate验证失败: is_valid={is_valid}, errors={len(validation_errors) if isinstance(validation_errors, list) else 0}个")
        return "失败"

    logger.info("ozon_validate验证成功，进入上传")
    return "成功"

# ✅ 验证成功 → 直接上传（配额已在 auth 后检查）
builder.add_conditional_edges(
    source="ozon_validate",
    path=should_upload_after_validate,
    path_map={
        "成功": "ozon_upload",
        "失败": "validation_retry_wrapper",
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
    # ✅ v4 (B12): 优先读 moderation_status，fallback 到 status (向后兼容)
    ozon_status_result = getattr(state, 'moderation_status', '') or ''
    if not ozon_status_result:
        ozon_status_result = state.status if hasattr(state, 'status') else ""
    
    # ✅ 检查errors数组（Ozon API返回的结构化错误）
    errors = state.errors if hasattr(state, 'errors') else []
    if not isinstance(errors, list):
        errors = []
    
    # ✅ v0.11: 三状态路由 — 审核中(pending) / 错误(error) / 批准(approved)
    product_id = state.product_id if hasattr(state, 'product_id') else None
    errors: list = getattr(state, 'errors', []) or []
    if not isinstance(errors, list):
        errors = []
    
    # 1. 批准：明确 approved → 成功
    if ozon_status_result == "approved" or "approved" in str(ozon_status_result):
        logger.info("✅ 审核通过(approved)，进入 learning_record")
        return "成功"
    
    # 2. 错误：有 errors 或明确 error/failed → 修复循环
    if ozon_status_result in ("error", "failed") or len(errors) > 0:
        logger.warning(f"❌ 审核失败({ozon_status_result})，{len(errors)}个错误，进入修复循环")
        return "失败"
    
    # 3. 审核中：pending 或无结果但有 product_id → 重试审核
    # ✅ v0.11: graph 路径函数只读 state，不写入（避免破坏 LangGraph reducer）
    if ozon_status_result == "pending" or (product_id and str(product_id) not in ("0", "None", "")):
        mod_retries = getattr(state, 'moderation_retry_count', 0)
        if mod_retries < 3:
            logger.info(f"⏳ 审核中(pending)，第{mod_retries+1}/3次重试 ozon_status")
            return "审核中"
        else:
            logger.warning("⚠️ 审核重试已达上限(3次)，视为成功（后台继续审核）")
            return "成功"
    
    # 4. 兜底：未知状态 → 修复循环
    logger.warning(f"未知状态: {ozon_status_result}，进入修复循环")
    return "失败"

# ✅ 修改条件分支：成功 → learning_record → END，失败 → validation_retry_wrapper（修复循环）
builder.add_conditional_edges(
    source="ozon_status",
    path=should_handle_error,
    path_map={
        "成功": "learning_record",
        "失败": "validation_retry_wrapper",
        "审核中": "ozon_status",  # ← 重新轮询审核
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