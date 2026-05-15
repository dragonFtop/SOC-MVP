import json
import os
import uuid
import hashlib
from .local_gateway import query_wazuh
from common import map_wazuh_to_ocsf
from config import OUTPUTS_DIR, DEFAULT_RULE_ID, DEFAULT_CASE_ID

def build_evidence(limit=20, timestamp=None):

    if timestamp is None:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 创建以时间戳命名的输出目录
    output_dir = f"{OUTPUTS_DIR}/{timestamp}"
    print(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    # 1. 从 DuckDB 查询告警数据
    query_id = f"qry-{uuid.uuid4().hex[:8]}"
    alerts = query_wazuh(rule_id=DEFAULT_RULE_ID, limit=limit)

    evidence_list = []

    for row in alerts:
        timestamp_field = row[0].isoformat() if row[0] else ""
        rule = row[1]
        agent = row[2]
        full_log = row[5]

        evidence_id = f"ev-{str(uuid.uuid4())[:8]}"
        node_id = agent.get("name", "unknown")
        raw_str = "|".join([str(f) for f in row])
        row_hash = hashlib.sha256(raw_str.encode()).hexdigest()[:16]

        evidence = {
            "evidence_id": evidence_id,
            "case_id": DEFAULT_CASE_ID,
            "timestamp": timestamp_field,
            "source": "wazuh-alerts",
            "agent_name": agent.get("name"),
            "agent_ip": agent.get("ip"),
            "rule_id": rule.get("id"),
            "description": rule.get("description"),
            "level": rule.get("level"),
            "full_log": full_log,
            "ready": True
        }
        evidence = map_wazuh_to_ocsf(evidence)
        # OCSF 映射后补上溯源字段（OCSF mapper 不传递这些字段）
        evidence["query_id"] = query_id
        evidence["raw_ref"] = f"{node_id}/wazuh-alerts#{timestamp_field}"
        evidence["lineage_id"] = f"{query_id}:{row_hash}"
        evidence["hash"] = row_hash
        evidence_list.append(evidence)

    # 2. 写入证据文件到时间戳目录
    filepath = f"{output_dir}/evidence.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(evidence_list, f, indent=2, ensure_ascii=False)

    print(f"✅ 已生成 {len(evidence_list)} 条证据 → {filepath}")
    return evidence_list

if __name__ == "__main__":
    build_evidence(limit=5)
