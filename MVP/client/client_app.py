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

import argparse
import asyncio
import os
import signal
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    NATS_SERVERS,
    NATS_QUERY_REQUESTS,
    DETECTION_RULES_PATH,
    EVENT_RETENTION_MINUTES,
    CLIENT_CONFIG_PATH,
    ROOT_DIR,
)

WATCH_INTERVAL = 2


def load_client_config(client_id: str, config_path: str | None = None) -> dict:
    """从 client_config.yaml 中加载指定 client 的配置。"""
    path = config_path or CLIENT_CONFIG_PATH

    if not os.path.exists(path):
        print(f"❌ 配置文件不存在: {path}", file=sys.stderr)
        print(f"   请先使用 register_client.sh 注册客户端，或手动创建该文件", file=sys.stderr)
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    clients = config.get("clients", [])
    for c in clients:
        if c.get("client_id") == client_id:
            return c

    print(f"❌ 未找到 client_id='{client_id}' 的注册信息", file=sys.stderr)
    print(f"   可用客户端:", file=sys.stderr)
    for c in clients:
        print(f"     - {c.get('client_id', '?')}  (节点: {c.get('node_id', '?')})", file=sys.stderr)
    print(f"   使用 register_client.sh 注册新客户端", file=sys.stderr)
    sys.exit(1)


def resolve_log_path(log_path: str) -> str:
    """将配置中的 log_path 解析为实际路径。绝对路径直接使用，相对路径相对于项目根。"""
    if os.path.isabs(log_path):
        return log_path
    return os.path.join(ROOT_DIR, log_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI-SOC Client - edge security detection")
    parser.add_argument(
        "--client-id",
        default=None,
        help="Client identifier registered in client_config.yaml",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to client_config.yaml (default: MVP/client_config.yaml)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all registered clients and exit",
    )
    return parser.parse_args()


def client_log(msg: str):
    print(f"[Client] {msg}", flush=True)


async def run_client(client_id: str, node_id: str, auth_log_path: str):
    from client.duckdb_sidecar import DuckDBQueryEngine
    from client.detection_engine import DetectionEngine

    client_log("=" * 56)
    client_log("  AI-SOC Client - 边缘侧实时安全检测")
    client_log("=" * 56)
    client_log(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    client_log(f"Client ID: {client_id}")
    client_log(f"边缘节点: {node_id}")
    client_log(f"NATS 服务器: {NATS_SERVERS}")
    client_log(f"监控文件: {auth_log_path}")
    client_log(f"检测规则: {DETECTION_RULES_PATH}")
    client_log(f"轮询间隔: {WATCH_INTERVAL}s")
    client_log("")

    engine = DetectionEngine(
        node_id=node_id,
        auth_log_path=auth_log_path,
        rules_path=DETECTION_RULES_PATH,
        watch_interval=WATCH_INTERVAL,
        retention_minutes=EVENT_RETENTION_MINUTES,
    )

    sidecar = DuckDBQueryEngine(
        node_id=node_id,
        detection_engine=engine,
    )

    engine_task = asyncio.create_task(engine.run_forever(), name="detection-engine")
    sidecar_task = asyncio.create_task(sidecar.start_listening(), name="sidecar")

    client_log("边缘引擎全部就绪:")
    client_log(f"  - Detection Engine : {auth_log_path} (YAML rules)")
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
    # 给 NATS 后台任务时间清理
    await asyncio.sleep(0.2)

    client_log("所有客户端组件已关闭")


def main():
    args = parse_args()

    # --list: 展示所有注册客户端
    if args.list:
        path = args.config or CLIENT_CONFIG_PATH
        if not os.path.exists(path):
            print(f"配置文件不存在: {path}")
            sys.exit(1)
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        clients = config.get("clients", [])
        if not clients:
            print("（无已注册客户端）")
        else:
            print(f"{'CLIENT ID':<24} {'NODE ID':<20} {'LOG PATH'}")
            print("-" * 72)
            for c in clients:
                print(f"{c.get('client_id', '?'):<24} {c.get('node_id', '?'):<20} {c.get('log_path', '?')}")
        return

    # 非 --list 模式下 --client-id 必填
    if not args.client_id:
        print("❌ 缺少 --client-id 参数", file=sys.stderr)
        print("   用法: python3 MVP/client/client_app.py --client-id <client-id>", file=sys.stderr)
        print("   使用 --list 查看已注册客户端", file=sys.stderr)
        sys.exit(1)

    # 加载配置
    cfg = load_client_config(args.client_id, args.config)
    client_id = cfg["client_id"]
    node_id = cfg["node_id"]
    log_path = cfg.get("log_path", "")

    auth_log_path = resolve_log_path(log_path)

    # 确保模拟节点日志目录存在
    if not os.path.isabs(log_path):
        os.makedirs(os.path.dirname(auth_log_path), exist_ok=True)

    from config import validate_config

    print(f"[Client] 加载配置: {cfg.get('description', client_id)}")
    print(f"[Client] Client: {client_id} | Node: {node_id}")
    print(f"[Client] 日志: {auth_log_path}")

    validate_config()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    main_task = loop.create_task(run_client(client_id, node_id, auth_log_path))

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
        # 清理剩余后台任务，避免 "Event loop is closed" 报错
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        if pending:
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        loop.close()


if __name__ == "__main__":
    main()
