"""
运行控制 — 触发安全事件、管理进程、监控流水线状态
"""

import os
import sys
import json
import time
import socket
import subprocess
from datetime import datetime

import yaml
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import OUTPUTS_DIR, CLIENT_CONFIG_PATH, ROOT_DIR, SIMULATED_NODES_DIR

st.set_page_config(page_title="运行控制", page_icon="🚀", layout="wide")
import sys as _s, os as _o; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from auth import require_auth; require_auth()


# ── 自动刷新 ──
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=3000, key="run_control_refresh")
except ImportError:
    pass

st.title("🚀 运行控制")
st.caption("触发测试事件 · 进程管理 · 流水线状态")

# ═══════════════════════════════════════════════
# 加载节点列表
# ═══════════════════════════════════════════════

def load_nodes():
    nodes = []
    if os.path.exists(CLIENT_CONFIG_PATH):
        with open(CLIENT_CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f)
        for c in config.get("clients", []):
            nodes.append({
                "client_id": c.get("client_id", ""),
                "node_id": c.get("node_id", ""),
                "log_path": c.get("log_path", ""),
                "is_real": c.get("log_path", "").startswith("/"),
            })
    return nodes

nodes = load_nodes()
node_ids = sorted(set(n["node_id"] for n in nodes))

# ═══════════════════════════════════════════════
# 审计日志
# ═══════════════════════════════════════════════

AUDIT_LOG = os.path.join(OUTPUTS_DIR, "audit.log")

def audit(action: str, detail: str = "", operator: str = "web-console"):
    """记录操作审计日志"""
    os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
    entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {operator} | {action} | {detail}\n"
    with open(AUDIT_LOG, "a") as f:
        f.write(entry)

# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

def get_log_path(node_id):
    """获取节点的实际日志文件路径"""
    for n in nodes:
        if n["node_id"] == node_id:
            lp = n["log_path"]
            if os.path.isabs(lp):
                return lp
            return os.path.join(ROOT_DIR, lp)
    # 回退
    return os.path.join(SIMULATED_NODES_DIR, node_id, "auth.log")

def inject_ssh_events(log_path, count):
    """向日志文件注入 SSH 暴力破解事件"""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    hostname = socket.gethostname()
    users = ["root", "admin", "deploy", "oracle", "postgres", "test", "guest", "ubuntu"]
    src_ip = "10.0.99.1"  # 固定 IP 确保达到规则阈值
    events = []
    ts_base = datetime.now()
    for i in range(count):
        user = users[i % len(users)]
        ts = ts_base.strftime("%b %d %H:%M:%S")
        pid = 10000 + i
        port = 40000 + i
        line = f"{ts} {hostname} sshd[{pid}]: Failed password for {user} from {src_ip} port {port} ssh2\n"
        events.append(line)
    with open(log_path, "a") as f:
        f.writelines(events)
    return events

def inject_sudo_events(log_path, count):
    """向日志文件注入 sudo 认证失败事件"""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    hostname = socket.gethostname()
    events = []
    ts_base = datetime.now()
    for i in range(count):
        ts = ts_base.strftime("%b %d %H:%M:%S")
        pid = 20000 + i
        line = f"{ts} {hostname} sudo[{pid}]: pam_unix(sudo:auth): authentication failure; logname= uid=0 euid=0 tty=/dev/pts/0 ruser= rhost=  user=admin\n"
        events.append(line)
    with open(log_path, "a") as f:
        f.writelines(events)
    return events

