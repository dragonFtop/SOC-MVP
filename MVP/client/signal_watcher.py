# MVP/client/signal_watcher.py
"""
实时信号监控器 (Signal Watcher)
================================
职责：
  1. 持续监控 Wazuh alerts.json (NDJSON) 的新增告警
  2. 实时解析新告警 → 生成轻量级微信号
  3. 通过 NATS JetStream 发布到中心侧

与 signal_generator.py 的区别：
  - signal_generator: 一次性批量生成信号（用于 main.py 流程）
  - signal_watcher:   持续实时监控文件变化（用于 client_app.py 守护进程）

对应实现方案：第二章 - 边缘采集 & 轻量级信令生成（实时模式）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    NATS_SERVERS,
    NATS_SIGNAL_SUBJECT,
    DEFAULT_NODE_ID,
    ALERTS_JSON_PATH,
)
from common.nats_utils import get_nats, ensure_stream, safe_ack
from common.monitor_events import MonitorEmitter

WATCH_INTERVAL = 2  # 文件监控轮询间隔（秒）


class SignalWatcher:
    """
    实时文件监控器 - 持续监控 alerts.json (NDJSON) 的新增告警

    工作原理:
      1. 启动时读取现有全部告警, 生成初始信号批次
      2. 记录文件末尾位置 (byte offset)
      3. 每隔 WATCH_INTERVAL 秒检查文件是否增长
      4. 读取新增行 → 解析 → 生成信号 → 发布到 NATS
      5. 通过 alert.id 去重, 避免重复发送
    """

    def __init__(self, node_id: str = DEFAULT_NODE_ID):
        self.node_id = node_id
        self.file_path = ALERTS_JSON_PATH
        self.nc = None
        self.js = None
        self.seen_ids: set = set()
        self.last_offset: int = 0
        self.stats = {"initial": 0, "new": 0, "errors": 0}
        self.monitor = None
        self._running = False

    async def connect(self):
        nats = get_nats()
        self.nc = await nats.connect(servers=NATS_SERVERS, name=f"watcher-{self.node_id}")
        self.js = self.nc.jetstream()
        await ensure_stream(self.js, "SIGNALS", [f"{NATS_SIGNAL_SUBJECT}.*"])
        self.monitor = MonitorEmitter(self.nc, "SignalWatcher", self.node_id)
        print(f"[SignalWatcher:{self.node_id}] NATS 已连接")

    def _read_new_lines(self) -> list[dict]:
        """从文件末尾读取新增的 NDJSON 行"""
        if not os.path.exists(self.file_path):
            return []

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                f.seek(0, 2)
                current_size = f.tell()

                if current_size <= self.last_offset:
                    return []

                f.seek(self.last_offset)
                raw = f.read()
                self.last_offset = current_size

            new_alerts = []
            for line in raw.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    alert = json.loads(line)
                    alert_id = alert.get("id", "")
                    if alert_id and alert_id not in self.seen_ids:
                        self.seen_ids.add(alert_id)
                        new_alerts.append(alert)
                except json.JSONDecodeError:
                    self.stats["errors"] += 1

            return new_alerts

        except Exception as e:
            print(f"[SignalWatcher:{self.node_id}] 读取告警文件失败: {e}")
            return []

    def _alert_to_signal(self, alert: dict) -> dict:
        """将 Wazuh 告警 JSON 转换为轻量级微信号"""
        rule = alert.get("rule", {})
        agent = alert.get("agent", {})
        ts = alert.get("timestamp", "")

        return {
            "signal_id": f"sig-{uuid.uuid4().hex[:8]}",
            "node_id": agent.get("name", self.node_id),
            "rule_id": str(rule.get("id", "")),
            "rule_level": rule.get("level", 0),
            "rule_desc": rule.get("description", ""),
            "src_ip": agent.get("ip", "0.0.0.0"),
            "event_time": ts,
            "suggested_logs": ["wazuh_alerts", "auth.log"],
            "raw_ref": f"wazuh-alerts#{ts}#{agent.get('name', self.node_id)}",
        }

    async def publish_batch(self, signals: list[dict], label: str = ""):
        """发布一批信号到 NATS"""
        count = 0
        for sig in signals:
            subject = f"{NATS_SIGNAL_SUBJECT}.{sig['node_id']}"
            await self.js.publish(subject, json.dumps(sig).encode())
            if self.monitor:
                await self.monitor.signal_sent(
                    signal_id=sig["signal_id"], rule_id=sig["rule_id"],
                    rule_level=sig.get("rule_level", 0), node_id=sig["node_id"],
                    rule_desc=sig.get("rule_desc", ""))
            count += 1
            print(f"[SignalWatcher:{self.node_id}] 已发送信号: {sig['signal_id']} "
                  f"| 规则={sig['rule_id']}(Lv{sig['rule_level']}) "
                  f"| {sig['rule_desc'][:40]} "
                  f"-> {subject}")
        if count > 0:
            print(f"[SignalWatcher:{self.node_id}] [{label}] 信号批次发布完成: {count} 条")
        return count

    async def run_forever(self):
        """持续监控文件变化并发布新信号"""
        await self.connect()
        self._running = True

        # Phase 1: 读取现有告警作为初始批次
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    initial_alerts = []
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            alert = json.loads(line)
                            alert_id = alert.get("id", "")
                            if alert_id and alert_id not in self.seen_ids:
                                self.seen_ids.add(alert_id)
                                initial_alerts.append(alert)
                        except json.JSONDecodeError:
                            pass
                    self.last_offset = f.tell()

                if initial_alerts:
                    recent = initial_alerts[-20:]
                    signals = [self._alert_to_signal(a) for a in recent]
                    await self.publish_batch(signals, label=f"初始批次 ({len(initial_alerts)} 条历史告警)")
                    self.stats["initial"] = len(signals)
                else:
                    print(f"[SignalWatcher:{self.node_id}] 告警文件中无有效记录，等待新数据...")
            except Exception as e:
                print(f"[SignalWatcher:{self.node_id}] 初始扫描失败: {e}")

        print(f"[SignalWatcher:{self.node_id}] 开始实时监控告警文件: {self.file_path}")
        print(f"[SignalWatcher:{self.node_id}]   轮询间隔: {WATCH_INTERVAL}s | 已跟踪: {len(self.seen_ids)} 条告警")

        # Phase 2: 持续监控新告警
        while self._running:
            try:
                new_alerts = self._read_new_lines()
                if new_alerts:
                    signals = [self._alert_to_signal(a) for a in new_alerts]
                    await self.publish_batch(signals, label=f"实时 ({len(new_alerts)} 条新告警)")
                    self.stats["new"] += len(signals)

                await asyncio.sleep(WATCH_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[SignalWatcher:{self.node_id}] 监控循环异常: {e}")
                await asyncio.sleep(WATCH_INTERVAL)

    async def shutdown(self):
        self._running = False
        if self.nc:
            await self.nc.close()
        print(f"[SignalWatcher:{self.node_id}] 已关闭 "
              f"(初始={self.stats['initial']} 实时新增={self.stats['new']} 错误={self.stats['errors']})")
