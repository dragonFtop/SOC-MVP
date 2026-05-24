"""
节点注册 — Web UI 管理 metadata.json 中的数据源节点
"""

import os
import sys
import json

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import METADATA_PATH

st.set_page_config(page_title="节点注册", page_icon="🔧", layout="wide")
import sys as _s, os as _o; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from auth import require_auth; require_auth()


st.title("🔧 数据源节点注册")

# ── 加载 metadata ──
def load_metadata():
    if not os.path.exists(METADATA_PATH):
        return []
    with open(METADATA_PATH, "r") as f:
        return json.load(f)

def save_metadata(data):
    with open(METADATA_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

metadata = load_metadata()

# ── 当前节点列表 ──
st.subheader("已注册数据源")

if not metadata:
    st.info("metadata.json 为空")
else:
    # 按照 node_id 分组
    from collections import defaultdict
    nodes = defaultdict(list)
    for entry in metadata:
        nodes[entry.get("node_id", "?")].append(entry)

    for node_id, sources in sorted(nodes.items()):
        with st.container(border=True):
            st.markdown(f"### 📦 {node_id}")
            for src in sources:
                c1, c2, c3, c4 = st.columns([2, 2, 3, 1])
                c1.write(f"**{src.get('source_name', '?')}**")
                c2.write(f"类型: {src.get('source_type', '?')}")
                c3.code(src.get('local_path', '?'))
                if c4.button("🗑️", key=f"del_{node_id}_{src.get('source_name')}"):
                    metadata.remove(src)
                    save_metadata(metadata)
                    st.success(f"已移除 {node_id}/{src.get('source_name')}")
                    st.rerun()

st.divider()

# ── 注册新节点/数据源 ──
st.subheader("➕ 注册数据源")

with st.form("register_node_form", clear_on_submit=True):
    st.markdown("为节点添加数据源配置到 metadata.json")

    c1, c2 = st.columns(2)
    node_id = c1.text_input("Node ID *", placeholder="node-web-01")
    source_name = c2.selectbox("数据源类型", ["auth_log", "nginx_access", "wazuh_alerts"])

    c1, c2 = st.columns(2)
    local_path = c1.text_input("本地路径 *",
                               placeholder="simulated_nodes/node-web-01/auth.log")
    format_type = c2.selectbox("格式", ["syslog", "json"])

    query_engine = st.selectbox("查询引擎",
                                ["duckdb-sidecar-authlog", "duckdb", "local"],
                                help="auth_log → duckdb-sidecar-authlog, wazuh → duckdb")

    retention = st.number_input("保留天数", value=7, min_value=1, max_value=365)

    submitted = st.form_submit_button("✅ 注册", type="primary", width='stretch')

    if submitted:
        if not node_id or not local_path:
            st.error("Node ID 和 本地路径 为必填项")
        else:
            new_entry = {
                "node_id": node_id,
                "source_name": source_name,
                "source_type": "syslog" if format_type == "syslog" else (
                    "web_log" if source_name == "nginx_access" else "wazuh_alert"
                ),
                "local_path": local_path,
                "format": format_type,
                "query_engine": query_engine,
                "retention_days": retention,
            }
            metadata.append(new_entry)
            save_metadata(metadata)
            st.success(f"✅ 已注册: {node_id}/{source_name}")
            st.rerun()

st.divider()

# ── JSON 预览 ──
with st.expander("📄 metadata.json 完整预览", expanded=False):
    st.json(metadata)

st.caption("metadata.json — 数据编织架构的数据源注册中心")