def get_running_processes():
    """获取正在运行的 AI-SOC 进程"""
    procs = []
    try:
        result = subprocess.run(
            ["ps", "aux", "--no-headers"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if not line or "grep" in line:
                continue
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue
            cmd = parts[10]
            if "client_app.py" in cmd:
                # 提取 --client-id
                cid = "?"
                if "--client-id" in cmd:
                    idx = cmd.find("--client-id") + len("--client-id")
                    rest = cmd[idx:].strip().split(None, 1)
                    cid = rest[0] if rest else "?"
                procs.append({
                    "pid": parts[1],
                    "cpu": parts[2],
                    "mem": parts[3],
                    "etime": parts[9],
                    "type": "Client",
                    "name": cid,
                    "cmd": cmd[:120],
                })
            elif "server_app.py" in cmd:
                procs.append({
                    "pid": parts[1],
                    "cpu": parts[2],
                    "mem": parts[3],
                    "etime": parts[9],
                    "type": "Server",
                    "name": "server",
                    "cmd": cmd[:120],
                })
    except Exception:
        pass
    return procs

def get_recent_pipeline_activity(limit=10):
    """获取最近的流水线活动"""
    activities = []
    if not os.path.exists(OUTPUTS_DIR):
        return activities
    dirs = sorted(
        [d for d in os.listdir(OUTPUTS_DIR) if os.path.isdir(os.path.join(OUTPUTS_DIR, d))],
        reverse=True,
    )[:limit]
    for d in dirs:
        task_path = os.path.join(OUTPUTS_DIR, d)
        agent_file = os.path.join(task_path, "agent_result.json")
        evidence_file = os.path.join(task_path, "evidence.json")
        verifier_file = os.path.join(task_path, "verifier_result.json")

        node = "?"
        event_type = "?"
        confidence = "?"
        evidence_count = 0
        verified = "?"

        if os.path.exists(agent_file):
            try:
                with open(agent_file) as f:
                    ar = json.load(f)
                node = ar.get("node_id", "?")
                event_type = ar.get("event_type", "?")
                confidence = ar.get("confidence", "?")
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

        activities.append({
            "timestamp": d,
            "node": node,
            "event_type": event_type,
            "evidence": evidence_count,
            "confidence": confidence,
            "verified": verified,
        })
    return activities

# ═══════════════════════════════════════════════
# 进程管理
# ═══════════════════════════════════════════════

# 链接到独立的安全测试页面
st.info("🎯 **安全测试已迁移至独立页面** → 左侧边栏点击 **🎯 安全测试**，支持自定义攻击场景和批量注入")

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("🖥️ 运行中的进程")

    procs = get_running_processes()

    if not procs:
        st.info("当前没有运行中的 AI-SOC 进程")
    else:
        for p in procs:
            icon = "🖧" if p["type"] == "Server" else "🖥️"
            with st.container(border=True):
                c1, c2 = st.columns([3, 1])
                c1.markdown(f"{icon} **{p['type']}** — `{p['name']}`")
                c1.caption(f"PID: {p['pid']} | CPU: {p['cpu']}% | MEM: {p['mem']}% | 运行: {p['etime']}")
                if p["type"] == "Client":
                    if c2.button("🛑 停止", key=f"kill_{p['pid']}"):
                        try:
                            os.kill(int(p["pid"]), 15)
                            audit("stop-client", f"client={p['name']} pid={p['pid']}")
                            st.success(f"已发送停止信号: PID {p['pid']}")
                            time.sleep(1)
                            st.rerun()
                        except Exception as e:
                            st.error(f"停止失败: {e}")

        st.caption(f"共 {len(procs)} 个进程 | 自动刷新")

    # ── 启动 Client ──
    st.subheader("▶️ 启动 Client")

    # 列出未运行的已注册客户端
    running_ids = {p["name"] for p in procs if p["type"] == "Client"}
    all_clients = []
    if os.path.exists(CLIENT_CONFIG_PATH):
        with open(CLIENT_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        all_clients = cfg.get("clients", [])

    stopped_clients = [c for c in all_clients if c["client_id"] not in running_ids]
    if not stopped_clients:
        st.caption("所有已注册 Client 均在运行中")
    else:
        for c in stopped_clients:
            cols = st.columns([3, 2, 2, 1])
            cols[0].write(f"**{c['client_id']}**")
            cols[1].write(f"→ {c['node_id']}")
            cols[2].write(f"`{c['log_path']}`")
            if cols[3].button("▶️", key=f"start_{c['client_id']}"):
                log_path = os.path.join(OUTPUTS_DIR, f"client_{c['client_id']}.log")
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                subprocess.Popen(
                    [sys.executable, "-u",
                     os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                  "client", "client_app.py"),
                     "--client-id", c["client_id"]],
                    stdout=open(log_path, "w"),
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                audit("start-client", f"client={c['client_id']} node={c['node_id']}")
                st.success(f"✅ 已启动: {c['client_id']}")
                time.sleep(1.5)
                st.rerun()

st.divider()

# ═══════════════════════════════════════════════
# 第二行: 流水线状态
# ═══════════════════════════════════════════════

st.subheader("📊 流水线活动 (最近研判)")

activities = get_recent_pipeline_activity(12)

if not activities:
    st.info("暂无研判活动，触发测试事件后在此查看结果")
else:
    # 统计
    total = len(activities)
    passed = sum(1 for a in activities if a["verified"] == "✅")
    nodes_active = len(set(a["node"] for a in activities))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("最近任务", total)
    c2.metric("复核通过", f"{passed}/{total}")
    c3.metric("活跃节点", nodes_active)
    # 最新事件时间
    latest_ts = activities[0]["timestamp"] if activities else "—"
    c4.metric("最新事件", latest_ts[9:15] if len(latest_ts) > 10 else latest_ts)

    st.divider()

    # 流水线时间线
    for a in activities[:10]:
        ts = a["timestamp"]
        time_display = f"{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}" if len(ts) >= 15 else ts

        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([2, 1.5, 1.5, 1, 1])
            c1.markdown(f"**{time_display}**")
            c1.caption(f"`{ts}`")
            c2.markdown(f"🖥️ {a['node']}")
            c3.markdown(f"🔴 {a['event_type']}")
            c4.metric("证据", a['evidence'])
            c5.markdown(f"{a['verified']} 复核")
            if a['confidence']:
                c5.caption(f"置信度: {a['confidence']}")

# ═══════════════════════════════════════════════
# 第三行: 快捷操作
# ═══════════════════════════════════════════════

st.divider()
st.subheader("⚡ 快捷操作")

c1, c2, c3 = st.columns(3)

with c1:
    if st.button("🧹 清空输出目录", width='stretch',
                 help="删除所有历史研判结果"):
        import shutil
        if os.path.exists(OUTPUTS_DIR):
            shutil.rmtree(OUTPUTS_DIR)
            os.makedirs(OUTPUTS_DIR, exist_ok=True)
        audit("clear-outputs")
        st.success("输出目录已清空")
        st.rerun()

with c3:
    if st.button("🔄 刷新状态", width='stretch'):
        st.rerun()

st.divider()
st.caption("运行控制 | 触发事件 → 注入日志 → DetectionEngine 检测 → 流水线研判 → 查看结果")
