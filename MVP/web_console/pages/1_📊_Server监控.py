"""
Server 监控 — NATS 实时事件流 + OpenSearch 状态
"""

import asyncio
import json
import os
import sys
import time
from collections import deque

import yaml
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import NATS_SERVERS, NATS_MONITOR_EVENTS, OPENSEARCH_HOST, OPENSEARCH_PORT, CLIENT_CONFIG_PATH
from common.nats_utils import get_nats

st.set_page_config(page_title="Server监控", page_icon="📊", layout="wide")

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from auth import require_auth
require_auth()

PANEL_SPECS = {
    "signal.received":   ("📥 信号已接收", ["时间", "信号ID", "来源节点", "规则"]),
    "query.sent":        ("📤 查询已下发", ["时间", "查询ID", "信号ID", "目标节点"]),
    "result.received":   ("📥 结果已接收", ["时间", "查询ID", "来源节点", "证据数"]),
    "evidence.saved":    ("💾 证据已保存", ["时间", "查询ID", "证据数", "节点"]),
    "signal.sent":       ("📤 Client信号", ["时间", "信号ID", "规则", "节点"]),
    "query.executed":    ("🔍 查询已执行", ["时间", "查询ID", "耗时ms", "证据数"]),
}

# ── NATS 收集器 ──

class _NatsCollector:
    def __init__(self):
        self.queue: deque = deque(maxlen=1000)
        self.status = "connecting"
        self._start()

    def _start(self):
        import threading
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._subscribe())
        except Exception:
            self.status = "disconnected"

    async def _subscribe(self):
        nats = get_nats()
        try:
            nc = await nats.connect(servers=NATS_SERVERS, name="web-monitor",
                                    reconnect_time_wait=2, max_reconnect_attempts=-1)
        except Exception:
            self.status = "disconnected"
            return
        self.status = "connected"
        sub = await nc.subscribe(NATS_MONITOR_EVENTS)
        async for msg in sub.messages:
            try:
                self.queue.append(json.loads(msg.data.decode()))
            except json.JSONDecodeError:
                pass

    def drain(self):
        events = []
        while self.queue:
            events.append(self.queue.popleft())
        return events


@st.cache_resource
def _get_collector():
    return _NatsCollector()


def _format_event(event):
    p = event.get("payload", {})
    row = {"时间": event.get("timestamp", "")}
    etype = event.get("event_type", "")
    if etype in ("signal.sent", "signal.received"):
        row["信号ID"] = p.get("signal_id", "-")
        row["规则"] = p.get("rule_id", "-")
        key = "节点" if etype == "signal.sent" else "来源节点"
        row[key] = p.get("node_id", "-")
    elif etype == "query.sent":
        row["查询ID"] = p.get("query_id", "-")
        row["信号ID"] = p.get("signal_id", "-")
        row["目标节点"] = p.get("node_id", "-")
    elif etype == "query.executed":
        row["查询ID"] = p.get("query_id", "-")
        row["耗时ms"] = p.get("duration_ms", 0)
        row["证据数"] = p.get("evidence_count", 0)
    elif etype == "result.received":
        row["查询ID"] = p.get("query_id", "-")
        row["来源节点"] = p.get("node_id", "-")
        row["证据数"] = p.get("evidence_count", 0)
    elif etype == "evidence.saved":
        row["查询ID"] = p.get("query_id", "-")
        row["证据数"] = p.get("evidence_count", 0)
        row["节点"] = p.get("node_id", "-")
    return row


# ── 自动刷新 ──
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=2000, key="server_monitor_refresh")
except ImportError:
    st.error("需要 pip install streamlit-autorefresh")

# ── 页面 ──
st.title("📊 Server 实时监控")
st.caption(f"主题 `{NATS_MONITOR_EVENTS}` · 每2秒自动刷新")

collector = _get_collector()

if "server_events" not in st.session_state:
    st.session_state.server_events = []
st.session_state.server_events.extend(collector.drain())

all_events = st.session_state.server_events

# ── 状态栏 ──
status_icon = {"connected": "🟢", "connecting": "🟡", "disconnected": "🔴"}.get(collector.status, "⚪")
c1, c2, c3 = st.columns([2, 2, 1])
c1.markdown(f"**{status_icon} NATS: {collector.status}**")
total = len(all_events)
c2.markdown(f"累计事件: **{total}** 条")

