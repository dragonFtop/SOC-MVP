#!/bin/bash
# AI-SOC Server 停止脚本
# 停止所有服务端守护进程 + 释放端口

echo "🛑 AI-SOC Server 停止中..."

STOPPED=0

# 停止 server_app.py 及其子进程 (用 pkill 按进程树清理)
for pattern in "server_app.py" "signal_listener.py" "query_result_listener.py" \
               "uvicorn.*query_gateway" "streamlit run.*🏠_首页" "streamlit run.*dashboard"; do
    if pkill -f "$pattern" 2>/dev/null; then
        echo "   ✅ 已停止: $pattern"
        STOPPED=1
    fi
done

# 释放端口
for port in 8000 8500; do
    fuser -k "$port/tcp" 2>/dev/null && echo "   ✅ 端口 $port 已释放" || true
done

if [ "$STOPPED" -eq 0 ]; then
    echo "   ⏭️ 没有运行中的服务端进程"
fi

echo ""
echo "✅ 服务端已关闭"
