"""
Web Console 认证模块
密码通过 WEB_CONSOLE_PASSWORD 环境变量或 .env 文件设置
为空时跳过认证
"""

import os
import sys
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import WEB_CONSOLE_PASSWORD


def require_auth():
    """在每个页面顶部调用。如果设置了密码则要求登录。"""
    if not WEB_CONSOLE_PASSWORD:
        return  # 未设置密码，跳过认证

    if "web_console_authenticated" not in st.session_state:
        st.session_state.web_console_authenticated = False

    if st.session_state.web_console_authenticated:
        # 侧边栏显示登出按钮
        with st.sidebar:
            if st.button("🔒 登出", key="logout_btn"):
                st.session_state.web_console_authenticated = False
                st.rerun()
        return

    # 登录表单
    st.markdown("""
    <style>
        .login-container {
            max-width: 400px;
            margin: 100px auto;
            padding: 40px;
            background: #1a1c23;
            border-radius: 12px;
            border: 1px solid #2d3139;
            text-align: center;
        }
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="login-container">', unsafe_allow_html=True)
    st.markdown("## 🛡️ AI-SOC 控制台")
    st.markdown("请输入密码访问")

    pwd = st.text_input("密码", type="password", key="login_password",
                        placeholder="输入 Web Console 密码")

    if st.button("登录", type="primary", width='stretch', key="login_btn"):
        if pwd == WEB_CONSOLE_PASSWORD:
            st.session_state.web_console_authenticated = True
            st.rerun()
        else:
            st.error("密码错误")

    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()
