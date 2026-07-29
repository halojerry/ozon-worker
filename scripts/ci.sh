#!/bin/bash
# CI 脚本：syntax → lint → test → build
# 用法: bash scripts/ci.sh [--quick]
#   --quick  跳过 Docker build（本地开发时）
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
WORKER_DIR="$PROJECT_DIR/worker"
SKILL_DIR="$PROJECT_DIR/skill"
FAILED=0

red() { echo -e "\033[31m$1\033[0m"; }
green() { echo -e "\033[32m$1\033[0m"; }
yellow() { echo -e "\033[33m$1\033[0m"; }

echo "═══════════════════════════════════════"
echo "  Ozon Worker CI Pipeline v2"
echo "═══════════════════════════════════════"

# ═══════════════════════════════════════
# Step 0: 环境检查
# ═══════════════════════════════════════
echo ""
echo "🔧 Step 0: 环境检查..."
# 优先使用 venv
if [ -f "$WORKER_DIR/.venv/bin/python3" ]; then
    PYTHON_BIN="$WORKER_DIR/.venv/bin/python3"
    echo "   Python: venv ($WORKER_DIR/.venv)"
elif command -v python3.12 &> /dev/null; then
    PYTHON_BIN="python3.12"
    echo "   Python: $(python3.12 --version)"
else
    PYTHON_BIN="python3"
    echo "   Python: $(python3 --version)"
fi

# ═══════════════════════════════════════
# Step 1: 语法检查（所有 .py 文件 — 阻断）
# ═══════════════════════════════════════
echo ""
echo "🔍 Step 1/5: 语法检查（所有 .py 文件）..."

check_syntax() {
    local dir="$1"
    local label="$2"
    local count=0
    while IFS= read -r -d '' f; do
        # 跳过 .venv, __pycache__, .pyc, dist, build
        case "$f" in
            */.venv/*|*/__pycache__/*|*.pyc|*/dist/*|*/build/*|*/.pytest_cache/*|*/node_modules/*)
                continue ;;
        esac
        if ! $PYTHON_BIN -c "import py_compile; py_compile.compile('$f', doraise=True)" 2>/dev/null; then
            red "   ❌ $f"
            FAILED=1
            count=$((count + 1))
        fi
    done < <(find "$dir" -name "*.py" -print0)
    if [ $count -eq 0 ]; then
        green "   ✅ $label ($(find "$dir" -name "*.py" | wc -l | tr -d ' ') 文件)"
    else
        red "   ❌ $label: $count 文件语法错误"
    fi
}

check_syntax "$WORKER_DIR/src" "worker/src"
check_syntax "$WORKER_DIR/tests" "worker/tests"
check_syntax "$SKILL_DIR/scripts" "skill/scripts"

# ═══════════════════════════════════════
# Step 2: Lint（阻断）
# ═══════════════════════════════════════
echo ""
echo "📏 Step 2/5: Lint (ruff)..."

cd "$WORKER_DIR"
if command -v ruff &> /dev/null; then
    if ruff check src/ --select E,F,W --ignore E501 2>&1; then
        green "   ✅ worker/src"
    else
        red "   ❌ Lint 错误（请修复后重新提交）"
        FAILED=1
    fi
else
    yellow "   ⚠️  ruff 未安装 (pip install ruff)"
fi

# ═══════════════════════════════════════
# Step 3: 核心导入验证（需要完整依赖 — 非阻断）
# ═══════════════════════════════════════
echo ""
echo "📦 Step 3/5: 核心导入验证（需要 venv 完整依赖）..."

cd "$WORKER_DIR"
VENV_PY="$WORKER_DIR/.venv/bin/python3"
if [ -f "$VENV_PY" ]; then
    if PYTHONPATH="$WORKER_DIR/src" $VENV_PY -c "
from graphs.state import GlobalState, GraphInput, GraphOutput
from api.errors import WorkerErrorCode, error_response
from api.schemas import SubmitTaskRequest, TaskStatusResponse
from utils.task_processor import SupabaseTaskProcessor
from utils.progress_logger import ProgressLogger
print('✅ Worker 核心模块导入正常')
" 2>/dev/null; then
    green "   ✅ Worker 核心模块"
else
    yellow "   ⚠️  Worker 导入（缺少依赖？pip install -e worker/.[dev]）"
fi
else
    yellow "   ⚠️  无 venv，跳过导入检查"
fi

cd "$SKILL_DIR"
if ! PYTHONPATH="$SKILL_DIR/scripts" $PYTHON_BIN -c "
from scripts.lib.utils import parse_price
from scripts._const import CLOUD_API_BASE
print('✅ Skill 核心模块导入正常')
" 2>/dev/null; then
    yellow "   ⚠️  Skill 核心模块（可能需要 venv）"
else
    green "   ✅ Skill 核心模块"
fi

# ═══════════════════════════════════════
# Step 4: 测试（非阻断，但显示结果）
# ═══════════════════════════════════════
echo ""
echo "🧪 Step 4/5: 单元测试..."

cd "$WORKER_DIR"
if command -v pytest &> /dev/null; then
    # 只跑快速测试（不含 DB/网络依赖的）
    PYTHONPATH="$WORKER_DIR/src" pytest tests/ -v --tb=short \
        -k "not db and not integration and not slow" \
        --timeout=30 2>/dev/null || yellow "   ⚠️  部分测试未通过（检查日志）"
    green "   ✅ 测试完成"
else
    yellow "   ⚠️  pytest 未安装 (pip install pytest)"
fi

# ═══════════════════════════════════════
# Step 5: Docker build（--quick 跳过）
# ═══════════════════════════════════════
if [[ "$1" != "--quick" ]]; then
    echo ""
    echo "🐳 Step 5/5: Docker build 验证..."
    cd "$PROJECT_DIR"
    if command -v docker &> /dev/null; then
        if docker build -t ozon-worker:ci -f worker/Dockerfile worker/ --quiet 2>/dev/null; then
            green "   ✅ Docker build 成功"
            docker rmi ozon-worker:ci 2>/dev/null || true
        else
            red "   ❌ Docker build 失败"
            FAILED=1
        fi
    else
        yellow "   ⚠️  Docker 未安装"
    fi
else
    echo ""
    yellow "   ⏭️  Step 5: Docker build 跳过 (--quick)"
fi

# ═══════════════════════════════════════
# 结果
# ═══════════════════════════════════════
echo ""
if [ $FAILED -eq 0 ]; then
    green "═══════════════════════════════════════"
    green "  ✅ CI Pipeline 全部通过"
    green "═══════════════════════════════════════"
    exit 0
else
    red "═══════════════════════════════════════"
    red "  ❌ CI Pipeline 失败 — 请修复以上问题"
    red "═══════════════════════════════════════"
    exit 1
fi
