#!/bin/bash
# AI-SOC Client 启动脚本
# 启动边缘侧本地检测引擎和查询引擎
# 用法: bash scripts/run_client.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "================================================"
echo "  AI-SOC Client - 边缘侧本地安全检测"
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
    AUTH_LOG="/var/log/auth.log"
    if [ -f "$AUTH_LOG" ]; then
        if [ -r "$AUTH_LOG" ]; then
            echo "   ✅ auth.log 存在且可读"
            echo "   最近条目:"
            tail -3 "$AUTH_LOG" 2>/dev/null | sed 's/^/     /' || true
        else
            echo "   ⚠️ auth.log 无读取权限，尝试修复..."
            sudo chmod o+r "$AUTH_LOG" 2>/dev/null || true
            if [ -r "$AUTH_LOG" ]; then
                echo "   ✅ 权限已修复"
            else
                echo "   ❌ 权限修复失败，请手动执行: sudo chmod o+r $AUTH_LOG"
            fi
        fi
    else
        echo "   ⚠️ auth.log 不存在: $AUTH_LOG"
        echo "   -> 检测引擎将等待文件出现"
    fi
    echo ""
}

check_data

# ---- 3. 启动客户端 Python 应用 ----
echo "🖥️ 启动边缘侧核心组件..."
echo ""
echo "即将启动:"
echo "  - Detection Engine      -> 监控 $AUTH_LOG (YAML rules)"
echo "  - DuckDB Sidecar        -> 监听 soc.query.requests"
echo ""
echo "服务端应在另一个终端窗口中运行 (bash scripts/run_server.sh)"
echo "================================================"
echo ""

python3 MVP/client/client_app.py
