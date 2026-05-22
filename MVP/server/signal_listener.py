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
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import nats

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from config import NATS_SERVERS, NATS_SIGNAL_SUBJECT, DEFAULT_CASE_ID
from common.nats_utils import get_nats, subscribe_safe, safe_ack, ensure_stream, MAX_DELIVERY_ATTEMPTS
from common.monitor_events import MonitorEmitter


class SignalListener:
    """
    信令监听器，持续监听 NATS 上的信令主题
    """

    def __init__(self):
        self.nc: Optional[nats.NATS] = None
        self.js: Optional[nats.JetStreamContext] = None
        self.processed_count = 0
        self.fail_count = 0
        self.monitor = None

    async def connect(self):
        """连接到 NATS JetStream"""
        nats = get_nats()
        self.nc = await nats.connect(servers=NATS_SERVERS)
        self.js = self.nc.jetstream()
        self.monitor = MonitorEmitter(self.nc, "SignalListener")
        print(f"✅ [SignalListener] 已连接到 NATS: {NATS_SERVERS}")

    async def handle_signal(self, msg):
        """
        处理接收到的微信号
        1. 解析信令
        2. 生成查询请求
        3. 下发到边缘查询
        """
        async def _process():
            signal = json.loads(msg.data.decode())
            print(f"📩 [SignalListener] 收到信号: {signal.get('signal_id')}")
            if self.monitor:
                await self.monitor.signal_received(
                    signal_id=signal.get("signal_id"),
                    node_id=signal.get("node_id"),
                    rule_id=signal.get("rule_id"))

            # 记录到 OpenSearch (best-effort, 不影响查询下发)
            try:
                from .opensearch_loader import OpenSearchClient
                os_client = OpenSearchClient()
                os_client.index("soc-signals", signal)
            except Exception as e:
                print(f"⚠️ [SignalListener] OpenSearch 信令索引失败: {e}")

            # 触发按需查询
            query_id = f"qry-{uuid.uuid4().hex[:8]}"
            source = signal.get("suggested_logs", ["wazuh-alerts"])[0]

            if source == "auth_log":
                # Build auth_log-specific filters for the DetectionEngine
                filters = {}
                src_ip = signal.get("src_ip")
                if src_ip:
                    filters["src_ip"] = src_ip
                detection_rule_id = signal.get("detection_rule_id", "")
                if "SSH" in detection_rule_id:
                    filters["process"] = "sshd"
                elif "SUDO" in detection_rule_id:
                    filters["process"] = "sudo"
                elif "SU_" in detection_rule_id:
                    filters["process"] = "su"
            else:
                filters = {"rule.id": signal.get("rule_id")}

            query_request = {
                "query_id": query_id,
                "case_id": DEFAULT_CASE_ID,
                "node_id": signal.get("node_id"),
                "signal_id": signal.get("signal_id"),
                "source": source,
                "filters": filters,
                "limit": 20,
            }

            await self.js.publish(
                "soc.query.requests",
                json.dumps(query_request).encode(),
            )
            print(f"📤 [SignalListener] 已下发查询: {query_id}")
            if self.monitor:
                await self.monitor.query_sent(query_id=query_id,
                                              signal_id=signal.get("signal_id"),
                                              node_id=signal.get("node_id"))

        acked = await safe_ack(msg, on_success=_process)
        if acked:
            self.processed_count += 1
        else:
            self.fail_count += 1
            md = getattr(msg, 'metadata', None)
            attempts = getattr(md, 'num_delivered', 1) if md else 1
            print(f"❌ [SignalListener] 处理信号失败，重试中 ({attempts}/{MAX_DELIVERY_ATTEMPTS})")

    async def listen_forever(self):
        """持续监听信令主题"""
        await self.connect()

        # 确保所需 Stream 存在（服务器可能比客户端先启动）
        await ensure_stream(self.js, "SIGNALS", [f"{NATS_SIGNAL_SUBJECT}.*"])
        await ensure_stream(self.js, "QUERY_REQUESTS", ["soc.query.requests"])

        sub = await subscribe_safe(self.js, f"{NATS_SIGNAL_SUBJECT}.*", "signal-listener")

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