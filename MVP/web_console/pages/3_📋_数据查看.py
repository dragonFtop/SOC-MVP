"""
数据查看 — 浏览研判任务，查看证据、AI分析、复核、报告
"""

import os
import sys
import json
import yaml

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import OUTPUTS_DIR, CLIENT_CONFIG_PATH

st.set_page_config(page_title="数据查看", page_icon="📋", layout="wide")
import sys as _s, os as _o; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from auth import require_auth; require_auth()

# 自动刷新 (8秒)
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=8000, key="data_viewer_refresh")
except ImportError:
    pass

st.title("📋 数据编织研判查看")

# ── 收集节点列表 (从 client_config) ──
node_ids = []
if os.path.exists(CLIENT_CONFIG_PATH):
    with open(CLIENT_CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)
    node_ids = sorted(set(c.get("node_id", "") for c in config.get("clients", [])))

# ── 收集任务 ──
all_tasks = []
if os.path.exists(OUTPUTS_DIR):
    for d in sorted(os.listdir(OUTPUTS_DIR), reverse=True):
        task_path = os.path.join(OUTPUTS_DIR, d)
        if not os.path.isdir(task_path):
            continue
        agent_file = os.path.join(task_path, "agent_result.json")
        node = "?"
        event_type = "?"
        priority = "?"
        if os.path.exists(agent_file):
            try:
                with open(agent_file) as f:
                    ar = json.load(f)
                node = ar.get("node_id", "?")
                event_type = ar.get("event_type", "?")
                priority = ar.get("priority", "?")
            except Exception:
                pass
        all_tasks.append({
            "timestamp": d,
            "node_id": node,
            "event_type": event_type,
            "priority": priority,
        })

# ── 过滤栏 ──
st.markdown("### 🔍 筛选条件")
c1, c2, c3 = st.columns(3)
with c1:
    filter_node = st.selectbox("节点", ["全部"] + node_ids, key="filter_node")
with c2:
    types = sorted(set(t["event_type"] for t in all_tasks))
    filter_type = st.selectbox("事件类型", ["全部"] + types, key="filter_type")
with c3:
    filter_search = st.text_input("搜索 (timestamp)", placeholder="20260524")

filtered = all_tasks
if filter_node != "全部":
    filtered = [t for t in filtered if t["node_id"] == filter_node]
if filter_type != "全部":
    filtered = [t for t in filtered if t["event_type"] == filter_type]
if filter_search:
    filtered = [t for t in filtered if filter_search in t["timestamp"]]

st.markdown(f"共 **{len(filtered)}** 条任务 | 总计 {len(all_tasks)} 条")
st.divider()

# ── 选择任务 ──
if not filtered:
    st.info("无匹配的研判任务")
    st.stop()

selected_ts = st.selectbox(
    "选择研判任务",
    [t["timestamp"] for t in filtered],
    format_func=lambda ts: f"{ts} | {next((t['node_id'] for t in filtered if t['timestamp']==ts), '?')} | {next((t['event_type'] for t in filtered if t['timestamp']==ts), '?')}",
)

task_path = os.path.join(OUTPUTS_DIR, selected_ts)

# ═══════════════════════════════════════════
# 卡片 1: 数据就绪度
# ═══════════════════════════════════════════
with st.container(border=True):
    st.subheader("✅ 数据就绪度")
    rd_file = os.path.join(task_path, "readiness.json")
    if os.path.exists(rd_file):
        with open(rd_file, encoding="utf-8") as f:
            rd = json.load(f)
        c1, c2, c3 = st.columns(3)
        c1.metric("就绪评分", f"{rd.get('score', '?')} 分")
        c2.metric("评估等级", rd.get('level', '?'))
        c3.metric("有效证据", rd.get('valid_count', '?'))
    else:
        st.info("无数据就绪度记录")

