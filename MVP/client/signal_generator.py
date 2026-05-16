# MVP/client/signal_generator.py
"""
边缘侧微信号生成器 (Micro-signal Generator)
===========================================
职责：
  1. 从 Wazuh 告警中提取轻量级信令（不上传全量日志）
  2. 信令字段：signal_id, node_id, rule_id, src_ip, event_time, suggested_logs
  3. 通过 NATS JetStream 发布到中心

实现方案对应：第二章 - 边缘采集 & 轻量级信令生成
"""

import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    CLIENT_DIR,
    NATS_SERVERS,
    NATS_SIGNAL_SUBJECT,
    DEFAULT_RULE_ID,
)
from .local_gateway import query_wazuh
from common.nats_utils import get_nats, ensure_stream


def build_signals_from_alerts(rule_id: str = DEFAULT_RULE_ID, limit: int = 20) -> list[dict]:
    """
    从 Wazuh 告警中构建轻量级微信号列表

    Args:
        rule_id: 告警规则 ID（默认 5503 = SSH 登录失败）
        limit:   最大提取条数

    Returns:
        signals: 微信号列表，每个信号仅包含关键元数据
    """
    alerts = query_wazuh(rule_id=rule_id, limit=limit)
    signals = []

    for row in alerts:
        # row 结构与 local_gateway.query_wazuh 返回一致
        timestamp_field = row[0].isoformat() if row[0] else ""
        rule = row[1] if len(row) > 1 else {}
        agent = row[2] if len(row) > 2 else {}

        signal = {
            "signal_id": f"sig-{uuid.uuid4().hex[:8]}",
            "node_id": agent.get("name", "unknown"),
            "rule_id": rule.get("id", rule_id),
            "src_ip": agent.get("ip", "0.0.0.0"),
            "event_time": timestamp_field,
            "suggested_logs": ["wazuh-alerts", "auth.log"],
            "raw_ref": f"wazuh-alerts#{timestamp_field}#{agent.get('name')}",
        }
        signals.append(signal)

    return signals


async def _ensure_stream_async(js, stream_name: str = "SIGNALS"):
    """异步确保 Stream 存在（幂等，带默认限制）"""
    await ensure_stream(js, stream_name, [f"{NATS_SIGNAL_SUBJECT}.*"])


async def publish_signals_to_nats(signals: list[dict]) -> int:
    """
    将微信号列表发布到 NATS JetStream

    Args:
        signals: 要发布的微信号列表

    Returns:
        count: 成功发布的信号数量
    """
    nc = None
    try:
        nats = get_nats()
        nc = await nats.connect(servers=NATS_SERVERS)
        js = nc.jetstream()
        await _ensure_stream_async(js)

        for signal in signals:
            subject = f"{NATS_SIGNAL_SUBJECT}.{signal['node_id']}"
            await js.publish(subject, json.dumps(signal).encode())

        print(f"✅ [SignalGenerator] 已发送 {len(signals)} 条微信号到 NATS")
        return len(signals)

    except Exception as e:
        print(f"❌ [SignalGenerator] NATS 发布失败: {e}")
        # 降级：写入本地文件
        fallback_path = f"{CLIENT_DIR}/signals_fallback.json"
        with open(fallback_path, "w", encoding="utf-8") as f:
            json.dump(signals, f, indent=2, ensure_ascii=False)
        print(f"⚠️ [SignalGenerator] 已降级写入本地文件: {fallback_path}")
        return 0

    finally:
        if nc:
            await nc.close()


async def generate_and_publish(
    rule_id: str = DEFAULT_RULE_ID, limit: int = 20
) -> tuple[list[dict], int]:
    """
    一键执行：生成信号 → 发布到 NATS

    Returns:
        (signals, published_count)
    """
    signals = build_signals_from_alerts(rule_id=rule_id, limit=limit)
    count = await publish_signals_to_nats(signals)
    return signals, count


# ====================== 独立运行入口 ======================
if __name__ == "__main__":
    import asyncio

    async def main():
        signals, count = await generate_and_publish()
        print(f"生成 {len(signals)} 条，发布 {count} 条")

    asyncio.run(main())