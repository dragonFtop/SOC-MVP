#!/usr/bin/env python3
"""NATS Stream 管理工具 — list / purge / rm"""

import asyncio, sys, nats
from nats.js.api import StreamConfig

NATS_URL = "nats://localhost:4222"
STREAMS = ["SIGNALS", "QUERY_REQUESTS", "QUERY_RESULTS"]


async def list_streams(js):
    print(f"{'Stream':<24} {'Messages':>10} {'Bytes':>12} {'Consumers':>10}")
    print("-" * 60)
    for name in STREAMS:
        try:
            info = await js.stream_info(name)
            msgs = info.state.messages
            size = info.state.bytes
            consumers = info.state.consumer_count
            print(f"{name:<24} {msgs:>10,} {size:>12,} {consumers:>10,}")
        except Exception as e:
            print(f"{name:<24} {'(不存在)':>10}")


async def purge_streams(js, names):
    for name in names:
        try:
            await js.purge_stream(name)
            print(f"  {name} 已清空")
        except Exception as e:
            print(f"  {name} 清空失败: {e}")


async def delete_streams(js, names):
    for name in names:
        try:
            await js.delete_stream(name)
            print(f"  {name} 已删除")
        except Exception as e:
            print(f"  {name} 删除失败: {e}")


async def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/nats_mgmt.py [list|purge|rm] [stream_name ...]")
        print("  list            列出所有 Stream 状态")
        print("  purge [name ..] 清空 Stream 消息（默认全部）")
        print("  rm [name ..]    删除 Stream（默认全部）")
        return

    cmd = sys.argv[1]
    targets = sys.argv[2:] if len(sys.argv) > 2 else STREAMS

    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()

    try:
        if cmd == "list":
            await list_streams(js)
        elif cmd == "purge":
            await purge_streams(js, targets)
        elif cmd == "rm":
            await delete_streams(js, targets)
        else:
            print(f"未知命令: {cmd}")
    finally:
        await nc.close()

asyncio.run(main())
