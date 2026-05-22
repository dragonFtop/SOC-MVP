#!/usr/bin/env python3
"""
AI-SOC Client 统一入口
======================
整合所有客户端边缘组件:
  - DetectionEngine   (detection_engine.py) - 本地检测引擎: tail auth.log, YAML规则检测, 生成信号
  - DuckDB Sidecar    (duckdb_sidecar.py)   - 监听 NATS 查询请求, 本地执行 DuckDB 查询 / auth_log 证据提取

在独立终端窗口中运行，展示边缘侧实时处理过程。

实时数据流:
  auth.log → DetectionEngine (tail + parse + YAML rules) → 信号 → NATS
    → Server Signal Listener 收到 → 触发查询 → NATS
    → DuckDB Sidecar 查询 DetectionEngine 预解析事件 → 返回证据 → NATS
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
    AUTH_LOG_PATH,
    DETECTION_RULES_PATH,
    EVENT_RETENTION_MINUTES,
)

WATCH_INTERVAL = 2


def client_log(msg: str):
    print(f"[Client] {msg}", flush=True)


async def run_client():
    from client.duckdb_sidecar import DuckDBQueryEngine
    from client.detection_engine import DetectionEngine

    client_log("=" * 56)
    client_log("  AI-SOC Client - 边缘侧实时安全检测")
    client_log("=" * 56)
    client_log(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    client_log(f"边缘节点: {DEFAULT_NODE_ID}")
    client_log(f"NATS 服务器: {NATS_SERVERS}")
    client_log(f"监控文件: {AUTH_LOG_PATH}")
    client_log(f"检测规则: {DETECTION_RULES_PATH}")
    client_log(f"轮询间隔: {WATCH_INTERVAL}s")
    client_log("")

    engine = DetectionEngine(
        node_id=DEFAULT_NODE_ID,
        auth_log_path=AUTH_LOG_PATH,
        rules_path=DETECTION_RULES_PATH,
        watch_interval=WATCH_INTERVAL,
        retention_minutes=EVENT_RETENTION_MINUTES,
    )

    sidecar = DuckDBQueryEngine(
        node_id=DEFAULT_NODE_ID,
        detection_engine=engine,
    )

    engine_task = asyncio.create_task(engine.run_forever(), name="detection-engine")
    sidecar_task = asyncio.create_task(sidecar.start_listening(), name="sidecar")

    client_log("边缘引擎全部就绪:")
    client_log(f"  - Detection Engine : {AUTH_LOG_PATH} (YAML rules)")
    client_log(f"  - DuckDB Sidecar   : 监听 {NATS_QUERY_REQUESTS}")
    client_log("")
    client_log("实时数据流已启动:")
    client_log("  auth.log → DetectionEngine → 信号 → NATS → Server")
    client_log("  Server → 查询请求 → NATS → Sidecar → 证据返回")
    client_log("")
    client_log("按 Ctrl+C 停止")
    client_log("=" * 56)

    tasks = [engine_task, sidecar_task]
    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await sidecar.shutdown()
    await engine.shutdown()

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
