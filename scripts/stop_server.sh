#!/bin/bash
# AI-SOC Server 停止脚本
# 停止所有服务端守护进程

echo "🛑 AI-SOC Server 停止中..."

STOPPED=0

# 停止 server_app.py (整合入口)
if pkill -f "server_app.py" 2>/dev/null; then
    echo "   ✅ Server App 已停止"
    STOPPED=1
fi

# 停止 Signal Listener (独立进程)
if pkill -f "signal_listener.py" 2>/dev/null; then
    echo "   ✅ Signal Listener 已停止"
    STOPPED=1
fi

# 停止 Query Gateway (uvicorn)
if pkill -f "uvicorn.*query_gateway" 2>/dev/null; then
    echo "   ✅ Query Gateway 已停止"
    STOPPED=1
fi

# 停止 Dashboard (streamlit)
if pkill -f "streamlit run.*dashboard" 2>/dev/null; then
    echo "   ✅ Dashboard 已停止"
    STOPPED=1
fi

# 停止查询结果监听器
if pkill -f "query-result-listener" 2>/dev/null; then
    echo "   ✅ Query Result Listener 已停止"
    STOPPED=1
fi

if [ "$STOPPED" -eq 0 ]; then
    echo "   ⏭️ 没有运行中的服务端进程"
fi

echo ""
echo "✅ 服务端已关闭"
