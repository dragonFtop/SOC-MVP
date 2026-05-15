# MVP/client/duckdb_sidecar.py
"""
DuckDB Sidecar - 边缘按需查询引擎
======================================
职责：
  1. 监听 NATS 上的查询请求（来自中心 Query Gateway）
  2. 使用 DuckDB 本地查询 JSON/CSV/Parquet 日志文件
  3. 只返回关键证据字段，不上传全量日志
  4. 将结果通过 NATS 发送回中心

对应实现方案：第四章 - 边缘按需查询
"""

from __future__ import annotations

import asyncio
import json
import hashlib
import os as _os
import sys as _sys
import uuid
from typing import Optional

_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import duckdb

from config import NATS_SERVERS, NATS_QUERY_REQUESTS, NATS_QUERY_RESULTS, DEFAULT_NODE_ID


def _get_nats():
    import nats
    return nats


class DuckDBQueryEngine:
    """
    边缘侧 DuckDB 查询引擎
    """

    def __init__(self, node_id: str = DEFAULT_NODE_ID):
        self.node_id = node_id
        self.con: Optional[duckdb.DuckDBPyConnection] = None
        self.nc: Optional[nats.NATS] = None
        self.js: Optional[nats.JetStreamContext] = None
        self.stats = {"received": 0, "processed": 0, "failed": 0}

    async def connect_nats(self):
        """连接到 NATS JetStream"""
        nats = _get_nats()
        self.nc = await nats.connect(servers=NATS_SERVERS, name=f"duckdb-sidecar-{self.node_id}")
        self.js = self.nc.jetstream()
        await self._ensure_streams()
        print(f"✅ [DuckDBSidecar:{self.node_id}] 已连接到 NATS")

    async def _ensure_streams(self):
        """确保所需的 JetStream Stream 存在"""
        for name, subject in [
            ("QUERY_REQUESTS", NATS_QUERY_REQUESTS),
            ("QUERY_RESULTS", NATS_QUERY_RESULTS),
        ]:
            try:
                await self.js.add_stream(name=name, subjects=[subject])
            except Exception:
                pass

    def connect_duckdb(self):
        """初始化 DuckDB 连接"""
        self.con = duckdb.connect()
        print(f"✅ [DuckDBSidecar:{self.node_id}] DuckDB 已就绪")

    def execute_query(self, sql: str) -> tuple[list[tuple], float]:
        """
        执行 DuckDB 查询

        Returns:
            (rows, execution_time_ms)
        """
        if self.con is None:
            raise RuntimeError("DuckDB 连接未初始化，请先调用 connect_duckdb()")
        import time
        start = time.time()

        result = self.con.execute(sql)
        rows = result.fetchall()

        elapsed = (time.time() - start) * 1000
        return rows, round(elapsed, 2)

    def build_evidence_from_rows(
        self, rows: list[tuple], query_id: str
    ) -> list[dict]:
        """
        将查询结果转换为轻量级证据（不上传全量日志）

        只返回：evidence_id, query_id, raw_ref, lineage_id, hash, 关键字段
        """
        evidence_list = []

        for row in rows:
            # 计算哈希用于溯源
            raw_str = "|".join([str(f) for f in row])
            row_hash = hashlib.sha256(raw_str.encode()).hexdigest()[:16]

            # 提取关键字段
            timestamp_field = row[0].isoformat() if hasattr(row[0], 'isoformat') else str(row[0])

            evidence = {
                "evidence_id": f"ev-{uuid.uuid4().hex[:8]}",
                "query_id": query_id,
                "node_id": self.node_id,
                "raw_ref": f"{self.node_id}/wazuh-alerts#{timestamp_field}",
                "lineage_id": f"{query_id}:{row_hash}",
                "hash": row_hash,
                "timestamp": timestamp_field,
                # 关键字段（不全量）
                "rule_id": row[1].get("id") if len(row) > 1 and isinstance(row[1], dict) else str(row[1]),
                "agent_name": row[2].get("name") if len(row) > 2 and isinstance(row[2], dict) else str(row[2]),
                "src_ip": row[2].get("ip") if len(row) > 2 and isinstance(row[2], dict) else "",
                "rows_returned": len(rows),
            }
            evidence_list.append(evidence)

        return evidence_list

    async def handle_query_request(self, msg):
        """
        处理来自中心的查询请求
        """
        self.stats["received"] += 1
        request = None

        try:
            request = json.loads(msg.data.decode())
            query_id = request.get("query_id", "unknown")
            sql = request.get("sql", "")
            source = request.get("source", "wazuh-alerts")

            print(f"📩 [DuckDBSidecar:{self.node_id}] 收到查询: {query_id}")

            if not sql:
                print(f"⚠️ [DuckDBSidecar] 查询 {query_id} 缺少 SQL 语句")
                await msg.ack()
                return

            # 执行查询
            rows, elapsed = self.execute_query(sql)

            # 构建轻量级证据
            evidence = self.build_evidence_from_rows(rows, query_id)

            # 发送结果回中心
            result_msg = {
                "query_id": query_id,
                "node_id": self.node_id,
                "source": source,
                "evidence_count": len(evidence),
                "execution_time_ms": elapsed,
                "evidence": evidence,
            }

            await self.js.publish(
                NATS_QUERY_RESULTS,
                json.dumps(result_msg).encode(),
            )

            print(f"📤 [DuckDBSidecar:{self.node_id}] 返回 {len(evidence)} 条证据 ({elapsed}ms)")

            self.stats["processed"] += 1
            await msg.ack()

        except Exception as e:
            print(f"❌ [DuckDBSidecar:{self.node_id}] 处理查询失败: {e}")
            self.stats["failed"] += 1

            # 发送错误回中心
            error_msg = {
                "query_id": request.get("query_id", "unknown") if request else "unknown",
                "node_id": self.node_id,
                "error": str(e),
            }
            if self.js is not None:
                await self.js.publish(
                    f"{NATS_QUERY_RESULTS}.error",
                    json.dumps(error_msg).encode(),
                )
            await msg.ack()

    async def _subscribe_safe(self, subject: str, durable: str):
        """订阅并自动处理残留 consumer 冲突"""
        try:
            return await self.js.subscribe(subject, durable=durable, manual_ack=True)
        except Exception:
            # consumer 残留，从对应 stream 中删除后重试
            for stream_name in ["QUERY_REQUESTS", "QUERY_RESULTS", "SIGNALS"]:
                try:
                    await self.js.delete_consumer(stream_name, durable)
                except Exception:
                    pass
            return await self.js.subscribe(subject, durable=durable, manual_ack=True)

    async def start_listening(self):
        """
        启动监听，持续处理查询请求
        """
        await self.connect_nats()
        self.connect_duckdb()

        # 订阅查询请求主题
        consumer_name = f"duckdb-sidecar-{self.node_id}"
        sub = await self._subscribe_safe(NATS_QUERY_REQUESTS, consumer_name)

        print(f"👂 [DuckDBSidecar:{self.node_id}] 开始监听 {NATS_QUERY_REQUESTS}")

        try:
            async for msg in sub.messages:
                await self.handle_query_request(msg)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self):
        """关闭连接，清理资源"""
        if self.con:
            self.con.close()
        if self.nc:
            await self.nc.close()
        print(f"📊 [DuckDBSidecar:{self.node_id}] 统计: {self.stats}")


# ====================== 独立运行入口 ======================
if __name__ == "__main__":
    async def main():
        engine = DuckDBQueryEngine(node_id=DEFAULT_NODE_ID)
        try:
            await engine.start_listening()
        except KeyboardInterrupt:
            print("\n🛑 [DuckDBSidecar] 收到中断信号")
            await engine.shutdown()

    asyncio.run(main())