# config.py
import os
from pathlib import Path

# ====================== 项目核心路径配置 ======================
# 当前文件所在目录（MVP/）
BASE_DIR = Path(__file__).parent
CLIENT_DIR = os.path.join(BASE_DIR, "client")
SERVER_DIR = os.path.join(BASE_DIR, "server")

# 项目根目录（SOC/）
ROOT_DIR = BASE_DIR.parent

# Wazuh 日志目录（你真实的日志路径）
WAZUH_LOGS_DIR = os.path.join(ROOT_DIR, "wazuh_logs")
ALERTS_JSON_PATH = os.path.join(WAZUH_LOGS_DIR, "alerts", "alerts.json")

# 输出目录（所有证据、报告都在这里）
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")

# ====================== OpenSearch 配置（可灵活修改） ======================
OPENSEARCH_HOST = "127.0.0.1"
OPENSEARCH_PORT = 9200
OPENSEARCH_USER = "admin"
OPENSEARCH_PASS = "admin"

# ====================== NATS JetStream 配置 ======================
NATS_SERVERS = ["nats://localhost:4222"]  # NATS 服务器地址
NATS_SIGNAL_SUBJECT = "soc.signals"       # 信号主题
NATS_QUERY_REQUESTS = "soc.query.requests"  # 查询请求主题
NATS_QUERY_RESULTS = "soc.query.results"    # 查询结果主题
NATS_MONITOR_EVENTS = "soc.monitor.events"  # 监控事件主题

# ====================== Metadata 配置 ======================
METADATA_PATH = os.path.join(BASE_DIR, "metadata.json")

# ====================== FastAPI Query Gateway 配置 ======================
QUERY_GATEWAY_HOST = "0.0.0.0"
QUERY_GATEWAY_PORT = 8000

# ====================== 默认值配置 ======================
DEFAULT_RULE_ID = "5503"          # SSH 登录失败
DEFAULT_NODE_ID = "node-web-01"  # 默认边缘节点
DEFAULT_CASE_ID = "case-soc-001" # 默认案件 ID
DEFAULT_QUERY_LIMIT = 20         # 默认查询数量

# ====================== LLM 配置 ======================
# 通过环境变量配置，与 Claude Code 共享 ANTHROPIC_* 环境变量
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
