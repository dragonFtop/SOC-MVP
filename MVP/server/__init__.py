import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
# Server 包 - 中心侧服务模块
# 核心模块（始终可用）
from .opensearch_loader import load_to_opensearch, OpenSearchClient
from .report_generator import generate_report
from .readiness import calculate_readiness, DataReadinessChecker
from .verifier import verify, VerifierAgent
from .agent_team import AgentTeamCoordinator, run_analysis

# 可选模块（需要额外依赖：fastapi, nats-py）
try:
    from .query_gateway import app
except ImportError:
    app = None

try:
    from .signal_listener import SignalListener
except ImportError:
    SignalListener = None

__all__ = [
    "load_to_opensearch",
    "OpenSearchClient",
    "generate_report",
    "calculate_readiness",
    "DataReadinessChecker",
    "verify",
    "VerifierAgent",
    "AgentTeamCoordinator",
    "run_analysis",
    "app",
    "SignalListener",
]
