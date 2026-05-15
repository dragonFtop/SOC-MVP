#!/bin/bash
# AI-SOC Server 启动脚本
# 启动基础设施 + 所有服务端守护进程
# 用法: bash scripts/run_server.sh

set -e

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

    INFRA_CONTAINERS=("opensearch" "nats" "wazuh-manager")
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
        docker compose up -d opensearch dashboards wazuh-manager logstash nats
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

# ---- 1.5. 修复 wazuh_logs 权限 (容器写文件后宿主机需可读) ----
echo ""
echo "🔧 修复 wazuh_logs 文件权限..."
sudo chmod -R a+rX "$SCRIPT_DIR/../wazuh_logs/" 2>/dev/null || true
sudo chmod g+s "$SCRIPT_DIR/../wazuh_logs/alerts/" 2>/dev/null || true
sudo setfacl -d -m o::r "$SCRIPT_DIR/../wazuh_logs/alerts/" 2>/dev/null || true
echo "   ✅ 权限已修复"

# ---- 2. 启动服务端 Python 应用 ----
echo "🖥️ 启动服务端核心组件..."
echo ""
echo "即将启动:"
echo "  - Query Gateway (FastAPI)  -> http://0.0.0.0:8000"
echo "  - Signal Listener (NATS)   -> 监听 soc.signals.*"
echo "  - Result Listener (NATS)   -> 监听 soc.query.results"
echo "  - Dashboard (Streamlit)    -> http://localhost:8501"
echo ""
echo "在另一个终端窗口运行: bash scripts/run_client.sh"
echo "================================================"
echo ""

python3 MVP/server/server_app.py
