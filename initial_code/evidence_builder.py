import json
import uuid
import os
from datetime import datetime
from query_gateway import query_wazuh

def build_evidence(limit=5):
    # 1. 从 DuckDB 查询告警数据
    alerts = query_wazuh(rule_id="5503", limit=limit)
    
    evidence_list = []
    
    for row in alerts:
        # 解析字段（和你查询结果的顺序完全对应）
        timestamp = row[0].isoformat() if row[0] else ""
        rule = row[1]
        agent = row[2]
        full_log = row[5]
        
        # 构建标准化证据
        evidence = {
            "evidence_id": f"ev-{str(uuid.uuid4())[:8]}",
            "case_id": "case-soc-001",
            "timestamp": timestamp,
            "source": "wazuh-alerts",
            "agent_name": agent.get("name"),
            "agent_ip": agent.get("ip"),
            "rule_id": rule.get("id"),
            "description": rule.get("description"),
            "level": rule.get("level"),
            "full_log": full_log,
            "ready": True
        }
        evidence_list.append(evidence)
    
    # 创建 outputs 目录（如果不存在）
    os.makedirs("outputs", exist_ok=True)
    
    # 生成带时间戳的文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"outputs/evidence_{timestamp}.json"
    
    # 2. 写入证据文件
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(evidence_list, f, indent=2, ensure_ascii=False)
    
    print(f"✅ 已生成 {len(evidence_list)} 条证据 → {filename}")
    return evidence_list

if __name__ == "__main__":
    build_evidence(limit=5)