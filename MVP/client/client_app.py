#!/usr/bin/env python3
"""
AI-SOC Client 统一入口
======================
整合所有客户端边缘组件:
  - DuckDB Sidecar   (duckdb_sidecar.py)  - 监听 NATS 查询请求, 本地执行 DuckDB 查询
  - Signal Watcher   (signal_watcher.py)  - 持续监控 Wazuh alerts.json, 实时生成微信号

在独立终端窗口中运行，展示边缘侧实时处理过程。

实时数据流:
  Wazuh Agent → Wazuh Manager → alerts.json (NDJSON, 逐行追加)
    → SignalWatcher 检测新行 → 解析 → 生成信号 → NATS
    → Server Signal Listener 收到 → 触发查询 → NATS
    → DuckDB Sidecar 执行查询 → 返回证据 → NATS
    → Server 接收、持久化、展示
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    NATS_SERVERS,
    NATS_QUERY_REQUESTS,
    DEFAULT_NODE_ID,
    ALERTS_JSON_PATH,
)

WATCH_INTERVAL = 2


# ====================== 日志工具 ======================

def client_log(msg: str):
    print(f"[Client] {msg}", flush=True)


# ====================== 主入口 ======================

async def run_client():
    """启动所有客户端组件 - 组装各模块并以 asyncio 并发运行"""
    from client.duckdb_sidecar import DuckDBQueryEngine
    from client.signal_watcher import SignalWatcher

    client_log("=" * 56)
    client_log("  AI-SOC Client - 边缘侧实时安全数据采集")
    client_log("=" * 56)
    client_log(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    client_log(f"边缘节点: {DEFAULT_NODE_ID}")
    client_log(f"NATS 服务器: {NATS_SERVERS}")
    client_log(f"告警文件: {ALERTS_JSON_PATH}")
    client_log(f"监控间隔: {WATCH_INTERVAL}s")
    client_log("")

    # 检查告警文件
    if not os.path.exists(ALERTS_JSON_PATH):
        client_log(f"告警文件不存在: {ALERTS_JSON_PATH}")
        client_log("等待 Wazuh Manager 写入告警...")
        client_log("")

    # Sidecar + Watcher 并发运行
    sidecar = DuckDBQueryEngine(node_id=DEFAULT_NODE_ID)
    watcher = SignalWatcher(node_id=DEFAULT_NODE_ID)

    sidecar_task = asyncio.create_task(sidecar.start_listening(), name="sidecar")
    watcher_task = asyncio.create_task(watcher.run_forever(), name="watcher")

    client_log("边缘引擎全部就绪:")
    client_log(f"  - DuckDB Sidecar : 监听 {NATS_QUERY_REQUESTS}")
    client_log(f"  - Signal Watcher  : 监控 {os.path.basename(ALERTS_JSON_PATH)} (间隔 {WATCH_INTERVAL}s)")
    client_log("")
    client_log("实时数据流已启动:")
    client_log("  Wazuh → alerts.json → Watcher → 信号 → NATS → Server")
    client_log("  Server → 查询请求 → NATS → Sidecar → DuckDB查询 → 证据返回")
    client_log("")
    client_log("按 Ctrl+C 停止")
    client_log("=" * 56)

    # 等待任意任务结束
    tasks = [sidecar_task, watcher_task]
    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await sidecar.shutdown()
    await watcher.shutdown()

    client_log("所有客户端组件已关闭")


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    main_task = loop.create_task(run_client())

    def _shutdown():
        client_log("收到中断信号，正在优雅关闭...")
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
