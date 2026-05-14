import json
import os
from datetime import datetime

def calculate_readiness():
    with open("evidence.json", "r", encoding="utf-8") as f:
        evidence = json.load(f)

    if not evidence:
        return {"score": 0, "level": "严重不足", "reason": "无证据"}

    score = 100

    # 1. 必须字段检查
    required_fields = ["evidence_id", "timestamp", "source", "agent_ip", "rule_id", "description"]
    for ev in evidence:
        for field in required_fields:
            if not ev.get(field):
                score -= 15

    # 2. 数据时间跨度
    timestamps = [ev["timestamp"] for ev in evidence]
    if len(timestamps) < 3:
        score -= 20

    # 3. 证据是否有效
    valid_ev = [ev for ev in evidence if ev.get("ready")]
    if len(valid_ev) == 0:
        score -= 30

    score = max(score, 0)

    if score >= 80:
        level = "完整可用"
    elif score >= 60:
        level = "基本可用"
    elif score >= 40:
        level = "数据不足"
    else:
        level = "严重不足"

    result = {
        "score": score,
        "level": level,
        "evidence_count": len(evidence),
        "valid_count": len(valid_ev)
    }

    # 创建 outputs 目录（如果不存在）
    os.makedirs("outputs", exist_ok=True)
    
    # 生成带时间戳的文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"outputs/readiness_{timestamp}.json"
    
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ 数据就绪度评分：{score} 分，等级：{level}，已保存到 {filename}")
    return result

if __name__ == "__main__":
    calculate_readiness()