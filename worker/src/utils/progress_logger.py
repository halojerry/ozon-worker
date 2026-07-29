"""进度日志助手 - 流程化可视化日志输出"""
import logging
import json
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ✅ 新增：节点顺序字典（根据workflow_progress.json定义）
NODE_ORDER = {
    "auth": 1,
    "ingest": 2,
    "category_lookup": 3,
    "pricing": 4,
    "attributes_fetch": 5,
    "attributes_llm": 6,
    "attributes_learning": 7,
    "scene_generation_llm": 8,
    "white_bg_gen": 9,
    "multi_angle_gen": 10,
    "variant_primary_loop": 11,
    "main_image_gen": 12,
    "multi_info_gen": 13,
    "detail_gen": 14,
    "social_proof_gen": 15,
    "scene_1_gen": 16,
    "scene_2_gen": 17,
    "scene_3_gen": 18,
    "comparison_gen": 19,
    "prepare_ozon_upload": 20,
    "ozon_validate": 21,
    "ozon_upload": 22,
    "ozon_status": 23,
    "error_handler": 24
}


class ProgressLogger:
    """流程化进度日志助手，提供可视化、可追溯的日志输出"""
    
    def __init__(self, run_id: str = "unknown", current_counter: int = 0, config_path: str = "config/workflow_progress.json"):
        """
        初始化进度日志助手
        
        Args:
            run_id: 工作流执行ID（用于追溯同一次执行的所有日志，默认"unknown")
            current_counter: 当前节点计数（从GlobalState传入，默认0）
            config_path: 进度配置文件路径
        """
        self.run_id = run_id
        self.current_node_count = current_counter  # ✅ 使用外部传入的计数器
        
        # ✅ 新增：返回更新后的计数器值（用于节点返回）
        self.next_counter = current_counter + 1
        
        # 加载进度配置
        workspace = os.getenv("APP_WORKSPACE_PATH") or os.getenv("APP_WORKSPACE_PATH") or os.getcwd()
        cfg_file = os.path.join(workspace, "assets/workflow_progress.json")
        with open(cfg_file, 'r', encoding='utf-8') as fd:
            config_data = json.load(fd)
        
        self.total_nodes = config_data.get('total_nodes', 24)
        self.stages = config_data.get('stages', {})
        self.node_titles = config_data.get('node_titles', {})
    
    def log_stage_start(self, stage_key: str):
        """
        记录阶段开始
        
        Args:
            stage_key: 阶段键名（如"phase1_data_preparation"）
        """
        stage = self.stages.get(stage_key)
        if stage:
            logger.info(f"🚀 {stage['name']} 开始 - {stage['description']}")
    
    def log_node_start(self, node_name: str, node_title: str = None) -> int:
        """
        记录节点开始（含进度百分比），并同步更新 _task_progress

        Args:
            node_name: 节点名称（如"white_bg_gen_node"或"white_bg_gen"，自动去_node后缀）
            node_title: 节点标题（如"白底图生成节点"，可选，默认从配置读取）

        Returns:
            int: 更新后的计数器值（用于节点返回）
        """
        # 自动去除 _node 后缀
        clean_name = node_name.removesuffix("_node")

        # 优先级：外部 current_counter > NODE_ORDER
        if self.current_node_count > 0:
            pass
        elif clean_name in NODE_ORDER:
            self.current_node_count = NODE_ORDER[clean_name]

        progress_percent = int((self.current_node_count / self.total_nodes) * 100)

        # 节点标题
        if node_title is None:
            node_title = self.node_titles.get(clean_name, clean_name)

        # 当前阶段
        current_stage = self._get_current_stage(clean_name)
        stage_name = current_stage.get('name', '')

        logger.info(f"📊 进度：{progress_percent}% | {stage_name} ▶️ {node_title}")

        # ✅ v0.10: 同步更新 _task_progress（内存 + PG），使 /progress 端点和 task_status 可读
        self._sync_progress(clean_name, progress_percent, node_title)

        return self.current_node_count

    def _sync_progress(self, stage: str, percent: int, message: str):
        """将进度同步到 main._task_progress 和 PG"""
        run_id = self.run_id
        # ✅ v0.10: 如果 run_id 是 "unknown"，尝试从全局上下文获取当前 task_id
        if (not run_id or run_id == "unknown"):
            try:
                from main import get_current_task_id
                ctx_id = get_current_task_id()
                if ctx_id:
                    run_id = ctx_id
            except Exception:
                pass
        if not run_id or run_id == "unknown":
            return
        try:
            from main import update_progress
            update_progress(run_id, stage, message)
        except Exception:
            pass  # 静默降级：不影响主流程
    
    def log_node_action(self, action: str):
        """
        记录节点具体动作（如"正在生成图片1/10"）
        
        Args:
            action: 动作描述（中文）
        """
        logger.info(f"⚙️ {action}")
    
    def log_node_success(self, result_summary: str):
        """
        记录节点成功完成
        
        Args:
            result_summary: 结果摘要（中文）
        """
        logger.info(f"✅ {result_summary}")
    
    def log_node_retry(self, retry_count: int, max_retries: int, reason: str):
        """
        记录节点重试
        
        Args:
            retry_count: 当前重试次数
            max_retries: 最大重试次数
            reason: 重试原因（中文）
        """
        logger.warning(f"🔄 重试 {retry_count}/{max_retries} - 原因：{reason}")
    
    def log_node_error(self, error_msg: str, suggestion: str = "请检查日志详情"):
        """
        记录节点错误
        
        Args:
            error_msg: 错误信息（中文）
            suggestion: 修复建议（中文）
        """
        logger.error(f"❌ 错误：{error_msg} | 建议：{suggestion}")
    
    def _get_current_stage(self, node_name: str) -> Dict:
        """
        根据节点名称获取当前阶段
        
        Args:
            node_name: 节点名称
            
        Returns:
            阶段配置字典
        """
        for stage_key, stage in self.stages.items():
            if node_name in stage.get('nodes', []):
                return stage
        return {}