# MVP/server/signal_listener.py
"""
中心侧信令监听器 (Signal Listener)
=====================================
职责：
  1. 监听 NATS JetStream 上的信令主题，接收边缘发来的微信号
  2. 解析信号，提取 suggested_logs 字段
  3. 触发按需取证流程（调用 Query Gateway）

对应实现方案：第二章（信令接收端）+ 第三章（触发查询）
"""

from __future__ import annotations

import asyncio
import json
import os as _os
import sys as _sys
import uuid
from typing import Optional

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from config import NATS_SERVERS, NATS_SIGNAL_SUBJECT, DEFAULT_CASE_ID


def _get_nats():
    import nats
    return nats


class SignalListener:
    """
    信令监听器，持续监听 NATS 上的信令主题
    """

    def __init__(self):
        self.nc: Optional[nats.NATS] = None
        self.js: Optional[nats.JetStreamContext] = None
        self.processed_count = 0
        self.fail_count = 0

    async def connect(self):
        """连接到 NATS JetStream"""
        nats = _get_nats()
        self.nc = await nats.connect(servers=NATS_SERVERS)
        self.js = self.nc.jetstream()
        print(f"✅ [SignalListener] 已连接到 NATS: {NATS_SERVERS}")

    async def handle_signal(self, msg):
        """
        处理接收到的微信号
        1. 解析信令
        2. 生成查询请求
        3. 下发到边缘查询
        """
        try:
            signal = json.loads(msg.data.decode())
            print(f"📩 [SignalListener] 收到信号: {signal.get('signal_id')}")

            # 记录到 OpenSearch
            from .opensearch_loader import OpenSearchClient
            os_client = OpenSearchClient()
            os_client.index("soc-signals", signal)

            # 触发按需查询
            query_id = f"qry-{uuid.uuid4().hex[:8]}"
            query_request = {
                "query_id": query_id,
                "case_id": DEFAULT_CASE_ID,
                "node_id": signal.get("node_id"),
                "signal_id": signal.get("signal_id"),
                "source": signal.get("suggested_logs", ["wazuh-alerts"])[0],
                "filters": {"rule.id": signal.get("rule_id")},
                "limit": 20,
            }

            # 发布查询请求到边缘
            await self.js.publish(
                "soc.query.requests",
                json.dumps(query_request).encode(),
            )
            print(f"📤 [SignalListener] 已下发查询: {query_id}")

            await msg.ack()
            self.processed_count += 1

        except Exception as e:
            print(f"❌ [SignalListener] 处理信号失败: {e}")
            self.fail_count += 1
            await msg.ack()  # 防止阻塞队列

    async def _subscribe_safe(self, subject: str, durable: str):
        """订阅并自动处理残留 consumer 冲突"""
        try:
            return await self.js.subscribe(subject, durable=durable, manual_ack=True)
        except Exception:
            for stream_name in ["SIGNALS", "QUERY_REQUESTS", "QUERY_RESULTS"]:
                try:
                    await self.js.delete_consumer(stream_name, durable)
                except Exception:
                    pass
            return await self.js.subscribe(subject, durable=durable, manual_ack=True)

    async def listen_forever(self):
        """持续监听信令主题"""
        await self.connect()

        # 创建持久订阅（自动处理残留 consumer）
        sub = await self._subscribe_safe(f"{NATS_SIGNAL_SUBJECT}.*", "signal-listener")

        print(f"👂 [SignalListener] 开始监听 {NATS_SIGNAL_SUBJECT}.*")

        async for msg in sub.messages:
            await self.handle_signal(msg)

    async def close(self):
        if self.nc:
            await self.nc.close()
            print(f"📊 [SignalListener] 处理统计: 成功={self.processed_count}, 失败={self.fail_count}")


# ====================== 独立运行入口 ======================
if __name__ == "__main__":
    async def main():
        listener = SignalListener()
        try:
            await listener.listen_forever()
        except KeyboardInterrupt:
            print("\n🛑 [SignalListener] 收到中断信号")
        finally:
            await listener.close()

    asyncio.run(main())