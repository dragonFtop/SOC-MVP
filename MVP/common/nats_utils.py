# MVP/common/nats_utils.py
"""
NATS JetStream 共享工具
========================
提供所有模块共用的 NATS 连接、Stream 配置、订阅与 ACK 工具。
"""

from __future__ import annotations

from typing import Optional

# Stream 默认配置 — 防止无限积压
# max_age 是秒 (nats-py 内部会 ×1e9 转纳秒)
STREAM_CONFIG = {
    "max_age":  24 * 3600,             # 24 小时后自动过期
    "max_bytes": 500 * 1024 * 1024,    # 单 Stream 最多 500 MB
}

# 每条消息最大重试次数（超过后 ACK 丢弃，防止死信阻塞）
MAX_DELIVERY_ATTEMPTS = 3

# Subject → Stream 名映射
_SUBJECT_STREAM_MAP = {
    "soc.signals":        ("SIGNALS",         ["soc.signals.*"]),
    "soc.query.requests": ("QUERY_REQUESTS",  ["soc.query.requests"]),
    "soc.query.results":  ("QUERY_RESULTS",   ["soc.query.results"]),
}


def get_nats():
    """延迟导入 nats，使得该依赖在不需要 NATS 时可选"""
    import nats
    return nats


def _streams_for_subject(subject: str) -> list[str]:
    """根据 subject 推导可能的 Stream 名称"""
    for prefix, (name, _) in _SUBJECT_STREAM_MAP.items():
        if subject.startswith(prefix):
            return [name]
    return ["SIGNALS", "QUERY_REQUESTS", "QUERY_RESULTS"]


async def ensure_stream(js, name: str, subjects: list[str]):
    """创建 JetStream Stream（带默认限制，幂等）"""
    try:
        await js.add_stream(name=name, subjects=subjects, **STREAM_CONFIG)
        print(f"📦 [NATS] Stream 已创建: {name} (subjects={subjects}, max_age=24h, max_bytes=500MB)")
    except Exception as e:
        err = str(e)
        if "already" in err.lower() or "duplicate" in err.lower():
            pass  # Stream 已存在，正常
        else:
            print(f"⚠️ [NATS] 创建 Stream {name} 失败 ({type(e).__name__}): {err}")


async def subscribe_safe(js, subject: str, durable: str,
                         stream_names: Optional[list[str]] = None,
                         deliver_policy: str = "all"):
    """
    安全订阅 NATS JetStream 主题。

    自动处理三种常见故障：
      1. Stream 不存在 → 创建 Stream 后重试
      2. Consumer 残留冲突 → 删除残留 consumer 后重试
      3. deliver_policy="all" 确保 Server 后启动也能收到 Client 先发布的信号；
         持久化的 durable consumer 不会重复投递已 ACK 的消息
    """
    if stream_names is None:
        stream_names = _streams_for_subject(subject)

    async def _try_subscribe():
        return await js.subscribe(subject, durable=durable, manual_ack=True,
                                  deliver_policy=deliver_policy)

    # 第一次尝试 (durable consumer 存在则恢复 ACK 位置)
    try:
        return await _try_subscribe()
    except Exception as e1:
        # Stream 不存在 → 创建后重试
        for name in stream_names:
            try:
                subs = _SUBJECT_STREAM_MAP.get(subject, (name, [subject]))[1]
            except Exception:
                subs = [subject]
            await ensure_stream(js, name, subs)

        # 第二次尝试 (不删除 consumer，保留 ACK 历史)
        try:
            return await _try_subscribe()
        except Exception as e2:
            raise RuntimeError(
                f"无法订阅 {subject} (durable={durable}): "
                f"首次={type(e1).__name__}, 重试={type(e2).__name__}"
            ) from e2


async def safe_ack(msg, on_success=None) -> bool:
    """
    带重试的 ACK 处理。

    - 成功时调用 on_success 回调，然后 ACK。
    - 失败时 NAK 请求重新投递；超过 MAX_DELIVERY_ATTEMPTS 次后 ACK 丢弃，防止死信阻塞。

    Returns: True 表示消息已最终处理（ACK），False 表示已 NAK 等待重试。
    """
    md = getattr(msg, 'metadata', None)
    attempts = getattr(md, 'num_delivered', 1) if md else 1

    try:
        if on_success:
            await on_success()
        await msg.ack()
        return True
    except Exception as e:
        import traceback
        print(f"   ⚠️ [safe_ack] 处理失败 (attempt {attempts}/{MAX_DELIVERY_ATTEMPTS}): {e}")
        traceback.print_exc()
        if attempts >= MAX_DELIVERY_ATTEMPTS:
            await msg.ack()
            print(f"   ⚠️ [safe_ack] 已达最大重试次数，消息已丢弃")
            return True
        else:
            await msg.nak()
            return False
