"""
OCSF-lite 标准化映射器
======================
职责：将 Wazuh 证据字段统一映射到 OCSF 轻量格式

对应实现方案：第五章 - OCSF-lite/ECS-lite 统一语义、字段标准化

支持扩展：添加新的 map_<source>_to_ocsf 函数即可接入新数据源
"""

from typing import Optional
from datetime import datetime


def _normalize_timestamp(ts) -> str:
    """确保时间戳带时区信息，与 OpenSearch date 类型兼容（RFC 822 格式 +0800）。"""
    if not ts:
        return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.astimezone()
        return ts.strftime("%Y-%m-%dT%H:%M:%S%z")
    s = str(ts).strip()
    if s.endswith("Z"):
        return s
    # 检查 T 之后是否已有 + 或 - （时区标记）: +08:00 → +0800,  +0800 → 直接返回
    t_pos = s.rfind("T")
    if t_pos > 0 and ("+" in s[t_pos:] or "-" in s[t_pos:]):
        if s[-3] == ":":           # +08:00 → +0800
            s = s[:-3] + s[-2:]
        return s
    # 无时区 → 附加本地 RFC 822 偏移
    offset = datetime.now().astimezone().strftime("%z")
    return s + offset


def map_wazuh_to_ocsf(evidence_item: dict) -> dict:
    """
    将 Wazuh 证据映射为标准 OCSF 轻量格式

    数据编织核心：统一字段、统一语义，屏蔽底层数据源差异。

    Args:
        evidence_item: 原始 Wazuh 证据字典

    Returns:
        OCSF 标准化后的字典
    """
    src_ip = evidence_item.get("agent_ip")
    if not src_ip:
        src_ip = "0.0.0.0"  # OpenSearch ip type requires valid IP, 0.0.0.0 = unknown
    return {
        "timestamp": _normalize_timestamp(evidence_item.get("timestamp")),
        "severity": evidence_item.get("level"),
        "rule_id": evidence_item.get("rule_id"),
        "description": evidence_item.get("description"),
        "src_ip": src_ip,
        "hostname": evidence_item.get("agent_name"),
        "evidence_id": evidence_item.get("evidence_id"),
        "source": "wazuh-alerts",
        "raw_log": evidence_item.get("full_log"),
    }


def extract_severity(evidence: dict, default: int = 0) -> int:
    """从证据中提取严重度数值"""
    severity = evidence.get("severity") or evidence.get("level")
    try:
        return int(severity) if severity is not None else default
    except (ValueError, TypeError):
        return default


def extract_src_ip(evidence: dict) -> Optional[str]:
    """从证据中提取源 IP"""
    return evidence.get("src_ip") or evidence.get("agent_ip") or evidence.get("data.srcip")


def extract_hostname(evidence: dict) -> Optional[str]:
    """从证据中提取主机名"""
    return evidence.get("hostname") or evidence.get("agent_name") or evidence.get("agent.name")


def map_authlog_to_ocsf(evidence_item: dict) -> dict:
    """
    将 auth.log 证据映射为标准 OCSF 轻量格式

    Args:
        evidence_item: auth.log 解析后的证据字典

    Returns:
        OCSF 标准化后的字典
    """
    severity_map = {
        "ssh_failed_password": 5,
        "ssh_auth_failure": 5,
        "ssh_scan": 3,
        "ssh_accepted": 1,
        "ssh_connection_closed": 3,
        "ssh_preauth_disconnect": 3,
        "sudo_auth_failure": 5,
        "su_failed": 5,
    }
    log_type = evidence_item.get("log_type", "unknown")
    src_ip = evidence_item.get("src_ip")
    if not src_ip:
        src_ip = "0.0.0.0"  # OpenSearch ip type requires valid IP, 0.0.0.0 = unknown
    return {
        "timestamp": _normalize_timestamp(evidence_item.get("timestamp")),
        "severity": severity_map.get(log_type, 3),
        "rule_id": log_type,
        "description": evidence_item.get("message", ""),
        "src_ip": src_ip,
        "hostname": evidence_item.get("hostname"),
        "evidence_id": evidence_item.get("evidence_id"),
        "source": "auth_log",
        "raw_log": evidence_item.get("raw_line", ""),
    }
