"""
AI-SOC 统一运维控制台 — 首页
=============================
系统概览：基础设施状态、已注册客户端、最近研判任务
"""

import os
import sys
import json
import time
import yaml

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import (
    OUTPUTS_DIR,
    CLIENT_CONFIG_PATH,
    NATS_SERVERS,
    OPENSEARCH_HOST,
    OPENSEARCH_PORT,
)

st.set_page_config(
    page_title="AI-SOC 控制台",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from auth import require_auth
require_auth()

# 自动刷新 (5秒)
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=5000, key="home_refresh")
except ImportError:
    pass

# ── 样式 ──
st.markdown("""
<style>
    .metric-card {
        background: linear-gradient(135deg, #1a1c23 0%, #21242e 100%);
        border: 1px solid #2d3139;
        border-radius: 12px;
        padding: 20px;
        text-align: center;
    }
    .status-ok { color: #4ade80; }
    .status-warn { color: #fbbf24; }
    .status-err { color: #f87171; }
</style>
""", unsafe_allow_html=True)

st.title("🛡️ AI-SOC 统一运维控制台")
st.markdown("##### 物理分布 · 逻辑统一 · 按需取证 · 可信研判")
st.divider()

# ═══════════════════════════════════════════════
# 基础设施状态
# ═══════════════════════════════════════════════

def check_nats():
    try:
        import socket
        host = NATS_SERVERS[0].replace("nats://", "").split(":")[0] if NATS_SERVERS else "localhost"
        port = 4222
        s = socket.socket()
        s.settimeout(2)
        s.connect((host, port))
        s.close()
        return True, "已连接"
    except Exception:
        return False, "不可达"

def check_opensearch():
    try:
        import requests
        r = requests.get(f"http://{OPENSEARCH_HOST}:{OPENSEARCH_PORT}/_cluster/health", timeout=3)
        if r.ok:
            data = r.json()
            return True, data.get("status", "green")
        return False, "无响应"
    except Exception:
        return False, "不可达"

st.subheader("🏗️ 基础设施")

nats_ok, nats_status = check_nats()
os_ok, os_status = check_opensearch()

c1, c2, c3, c4 = st.columns(4)
c1.metric("NATS", nats_status, delta=None,
          delta_color="off" if nats_ok else "inverse")
c2.metric("OpenSearch", os_status, delta=None,
          delta_color="off" if os_ok else "inverse")
c3.metric("NATS 节点", f"{len(NATS_SERVERS)}", delta=None)

# Docker 容器检查
try:
    import subprocess
    result = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                          capture_output=True, text=True, timeout=5)
    containers = result.stdout.strip().split("\n") if result.stdout.strip() else []
    running = [c for c in containers if c in ("opensearch", "nats", "dashboards")]
    c4.metric("Docker 容器", f"{len(running)}/3")
except Exception:
    c4.metric("Docker 容器", "?")
st.divider()

# ═══════════════════════════════════════════════
# 客户端状态
# ═══════════════════════════════════════════════

# 检查哪些 client 实际在运行
def get_running_client_ids():
    running = set()
    try:
        import subprocess
        result = subprocess.run(["ps", "aux", "--no-headers"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.strip().split("\n"):
            if "client_app.py" not in line or "grep" in line:
                continue
            parts = line.split(None, 10)
            if len(parts) >= 11:
                cmd = parts[10]
                if "--client-id" in cmd:
                    idx = cmd.find("--client-id") + len("--client-id")
                    cid = cmd[idx:].strip().split(None, 1)[0]
                    running.add(cid)
    except Exception:
        pass
    return running

running_ids = get_running_client_ids()

clients = []
if os.path.exists(CLIENT_CONFIG_PATH):
    with open(CLIENT_CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)
    clients = config.get("clients", [])

running_count = sum(1 for c in clients if c["client_id"] in running_ids)
st.subheader(f"🖥️ 客户端 ({len(clients)} 注册, {running_count} 运行中)")

if not clients:
    st.info("暂无已注册客户端，前往 **Client注册** 页面添加")
else:
    cols = st.columns(min(len(clients), 4))
    for i, c in enumerate(clients):
        cid = c.get("client_id", "?")
        is_running = cid in running_ids
        is_real = c.get("log_path", "").startswith("/")
        icon = "🖥️" if is_real else "📦"
        status = "🟢 运行中" if is_running else "⚫ 已停止"
        sts_color = "#4ade80" if is_running else "#666"
        cols[i % 4].markdown(f"""
        <div class="metric-card">
            <h3>{icon} {cid}</h3>
            <p style="color:{sts_color};font-weight:bold">{status}</p>
            <p><b>节点:</b> {c.get('node_id', '?')}</p>
            <p><b>日志:</b> <code>{c.get('log_path', '?')}</code></p>
            <p style="color:#888;font-size:0.8em">{c.get('description', '')}</p>
        </div>
        """, unsafe_allow_html=True)

# 如果没有任何运行中的 client，提醒用户
if running_count == 0:
    st.warning("⚠️ 没有运行中的 Client — 请前往 **🚀 运行控制** 页面启动 Client，否则不会检测安全事件")

st.divider()

# ═══════════════════════════════════════════════
# 最近研判任务
# ═══════════════════════════════════════════════

st.subheader("📋 最近研判任务")

if not os.path.exists(OUTPUTS_DIR):
    st.info("尚无研判记录")
else:
    tasks = sorted(
        [d for d in os.listdir(OUTPUTS_DIR) if os.path.isdir(os.path.join(OUTPUTS_DIR, d))],
        reverse=True,
    )[:10]

    if not tasks:
        st.info("尚无研判记录")
    else:
        rows = []
        for t in tasks:
            task_path = os.path.join(OUTPUTS_DIR, t)
            agent_file = os.path.join(task_path, "agent_result.json")
            evidence_file = os.path.join(task_path, "evidence.json")
            verifier_file = os.path.join(task_path, "verifier_result.json")

            node = "?"
            event_type = "?"
            evidence_count = 0
            verified = "?"

            if os.path.exists(agent_file):
                try:
                    with open(agent_file) as f:
                        ar = json.load(f)
                    node = ar.get("node_id", "?")
                    event_type = ar.get("event_type", "?")
                except Exception:
                    pass

            if os.path.exists(evidence_file):
                try:
                    with open(evidence_file) as f:
                        ev = json.load(f)
                    evidence_count = len(ev) if isinstance(ev, list) else 0
                except Exception:
                    pass

            if os.path.exists(verifier_file):
                try:
                    with open(verifier_file) as f:
                        vr = json.load(f)
                    verified = "✅" if vr.get("verified") else "⚠️"
                except Exception:
                    pass

            rows.append({
                "时间": t,
                "节点": node,
                "事件类型": event_type,
                "证据数": evidence_count,
                "复核": verified,
            })

        st.dataframe(rows, width='stretch', height=350, column_config={
            "时间": st.column_config.TextColumn(width="medium"),
            "节点": st.column_config.TextColumn(width="small"),
            "事件类型": st.column_config.TextColumn(width="small"),
            "证据数": st.column_config.NumberColumn(width="small"),
            "复核": st.column_config.TextColumn(width="small"),
        })

st.divider()
st.caption("AI-SOC 统一运维控制台 | 端口 8500 | 所有功能通过侧边栏页面访问")
