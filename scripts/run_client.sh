#!/bin/bash
# AI-SOC Client 启动脚本
# 启动边缘侧本地检测引擎和查询引擎
# 用法: bash scripts/run_client.sh

set -e
export PYTHONUNBUFFERED=1

CLIENT_ID="${1:?用法: bash scripts/run_client.sh <client-id>}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "================================================"
echo "  AI-SOC Client - 边缘侧本地安全检测"
echo "  启动时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Client ID: $CLIENT_ID"
echo "================================================"
echo ""

# ---- 1. 检查 NATS 连接 ----
check_nats() {
    echo "🔗 检查 NATS 连接..."
    if curl -s http://localhost:8222/healthz > /dev/null 2>&1; then
        echo "   ✅ NATS 服务可访问"
    else
        echo "   ❌ NATS (localhost:4222) 不可达"
        echo "   -> 请先在另一个终端运行: bash scripts/run_server.sh"
        echo "   -> 确保 Docker 基础设施已启动: docker compose up -d nats"
        exit 1
    fi
    echo ""
}

check_nats

# ---- 2. 检查配置文件 ----
echo "📂 检查客户端配置..."
CONFIG_FILE="MVP/client_config.yaml"
if [ -f "$CONFIG_FILE" ]; then
    echo "   ✅ $CONFIG_FILE 存在"
else
    echo "   ⚠️ $CONFIG_FILE 不存在"
    echo "   -> 运行: bash scripts/register_client.sh add $CLIENT_ID --node-id <节点ID>"
    exit 1
fi
echo ""

# ---- 3. 启动客户端 Python 应用 ----
echo "🖥️ 启动边缘侧核心组件..."
echo ""
echo "服务端应在另一个终端窗口中运行 (bash scripts/run_server.sh)"
echo "================================================"
echo ""

python3 MVP/client/client_app.py --client-id "$CLIENT_ID"
