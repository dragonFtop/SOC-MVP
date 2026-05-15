#!/usr/bin/env python3
"""
AI-SOC Server 统一入口
======================
整合所有服务端守护进程:
  - Signal Listener (NATS 信令监听)
  - Query Gateway (FastAPI 查询网关)
  - Dashboard (Streamlit 可视化)

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

from config import NATS_SERVERS, QUERY_GATEWAY_HOST, QUERY_GATEWAY_PORT


# ====================== 日志工具 ======================

def server_log(msg: str):
    """统一的服务端日志输出"""
    print(f"[Server] {msg}", flush=True)


# ====================== Query Gateway 线程 ======================

def run_query_gateway():
    """在独立线程中启动 FastAPI Query Gateway"""
    import uvicorn
    server_log(f"Query Gateway 启动中 -> http://{QUERY_GATEWAY_HOST}:{QUERY_GATEWAY_PORT}")
    uvicorn.run(
        "server.query_gateway:app",
        host=QUERY_GATEWAY_HOST,
        port=QUERY_GATEWAY_PORT,
        log_level="warning",
    )


# ====================== Dashboard 线程 ======================

def run_dashboard():
    """在独立线程中启动 Streamlit Dashboard"""
    import subprocess
    server_log("Dashboard 启动中 -> http://localhost:8501")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run",
         os.path.join(os.path.dirname(__file__), "dashboard.py"),
         "--server.port", "8501",
         "--server.headless", "true",
         "--browser.gatherUsageStats", "false"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ====================== Signal Listener (asyncio) ======================

class ServerSignalListener:
    """服务端信号监听器 - 接收边缘信号并触发查询"""

    def __init__(self):
        self.nc = None
        self.js = None
        self.processed = 0
        self.failed = 0

    async def connect(self):
        import nats
        self.nc = await nats.connect(servers=NATS_SERVERS, name="signal-listener-server")
        self.js = self.nc.jetstream()
        server_log(f"NATS 已连接: {NATS_SERVERS}")

    async def handle_signal(self, msg):
        try:
            import json, uuid
            signal_data = json.loads(msg.data.decode())
            server_log(f"收到信号: {signal_data.get('signal_id')} "
                       f"| 节点={signal_data.get('node_id')} "
                       f"| 规则={signal_data.get('rule_id')}")

            # 触发按需查询 - 发布到 NATS
            from config import DEFAULT_CASE_ID
            query_id = f"qry-{uuid.uuid4().hex[:8]}"
            query_request = {
                "query_id": query_id,
                "case_id": DEFAULT_CASE_ID,
                "node_id": signal_data.get("node_id"),
                "signal_id": signal_data.get("signal_id"),
                "source": signal_data.get("suggested_logs", ["wazuh_alerts"])[0],
                "filters": {"rule.id": signal_data.get("rule_id")},
                "limit": 20,
            }
            await self.js.publish(
                "soc.query.requests",
                json.dumps(query_request).encode(),
            )
            server_log(f"已下发查询: {query_id} -> 边缘节点 {signal_data.get('node_id')}")

            await msg.ack()
            self.processed += 1

        except Exception as e:
            server_log(f"信号处理失败: {e}")
            self.failed += 1
            try:
                await msg.ack()
            except Exception:
                pass

    async def _subscribe_safe(self, subject, durable):
        try:
            return await self.js.subscribe(subject, durable=durable, manual_ack=True)
        except Exception:
            for stream_name in ["SIGNALS"]:
                try:
                    await self.js.delete_consumer(stream_name, durable)
                except Exception:
                    pass
            return await self.js.subscribe(subject, durable=durable, manual_ack=True)

    async def listen_forever(self):
        await self.connect()

        from config import NATS_SIGNAL_SUBJECT
        sub = await self._subscribe_safe(f"{NATS_SIGNAL_SUBJECT}.*", "signal-listener-server")
        server_log(f"开始监听信令主题: {NATS_SIGNAL_SUBJECT}.*")
        server_log("等待边缘节点发送微信号...")
        server_log("")

        async for msg in sub.messages:
            await self.handle_signal(msg)

    async def shutdown(self):
        if self.nc:
            await self.nc.close()
        server_log(f"信号监听器已关闭 (处理={self.processed}, 失败={self.failed})")


# ====================== 查询结果监听器 ======================

class QueryResultListener:
    """监听边缘返回的查询结果"""

    def __init__(self):
        self.nc = None
        self.js = None
        self.results_received = 0

    async def connect(self):
        import nats
        self.nc = await nats.connect(servers=NATS_SERVERS, name="query-result-listener")
        self.js = self.nc.jetstream()

    async def handle_result(self, msg):
        try:
            import json
            result = json.loads(msg.data.decode())
            server_log(f"收到查询结果: {result.get('query_id')} "
                       f"| 证据={result.get('evidence_count')} 条 "
                       f"| 耗时={result.get('execution_time_ms')}ms "
                       f"| 节点={result.get('node_id')}")
            self.results_received += 1
            await msg.ack()

            # 持久化证据到本地 outputs 目录
            try:
                from config import OUTPUTS_DIR
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                output_dir = os.path.join(OUTPUTS_DIR, f"nats_{timestamp}")
                os.makedirs(output_dir, exist_ok=True)
                evidence_path = os.path.join(output_dir, "evidence.json")
                with open(evidence_path, "w", encoding="utf-8") as f:
                    json.dump(result.get("evidence", []), f, indent=2, ensure_ascii=False)
                server_log(f"证据已保存: {evidence_path}")
            except Exception as e:
                server_log(f"证据保存失败: {e}")

        except Exception as e:
            server_log(f"查询结果处理失败: {e}")
            try:
                await msg.ack()
            except Exception:
                pass

    async def _subscribe_safe(self, subject, durable):
        try:
            return await self.js.subscribe(subject, durable=durable, manual_ack=True)
        except Exception:
            for stream_name in ["QUERY_RESULTS"]:
                try:
                    await self.js.delete_consumer(stream_name, durable)
                except Exception:
                    pass
            return await self.js.subscribe(subject, durable=durable, manual_ack=True)

    async def listen_forever(self):
        await self.connect()
        sub = await self._subscribe_safe("soc.query.results", "query-result-listener")
        server_log("开始监听查询结果: soc.query.results")

        async for msg in sub.messages:
            await self.handle_result(msg)

    async def shutdown(self):
        if self.nc:
            await self.nc.close()
        server_log(f"查询结果监听器已关闭 (收到={self.results_received} 条)")


# ====================== 主入口 ======================

async def run_server():
    """启动所有服务端组件"""
    server_log("=" * 50)
    server_log("  AI-SOC Server - 中心侧安全运营平台")
    server_log("=" * 50)
    server_log(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    server_log(f"NATS 服务器: {NATS_SERVERS}")
    server_log("")

    # 1. Query Gateway (独立线程)
    gateway_thread = threading.Thread(target=run_query_gateway, daemon=True)
    gateway_thread.start()
    await asyncio.sleep(1)

    # 2. Dashboard (独立线程)
    dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
    dashboard_thread.start()
    await asyncio.sleep(1)

    # 3. Signal Listener + Query Result Listener (asyncio 并发)
    signal_listener = ServerSignalListener()
    result_listener = QueryResultListener()

    signal_task = asyncio.create_task(signal_listener.listen_forever())
    result_task = asyncio.create_task(result_listener.listen_forever())

    server_log("")
    server_log("所有服务已启动:")
    server_log("  - Query Gateway:  http://localhost:8000")
    server_log("  - Dashboard:      http://localhost:8501")
    server_log("  - Signal Listener: 监听 NATS 信号")
    server_log("  - Result Listener: 监听 NATS 查询结果")
    server_log("")
    server_log("等待客户端连接...")
    server_log("  在另一个终端窗口运行: bash run_client.sh")
    server_log("")
    server_log("按 Ctrl+C 停止所有服务")
    server_log("")

    # 等待任意任务完成（或用户中断）
    done, pending = await asyncio.wait(
        [signal_task, result_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # 取消剩余任务
    for task in pending:
        task.cancel()

    await signal_listener.shutdown()
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