# OpenSearch 状态
try:
    import requests
    r = requests.get(f"http://{OPENSEARCH_HOST}:{OPENSEARCH_PORT}/_cluster/health", timeout=2)
    if r.ok:
        health = r.json()
        c3.markdown(f"OpenSearch: **{health.get('status', '?')}** | 节点: {health.get('number_of_nodes', '?')}")
except Exception:
    c3.markdown("OpenSearch: 🔴 不可达")

if c3.button("🗑️ 清空", width='stretch'):
    st.session_state.server_events = []

st.divider()

# ── Server 事件面板 ──
st.subheader("🖧 Server 事件流")
cols = st.columns(2)
server_types = ["signal.received", "query.sent", "result.received", "evidence.saved"]
for i, etype in enumerate(server_types):
    subset = [e for e in all_events if e.get("event_type") == etype]
    title, _ = PANEL_SPECS.get(etype, (etype,))
    with cols[i % 2]:
        with st.container(border=True):
            st.markdown(f"**{title}** — *{len(subset)}* 条")
            if subset:
                rows = [_format_event(e) for e in reversed(subset[-10:])]
                st.dataframe(rows, height=160, width='stretch')

st.divider()

# ── Client 事件面板（按客户端分别展示）──
st.subheader("🖥️ Client 事件（按客户端分开）")

# 读取 node_id → client_id 映射
node_to_client = {}
if os.path.exists(CLIENT_CONFIG_PATH):
    with open(CLIENT_CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f)
    for c in cfg.get("clients", []):
        node_to_client[c.get("node_id", "")] = c.get("client_id", "?")

# 提取所有 client 节点并分类事件
client_nodes = {}
for e in all_events:
    if e.get("event_type") in ("signal.sent", "query.executed"):
        nid = e.get("payload", {}).get("node_id", "?")
        if nid not in client_nodes:
            client_nodes[nid] = {"signal.sent": [], "query.executed": []}
        client_nodes[nid][e.get("event_type")].append(e)

if not client_nodes:
    st.info("暂无 Client 事件")
else:
    for node_id in sorted(client_nodes.keys()):
        node_events = client_nodes[node_id]
        total_node = sum(len(v) for v in node_events.values())
        signal_count = len(node_events["signal.sent"])
        query_count = len(node_events["query.executed"])

        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            client_label = f"{node_to_client.get(node_id, '?')} → {node_id}"
            c1.markdown(f"### 🖥️ {client_label}")
            c1.caption(f"信号: {signal_count} | 查询执行: {query_count} | 总计: {total_node} 条事件")

            # 最近信号
            if node_events["signal.sent"]:
                recent_signal = node_events["signal.sent"][-1]
                sig_payload = recent_signal.get("payload", {})
                c2.metric("最近信号", sig_payload.get("rule_id", "?"))
                c2.caption(recent_signal.get("timestamp", ""))

            # 事件详情表格
            all_node_events = node_events["signal.sent"] + node_events["query.executed"]
            all_node_events.sort(key=lambda e: e.get("timestamp", ""))
            recent = all_node_events[-5:]
            rows = []
            for e in reversed(recent):
                p = e.get("payload", {})
                rows.append({
                    "时间": e.get("timestamp", ""),
                    "类型": e.get("event_type", ""),
                    "信号/查询ID": p.get("signal_id") or p.get("query_id", "-"),
                    "规则": p.get("rule_id", "-"),
                    "证据数": p.get("evidence_count", ""),
                })
            st.dataframe(rows, height=120, width='stretch')

# ── 事件日志 ──
st.divider()
with st.expander("📋 原始事件日志 (最新 100 条)", expanded=False):
    if not all_events:
        st.caption("暂无事件")
    else:
        log_rows = []
        for e in reversed(all_events[-100:]):
            log_rows.append({
                "时间": e.get("timestamp", ""),
                "类型": e.get("event_type", ""),
                "来源": e.get("source", ""),
                "组件": e.get("component", ""),
                "数据": json.dumps(e.get("payload", {}), ensure_ascii=False)[:100],
            })
        st.dataframe(log_rows, height=300, width='stretch')
