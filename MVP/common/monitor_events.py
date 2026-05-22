# MVP/common/monitor_events.py
"""
监控事件发射器
==============
所有组件通过 MonitorEmitter 发布轻量级结构化事件到核心 NATS（非 JetStream），
供实时监控 Dashboard 消费。所有发布均为 best-effort，失败不影响主流程。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Optional

from config import NATS_MONITOR_EVENTS


class MonitorEmitter:
    """发布结构化监控事件到 NATS（核心发布/订阅，即发即弃）"""

    def __init__(self, nc, component: str, node_id: str = ""):
        self.nc = nc
        self.component = component
        self.source = "client" if component in ("SignalWatcher", "DuckDBSidecar", "DetectionEngine") else "server"
        self.node_id = node_id

    # ── 8 种事件类型 ──────────────────────────────────────────

    async def signal_sent(self, signal_id="", rule_id="", rule_level=0, node_id="", rule_desc=""):
        await self._emit("signal.sent", signal_id=signal_id, rule_id=rule_id,
                         rule_level=rule_level, node_id=node_id, rule_desc=rule_desc)

    async def signal_received(self, signal_id="", node_id="", rule_id=""):
        await self._emit("signal.received", signal_id=signal_id, node_id=node_id, rule_id=rule_id)

    async def query_sent(self, query_id="", signal_id="", node_id=""):
        await self._emit("query.sent", query_id=query_id, signal_id=signal_id, node_id=node_id)

    async def query_received(self, query_id="", node_id=""):
        await self._emit("query.received", query_id=query_id, node_id=node_id)

    async def query_executed(self, query_id="", duration_ms=0, evidence_count=0):
        await self._emit("query.executed", query_id=query_id, duration_ms=duration_ms,
                         evidence_count=evidence_count)

    async def result_sent(self, query_id="", node_id="", evidence_count=0, duration_ms=0):
        await self._emit("result.sent", query_id=query_id, node_id=node_id,
                         evidence_count=evidence_count, duration_ms=duration_ms)

    async def result_received(self, query_id="", node_id="", evidence_count=0):
        await self._emit("result.received", query_id=query_id, node_id=node_id,
                         evidence_count=evidence_count)

    async def evidence_saved(self, query_id="", evidence_count=0, path=""):
        await self._emit("evidence.saved", query_id=query_id, evidence_count=evidence_count, path=path)

    # ── 内部方法 ──────────────────────────────────────────────

    async def _emit(self, event_type: str, **payload):
        """发布单个事件到 NATS 核心主题（best-effort）"""
        event = {
            "event_id":   f"evt-{uuid.uuid4().hex[:8]}",
            "event_type": event_type,
            "source":     self.source,
            "component":  self.component,
            "node_id":    self.node_id,
            "timestamp":  time.strftime("%H:%M:%S"),
            "payload":    payload,
        }
        try:
            await self.nc.publish(NATS_MONITOR_EVENTS, json.dumps(event, ensure_ascii=False).encode())
        except Exception:
            pass  # 监控事件不影响主流程
