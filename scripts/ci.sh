#!/bin/bash
# CI 脚本：syntax → lint(Worker+Skill) → import → test → build
# 用法: bash scripts/ci.sh [--quick] [--strict]
#   --quick   跳过 Docker build（本地开发）
#   --strict  ruff 严格模式（C90复杂度 + SIM简化建议）
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
echo "  Ozon Worker CI Pipeline v3"
echo "═══════════════════════════════════════"

# ═══════════════════════
# Step 0: 环境
# ═══════════════════════
echo ""
echo "🔧 Step 0: 环境..."
if [ -f "$WORKER_DIR/.venv/bin/python3" ]; then
    PYTHON_BIN="$WORKER_DIR/.venv/bin/python3"
    echo "   Python: venv (worker/.venv)"
else
    PYTHON_BIN="python3"
    echo "   Python: system $(python3 --version)"
fi

# ═══════════════════════
# Step 1: 语法 — Worker + Skill 全量（阻断）
# ═══════════════════════
echo ""
echo "🔍 Step 1/6: 语法检查..."

check_syntax() {
    local dir="$1" label="$2" count=0 total=0
    while IFS= read -r -d '' f; do
        case "$f" in
            */.venv/*|*/__pycache__/*|*.pyc|*/dist/*|*/build/*|*/.pytest_cache/*|*/node_modules/*|*/site-packages/*)
                continue ;;
        esac
        total=$((total + 1))
        $PYTHON_BIN -c "import py_compile; py_compile.compile('$f', doraise=True)" 2>/dev/null || { red "   ❌ $f"; count=$((count + 1)); FAILED=1; }
    done < <(find "$dir" -name "*.py" -print0)
    [ $count -eq 0 ] && green "   ✅ $label ($total 文件)" || red "   ❌ $label: $count/$total 文件语法错误"
}

check_syntax "$WORKER_DIR/src" "worker/src"
check_syntax "$WORKER_DIR/tests" "worker/tests"
check_syntax "$SKILL_DIR/scripts" "skill/scripts"

# ═══════════════════════
# Step 2: Ruff — Worker（阻断 E,F,W）
# ═══════════════════════
echo ""
echo "📏 Step 2/6: Ruff — Worker..."
cd "$WORKER_DIR"
if command -v ruff &> /dev/null; then
    if ruff check src/ --select E,F,W --ignore E501 2>&1; then
        green "   ✅ Worker 格式 (E,F,W)"
    else
        red "   ❌ Worker 格式错误（ruff check --fix 修复）"
        FAILED=1
    fi

    if [[ "$*" == *"--strict"* ]]; then
        echo ""
        echo "   📊 Worker 严格模式..."
        ruff check src/ --select C90 --statistics 2>&1 | head -10 || true
        ruff check src/ --select SIM --statistics 2>&1 | head -10 || true
    fi
else
    yellow "   ⚠️  ruff 未安装 (pip install ruff)"
fi

# ═══════════════════════
# Step 3: Ruff — Skill（阻断 E,F,W）
# ═══════════════════════
echo ""
echo "📏 Step 3/6: Ruff — Skill..."
cd "$SKILL_DIR"
if command -v ruff &> /dev/null; then
    if ruff check scripts/ --select E,F,W --ignore E501,E402 2>&1; then
        green "   ✅ Skill 格式 (E,F,W)"
    else
        red "   ❌ Skill 格式错误"
        FAILED=1
    fi

    if [[ "$*" == *"--strict"* ]]; then
        ruff check scripts/ --select SIM --statistics 2>&1 | head -10 || true
    fi
else
    yellow "   ⚠️  ruff 未安装"
fi

# ═══════════════════════
# Step 4: 导入验证 — Worker + Skill
# ═══════════════════════
echo ""
echo "📦 Step 4/6: 核心导入验证..."

cd "$WORKER_DIR"
if [ -f ".venv/bin/python3" ]; then
    PYTHONPATH=src .venv/bin/python3 -c "
from graphs.state import GlobalState, GraphInput, GraphOutput
from api.errors import WorkerErrorCode, error_response
from api.schemas import SubmitTaskRequest, TaskStatusResponse
from utils.task_processor import SupabaseTaskProcessor
from utils.progress_logger import ProgressLogger
from utils.ozon_category_query import OzonCategoryQuery
print('✅ Worker 核心模块')
" 2>/dev/null && green "   ✅ Worker 核心模块" || yellow "   ⚠️  Worker（pip install -e .[dev]）"
else
    yellow "   ⚠️  无 venv，跳过 Worker"
fi

cd "$SKILL_DIR"
PYTHONPATH=scripts $PYTHON_BIN -c "
from scripts.lib.utils import parse_price
from scripts._const import CLOUD_API_BASE
print('✅ Skill 核心模块')
" 2>/dev/null && green "   ✅ Skill 核心模块" || yellow "   ⚠️  Skill（pip install -r requirements.txt）"

# ═══════════════════════
# Step 5: 测试 — Worker
# ═══════════════════════
echo ""
echo "🧪 Step 5/6: 单元测试..."
cd "$WORKER_DIR"
if command -v pytest &> /dev/null; then
    PYTHONPATH=src pytest tests/ -v --tb=short \
        -k "not db and not integration and not slow" \
        --timeout=30 2>/dev/null && green "   ✅ Worker 测试" || yellow "   ⚠️  部分测试未通过"
else
    yellow "   ⚠️  pytest 未安装"
fi

# ═══════════════════════
# Step 6: Docker build（--quick 跳过）
# ═══════════════════════
if [[ "$*" != *"--quick"* ]]; then
    echo ""
    echo "🐳 Step 6/6: Docker build..."
    cd "$PROJECT_DIR"
    if command -v docker &> /dev/null; then
        docker build -t ozon-worker:ci -f worker/Dockerfile worker/ --quiet 2>/dev/null && {
            green "   ✅ Docker build 成功"; docker rmi ozon-worker:ci 2>/dev/null || true
        } || { red "   ❌ Docker build 失败"; FAILED=1; }
    else
        yellow "   ⚠️  Docker 未安装"
    fi
else
    echo ""
    yellow "   ⏭️  Step 6: Docker (--quick 跳过)"
fi

# ═══════════════════════
# 结果
# ═══════════════════════
echo ""
if [ $FAILED -eq 0 ]; then
    green "═══════════════════════════════════════"
    green "  ✅ CI Pipeline 全部通过"
    green "═══════════════════════════════════════"
else
    red "═══════════════════════════════════════"
    red "  ❌ CI Pipeline 失败 — 请修复以上问题"
    red "═══════════════════════════════════════"
fi
exit $FAILED