# ═══════════════════════════════════════════
# 卡片 2: 标准化证据
# ═══════════════════════════════════════════
with st.container(border=True):
    st.subheader("🔍 标准化安全证据")
    evi_file = os.path.join(task_path, "evidence.json")
    if os.path.exists(evi_file):
        with open(evi_file, encoding="utf-8") as f:
            evidences = json.load(f)
        if not evidences:
            st.info("证据列表为空")
        else:
            st.markdown(f"共 **{len(evidences)}** 条证据")
            for idx, ev in enumerate(evidences, 1):
                title = f"证据 {idx} | {ev.get('rule_id', '?')} | {ev.get('description', '')[:60]}"
                with st.expander(title):
                    c1, c2 = st.columns(2)
                    c1.write(f"🆔 `{ev.get('evidence_id', '?')}`")
                    c1.write(f"⏱ {ev.get('timestamp', '?')}")
                    c1.write(f"🌐 {ev.get('src_ip', '?')}")
                    c2.write(f"🔴 Severity: {ev.get('severity', '?')}")
                    c2.write(f"💻 {ev.get('hostname', '?')}")
                    c2.write(f"📦 Source: {ev.get('source', '?')}")
                    raw = ev.get('raw_log', '')
                    if raw:
                        st.code(raw, language="text")
    else:
        st.info("无证据记录")

# ═══════════════════════════════════════════
# 卡片 3: AI 研判
# ═══════════════════════════════════════════
with st.container(border=True):
    st.subheader("🧠 AI 研判结论")
    agent_file = os.path.join(task_path, "agent_result.json")
    draft_file = os.path.join(task_path, "agent_draft.json")

    agent_data = None
    if os.path.exists(agent_file):
        with open(agent_file, encoding="utf-8") as f:
            agent_data = json.load(f)
    elif os.path.exists(draft_file):
        with open(draft_file, encoding="utf-8") as f:
            draft = json.load(f)
        triage = draft.get("triage", {})
        agent_data = {
            "summary": triage.get("summary", ""),
            "event_type": triage.get("event_type", ""),
            "priority": triage.get("priority", ""),
            "confidence": triage.get("confidence", ""),
            "actions": draft.get("suggested_actions", []),
            "conclusion": draft.get("attack_chain", {}).get("progress", ""),
        }

    if agent_data:
        st.success(f"**结论**: {agent_data.get('summary', agent_data.get('conclusion', ''))}")
        c1, c2, c3 = st.columns(3)
        c1.metric("事件类型", agent_data.get("event_type", "?"))
        c2.metric("优先级", agent_data.get("priority", "?"))
        c3.metric("置信度", agent_data.get("confidence", "?"))

        actions = agent_data.get("actions", [])
        if actions:
            st.subheader("📋 处置建议")
            for act in actions:
                st.markdown(f"- {act}")
    else:
        st.info("无 AI 研判结果")

# ═══════════════════════════════════════════
# 卡片 4: 复核结果
# ═══════════════════════════════════════════
with st.container(border=True):
    st.subheader("🛡️ 复核结果")
    veri_file = os.path.join(task_path, "verifier_result.json")
    if os.path.exists(veri_file):
        with open(veri_file, encoding="utf-8") as f:
            veri = json.load(f)
        if veri.get("verified"):
            st.success("✅ 复核通过")
        else:
            st.error("❌ 复核未通过")
        for issue in veri.get("issues", []):
            st.warning(f"⚠️ {issue}")

        checks = veri.get("checks", {})
        if checks:
            cols = st.columns(len(checks))
            for i, (check_name, status) in enumerate(checks.items()):
                cols[i].metric(check_name, "✅" if status == "passed" else "❌")

        st.caption(f"最终置信度: {veri.get('final_confidence', '?')}")
    else:
        st.info("无复核记录")

# ═══════════════════════════════════════════
# 卡片 5: 完整报告
# ═══════════════════════════════════════════
with st.container(border=True):
    st.subheader("📄 研判报告")
    report_file = os.path.join(task_path, "report.md")
    if os.path.exists(report_file):
        with open(report_file, encoding="utf-8") as f:
            st.markdown(f.read())
    else:
        st.info("无报告")

st.divider()
st.caption("AI-SOC 数据编织研判平台 | 选择任务查看完整分析链路")
