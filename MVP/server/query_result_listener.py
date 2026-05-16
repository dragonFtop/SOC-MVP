# MVP/server/query_result_listener.py
"""
查询结果监听器 (Query Result Listener)
========================================
职责：
  1. 监听 NATS 上边缘返回的查询结果
  2. 将证据持久化到本地 outputs 目录
  3. 记录接收统计

对应实现方案：第四章 - 边缘按需查询（结果接收端）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import NATS_SERVERS, NATS_QUERY_RESULTS, OUTPUTS_DIR
from common.nats_utils import get_nats, subscribe_safe, safe_ack, ensure_stream, MAX_DELIVERY_ATTEMPTS
from common.monitor_events import MonitorEmitter


class QueryResultListener:
    """监听边缘返回的查询结果并持久化"""

    def __init__(self):
        self.nc = None
        self.js = None
        self.results_received = 0
        self.monitor = None

    async def connect(self):
        nats = get_nats()
        self.nc = await nats.connect(servers=NATS_SERVERS, name="query-result-listener")
        self.js = self.nc.jetstream()
        self.monitor = MonitorEmitter(self.nc, "QueryResultListener")
        print(f"[ResultListener] NATS 已连接")

    async def handle_result(self, msg):
        async def _process():
            result = json.loads(msg.data.decode())
            print(f"[ResultListener] 收到查询结果: {result.get('query_id')} "
                  f"| 证据={result.get('evidence_count')} 条 "
                  f"| 耗时={result.get('execution_time_ms')}ms "
                  f"| 节点={result.get('node_id')}")
            if self.monitor:
                await self.monitor.result_received(query_id=result.get("query_id"),
                                                   node_id=result.get("node_id"),
                                                   evidence_count=result.get("evidence_count", 0))

            # 持久化证据到本地 outputs 目录
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            output_dir = os.path.join(OUTPUTS_DIR, f"nats_{timestamp}")
            os.makedirs(output_dir, exist_ok=True)
            evidence_path = os.path.join(output_dir, "evidence.json")
            with open(evidence_path, "w", encoding="utf-8") as f:
                json.dump(result.get("evidence", []), f, indent=2, ensure_ascii=False)
            print(f"[ResultListener] 证据已保存: {evidence_path}")
            if self.monitor:
                await self.monitor.evidence_saved(query_id=result.get("query_id"),
                                                  evidence_count=result.get("evidence_count", 0),
                                                  path=evidence_path)

        acked = await safe_ack(msg, on_success=_process)
        if acked:
            self.results_received += 1
        else:
            md = getattr(msg, 'metadata', None)
            attempts = getattr(md, 'num_delivered', 1) if md else 1
            print(f"[ResultListener] 处理失败，重试中 ({attempts}/{MAX_DELIVERY_ATTEMPTS})")

    async def listen_forever(self):
        await self.connect()
        await ensure_stream(self.js, "QUERY_RESULTS", [NATS_QUERY_RESULTS])
        sub = await subscribe_safe(self.js, NATS_QUERY_RESULTS, "query-result-listener", stream_names=["QUERY_RESULTS"])
        print(f"[ResultListener] 开始监听查询结果: {NATS_QUERY_RESULTS}")

        async for msg in sub.messages:
            await self.handle_result(msg)

    async def shutdown(self):
        if self.nc:
            await self.nc.close()
        print(f"[ResultListener] 已关闭 (收到={self.results_received} 条)")
