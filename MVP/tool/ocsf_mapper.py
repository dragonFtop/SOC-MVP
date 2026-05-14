def map_wazuh_to_ocsf(evidence_item):
    """
    将 Wazuh 证据 → 标准 OCSF 轻量格式
    数据编织核心：统一字段、统一语义
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
        "raw_log": evidence_item.get("full_log")
    }