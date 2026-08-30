"""进度日志助手 - 流程化可视化日志输出"""
import logging
import json
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ⚠️ v0.14 C4: 进度配置模块级缓存（惰性加载一次，全进程复用；config_path 非空优先）
_cfg_cache: Dict[str, dict] = {}


def _load_progress_config(config_path: str = "") -> dict:
    """加载 workflow_progress.json（模块级缓存，避免每节点重复读磁盘）。

    v0.62.1 P1-4: 修复路径错位 — 默认 config_path="config/workflow_progress.json"
    相对路径在容器 cwd=/app 下解析到 /app/config/（不存在），文件实际在
    assets/ 下 → 每节点实例化一次 warning（生产 6h 256 次）。
    现在候选链：显式路径原样 → workspace 相对 → workspace/assets/；任一命中即
    缓存返回；全部失败缓存默认值（第二次实例化不再 warning，消灭刷屏）。
    """
    cache_key = config_path or "assets/workflow_progress.json"
    if cache_key in _cfg_cache:
        return _cfg_cache[cache_key]
    workspace = os.getenv("APP_WORKSPACE_PATH") or os.getcwd()
    candidates: list[str] = []
    if config_path:
        candidates.append(config_path)                       # 显式相对路径原样（兼容旧调用方）
        if not os.path.isabs(config_path):
            candidates.append(os.path.join(workspace, config_path))  # workspace 相对
    candidates.append(os.path.join(workspace, "assets/workflow_progress.json"))
    for path in candidates:
        try:
            with open(path, 'r', encoding='utf-8') as fd:
                data = json.load(fd)
            _cfg_cache[cache_key] = data
            return data
        except FileNotFoundError:
            continue
        except Exception as e:
            logger.warning("加载进度配置失败(%s): %s", path, e)
            break
    # 全部失败：缓存默认值（防每节点重复刷屏）
    _cfg_cache[cache_key] = {"total_nodes": 24, "stages": {}, "node_titles": {}}
    return _cfg_cache[cache_key]


# ✅ 新增：节点顺序字典（根据workflow_progress.json定义）
# ⚠️ v0.14 D4/C4: 同步到真实图节点集 — 删除已废弃节点(category_lookup/attributes_fetch/attributes_llm/
# attributes_learning/error_handler/multi_info_gen)，补新节点(follow_sell_import/check_quota/assemble_ozon_product)
NODE_ORDER = {
    "auth": 1,
    "check_quota": 2,
    "ingest": 3,
    "follow_sell_import": 3,
    "pricing": 4,
    "assemble_ozon_product": 5,
    "scene_generation_llm": 8,
    "white_bg_gen": 9,
    "multi_angle_gen": 10,
    "variant_primary_loop": 11,
    "main_image_gen": 12,
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
    "validation_retry_wrapper": 24,
    "learning_record": 25,
}


class ProgressLogger:
    """流程化进度日志助手，提供可视化、可追溯的日志输出"""
    
    def __init__(self, run_id: str = "unknown", current_counter: int = 0, config_path: str = "assets/workflow_progress.json"):
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
        # ⚠️ v0.14 C4: 模块级缓存只读一次（旧代码每次实例化都 open+json.load，20+ 节点重复读磁盘）
        # config_path 参数生效：非空时优先使用（旧代码硬编码 assets/workflow_progress.json，参数被忽略）
        self._config = _load_progress_config(config_path)
        self.total_nodes = self._config.get('total_nodes', 24)
        self.stages = self._config.get('stages', {})
        self.node_titles = self._config.get('node_titles', {})
    
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
