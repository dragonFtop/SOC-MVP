#!/bin/bash
# AI-SOC Client 启动脚本
# 启动边缘侧数据采集和查询引擎
# 用法: bash scripts/run_client.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "================================================"
echo "  AI-SOC Client - 边缘侧安全数据采集"
echo "  启动时间: $(date '+%Y-%m-%d %H:%M:%S')"
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

# ---- 2. 检查数据源 ----
check_data() {
    echo "📂 检查数据源..."
    ALERTS_FILE="wazuh_logs/alerts/alerts.json"
    if [ -f "$ALERTS_FILE" ]; then
        if [ -r "$ALERTS_FILE" ]; then
            echo "   ✅ Wazuh 告警数据存在且可读"
        else
            echo "   ⚠️ 告警数据无读取权限，尝试修复..."
            sudo chmod o+r "$ALERTS_FILE" 2>/dev/null || true
            if [ -r "$ALERTS_FILE" ]; then
                echo "   ✅ 权限已修复"
            else
                echo "   ❌ 权限修复失败，请手动执行: sudo chmod o+r $SCRIPT_DIR/../wazuh_logs/alerts/"
            fi
        fi
    else
        echo "   ⚠️ Wazuh 告警数据不存在: $ALERTS_FILE"
        echo "   -> 信号生成可能失败"
    fi
    echo ""
}

check_data

# ---- 3. 启动客户端 Python 应用 ----
echo "🖥️ 启动边缘侧核心组件..."
echo ""
echo "即将启动:"
echo "  - DuckDB Sidecar       -> 监听 soc.query.requests"
echo "  - Signal Generator     -> 发布信号到 NATS"
echo ""
echo "服务端应在另一个终端窗口中运行 (bash scripts/run_server.sh)"
echo "================================================"
echo ""

python3 MVP/client/client_app.py
