# MVP/server/monitor_dashboard.py
"""
AI-SOC 实时事件监控仪表盘
==========================
通过 NATS 核心 Pub/Sub 订阅 `soc.monitor.events`，实时展示
Client 和 Server 两侧的所有事件。

启动: streamlit run MVP/server/monitor_dashboard.py --server.port 8502
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import deque
from typing import Any

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import NATS_SERVERS, NATS_MONITOR_EVENTS
from common.nats_utils import get_nats

# ═══════════════════════════════════════════════════════════════
# 事件面板定义
# ═══════════════════════════════════════════════════════════════

CLIENT_EVENTS = {"signal.sent", "query.received", "query.executed", "result.sent"}
SERVER_EVENTS = {"signal.received", "query.sent", "result.received", "evidence.saved"}

PANEL_SPECS: dict[str, tuple[str, list[str]]] = {
    "signal.sent":       ("📤 信号已发送",     ["时间", "信号ID", "规则", "目标节点"]),
    "query.received":    ("📥 查询已接收",     ["时间", "查询ID"]),
    "query.executed":    ("🔍 查询已执行",     ["时间", "查询ID", "耗时ms", "证据数"]),
    "result.sent":       ("📤 结果已发送",     ["时间", "查询ID", "证据数", "耗时ms"]),
    "signal.received":   ("📥 信号已接收",     ["时间", "信号ID", "来源节点", "规则"]),
    "query.sent":        ("📤 查询已下发",     ["时间", "查询ID", "信号ID", "目标节点"]),
    "result.received":   ("📥 结果已接收",     ["时间", "查询ID", "来源节点", "证据数"]),
    "evidence.saved":    ("💾 证据已保存",     ["时间", "查询ID", "证据数"]),
}

# ═══════════════════════════════════════════════════════════════
# 持久化的 NATS 事件收集器（跨 Streamlit re-run 存活的单例）
# ═══════════════════════════════════════════════════════════════

class _NatsCollector:
    """后台线程：持续从 NATS 收集监控事件到队列中。
    使用 @st.cache_resource 保持跨 re-run 存活，只初始化一次。"""

    def __init__(self):
        self.queue: deque = deque(maxlen=1000)
        self.status: str = "connecting"
        self.errors: list[str] = []
        self._thread_started: bool = False
        self._start_thread()

    def _start_thread(self):
        import threading
        self._thread_started = True
        t = threading.Thread(target=self._run_loop, daemon=True)
        t.start()

    def _run_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._subscribe())
        except Exception as e:
            self.errors.append(f"Loop: {e}")
            self.status = "disconnected"

    async def _subscribe(self):
        nats = get_nats()
        try:
            nc = await nats.connect(
                servers=NATS_SERVERS,
                name="monitor-dashboard",
                reconnect_time_wait=2,
                max_reconnect_attempts=-1,
            )
        except Exception as e:
            self.errors.append(f"Connect: {e}")
            self.status = "disconnected"
            return

        self.status = "connected"
        sub = await nc.subscribe(NATS_MONITOR_EVENTS)
        async for msg in sub.messages:
            try:
                event = json.loads(msg.data.decode())
                self.queue.append(event)
            except json.JSONDecodeError:
                self.errors.append("Decode error")

    def drain(self) -> list[dict[str, Any]]:
        """一次性取出队列中所有事件"""
        events: list[dict[str, Any]] = []
        while self.queue:
            events.append(self.queue.popleft())
        return events

    def publish_test(self):
        """通过 NATS 发送自检事件"""
        async def _pub():
            nats = get_nats()
            nc = await nats.connect(servers=NATS_SERVERS, name="dashboard-test")
            event = {
                "event_id": "evt-self-test",
                "event_type": "signal.sent",
                "source": "client",
                "component": "DashboardSelfTest",
                "node_id": "test",
                "timestamp": time.strftime("%H:%M:%S"),
                "payload": {
                    "signal_id": "sig-self-test", "rule_id": "9999",
                    "rule_level": 1, "node_id": "test",
                    "rule_desc": "Dashboard自检事件",
                },
            }
            await nc.publish(NATS_MONITOR_EVENTS,
                             json.dumps(event, ensure_ascii=False).encode())
            await nc.close()
        asyncio.run(_pub())


@st.cache_resource
def _get_collector() -> _NatsCollector:
    """确保后台 NATS 收集器只创建一次（Streamlit cache_resource）"""
    return _NatsCollector()


# ═══════════════════════════════════════════════════════════════
# 渲染辅助
# ═══════════════════════════════════════════════════════════════

def _format_event(event: dict) -> dict:
    p = event.get("payload", {})
    row: dict[str, Any] = {"时间": event.get("timestamp", "")}
    etype = event.get("event_type", "")

    if etype in ("signal.sent", "signal.received"):
        row["信号ID"] = p.get("signal_id", "-")
        row["规则"] = p.get("rule_id", "-")
        key = "目标节点" if etype == "signal.sent" else "来源节点"
        row[key] = p.get("node_id", "-")
    elif etype == "query.sent":
        row["查询ID"] = p.get("query_id", "-")
        row["信号ID"] = p.get("signal_id", "-")
        row["目标节点"] = p.get("node_id", "-")
    elif etype == "query.received":
        row["查询ID"] = p.get("query_id", "-")
    elif etype == "query.executed":
        row["查询ID"] = p.get("query_id", "-")
        row["耗时ms"] = p.get("duration_ms", 0)
        row["证据数"] = p.get("evidence_count", 0)
    elif etype == "result.sent":
        row["查询ID"] = p.get("query_id", "-")
        row["证据数"] = p.get("evidence_count", 0)
        row["耗时ms"] = p.get("duration_ms", 0)
    elif etype == "result.received":
        row["查询ID"] = p.get("query_id", "-")
        row["来源节点"] = p.get("node_id", "-")
        row["证据数"] = p.get("evidence_count", 0)
    elif etype == "evidence.saved":
        row["查询ID"] = p.get("query_id", "-")
        row["证据数"] = p.get("evidence_count", 0)
    return row


def _render_panel(etype: str, events_subset: list[dict]):
    title, _cols = PANEL_SPECS.get(etype, (etype, ["时间"]))
    total = len(events_subset)

    with st.container(border=True):
        st.markdown(f"**{title}**  — *{total}* 条")
        if not events_subset:
            st.caption("等待事件...")
            return

        recent = events_subset[-10:]
        rows = [_format_event(e) for e in reversed(recent)]
        try:
            st.dataframe(rows, height=180, width='stretch')
        except Exception as e:
            st.warning(f"渲染失败: {e}")


def _render_event_log(all_events: list[dict]):
    with st.expander("📋 实时事件日志 (最新 50 条)", expanded=False):
        if not all_events:
            st.caption("暂无事件")
            return
        rows = []
        for e in reversed(all_events[-50:]):
            rows.append({
                "时间": e.get("timestamp", ""),
                "类型": e.get("event_type", ""),
                "来源": e.get("source", ""),
                "组件": e.get("component", ""),
                "数据": json.dumps(e.get("payload", {}), ensure_ascii=False)[:80],
            })
        try:
            st.dataframe(rows, height=250, width='stretch')
        except Exception as e:
            st.warning(f"日志渲染失败: {e}")


# ═══════════════════════════════════════════════════════════════
# 主页面
# ═══════════════════════════════════════════════════════════════

def main():
    st.set_page_config(page_title="AI-SOC 实时监控", page_icon="🔴", layout="wide")

    # 自动刷新
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=2000, key="monitor")
    except ImportError:
        st.error("需要 `pip install streamlit-autorefresh`")
        st.stop()

    # 获取持久化的收集器单例
    collector = _get_collector()

    # 将后台线程收集的事件转移到 session_state
    if "events" not in st.session_state:
        st.session_state.events = []
    st.session_state.events.extend(collector.drain())

    all_events: list[dict] = st.session_state.events

    # ── 分类 ──
    client_events: dict[str, list[dict]] = {}
    server_events: dict[str, list[dict]] = {}
    for etype in PANEL_SPECS:
        subset = [e for e in all_events if e.get("event_type") == etype]
        if etype in CLIENT_EVENTS:
            client_events[etype] = subset
        else:
            server_events[etype] = subset

    total_client = sum(len(v) for v in client_events.values())
    total_server = sum(len(v) for v in server_events.values())

    # ── 顶栏 ──
    status = collector.status
    icon = {"connected": "🟢", "connecting": "🟡", "disconnected": "🔴"}.get(status, "⚪")
    label = {"connected": "已连接", "connecting": "连接中...", "disconnected": "已断开"}.get(status, status)

    c1, c2, c3 = st.columns([2, 2, 1])
    c1.markdown(f"**{icon} NATS: {label}**")
    c2.markdown(f"事件总计 **{total_client + total_server}** — Client **{total_client}** | Server **{total_server}**")
    if c3.button("🔧 自检", width='stretch', help="发送测试事件验证管道"):
        collector.publish_test()
        st.success("测试事件已发送，2秒后刷新面板")

    if collector.errors:
        for err in collector.errors[-3:]:
            st.caption(f"⚠️ {err}")

    st.caption(f"主题 `{NATS_MONITOR_EVENTS}` · 每 2 秒自动刷新")
    st.divider()

    # ── 双列 ──
    left, right = st.columns(2)

    with left:
        st.subheader("🖥️ Client 事件")
        for etype in ["signal.sent", "query.received", "query.executed", "result.sent"]:
            _render_panel(etype, client_events.get(etype, []))

    with right:
        st.subheader("🖧 Server 事件")
        for etype in ["signal.received", "query.sent", "result.received", "evidence.saved"]:
            _render_panel(etype, server_events.get(etype, []))

    st.divider()
    _render_event_log(all_events)


if __name__ == "__main__":
    main()
