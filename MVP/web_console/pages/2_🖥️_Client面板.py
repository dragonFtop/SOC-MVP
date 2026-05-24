"""
Client 面板 — 侧边栏选择客户端，查看详情和检测统计
"""

import os
import sys
import json
import yaml
import time

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import OUTPUTS_DIR, CLIENT_CONFIG_PATH

st.set_page_config(page_title="Client面板", page_icon="🖥️", layout="wide")

import sys as _s, os as _o; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from auth import require_auth; require_auth()

# 自动刷新 (5秒)
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=5000, key="client_panel_refresh")
except ImportError:
    pass

st.title("🖥️ Client 管理面板")

# ── 加载已注册客户端 ──
if not os.path.exists(CLIENT_CONFIG_PATH):
    st.error("client_config.yaml 不存在，请先在 Client注册 页面添加客户端")
    st.stop()

with open(CLIENT_CONFIG_PATH, "r") as f:
    config = yaml.safe_load(f)
clients = config.get("clients", [])

if not clients:
    st.warning("没有已注册的客户端")
    st.stop()

# ── 侧边栏：客户端列表 ──
client_ids = [c["client_id"] for c in clients]
default_idx = 0
if "selected_client_idx" in st.session_state:
    default_idx = min(st.session_state.selected_client_idx, len(client_ids) - 1)

selected = st.sidebar.selectbox(
    "选择客户端",
    client_ids,
    index=default_idx,
    key="client_selector",
)

# 找到对应的 client 配置
client_cfg = None
for i, c in enumerate(clients):
    if c["client_id"] == selected:
        client_cfg = c
        st.session_state.selected_client_idx = i
        break

if not client_cfg:
    st.error("客户端配置未找到")
    st.stop()

node_id = client_cfg["node_id"]
log_path = client_cfg.get("log_path", "")

# ── Client 信息卡片 ──
st.subheader(f"📋 {selected}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Client ID", selected)
c2.metric("节点 ID", node_id)
is_real = log_path.startswith("/")
c3.metric("模式", "🖥️ 真机" if is_real else "📦 模拟")
c4.metric("日志路径", log_path)

desc = client_cfg.get("description", "")
if desc:
    st.caption(f"📝 {desc}")

st.divider()

# ── 该节点关联的研判任务 ──
st.subheader(f"📋 {node_id} 的研判记录")

if not os.path.exists(OUTPUTS_DIR):
    st.info("尚无研判记录")
else:
    tasks = sorted(
        [d for d in os.listdir(OUTPUTS_DIR) if os.path.isdir(os.path.join(OUTPUTS_DIR, d))],
        reverse=True,
    )

    node_tasks = []
    for t in tasks:
        task_path = os.path.join(OUTPUTS_DIR, t)
        agent_file = os.path.join(task_path, "agent_result.json")
        if os.path.exists(agent_file):
            try:
                with open(agent_file) as f:
                    ar = json.load(f)
                if ar.get("node_id") == node_id:
                    node_tasks.append((t, ar))
            except Exception:
                pass

    if not node_tasks:
        st.info(f"节点 {node_id} 暂无研判记录")
    else:
        st.markdown(f"共 **{len(node_tasks)}** 条记录")

        for t, ar in node_tasks[:20]:
            task_path = os.path.join(OUTPUTS_DIR, t)
            verifier_file = os.path.join(task_path, "verifier_result.json")
            evidence_file = os.path.join(task_path, "evidence.json")

            verified = "?"
            v_score = 0
            if os.path.exists(verifier_file):
                try:
                    with open(verifier_file) as f:
                        vr = json.load(f)
                    verified = "✅ 通过" if vr.get("verified") else "⚠️ 未通过"
                    v_score = vr.get("readiness_score", 0)
                except Exception:
                    pass

            evidence_count = 0
            if os.path.exists(evidence_file):
                try:
                    with open(evidence_file) as f:
                        ev = json.load(f)
                    evidence_count = len(ev) if isinstance(ev, list) else 0
                except Exception:
                    pass

            with st.expander(
                f"{t} | {ar.get('event_type', '?')} | 优先级:{ar.get('priority', '?')} | "
                f"置信度:{ar.get('confidence', '?')} | 复核:{verified}"
            ):
                st.markdown(f"**摘要**: {ar.get('summary', '无')}")
                st.markdown(f"**结论**: {ar.get('conclusion', '无')}")
                st.markdown(f"**就绪度**: {v_score} 分 | **证据**: {evidence_count} 条")

                actions = ar.get("actions", [])
                if actions:
                    st.markdown("**处置建议**:")
                    for act in actions:
                        st.markdown(f"- {act}")

st.divider()
st.caption(f"Client 面板 | 当前选中: {selected} → {node_id}")

# ── 日志文件预览 ──
with st.expander("📄 日志文件预览 (最近 20 行)", expanded=False):
    actual_path = log_path
    if not os.path.isabs(log_path):
        actual_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), log_path)

    if os.path.exists(actual_path):
        try:
            with open(actual_path, "r") as f:
                lines = f.readlines()[-20:]
            st.code("".join(lines), language="text", line_numbers=True)
        except Exception as e:
            st.error(f"读取失败: {e}")
    else:
        st.warning(f"日志文件不存在: {actual_path}")
