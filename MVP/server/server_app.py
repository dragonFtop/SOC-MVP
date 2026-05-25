#!/usr/bin/env python3
"""
AI-SOC Server 统一入口
======================
整合所有服务端守护进程:
  - Signal Listener       (signal_listener.py)        - NATS 信令监听
  - Query Result Listener (query_result_listener.py)  - 查询结果接收与持久化
  - Query Gateway         (query_gateway.py)          - FastAPI 查询网关
  - Web Console           (web_console/)             - 统一运维面板

在独立终端窗口中运行，展示中心侧实时处理过程。
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import NATS_SERVERS


def server_log(msg: str):
    print(f"[Server] {msg}", flush=True)


async def run_server():
    """启动所有服务端组件 - 组装各个模块并以 asyncio 并发运行"""
    from config import validate_config

    server_log("=" * 50)
    server_log("  AI-SOC Server - 中心侧安全运营平台")
    server_log("=" * 50)

    validate_config()

    from server.signal_listener import SignalListener
    from server.query_result_listener import QueryResultListener
    from server.query_gateway import run_gateway
    server_log(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    server_log(f"NATS 服务器: {NATS_SERVERS}")
    server_log("")

    # 0. 启动 OpenSearch Dashboards (Docker)
    def _launch_dashboards():
        import subprocess
        try:
            subprocess.run(
                ["docker", "compose", "up", "-d", "dashboards"],
                cwd=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=30,
            )
            server_log("OpenSearch Dashboards 启动中 -> http://localhost:5601")
        except Exception as e:
            server_log(f"⚠️ Dashboards 启动失败: {e}")

    _launch_dashboards()

    # 1. Query Gateway (独立线程)
    gateway_thread = threading.Thread(target=run_gateway, daemon=True)
    gateway_thread.start()
    await asyncio.sleep(1)

    # 2. Web Console - 统一运维控制台 (端口 8500)
    #    集成: 首页/Server监控/Client面板/数据查看/Client注册/节点注册
    def _launch_web_console():
        import subprocess
        web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web_console")
        server_log("Web Console 启动中 -> http://localhost:8500")
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run",
             os.path.join(web_dir, "🏠_首页.py"),
             "--server.port", "8500",
             "--server.headless", "true",
             "--browser.gatherUsageStats", "false"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    web_thread = threading.Thread(target=_launch_web_console, daemon=True)
    web_thread.start()
    await asyncio.sleep(1)

    # 4. Signal Listener + Query Result Listener (asyncio 并发)
    signal_listener = SignalListener()
    result_listener = QueryResultListener()

    signal_task = asyncio.create_task(signal_listener.listen_forever())
    result_task = asyncio.create_task(result_listener.listen_forever())

    server_log("")
    server_log("所有服务已启动:")
    server_log("  - OpenSearch Dashboards: http://localhost:5601")
    server_log("  - Query Gateway:   http://localhost:8000")
    server_log("  - Web Console:     http://localhost:8500")
    server_log("  - Signal Listener: 监听 NATS 信号")
    server_log("  - Result Listener: 监听 NATS 查询结果")
    server_log("")
    server_log("等待客户端连接...")
    server_log("  在另一个终端窗口运行: bash run_client.sh")
    server_log("")
    server_log("按 Ctrl+C 停止所有服务")
    server_log("")

    # 等待任意任务完成（或用户中断）
    _, pending = await asyncio.wait(
        [signal_task, result_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # 取消剩余任务
    for task in pending:
        task.cancel()

    await signal_listener.close()
    await result_listener.shutdown()

    server_log("所有服务端组件已关闭")


def main():
    """主函数 - 处理信号并启动事件循环"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    main_task = loop.create_task(run_server())

    def _shutdown():
        server_log("收到中断信号，正在优雅关闭...")
        main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(main_task)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
