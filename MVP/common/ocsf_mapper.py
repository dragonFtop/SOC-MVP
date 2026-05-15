"""
OCSF-lite 标准化映射器
======================
职责：将 Wazuh 证据字段统一映射到 OCSF 轻量格式

对应实现方案：第五章 - OCSF-lite/ECS-lite 统一语义、字段标准化

支持扩展：添加新的 map_<source>_to_ocsf 函数即可接入新数据源
"""

from typing import Optional


def map_wazuh_to_ocsf(evidence_item: dict) -> dict:
    """
    将 Wazuh 证据映射为标准 OCSF 轻量格式

    数据编织核心：统一字段、统一语义，屏蔽底层数据源差异。

    Args:
        evidence_item: 原始 Wazuh 证据字典

    Returns:
        OCSF 标准化后的字典
    """
    return {
        "timestamp": evidence_item.get("timestamp"),
        "severity": evidence_item.get("level"),
        "rule_id": evidence_item.get("rule_id"),
        "description": evidence_item.get("description"),
        "src_ip": evidence_item.get("agent_ip"),
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
