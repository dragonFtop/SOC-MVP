"""
安全测试 — 可配置的攻击场景测试平台
"""

import os
import sys
import json
import socket
import subprocess
from datetime import datetime

import yaml
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import OUTPUTS_DIR, CLIENT_CONFIG_PATH, ROOT_DIR, SIMULATED_NODES_DIR, TEST_SCENARIOS_PATH

st.set_page_config(page_title="安全测试", page_icon="🎯", layout="wide")

import sys as _s, os as _o; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from auth import require_auth; require_auth()

# 自动刷新
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=3000, key="test_page_refresh")
except ImportError:
    pass

# ═══════════════════════════════════════════════
# 加载配置
# ═══════════════════════════════════════════════

def load_scenarios():
    if not os.path.exists(TEST_SCENARIOS_PATH):
        return []
    with open(TEST_SCENARIOS_PATH, "r") as f:
        data = yaml.safe_load(f)
    return data.get("scenarios", [])

def save_scenarios(scenarios):
    with open(TEST_SCENARIOS_PATH, "w") as f:
        yaml.safe_dump({"scenarios": scenarios}, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

def load_nodes():
    nodes = []
    if os.path.exists(CLIENT_CONFIG_PATH):
        with open(CLIENT_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
        for c in cfg.get("clients", []):
            nodes.append({
                "client_id": c.get("client_id", ""),
                "node_id": c.get("node_id", ""),
                "log_path": c.get("log_path", ""),
                "is_real": c.get("log_path", "").startswith("/"),
            })
    return nodes

def get_log_path(node_id):
    for n in nodes:
        if n["node_id"] == node_id:
            lp = n["log_path"]
            return lp if os.path.isabs(lp) else os.path.join(ROOT_DIR, lp)
    return os.path.join(SIMULATED_NODES_DIR, node_id, "auth.log")

def get_running_processes():
    procs = []
    try:
        result = subprocess.run(["ps", "aux", "--no-headers"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.strip().split("\n"):
            if "grep" in line: continue
            parts = line.split(None, 10)
            if len(parts) < 11: continue
            cmd = parts[10]
            if "client_app.py" in cmd:
                cid = "?"
                if "--client-id" in cmd:
                    idx = cmd.find("--client-id") + len("--client-id")
                    cid = cmd[idx:].strip().split(None, 1)[0]
                procs.append({"pid": parts[1], "type": "Client", "name": cid})
            elif "server_app.py" in cmd:
                procs.append({"pid": parts[1], "type": "Server", "name": "server"})
    except Exception:
        pass
    return procs

def audit(action, detail=""):
    AUDIT_LOG = os.path.join(OUTPUTS_DIR, "audit.log")
    os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
    entry = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] web-console | {action} | {detail}\n"
    with open(AUDIT_LOG, "a") as f:
        f.write(entry)

# ═══════════════════════════════════════════════
# 场景注入引擎
# ═══════════════════════════════════════════════

def inject_scenario(scenario: dict, log_path: str, count: int) -> list:
    """根据场景模板批量生成并写入日志行"""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    hostname = socket.gethostname()
    lines = []
    ts_base = datetime.now()

    fmt = scenario.get("log_format", "").strip()
    if not fmt:
        return []

    # 处理多行模板 (multi_line 场景)
    fmt_lines = fmt.split("\n")
    is_multi = scenario.get("multi_line", False)

    for i in range(count):
        ts = ts_base.strftime("%b %d %H:%M:%S")
        pid = 10000 + i
        pid_sudo = 30000 + i
        port = 40000 + i

        # 展开变量
        vars_dict = dict(scenario.get("variables", {}))
        resolved = {}
        for key, val in vars_dict.items():
            if isinstance(val, list):
                resolved[key] = val[i % len(val)]
            else:
                resolved[key] = val

        # 公共变量
        resolved["ts"] = ts
        resolved["host"] = hostname
        resolved["pid"] = pid
        resolved["pid_sudo"] = pid_sudo
        resolved["port"] = port

        if is_multi:
            for fl in fmt_lines:
                fl = fl.strip()
                if fl:
                    try:
                        lines.append(fl.format(**resolved).rstrip() + "\n")
                    except KeyError:
                        pass
        else:
            try:
                line = fmt.format(**resolved).rstrip()
                if "\\n" in line:  # 处理 Web 表单中输入的文本 \n
                    line = line.replace("\\n", "\n")
                lines.append(line + "\n")
            except KeyError as e:
                st.error(f"模板变量缺失: {e}")
                return []

    with open(log_path, "a") as f:
        f.writelines(lines)
    return lines


# ═══════════════════════════════════════════════
# 页面
# ═══════════════════════════════════════════════

scenarios = load_scenarios()
nodes = load_nodes()
node_ids = sorted(set(n["node_id"] for n in nodes))
running = get_running_processes()
running_clients = [p for p in running if p["type"] == "Client"]
running_client_ids = {p["name"] for p in running_clients}

st.title("🎯 安全测试平台")
st.caption("可配置的攻击场景 | 选择节点 → 选择场景 → 一键注入 → 观察研判")

# ── 状态警告 ──
if not running_clients:
    st.error("⚠️ 当前没有运行中的 Client 进程 — 注入事件不会被 DetectionEngine 检测")
    st.caption("请先在 **🚀 运行控制** 页面启动 Client")
elif not nodes:
    st.warning("没有已注册的节点")

st.divider()

# ═══════════════════════════════════════════════
# 第一行：快速测试 + 场景库
# ═══════════════════════════════════════════════

col_test, col_lib = st.columns([1, 1])

# ── 快速测试 ──
with col_test:
    st.subheader("🔥 快速测试")

    if nodes and scenarios:
        # 场景选择 (form 外，可以实时展示详情)
        scenario_names = [f"{s['name']} ({s['category']})" for s in scenarios]
        selected_idx = st.selectbox("攻击场景", range(len(scenario_names)),
                                    format_func=lambda i: scenario_names[i],
                                    key="quick_scenario_select")
        scenario = scenarios[selected_idx]

        # 展示选中场景的详情
        with st.container(border=True):
            st.caption(f"**{scenario['name']}** — `{scenario['id']}`")
            st.caption(f"📎 预期触发: {scenario.get('rule_triggers', '?')}")
            st.caption(f"📝 {scenario.get('description', '')}")
            st.caption(f"📄 默认 {scenario.get('default_count', 5)} 条")
            vars_preview = scenario.get("variables", {})
            if vars_preview:
                st.caption(f"🔧 变量: `{json.dumps(vars_preview, ensure_ascii=False)[:100]}`")

        # 执行表单
        with st.form("quick_test_form", clear_on_submit=False):
            target_node = st.selectbox("目标节点", node_ids)
            count = st.slider("注入条数", 1, 30, scenario.get("default_count", 5))
            submitted = st.form_submit_button("🔥 执行测试", type="primary", width='stretch')

        if submitted:
            scenario = scenarios[selected_idx]
            log_path = get_log_path(target_node)
            lines = inject_scenario(scenario, log_path, count)

            # 检查目标节点是否有运行中 client
            node_has_client = any(
                n["node_id"] == target_node and n["client_id"] in running_client_ids
                for n in nodes
            )

            if node_has_client:
                st.success(f"✅ 已注入 {len(lines)} 条 `{scenario['name']}` 事件到 `{target_node}`")
                st.info(f"⏳ 预期触发规则: {scenario.get('rule_triggers', '?')}")
            else:
                st.warning(f"⚠️ 已写入 {len(lines)} 条事件，但 `{target_node}` 没有运行的 Client")
                target_cid = next((n["client_id"] for n in nodes if n["node_id"] == target_node), "?")
                st.caption(f"启动命令: `bash scripts/run_client.sh {target_cid}`")

            audit("test", f"scenario={scenario['id']} node={target_node} count={count}")

            with st.expander("📝 注入日志预览"):
                st.code("".join(lines[-5:]), language="text")

# ── 场景库 ──
with col_lib:
    st.subheader("📚 场景库")

    if not scenarios:
        st.info("无场景配置")
    else:
        # 按分类分组
        categories = {}
        for s in scenarios:
            cat = s.get("category", "custom")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(s)

        cat_icons = {
            "brute_force": "🔴", "reconnaissance": "🔍",
            "privilege_escalation": "🔐", "normal": "🟢", "custom": "⚙️"
        }

        for cat, scns in sorted(categories.items()):
            icon = cat_icons.get(cat, "📦")
            with st.expander(f"{icon} {cat.upper()} ({len(scns)} 个场景)", expanded=False):
                for s in scns:
                    cols = st.columns([3, 1])
                    cols[0].markdown(f"**{s.get('name', '?')}**")
                    cols[0].caption(f"`{s.get('id')}` | 默认 {s.get('default_count', '?')} 条")
                    cols[0].caption(f"📎 {s.get('rule_triggers', '?')}")
                    if s.get("description"):
                        cols[0].caption(f"📝 {s['description']}")
                    # 显示模板预览
                    fmt_preview = s.get("log_format", "").strip()[:80]
                    cols[0].caption(f"📄 模板: `{fmt_preview}...`")

st.divider()

# ═══════════════════════════════════════════════
# 第二行：自定义场景编辑器
# ═══════════════════════════════════════════════

st.subheader("➕ 自定义场景")

with st.form("add_scenario_form", clear_on_submit=True):
    c1, c2, c3 = st.columns(3)
    new_id = c1.text_input("场景 ID *", placeholder="my_custom_attack")
    new_name = c2.text_input("场景名称 *", placeholder="自定义攻击")
    new_cat = c3.selectbox("分类", ["brute_force", "reconnaissance", "privilege_escalation", "custom"])

    c1, c2 = st.columns(2)
    new_desc = c1.text_input("描述", placeholder="场景描述")
    new_rule = c2.text_input("预期触发规则", placeholder="LOCAL_SSH_BRUTE_FORCE")

    c1, c2 = st.columns(2)
    new_count = c1.number_input("默认条数", 1, 100, 5)
    new_template = st.text_area(
        "日志模板 *",
        placeholder="{ts} {host} sshd[{pid}]: Failed password for {user} from {ip} port {port} ssh2\n"
                  "可用变量: {ts} {host} {pid} {user} {ip} {port}",
        height=80,
    )

    new_vars = st.text_input(
        "变量定义 (YAML格式)",
        placeholder='ip: "10.0.99.1"\nuser: ["root", "admin", "deploy"]',
        help="每行一个变量: key: value 或 key: [val1, val2]"
    )

    submitted_add = st.form_submit_button("✅ 添加场景", type="primary", width='stretch')

    if submitted_add:
        if not new_id or not new_name or not new_template:
            st.error("场景 ID、名称、日志模板为必填项")
        elif any(s["id"] == new_id for s in scenarios):
            st.error(f"场景 ID '{new_id}' 已存在")
        else:
            try:
                vars_dict = {}
                if new_vars.strip():
                    vars_dict = yaml.safe_load("{" + new_vars.strip().replace("\n", ", ") + "}")
            except Exception:
                vars_dict = {}

            new_scenario = {
                "id": new_id,
                "name": new_name,
                "category": new_cat,
                "description": new_desc,
                "rule_triggers": new_rule or "—",
                "default_count": new_count,
                "log_format": new_template,
                "variables": vars_dict,
            }
            scenarios.append(new_scenario)
            save_scenarios(scenarios)
            audit("add-scenario", f"id={new_id} name={new_name}")
            st.success(f"✅ 场景 '{new_name}' 已添加")
            st.rerun()

# ── 删除场景 ──
with st.expander("🗑️ 管理场景 (删除)", expanded=False):
    to_remove = None
    for i, s in enumerate(scenarios):
        cols = st.columns([4, 1])
        cols[0].write(f"**{s['name']}** (`{s['id']}`) — {s.get('category', '?')}")
        if cols[1].button("🗑️", key=f"del_scn_{i}"):
            to_remove = i
    if to_remove is not None:
        removed = scenarios.pop(to_remove)
        save_scenarios(scenarios)
        audit("remove-scenario", f"id={removed['id']}")
        st.success(f"已删除: {removed['name']}")
        st.rerun()

st.divider()
st.caption("测试场景配置存储在 test_scenarios.yaml | 支持自定义日志模板和变量")
