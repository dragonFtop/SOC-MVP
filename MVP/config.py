# config.py
import os
from pathlib import Path

# 加载 .env 文件中的环境变量
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

# ====================== 项目核心路径配置 ======================
# 当前文件所在目录（MVP/）
BASE_DIR = Path(__file__).parent
CLIENT_DIR = os.path.join(BASE_DIR, "client")
SERVER_DIR = os.path.join(BASE_DIR, "server")

# 项目根目录（SOC/）
ROOT_DIR = BASE_DIR.parent

# 模拟多节点日志目录
SIMULATED_NODES_DIR = os.path.join(ROOT_DIR, "simulated_nodes")

# Client 注册配置文件
CLIENT_CONFIG_PATH = os.path.join(BASE_DIR, "client_config.yaml")

# 安全测试场景配置
TEST_SCENARIOS_PATH = os.path.join(BASE_DIR, "test_scenarios.yaml")

# 告警日志目录
WAZUH_LOGS_DIR = os.path.join(ROOT_DIR, "wazuh_logs")
ALERTS_JSON_PATH = os.path.join(WAZUH_LOGS_DIR, "alerts", "alerts.json")

# 输出目录（所有证据、报告都在这里）
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")

# ====================== OpenSearch 配置 ======================
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "127.0.0.1")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASS = os.getenv("OPENSEARCH_PASS", "admin")

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

# ====================== Auth Log Detection Engine ======================
AUTH_LOG_PATH = "/var/log/auth.log"              # System auth log path
DETECTION_RULES_PATH = os.path.join(CLIENT_DIR, "detection_rules.yaml")
EVENT_RETENTION_MINUTES = 60                     # Cleanup events older than N minutes
DUCKDB_PATH = None                               # None = in-memory, or path for persistent DB

# ====================== LLM 配置 (DeepSeek) ======================
# DeepSeek API 使用 OpenAI 兼容接口 — Key 从环境变量或 .env 文件读取
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# 默认模型 (未按 Agent 细分时使用)
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# 按 Agent 分配模型 (可通过环境变量覆盖)
# Triage: 快速分类，chat 模型足够
LLM_MODEL_TRIAGE = os.getenv("LLM_MODEL_TRIAGE", "deepseek-chat")
# AttackChain: 攻击链推理，用 reasoner 增强推理
LLM_MODEL_ATTACK_CHAIN = os.getenv("LLM_MODEL_ATTACK_CHAIN", "deepseek-reasoner")
# Report: 中文报告生成，chat 模型语言质量好 + 响应快
LLM_MODEL_REPORT = os.getenv("LLM_MODEL_REPORT", "deepseek-chat")

# 保留 Anthropic 配置作为备选 (通过 LLM_PROVIDER=anthropic 切换)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_AUTH_TOKEN", "")
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# LLM provider: "deepseek" (默认) 或 "anthropic"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")

# ====================== Web Console 认证 ======================
WEB_CONSOLE_PASSWORD = os.getenv("WEB_CONSOLE_PASSWORD", "")


# ====================== 启动校验 ======================

def validate_config():
    """检查关键配置项，缺失时给出明确报错。Server 和 Client 启动时调用。"""
    errors = []

    # LLM: 如果 API Key 为空，LLM 将回退规则引擎
    if LLM_PROVIDER == "deepseek" and not DEEPSEEK_API_KEY:
        errors.append("DEEPSEEK_API_KEY 未设置 — LLM 将回退规则引擎。请在 .env 文件中设置")
    if LLM_PROVIDER == "anthropic" and not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_AUTH_TOKEN 未设置 — LLM 将回退规则引擎。请在 .env 文件中设置")

    # NATS
    if not NATS_SERVERS or not NATS_SERVERS[0]:
        errors.append("NATS_SERVERS 为空 — Client/Server 间通信将失败")

    # OpenSearch (仅告警，不影响核心功能)
    if not OPENSEARCH_HOST:
        errors.append("OPENSEARCH_HOST 未设置 — 证据索引功能将不可用")

    for err in errors:
        print(f"⚠️  [Config] {err}")

    if errors:
        print(f"   💡 配置 .env 文件: cp .env.example .env 然后编辑\n")

    return len([e for e in errors if "未设置" in e and "回退" not in e]) == 0
