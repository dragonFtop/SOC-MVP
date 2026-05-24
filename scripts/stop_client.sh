#!/bin/bash
# AI-SOC Client 停止脚本
# 停止所有客户端边缘进程

echo "🛑 AI-SOC Client 停止中..."

STOPPED=0

# 按进程树清理所有 client 相关进程
for pattern in "client_app.py" "duckdb_sidecar.py" "signal_generator.py" \
               "detection_engine.py"; do
    if pkill -f "$pattern" 2>/dev/null; then
        echo "   ✅ 已停止: $pattern"
        STOPPED=1
    fi
done

if [ "$STOPPED" -eq 0 ]; then
    echo "   ⏭️ 没有运行中的客户端进程"
fi

echo ""
echo "✅ 客户端已关闭"
