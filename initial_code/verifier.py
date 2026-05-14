import json
import os
from datetime import datetime

def verify():
    # 获取最新的 agent_result 文件
    import glob
    agent_files = glob.glob("outputs/agent_result_*.json")
    if not agent_files:
        print("❌ 未找到 agent_result 文件")
        return None
        
    latest_agent_file = max(agent_files, key=os.path.getctime)
    with open(latest_agent_file, "r", encoding="utf-8") as f:
        agent = json.load(f)
    with open("evidence.json", "r", encoding="utf-8") as f:
        evidence = json.load(f)

    issues = []
    passed = True

    # 1. 高置信度必须有足够证据
    if agent["confidence"] == "高" and len(evidence) < 5:
        issues.append("置信度过高，证据不足")
        passed = False

    # 2. 结论不能绝对化
    if any(word in agent["conclusion"] for word in ["完全控制", "已被攻陷", "数据泄露"]):
        issues.append("结论存在绝对化表述，风险过高")
        passed = False

    # 3. 数据就绪度低于60不能给中高置信度
    if agent["readiness_score"] < 60 and agent["confidence"] in ["中", "中高"]:
        issues.append("数据就绪度不足，置信度过高")
        passed = False

    result = {
        "passed": passed,
        "issues": issues,
        "final_confidence": "中" if passed else "低",
        "final_conclusion": agent["conclusion"] if passed else "证据有限，存在登录爆破风险，结论谨慎"
    }

    # 创建 outputs 目录（如果不存在）
    os.makedirs("outputs", exist_ok=True)
    
    # 生成带时间戳的文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"outputs/verifier_result_{timestamp}.json"
    
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ Verifier 复核完成：{filename}，结果：{'通过' if passed else '不通过'}")
    return result

if __name__ == "__main__":
    verify()