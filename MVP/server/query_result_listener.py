# MVP/server/query_result_listener.py
"""
查询结果监听器 (Query Result Listener)
========================================
职责：
  1. 监听 NATS 上边缘返回的查询结果
  2. 将证据持久化到本地 outputs 目录
  3. 触发自动研判流水线: 数据就绪度 → Agent Team → 复核 → 报告
  4. 记录接收统计

对应实现方案：第四章 - 边缘按需查询（结果接收端）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import NATS_SERVERS, NATS_QUERY_RESULTS, OUTPUTS_DIR
from common.nats_utils import get_nats, subscribe_safe, safe_ack, ensure_stream, MAX_DELIVERY_ATTEMPTS
from common.monitor_events import MonitorEmitter


class QueryResultListener:
    """监听边缘返回的查询结果并持久化，触发自动研判流水线"""

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
            query_id = result.get("query_id", "?")
            node_id = result.get("node_id", "?")
            evidence_count = result.get("evidence_count", 0)
            print(f"[ResultListener] 收到查询结果: {query_id} "
                  f"| 证据={evidence_count} 条 "
                  f"| 耗时={result.get('execution_time_ms')}ms "
                  f"| 节点={node_id}")
            if self.monitor:
                await self.monitor.result_received(query_id=query_id,
                                                   node_id=node_id,
                                                   evidence_count=evidence_count)

            # OCSF 标准化映射
            from common.ocsf_mapper import map_authlog_to_ocsf
            source = result.get("source", "")
            evidence_list = result.get("evidence", [])
            if source == "auth_log":
                evidence_list = [map_authlog_to_ocsf(ev) for ev in evidence_list]

            # 持久化证据到本地 outputs 目录
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            output_dir = os.path.join(OUTPUTS_DIR, timestamp)
            os.makedirs(output_dir, exist_ok=True)
            evidence_path = os.path.join(output_dir, "evidence.json")
            with open(evidence_path, "w", encoding="utf-8") as f:
                json.dump(evidence_list, f, indent=2, ensure_ascii=False)
            print(f"[ResultListener] 证据已保存: {evidence_path}")

            # 持久化到 OpenSearch (best-effort)
            if evidence_list:
                try:
                    from .opensearch_loader import OpenSearchClient
                    os_client = OpenSearchClient()
                    for doc in evidence_list:
                        os_client.index("soc-evidence", doc)
                    print(f"[ResultListener] {len(evidence_list)} 条证据已索引入 OpenSearch")
                except Exception as e:
                    print(f"[ResultListener] OpenSearch 索引失败 (best-effort): {e}")

            if self.monitor:
                await self.monitor.evidence_saved(query_id=query_id,
                                                  evidence_count=evidence_count,
                                                  path=evidence_path)

            # 触发异步研判流水线（不阻塞 ACK）
            if evidence_list:
                asyncio.create_task(self._run_analysis_pipeline(evidence_list, timestamp, node_id))

        acked = await safe_ack(msg, on_success=_process)
        if acked:
            self.results_received += 1
        else:
            md = getattr(msg, 'metadata', None)
            attempts = getattr(md, 'num_delivered', 1) if md else 1
            print(f"[ResultListener] 处理失败，重试中 ({attempts}/{MAX_DELIVERY_ATTEMPTS})")

    async def _run_analysis_pipeline(
        self,
        evidence: list,
        timestamp: str,
        node_id: str,
    ):
        """
        自动研判流水线: 数据就绪度 → Agent Team → 复核 → 报告
        以异步后台任务运行，不阻塞主消息循环。
        """
        print(f"\n{'='*50}")
        print(f"[Pipeline] 自动研判流水线启动 | 节点={node_id} | 证据={len(evidence)}条")
        print(f"{'='*50}")

        output_dir = os.path.join(OUTPUTS_DIR, timestamp)

        # ---- Step 1: 数据就绪度检查 ----
        print(f"\n[Pipeline:Step 1] 数据质量门控 (Readiness)...")
        try:
            from .readiness import calculate_readiness
            readiness = calculate_readiness(timestamp=timestamp)
            print(f"   ✅ 就绪度: {readiness.get('score')}分 ({readiness.get('level')})")
        except Exception as e:
            print(f"   ⚠️ 就绪度检查失败: {e}")
            readiness = {"score": 0, "level": "严重不足"}

        # ---- Step 2: Agent Team 多Agent研判 ----
        print(f"\n[Pipeline:Step 2] Agent Team 多Agent研判...")
        try:
            from .agent_team import run_analysis
            draft = run_analysis(evidence, timestamp)
            print(f"   ✅ 分诊: {draft['triage']['event_type']} (优先级: {draft['triage']['priority']})")

            # 构建 agent_result.json（格式兼容 verifier / report_generator）
            agent_result = {
                "summary": draft["triage"]["summary"],
                "timeline": [],
                "conclusion": (
                    f"{draft['triage']['summary']}"
                    f"攻击链状态: {draft.get('attack_chain', {}).get('progress', '未分析')}"
                ),
                "confidence": draft["triage"]["confidence"],
                "actions": draft["suggested_actions"],
                "evidence_ref": draft["evidence_ref"],
                "event_type": draft["triage"]["event_type"],
                "priority": draft["triage"]["priority"],
                "node_id": node_id,
            }
            with open(os.path.join(output_dir, "agent_result.json"), "w", encoding="utf-8") as f:
                json.dump(agent_result, f, indent=2, ensure_ascii=False)
            print(f"   📄 agent_result.json 已保存")
        except Exception as e:
            print(f"   ⚠️ Agent Team 研判失败: {e}")
            traceback.print_exc()
            agent_result = {
                "summary": f"研判异常: {e}",
                "conclusion": "自动研判失败，需人工介入",
                "confidence": "低",
                "actions": ["人工核查"],
                "evidence_ref": [],
                "event_type": "unknown",
                "priority": "medium",
            }

        # ---- Step 3: 复核校验 (防AI幻觉) ----
        print(f"\n[Pipeline:Step 3] 复核校验 (Verifier)...")
        try:
            from .verifier import verify
            verifier_result = verify(timestamp=timestamp)
            verified = verifier_result.get("verified", False)
            print(f"   {'✅ 复核通过' if verified else '⚠️ 复核未通过'}")
            if verifier_result.get("issues"):
                for issue in verifier_result["issues"]:
                    print(f"      - {issue}")
        except Exception as e:
            print(f"   ⚠️ 复核校验失败: {e}")
            traceback.print_exc()

        # ---- Step 4: 生成研判报告 ----
        print(f"\n[Pipeline:Step 4] 研判报告生成...")
        try:
            from .report_generator import generate_report
            generate_report(timestamp=timestamp)
        except Exception as e:
            print(f"   ⚠️ 报告生成失败: {e}")
            traceback.print_exc()

        # ---- Step 5: 索引入 OpenSearch ----
        print(f"\n[Pipeline:Step 5] OpenSearch 索引...")
        try:
            # 将目录名 "20260525_184707" 转为 ISO "2026-05-25T18:47:07+08:00"
            from datetime import datetime, timezone as dt_timezone, timedelta
            ts_dt = datetime.strptime(timestamp, "%Y%m%d_%H%M%S")
            local_offset = datetime.now().astimezone().strftime('%z')
            # '+0800' → '+08:00'
            offset_str = f"{local_offset[:3]}:{local_offset[3:]}"
            iso_ts = ts_dt.isoformat() + offset_str
            from .opensearch_loader import OpenSearchClient
            os_client = OpenSearchClient()
            for filename, index_name in [
                ("readiness.json", "soc-readiness"),
                ("agent_result.json", "soc-analysis"),
                ("verifier_result.json", "soc-verification"),
            ]:
                filepath = os.path.join(output_dir, filename)
                if os.path.exists(filepath):
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        data["@timestamp"] = iso_ts
                    os_client.index(index_name, data)
                    print(f"   ✅ {index_name} 已索引")
            # 报告文件
            report_path = os.path.join(output_dir, "report.md")
            if os.path.exists(report_path):
                with open(report_path, "r", encoding="utf-8") as f:
                    report_content = f.read()
                os_client.index("soc-reports", {"content": report_content, "@timestamp": iso_ts, "node_id": node_id})
                print(f"   ✅ soc-reports 已索引")
        except Exception as e:
            print(f"   ⚠️ OpenSearch 索引失败: {e}")

        print(f"\n{'='*50}")
        print(f"[Pipeline] 自动研判流水线完成 | 节点={node_id}")
        print(f"   📂 输出目录: {output_dir}")
        print(f"{'='*50}\n")

    async def listen_forever(self):
        await self.connect()
        await ensure_stream(self.js, "QUERY_RESULTS", [NATS_QUERY_RESULTS])
        sub = await subscribe_safe(self.js, NATS_QUERY_RESULTS, "query-result-listener", stream_names=["QUERY_RESULTS"])
        print(f"[ResultListener] 开始监听查询结果: {NATS_QUERY_RESULTS}")
        print(f"[ResultListener] 自动研判流水线已启用 (Agent Team → Verifier → Report)")

        async for msg in sub.messages:
            await self.handle_result(msg)

    async def shutdown(self):
        if self.nc:
            await self.nc.close()
        print(f"[ResultListener] 已关闭 (收到={self.results_received} 条)")
