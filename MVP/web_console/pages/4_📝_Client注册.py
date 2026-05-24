"""
Client 注册 — Web UI 管理客户端注册
"""

import json
import os
import sys
import yaml

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import socket
import subprocess
from datetime import datetime

from config import CLIENT_CONFIG_PATH, ROOT_DIR, METADATA_PATH, OUTPUTS_DIR


def audit(action: str, detail: str = "", operator: str = "web-console"):
    AUDIT_LOG = os.path.join(OUTPUTS_DIR, "audit.log")
    os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
    entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {operator} | {action} | {detail}\n"
    with open(AUDIT_LOG, "a") as f:
        f.write(entry)

st.set_page_config(page_title="Client注册", page_icon="📝", layout="wide")
import sys as _s, os as _o; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from auth import require_auth; require_auth()


st.title("📝 Client 注册管理")

# ── 加载配置 ──
def load_config():
    if not os.path.exists(CLIENT_CONFIG_PATH):
        return {"clients": []}
    with open(CLIENT_CONFIG_PATH, "r") as f:
        return yaml.safe_load(f) or {"clients": []}

def save_config(config):
    with open(CLIENT_CONFIG_PATH, "w") as f:
        yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

# ── 当前客户端列表 ──
config = load_config()
clients = config.get("clients", [])

st.subheader("已注册客户端")

if not clients:
    st.info("暂无已注册客户端")
else:
    cols = st.columns([2, 2, 3, 2, 1.5, 1])
    cols[0].markdown("**Client ID**")
    cols[1].markdown("**Node ID**")
    cols[2].markdown("**日志路径**")
    cols[3].markdown("**描述**")
    cols[4].markdown("**模式**")
    cols[5].markdown("**操作**")

    to_remove = None
    for i, c in enumerate(clients):
        cols = st.columns([2, 2, 3, 2, 1.5, 1])
        cols[0].write(c.get("client_id", "?"))
        cols[1].write(c.get("node_id", "?"))
        cols[2].code(c.get("log_path", "?"))
        cols[3].write(c.get("description", ""))
        is_real = c.get("log_path", "").startswith("/")
        cols[4].write("🖥️ 真机" if is_real else "📦 模拟")
        if cols[5].button("🗑️", key=f"del_{i}"):
            to_remove = i

    if to_remove is not None:
        removed = clients.pop(to_remove)
        config["clients"] = clients
        save_config(config)
        audit("unregister-client", f"client={removed['client_id']} node={removed.get('node_id', '?')}")
        st.success(f"已移除: {removed['client_id']}")
        st.rerun()

st.divider()

# ── 加载已注册节点 (从 metadata.json 中有 auth_log 数据源的节点) ──
def load_available_nodes():
    nodes = []
    if os.path.exists(METADATA_PATH):
        with open(METADATA_PATH, "r") as f:
            data = json.load(f)
        seen = set()
        for entry in data:
            nid = entry.get("node_id", "")
            if nid and nid not in seen and entry.get("source_name") == "auth_log":
                seen.add(nid)
                lp = entry.get("local_path", "")
                is_real = lp.startswith("/")
                nodes.append({
                    "node_id": nid,
                    "log_path": lp,
                    "is_real": is_real,
                    "label": f"🖥️ {nid} ({lp})" if is_real else f"📦 {nid} (simulated_nodes/{nid}/auth.log)",
                })
    return nodes

available_nodes = load_available_nodes()

# ── 注册新客户端 ──
st.subheader("➕ 注册新客户端")

if not available_nodes:
    st.warning("⚠️ 没有可用节点 — 请先在 **🔧 节点注册** 页面注册节点 (需要 auth_log 数据源)")
    st.caption("节点是客户端的基础。每个 Client 必须绑定到一个已注册的节点上。")
    st.stop()

with st.form("register_client_form", clear_on_submit=True):
    new_client_id = st.text_input("Client ID *", placeholder="gateway-guard",
                                  help="客户端唯一标识，用于启动命令: bash scripts/run_client.sh <client-id>")

    # 从已注册节点中选择
    node_options = [n["label"] for n in available_nodes]
    selected_idx = st.selectbox(
        "选择节点 *",
        range(len(node_options)),
        format_func=lambda i: node_options[i],
        help="来自 metadata.json 中已注册 auth_log 数据源的节点"
    )
    selected_node = available_nodes[selected_idx]

    new_desc = st.text_input("描述 (可选)", placeholder="Web网关监控")

    # 显示将要绑定的信息
    st.info(f"📋 Client `{new_client_id or '?'}` → 节点 `{selected_node['node_id']}` → 日志 `{selected_node['log_path']}`")

    submitted = st.form_submit_button("✅ 注册", type="primary", width='stretch')

    if submitted:
        if not new_client_id:
            st.error("Client ID 为必填项")
        elif any(c.get("client_id") == new_client_id for c in clients):
            st.error(f"Client ID '{new_client_id}' 已存在")
        else:
            node_id = selected_node["node_id"]
            log_path = selected_node["log_path"]

            new_client = {
                "client_id": new_client_id,
                "node_id": node_id,
                "log_path": log_path,
            }
            if new_desc:
                new_client["description"] = new_desc

            clients.append(new_client)
            config["clients"] = clients
            save_config(config)

            # 模拟模式: 确保日志目录存在并创建样本
            if not selected_node["is_real"]:
                sim_dir = os.path.join(ROOT_DIR, "simulated_nodes", node_id)
                os.makedirs(sim_dir, exist_ok=True)
                auth_log = os.path.join(sim_dir, "auth.log")
                if not os.path.exists(auth_log):
                    import socket
                    hostname = socket.gethostname()
                    from datetime import datetime
                    ts = datetime.now().strftime("%b %d %H:%M:%S")
                    sample = (
                        f"{ts} {hostname} sshd[1001]: Accepted publickey for admin from 192.168.1.10 port 40000 ssh2: RSA SHA256:abc123\n"
                        f"{ts} {hostname} sshd[1002]: Failed password for root from 10.0.0.5 port 50001 ssh2\n"
                        f"{ts} {hostname} sshd[1003]: Failed password for root from 10.0.0.5 port 50002 ssh2\n"
                        f"{ts} {hostname} sshd[1004]: Failed password for root from 10.0.0.5 port 50003 ssh2\n"
                        f"{ts} {hostname} sshd[1005]: Failed password for root from 10.0.0.5 port 50004 ssh2\n"
                        f"{ts} {hostname} sshd[1006]: Failed password for admin from 10.0.0.8 port 51001 ssh2\n"
                    )
                    with open(auth_log, "w") as f:
                        f.write(sample)

            st.success(f"✅ 已注册: {new_client_id} → {node_id}")
            st.markdown(f"启动命令: `bash scripts/run_client.sh {new_client_id}`")
            audit("register-client", f"client={new_client_id} node={node_id} path={log_path}")
            st.rerun()

st.caption("💡 如果没有可用节点，请先前往 **🔧 节点注册** 页面注册节点")

st.divider()
st.caption("Client 配置存储在 client_config.yaml | 也支持命令: bash scripts/register_client.sh")
