# 图片生成子图编排
import os
import json
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END

from graphs.state_image_gen import (
    ImageGenSubgraphState,
    ImageGenSubgraphInput,
    ImageGenSubgraphOutput
)

# 导入变体循环相关状态定义
from graphs.state import (
    VariantLoopState,
    VariantLoopInput,
    VariantLoopOutput
)

# 导入10个图片生成节点
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

# 注意：variant_primary_loop_node在主图中使用，不需要在此导入

def prepare_image_gen(state: ImageGenSubgraphState) -> ImageGenSubgraphState:
    """
    准备图片生成（从draft和token提取数据）
    """
    # 数据已经通过Input传递，直接返回
    return state

def merge_images(state: ImageGenSubgraphState) -> ImageGenSubgraphOutput:
    """
    汇聚所有图片生成结果
    """
    all_images: Dict[str, str] = {}
    
    # 添加Phase1图片（白底图 + 多角度图）
    if state.white_bg_image:
        all_images["white_bg"] = state.white_bg_image
    if state.multi_angle_image:
        all_images["multi_angle"] = state.multi_angle_image
    
    # 添加Phase2图片（8张营销图片）
    if state.main_image:
        all_images["main"] = state.main_image
    if state.multi_info_image:
        all_images["multi_info"] = state.multi_info_image
    if state.detail_image:
        all_images["detail"] = state.detail_image
    if state.social_proof_image:
        all_images["social_proof"] = state.social_proof_image
    if state.scene_1_image:
        all_images["scene_1"] = state.scene_1_image
    if state.scene_2_image:
        all_images["scene_2"] = state.scene_2_image
    if state.scene_3_image:
        all_images["scene_3"] = state.scene_3_image
    if state.comparison_image:
        all_images["comparison"] = state.comparison_image
    
    # 收集所有错误信息
    errors: list[str] = []
    if state.white_bg_image is None:
        errors.append("white_bg生成失败")
    if state.multi_angle_image is None:
        errors.append("multi_angle生成失败")
    if state.main_image is None:
        errors.append("main_image生成失败")
    if state.multi_info_image is None:
        errors.append("multi_info生成失败")
    if state.detail_image is None:
        errors.append("detail生成失败")
    if state.social_proof_image is None:
        errors.append("social_proof生成失败")
    if state.scene_1_image is None:
        errors.append("scene_1生成失败")
    if state.scene_2_image is None:
        errors.append("scene_2生成失败")
    if state.scene_3_image is None:
        errors.append("scene_3生成失败")
    if state.comparison_image is None:
        errors.append("comparison生成失败")
    
    error_message = "; ".join(errors) if errors else ""
    failed_stage = "image_gen_merge" if errors else ""
    
    return ImageGenSubgraphOutput(
        all_images=all_images,
        error_message=error_message,
        failed_stage=failed_stage
    )

# 创建图片生成子图
def create_image_gen_subgraph():
    """
    创建图片生成子图（10个节点并行执行）
    """
    builder = StateGraph(
        ImageGenSubgraphState,
        input_schema=ImageGenSubgraphInput,
        output_schema=ImageGenSubgraphOutput
    )
    
    # 添加准备节点
    builder.add_node("prepare_image_gen", prepare_image_gen)
    
    # Phase1节点（2并行）
    builder.add_node("white_bg_gen", white_bg_gen_node)
    builder.add_node("multi_angle_gen", multi_angle_gen_node)
    
    # Phase2节点（8并行）
    builder.add_node("main_image_gen", main_image_gen_node)
    builder.add_node("multi_info_gen", multi_info_gen_node)
    builder.add_node("detail_gen", detail_gen_node)
    builder.add_node("social_proof_gen", social_proof_gen_node)
    builder.add_node("scene_1_gen", scene_1_gen_node)
    builder.add_node("scene_2_gen", scene_2_gen_node)
    builder.add_node("scene_3_gen", scene_3_gen_node)
    builder.add_node("comparison_gen", comparison_gen_node)
    
    # 添加汇聚节点
    builder.add_node("merge_images", merge_images)
    
    # 设置入口点
    builder.set_entry_point("prepare_image_gen")
    
    # Phase1并行编排：prepare → white_bg_gen + multi_angle_gen
    builder.add_edge("prepare_image_gen", "white_bg_gen")
    builder.add_edge("prepare_image_gen", "multi_angle_gen")
    
    # Phase2并行编排：Phase1完成后 → 8个节点并行
    # 注意：需要等Phase1的两个节点都完成后再启动Phase2
    builder.add_edge(["white_bg_gen", "multi_angle_gen"], "main_image_gen")
    builder.add_edge(["white_bg_gen", "multi_angle_gen"], "multi_info_gen")
    builder.add_edge(["white_bg_gen", "multi_angle_gen"], "detail_gen")
    builder.add_edge(["white_bg_gen", "multi_angle_gen"], "social_proof_gen")
    builder.add_edge(["white_bg_gen", "multi_angle_gen"], "scene_1_gen")
    builder.add_edge(["white_bg_gen", "multi_angle_gen"], "scene_2_gen")
    builder.add_edge(["white_bg_gen", "multi_angle_gen"], "scene_3_gen")
    builder.add_edge(["white_bg_gen", "multi_angle_gen"], "comparison_gen")
    
    # 汇聚：所有Phase2节点完成 → merge_images
    builder.add_edge([
        "main_image_gen",
        "multi_info_gen",
        "detail_gen",
        "social_proof_gen",
        "scene_1_gen",
        "scene_2_gen",
        "scene_3_gen",
        "comparison_gen"
    ], "merge_images")
    
    # 结束
    builder.add_edge("merge_images", END)
    
    return builder.compile()

# 编译子图
image_gen_subgraph = create_image_gen_subgraph()


# 注意：VariantLoopState/VariantLoopInput/VariantLoopOutput 已从 graphs.state 导入
# 不再在此文件中重复定义（避免遮蔽导入的完整版本）
# variant_primary_loop_node在nodes/variant_primary_loop_node.py中直接循环生成所有主图
# 已删除废弃的variant_primary_gen_loop_node和should_continue_loop函数