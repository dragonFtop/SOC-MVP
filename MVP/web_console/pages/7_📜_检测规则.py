"""
检测规则编辑器 — 在线编辑 detection_rules.yaml，保存后 DetectionEngine 自动热加载
"""

import os
import sys
import time

import yaml
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import DETECTION_RULES_PATH

st.set_page_config(page_title="检测规则", page_icon="📜", layout="wide")
import sys as _s, os as _o; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from auth import require_auth; require_auth()


st.title("📜 检测规则编辑器")

# ── 加载当前规则 ──
def load_rules():
    if not os.path.exists(DETECTION_RULES_PATH):
        return ""
    with open(DETECTION_RULES_PATH, "r") as f:
        return f.read()

def save_rules(content):
    with open(DETECTION_RULES_PATH, "w") as f:
        f.write(content)

if "rules_text" not in st.session_state:
    st.session_state.rules_text = load_rules()

# ── 提示 ──
st.info("💡 编辑 YAML 规则后点击「保存并应用」，运行中的 DetectionEngine 将在 **2 秒内** 自动热加载，无需重启 Client。")
st.caption(f"规则文件: `{DETECTION_RULES_PATH}`")

# ── 编辑器 ──
edited = st.text_area(
    "YAML 规则内容",
    value=st.session_state.rules_text,
    height=500,
    key="rules_editor",
    label_visibility="collapsed",
)

c1, c2, c3 = st.columns([1, 1, 3])
with c1:
    if st.button("💾 保存并应用", type="primary", width='stretch'):
        # 校验 YAML 语法
        try:
            parsed = yaml.safe_load(edited)
            if not parsed or "rules" not in parsed:
                st.error("❌ YAML 格式错误: 缺少顶层 'rules' 字段")
            else:
                save_rules(edited)
                st.session_state.rules_text = edited
                st.success(f"✅ 已保存 {len(parsed['rules'])} 条规则 — DetectionEngine 将自动热加载")
                time.sleep(0.5)
                st.rerun()
        except yaml.YAMLError as e:
            st.error(f"❌ YAML 语法错误: {e}")

with c2:
    if st.button("🔄 重置为已保存", width='stretch'):
        st.session_state.rules_text = load_rules()
        st.rerun()

# ── 规则参考 ──
st.divider()
st.subheader("📖 规则编写参考")

with st.expander("规则字段说明", expanded=False):
    st.markdown("""
每条规则包含以下字段：

| 字段 | 说明 | 示例 |
|------|------|------|
| `id` | 唯一规则 ID | `LOCAL_SSH_BRUTE_FORCE` |
| `name` | 规则名称 | `SSH Brute Force Attack` |
| `severity` | 严重度 | `high` / `medium` / `low` / `critical` |
| `category` | 事件分类 | `brute_force` / `reconnaissance` / `privilege_escalation` |
| `description` | 规则描述 | |
| `match.process` | 匹配进程名 | `sshd` / `sudo` / `su` |
| `match.patterns` | 匹配关键词列表 | `["Failed password", "authentication failure"]` |
| `threshold.count` | 触发阈值 | `5` |
| `threshold.window_seconds` | 时间窗口 | `300` |
| `threshold.group_by` | 分组字段 | `src_ip` / `dst_user` |
| `cooldown_seconds` | 冷却时间 | `600` |
| `signal.priority` | 信号优先级 | `5` (数字越大越紧急) |
| `signal.event_type` | 事件类型 | `brute_force` / `reconnaissance` / `privilege_escalation` / `unknown` |
| `signal.suggested_logs` | 建议日志源 | `["auth_log"]` |
""")

with st.expander("示例: 添加一条自定义规则", expanded=False):
    st.code("""  - id: "LOCAL_CUSTOM_RULE"
    name: "Custom Detection"
    severity: "medium"
    category: "custom"
    description: "自定义检测规则示例"
    match:
      process: sshd
      patterns:
        - "Bad protocol version identification"
    threshold:
      count: 3
      window_seconds: 120
      group_by: src_ip
    cooldown_seconds: 600
    signal:
      priority: 4
      event_type: "reconnaissance"
      suggested_logs: ["auth_log"]
""", language="yaml")

st.divider()
st.caption("规则变更实时生效 | DetectionEngine 每 2 秒检查文件变更 | 无需重启")
