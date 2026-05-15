import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
# Client 包 - 边缘侧模块
from .agent_analyzer import analyze_event
from .evidence_builder import build_evidence
from .local_gateway import query_wazuh
from common import map_wazuh_to_ocsf

# 可选模块（需要 nats-py）
try:
    from .signal_generator import build_signals_from_alerts, publish_signals_to_nats, generate_and_publish
except ImportError:
    build_signals_from_alerts = None
    publish_signals_to_nats = None
    generate_and_publish = None

try:
    from .duckdb_sidecar import DuckDBQueryEngine
except ImportError:
    DuckDBQueryEngine = None

__all__ = [
    "analyze_event",
    "build_evidence",
    "query_wazuh",
    "map_wazuh_to_ocsf",
    "build_signals_from_alerts",
    "publish_signals_to_nats",
    "generate_and_publish",
    "DuckDBQueryEngine",
]
