#!/bin/bash
# AI-SOC Server 启动脚本
# 启动基础设施 + 所有服务端守护进程
# 用法: bash scripts/run_server.sh

set -e
export PYTHONUNBUFFERED=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "================================================"
echo "  AI-SOC Server - 中心侧安全运营平台"
echo "  启动时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"
echo ""

# ---- 1. 检查并启动基础设施 ----
start_infra() {
    echo "🐳 检查 Docker 基础设施..."

    INFRA_CONTAINERS=("opensearch" "nats")
    ALL_RUNNING=true

    for c in "${INFRA_CONTAINERS[@]}"; do
        if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${c}$"; then
            echo "   ✅ ${c} 已运行"
        else
            echo "   ⚠️ ${c} 未运行"
            ALL_RUNNING=false
        fi
    done

    if [ "$ALL_RUNNING" = false ]; then
        echo ""
        echo "📦 启动基础设施 (Docker Compose)..."
        docker compose up -d opensearch nats
        echo ""
        echo "⏳ 等待基础设施就绪 (15秒)..."
        sleep 15

        # 再次检查
        for c in "${INFRA_CONTAINERS[@]}"; do
            if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${c}$"; then
                echo "   ✅ ${c} 启动成功"
            else
                echo "   ❌ ${c} 启动失败！请检查 docker compose logs"
            fi
        done
    fi

    echo ""
}

start_infra

# ---- 2. 启动服务端 Python 应用 ----
echo "🖥️ 启动服务端核心组件..."
echo ""
echo "即将启动:"
echo "  - Query Gateway (FastAPI)  -> http://0.0.0.0:8000"
echo "  - Web Console (Streamlit)  -> http://localhost:8500"
echo "  - Signal Listener (NATS)   -> 监听 soc.signals.*"
echo "  - Result Listener (NATS)   -> 监听 soc.query.results"
echo ""
echo "在另一个终端窗口运行: bash scripts/run_client.sh <client-id>"
echo "================================================"
echo ""

python3 MVP/server/server_app.py
