import json
import os

def calculate_readiness(timestamp=None):
    
    input_path = f"outputs/{timestamp}/evidence.json"
    
    with open(input_path, "r", encoding="utf-8") as f:
        evidence = json.load(f)

    if not evidence:
        return {"score": 0, "level": "严重不足", "reason": "无证据"}

    score = 100

    # ======================
    # 适配 OCSF 标准字段
    # ======================
    required_fields = [
        "evidence_id", 
        "timestamp", 
        "source", 
        "src_ip",       
        "rule_id", 
        "description"
    ]

    for ev in evidence:
        for field in required_fields:
            if not ev.get(field):
                score -= 15

    # 时间跨度
    timestamps = [ev["timestamp"] for ev in evidence]
    if len(timestamps) < 3:
        score -= 20

    # ======================
    # 【修复】OCSF 不再需要 ready 字段，直接视为有效
    # ======================
    valid_ev = evidence  # 全部都有效
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

    output_dir = f"outputs/{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    
    filepath = f"{output_dir}/readiness.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ 数据就绪度评分：{score} 分，等级：{level}，已保存到 {filepath}")
    return result

if __name__ == "__main__":
    # 读取当前时间戳
    with open("current_timestamp.txt", "r") as f:
        ts = f.read().strip()
    
    calculate_readiness(timestamp=ts)