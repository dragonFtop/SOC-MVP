#!/bin/bash
# AI-SOC Client 停止脚本
# 停止所有客户端边缘进程

echo "🛑 AI-SOC Client 停止中..."

STOPPED=0

# 停止 client_app.py (整合入口)
if pkill -f "client_app.py" 2>/dev/null; then
    echo "   ✅ Client App 已停止"
    STOPPED=1
fi

# 停止 DuckDB Sidecar
if pkill -f "duckdb_sidecar.py" 2>/dev/null; then
    echo "   ✅ DuckDB Sidecar 已停止"
    STOPPED=1
fi

# 停止 Signal Generator (独立进程)
if pkill -f "signal_generator.py" 2>/dev/null; then
    echo "   ✅ Signal Generator 已停止"
    STOPPED=1
fi

# 停止 sidecar 相关的 NATS 连接
if pkill -f "sidecar-" 2>/dev/null; then
    echo "   ✅ Sidecar NATS 连接已清理"
    STOPPED=1
fi

if [ "$STOPPED" -eq 0 ]; then
    echo "   ⏭️ 没有运行中的客户端进程"
fi

echo ""
echo "✅ 客户端已关闭"
