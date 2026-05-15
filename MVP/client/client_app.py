#!/usr/bin/env python3
"""
AI-SOC Client 统一入口
======================
整合所有客户端边缘组件:
  - DuckDB Sidecar (监听 NATS 查询请求, 本地执行 DuckDB 查询)
  - Signal Watcher (持续监控 Wazuh alerts.json, 实时生成微信号)

在独立终端窗口中运行，展示边缘侧实时处理过程。

实时数据流:
  Wazuh Agent → Wazuh Manager → alerts.json (NDJSON, 逐行追加)
    → SignalWatcher 检测新行 → 解析 → 生成信号 → NATS
    → Server Signal Listener 收到 → 触发查询 → NATS
    → DuckDB Sidecar 执行查询 → 返回证据 → NATS
    → Server 接收、持久化、展示
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    NATS_SERVERS,
    NATS_QUERY_REQUESTS,
    NATS_QUERY_RESULTS,
    NATS_SIGNAL_SUBJECT,
    DEFAULT_RULE_ID,
    DEFAULT_NODE_ID,
    ALERTS_JSON_PATH,
)

# 文件监控轮询间隔（秒）
WATCH_INTERVAL = 2


# ====================== 日志工具 ======================

def client_log(msg: str):
    print(f"[Client] {msg}", flush=True)


# ====================== DuckDB 查询引擎 ======================

class SidecarQueryEngine:
    """边缘 DuckDB 查询引擎 - 响应中心下发的查询请求"""

    def __init__(self, node_id: str = DEFAULT_NODE_ID):
        self.node_id = node_id
        self.nc = None
        self.js = None
        self.con = None
        self.stats = {"received": 0, "processed": 0, "failed": 0}

    async def connect_nats(self):
        import nats
        self.nc = await nats.connect(servers=NATS_SERVERS, name=f"sidecar-{self.node_id}")
        self.js = self.nc.jetstream()
        await self._ensure_streams()

    async def _ensure_streams(self):
        for name, subject in [
            ("QUERY_REQUESTS", NATS_QUERY_REQUESTS),
            ("QUERY_RESULTS", NATS_QUERY_RESULTS),
        ]:
            try:
                await self.js.add_stream(name=name, subjects=[subject])
            except Exception:
                pass

    def connect_duckdb(self):
        import duckdb
        self.con = duckdb.connect()

    async def handle_query(self, msg):
        self.stats["received"] += 1
        request = None

        try:
            request = json.loads(msg.data.decode())
            query_id = request.get("query_id", "unknown")
            sql = request.get("sql", "")
            source = request.get("source", "wazuh_alerts")

            client_log(f"收到查询: {query_id} | 来源={source}")

            if not sql or not sql.strip():
                if "filters" in request:
                    sql = self._build_sql(request)
                else:
                    client_log(f"查询 {query_id} 缺少 SQL，跳过")
                    await msg.ack()
                    return

            t0 = time.time()
            result = self.con.execute(sql)
            rows = result.fetchall()
            elapsed = (time.time() - t0) * 1000

            evidence = []
            for row in rows:
                ts = row[0].isoformat() if hasattr(row[0], 'isoformat') else str(row[0])
                row_hash = abs(hash(str(row))) % (2**32)
                evidence.append({
                    "evidence_id": f"ev-{uuid.uuid4().hex[:8]}",
                    "query_id": query_id,
                    "node_id": self.node_id,
                    "raw_ref": f"{self.node_id}/{source}#{ts}",
                    "lineage_id": f"{query_id}:{row_hash}",
                    "hash": str(row_hash),
                    "timestamp": ts,
                    "rule_id": row[1].get("id") if len(row) > 1 and isinstance(row[1], dict) else str(row[1]) if len(row) > 1 else "",
                    "agent_name": row[2].get("name") if len(row) > 2 and isinstance(row[2], dict) else str(row[2]) if len(row) > 2 else "",
                    "src_ip": row[2].get("ip") if len(row) > 2 and isinstance(row[2], dict) else "",
                    "rows_returned": len(rows),
                })

            result_msg = {
                "query_id": query_id,
                "node_id": self.node_id,
                "source": source,
                "evidence_count": len(evidence),
                "execution_time_ms": round(elapsed, 2),
                "evidence": evidence,
            }

            await self.js.publish(NATS_QUERY_RESULTS, json.dumps(result_msg).encode())
            self.stats["processed"] += 1
            client_log(f"返回证据: {query_id} | {len(evidence)} 条 | {elapsed:.1f}ms")
            await msg.ack()

        except Exception as e:
            client_log(f"查询处理失败: {e}")
            self.stats["failed"] += 1
            try:
                error_msg = {
                    "query_id": request.get("query_id", "unknown") if request else "unknown",
                    "node_id": self.node_id,
                    "error": str(e),
                }
                await self.js.publish(f"{NATS_QUERY_RESULTS}.error", json.dumps(error_msg).encode())
                await msg.ack()
            except Exception:
                pass

    def _build_sql(self, request: dict) -> str:
        fields = request.get("fields", ["*"])
        fields_str = ", ".join(fields) if fields != ["*"] else "*"
        data_path = ALERTS_JSON_PATH
        sql = f"SELECT {fields_str} FROM read_json_auto('{data_path}')"
        where_clauses = []
        rule_id = request.get("filters", {}).get("rule.id") or request.get("filters", {}).get("rule_id")
        if rule_id:
            where_clauses.append(f"\"rule\".\"id\" = '{rule_id}'")
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        sql += f" LIMIT {request.get('limit', 20)}"
        return sql

    async def _subscribe_safe(self, subject, durable):
        try:
            return await self.js.subscribe(subject, durable=durable, manual_ack=True)
        except Exception:
            for stream_name in ["QUERY_REQUESTS"]:
                try:
                    await self.js.delete_consumer(stream_name, durable)
                except Exception:
                    pass
            return await self.js.subscribe(subject, durable=durable, manual_ack=True)

    async def start(self):
        await self.connect_nats()
        self.connect_duckdb()
        client_log(f"DuckDB Sidecar 已就绪 (节点: {self.node_id})")

        sub = await self._subscribe_safe(NATS_QUERY_REQUESTS, f"sidecar-{self.node_id}")
        client_log(f"开始监听查询请求: {NATS_QUERY_REQUESTS}")

        async for msg in sub.messages:
            await self.handle_query(msg)

    async def shutdown(self):
        if self.con:
            self.con.close()
        if self.nc:
            await self.nc.close()
        client_log(f"Sidecar 已关闭 (收到={self.stats['received']} "
                   f"处理={self.stats['processed']} 失败={self.stats['failed']})")


# ====================== 实时信号监控器 ======================

class SignalWatcher:
    """
    实时文件监控器 - 持续监控 alerts.json (NDJSON) 的新增告警

    工作原理:
      1. 启动时读取现有全部告警, 生成初始信号批次
      2. 记录文件末尾位置 (byte offset)
      3. 每隔 WATCH_INTERVAL 秒检查文件是否增长
      4. 读取新增行 → 解析 → 生成信号 → 发布到 NATS
      5. 通过 alert.id 去重, 避免重复发送

    这使得 SOC 可以实时响应 Wazuh Agent → Manager → alerts.json 的数据流。
    """

    def __init__(self, node_id: str = DEFAULT_NODE_ID):
        self.node_id = node_id
        self.file_path = ALERTS_JSON_PATH
        self.nc = None
        self.js = None
        self.seen_ids: set = set()       # 已处理告警 ID, 去重用
        self.last_offset: int = 0          # 上次读取到的字节偏移
        self.stats = {"initial": 0, "new": 0, "errors": 0}
        self._running = False

    async def connect(self):
        import nats
        self.nc = await nats.connect(servers=NATS_SERVERS, name=f"watcher-{self.node_id}")
        self.js = self.nc.jetstream()
        try:
            await self.js.add_stream(name="SIGNALS", subjects=[f"{NATS_SIGNAL_SUBJECT}.*"])
        except Exception:
            pass
        client_log(f"SignalWatcher NATS 已连接")

    def _read_new_lines(self) -> list[dict]:
        """
        从文件末尾读取新增的 NDJSON 行

        Returns:
            新告警列表 (尚未处理的)
        """
        if not os.path.exists(self.file_path):
            return []

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                f.seek(0, 2)  # 移到文件末尾
                current_size = f.tell()

                if current_size <= self.last_offset:
                    return []  # 文件未增长或变小 (被轮转)

                # 从上次位置读取新内容
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
            client_log(f"读取告警文件失败: {e}")
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
            count += 1
            client_log(f"已发送信号: {sig['signal_id']} "
                       f"| 规则={sig['rule_id']}(Lv{sig['rule_level']}) "
                       f"| {sig['rule_desc'][:40]} "
                       f"-> {subject}")
        if count > 0:
            client_log(f"[{label}] 信号批次发布完成: {count} 条")
        return count

    async def run_forever(self):
        """持续监控文件变化并发布新信号"""
        await self.connect()
        self._running = True

        # Phase 1: 读取现有告警作为初始批次
        if os.path.exists(self.file_path):
            # 先扫描全部现有告警, 填充 seen_ids 并发布初始信号
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
                    # 只发布最后 20 条作为初始信号（避免启动时海量发送）
                    recent = initial_alerts[-20:]
                    signals = [self._alert_to_signal(a) for a in recent]
                    await self.publish_batch(signals, label=f"初始批次 ({len(initial_alerts)} 条历史告警)")
                    self.stats["initial"] = len(signals)
                else:
                    client_log("告警文件中无有效记录，等待新数据...")
            except Exception as e:
                client_log(f"初始扫描失败: {e}")

        client_log(f"开始实时监控告警文件: {self.file_path}")
        client_log(f"  轮询间隔: {WATCH_INTERVAL}s | 已跟踪: {len(self.seen_ids)} 条告警")

        # Phase 2: 持续监控新告警
        while self._running:
            try:
                new_alerts = self._read_new_lines()
                if new_alerts:
                    signals = [self._alert_to_signal(a) for a in new_alerts]
                    await self.publish_batch(signals, label=f"实时 ({len(new_alerts)} 条新告警)")
                    self.stats["new"] += len(signals)
                elif self.stats["new"] == 0 and self.stats["initial"] == 0:
                    # 尚无任何信号发出时给出提示
                    pass

                await asyncio.sleep(WATCH_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                client_log(f"监控循环异常: {e}")
                await asyncio.sleep(WATCH_INTERVAL)

    async def shutdown(self):
        self._running = False
        if self.nc:
            await self.nc.close()
        client_log(f"SignalWatcher 已关闭 "
                   f"(初始={self.stats['initial']} 实时新增={self.stats['new']} 错误={self.stats['errors']})")


# ====================== 主入口 ======================

async def run_client():
    """启动所有客户端组件 - Sidecar + Watcher 并发运行"""
    client_log("=" * 56)
    client_log("  AI-SOC Client - 边缘侧实时安全数据采集")
    client_log("=" * 56)
    client_log(f"启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    client_log(f"边缘节点: {DEFAULT_NODE_ID}")
    client_log(f"NATS 服务器: {NATS_SERVERS}")
    client_log(f"告警文件: {ALERTS_JSON_PATH}")
    client_log(f"监控间隔: {WATCH_INTERVAL}s")
    client_log("")

    # 检查告警文件
    if not os.path.exists(ALERTS_JSON_PATH):
        client_log(f"告警文件不存在: {ALERTS_JSON_PATH}")
        client_log("等待 Wazuh Manager 写入告警...")
        client_log("")

    # Sidecar + Watcher 并发运行
    sidecar = SidecarQueryEngine(node_id=DEFAULT_NODE_ID)
    watcher = SignalWatcher(node_id=DEFAULT_NODE_ID)

    sidecar_task = asyncio.create_task(sidecar.start(), name="sidecar")
    watcher_task = asyncio.create_task(watcher.run_forever(), name="watcher")

    client_log("边缘引擎全部就绪:")
    client_log(f"  - DuckDB Sidecar : 监听 {NATS_QUERY_REQUESTS}")
    client_log(f"  - Signal Watcher  : 监控 {os.path.basename(ALERTS_JSON_PATH)} (间隔 {WATCH_INTERVAL}s)")
    client_log("")
    client_log("实时数据流已启动:")
    client_log("  Wazuh → alerts.json → Watcher → 信号 → NATS → Server")
    client_log("  Server → 查询请求 → NATS → Sidecar → DuckDB查询 → 证据返回")
    client_log("")
    client_log("按 Ctrl+C 停止")
    client_log("=" * 56)

    # 等待任意任务结束
    tasks = [sidecar_task, watcher_task]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await sidecar.shutdown()
    await watcher.shutdown()

    client_log("所有客户端组件已关闭")


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    main_task = loop.create_task(run_client())

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
        loop.close()


if __name__ == "__main__":
    main()
