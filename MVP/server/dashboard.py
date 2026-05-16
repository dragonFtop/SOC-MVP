import streamlit as st
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import OUTPUTS_DIR

# -------------------------- 页面配置 --------------------------
st.set_page_config(
    page_title="AI-SOC 数据编织研判平台",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 🔥 美化主题（专业深色 SOC 风格）
st.markdown("""
<style>
    .main {
        background-color: #0E1117;
        color: #EAECEF;
    }
    .card {
        background-color: #1A1C23;
        border-radius: 14px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.4);
    }
    .sidebar {
        background-color: #14161D;
    }
</style>
""", unsafe_allow_html=True)

# -------------------------- 标题 --------------------------
st.title("🛡️ AI-SOC 数据编织智能研判平台")
st.markdown("#### 物理分布 · 逻辑统一 · 按需取证 · 可信研判")
st.divider()

# -------------------------- 获取历史任务 --------------------------
def get_all_tasks():
    base = OUTPUTS_DIR
    if not os.path.exists(base):
        return []
    dirs = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    dirs.sort(reverse=True)
    return dirs

tasks = get_all_tasks()

# -------------------------- 侧边栏 --------------------------
with st.sidebar:
    st.header("📂 历史研判任务")
    if not tasks:
        st.error("未找到研判记录，请先运行 main.py")
        st.stop()
    selected_task = st.selectbox("选择任务", tasks)
    st.divider()
    st.success(f"当前任务：\n{selected_task}")

task_path = os.path.join(OUTPUTS_DIR, selected_task) if selected_task else None

# -------------------------- 工具函数：安全读取字段 --------------------------
def safe_get(data, key, default="未知"):
    return data.get(key, default)

# ========================================================================
# 【卡片 1】数据就绪度
# ========================================================================
with st.container(border=True):
    st.subheader("✅ 数据就绪度")
    rd_file = os.path.join(task_path, "readiness.json")
    if os.path.exists(rd_file):
        with open(rd_file, encoding="utf-8") as f:
            rd = json.load(f)
        col1, col2, col3 = st.columns(3)
        col1.metric("就绪评分", f"{safe_get(rd, 'score')} 分")
        col2.metric("评估等级", safe_get(rd, 'level'))
        col3.metric("有效证据", safe_get(rd, 'valid_count'))
    else:
        st.warning("无数据就绪度记录")

# ========================================================================
# 【卡片 2】标准化证据列表（✅ 不会再报错！）
# ========================================================================
with st.container(border=True):
    st.subheader("🔍 标准化安全证据")
    evi_file = os.path.join(task_path, "evidence.json")
    if os.path.exists(evi_file):
        with open(evi_file, encoding="utf-8") as f:
            evidences = json.load(f)

        for idx, ev in enumerate(evidences, 1):
            title = f"证据 {idx} | {safe_get(ev, 'rule_id')} | {safe_get(ev, 'description')}"
            with st.expander(title):
                st.write(f"🆔 证据ID：`{safe_get(ev, 'evidence_id')}`")
                st.write(f"⏱ 时间：{safe_get(ev, 'timestamp')}")
                st.write(f"🔴 级别：{safe_get(ev, 'severity')}")
                st.write(f"🌐 源IP：{safe_get(ev, 'src_ip')}")
                st.write(f"💻 主机：{safe_get(ev, 'hostname')}")
                st.code(safe_get(ev, 'raw_log'), language="text")
    else:
        st.warning("无证据记录")

# ========================================================================
# 【卡片 3】AI 研判结果
# ========================================================================
with st.container(border=True):
    st.subheader("🧠 AI 智能研判结论")
    agent_file = os.path.join(task_path, "agent_result.json")
    draft_file = os.path.join(task_path, "agent_draft.json")
    if os.path.exists(agent_file):
        with open(agent_file, encoding="utf-8") as f:
            agent = json.load(f)
        st.success(f"结论：{safe_get(agent, 'conclusion')}")
        st.write(f"✅ 置信度：{safe_get(agent, 'confidence')}")
        st.subheader("📋 处置建议")
        for act in agent.get("actions", ["无建议"]):
            st.write(f"- {act}")
    elif os.path.exists(draft_file):
        with open(draft_file, encoding="utf-8") as f:
            draft = json.load(f)
        triage = draft.get("triage", {})
        st.success(f"分诊结论：{safe_get(triage, 'summary')}")
        st.write(f"📌 事件类型：{safe_get(triage, 'event_type')}")
        st.write(f"⚡ 优先级：{safe_get(triage, 'priority')}")
        st.write(f"✅ 置信度：{safe_get(triage, 'confidence')}")
        if draft.get("attack_chain"):
            ac = draft["attack_chain"]
            st.write(f"🔗 攻击链：{safe_get(ac, 'progress')}")
        st.subheader("📋 处置建议")
        for act in draft.get("suggested_actions", ["无建议"]):
            st.write(f"- {act}")
    else:
        st.warning("无 AI 研判结果")

# ========================================================================
# 【卡片 4】复核结果（防幻觉）
# ========================================================================
with st.container(border=True):
    st.subheader("🛡️ 结果复核（防AI幻觉）")
    veri_file = os.path.join(task_path, "verifier_result.json")
    if os.path.exists(veri_file):
        try:
            with open(veri_file, encoding="utf-8") as f:
                veri = json.load(f)
            if veri.get("verified"):
                st.success("✅ 复核通过")
            else:
                st.error("❌ 复核未通过")
            for issue in veri.get("issues", []):
                st.write(f"⚠️ {issue}")
        except:
            st.warning("复核数据格式异常")
    else:
        st.warning("无复核记录")

# ========================================================================
# 【卡片 5】研判报告
# ========================================================================
with st.container(border=True):
    st.subheader("📄 完整研判报告")
    report_file = os.path.join(task_path, "report.md")
    if os.path.exists(report_file):
        with open(report_file, encoding="utf-8") as f:
            st.markdown(f.read())
    else:
        st.warning("无报告")

# -------------------------- 底部信息 --------------------------
st.divider()
st.caption("AI-SOC | 数据编织架构 | 边缘采集 → 按需取证 → 标准化 → 可信研判")


def run_dashboard(port: int = 8501):
    """启动 Streamlit Dashboard（阻塞，通常在独立线程中调用）"""
    import subprocess
    import sys
    print(f"[Dashboard] 启动中 -> http://localhost:{port}")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run",
         __file__,
         "--server.port", str(port),
         "--server.headless", "true",
         "--browser.gatherUsageStats", "false"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )