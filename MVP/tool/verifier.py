import json
import os

def verify(timestamp=None):
        
    agent_path = f"outputs/{timestamp}/agent_result.json"
    evidence_path = f"outputs/{timestamp}/evidence.json"
    readiness_path = f"outputs/{timestamp}/readiness.json"

    # 读取证据
    with open(agent_path, "r", encoding="utf-8") as f:
        agent = json.load(f)
    with open(evidence_path, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    with open(readiness_path, "r", encoding="utf-8") as f:
        readiness = json.load(f)

    issues = []
    verified = True  # 🔥 这里改成 verified，和 dashboard 对应
    readiness_score = readiness.get("score", 0)

    # 1. 高置信度必须有足够证据
    if agent["confidence"] == "高" and len(evidence) < 5:
        issues.append("置信度过高，证据不足")
        verified = False

    # 2. 结论不能绝对化
    if any(word in agent["conclusion"] for word in ["完全控制", "已被攻陷", "数据泄露"]):
        issues.append("结论存在绝对化表述，风险过高")
        verified = False

    # 3. 数据就绪度低于60不能给中高置信度
    if readiness_score < 60 and agent["confidence"] in ["中", "中高"]:
        issues.append("数据就绪度不足，置信度过高")
        verified = False

    # 输出结果（字段标准化）
    result = {
        "verified": verified,  
        "issues": issues,
        "final_confidence": "中" if verified else "低",
        "final_conclusion": agent["conclusion"] if verified else "证据有限，存在登录爆破风险，结论谨慎"
    }

    output_dir = f"outputs/{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    
    filepath = f"{output_dir}/verifier_result.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ Verifier 复核完成：{filepath}，结果：{'通过' if verified else '不通过'}")
    return result

if __name__ == "__main__":
    verify()