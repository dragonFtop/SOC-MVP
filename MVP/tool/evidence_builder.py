import json
import os
import uuid
from datetime import datetime
from .query_gateway import query_wazuh

def build_evidence(limit=5):
    # 生成时间戳
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 创建以时间戳命名的输出目录
    output_dir = f"outputs/{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    
    # 保存时间戳到文件，以便其他脚本可以获取
    with open("current_timestamp.txt", "w", encoding="utf-8") as f:
        f.write(timestamp)
    
    # 1. 从 DuckDB 查询告警数据
    alerts = query_wazuh(rule_id="5503", limit=limit)
    
    evidence_list = []
    
    for row in alerts:
        # 解析字段（和你查询结果的顺序完全对应）
        timestamp_field = row[0].isoformat() if row[0] else ""
        rule = row[1]
        agent = row[2]
        full_log = row[5]
        
        # 构建标准化证据
        evidence = {
            "evidence_id": f"ev-{str(uuid.uuid4())[:8]}",
            "case_id": "case-soc-001",
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
        evidence_list.append(evidence)
    
    # 2. 写入证据文件到时间戳目录
    filepath = f"{output_dir}/evidence.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(evidence_list, f, indent=2, ensure_ascii=False)
    
    print(f"✅ 已生成 {len(evidence_list)} 条证据 → {filepath}")
    return evidence_list

if __name__ == "__main__":
    build_evidence(limit=5)