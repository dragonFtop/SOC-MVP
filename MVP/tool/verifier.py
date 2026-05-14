import json
import os

def get_current_timestamp():
    """获取当前时间戳，优先从文件读取，如果没有则使用最新的输出目录"""
    try:
        with open("current_timestamp.txt", "r", encoding="utf-8") as f:
            timestamp_from_file = f.read().strip()
        
        # 检查目录是否存在
        if os.path.exists(f"outputs/{timestamp_from_file}"):
            return timestamp_from_file
        else:
            print(f"⚠️  时间戳目录不存在: outputs/{timestamp_from_file}, 尝试查找最新的目录...")
    except FileNotFoundError:
        print("⚠️  未找到时间戳文件, 尝试查找最新的输出目录...")
    
    # 如果时间戳文件不存在或目录不存在，查找最新的输出目录
    import glob
    dirs = glob.glob("outputs/*")
    if dirs:
        latest_dir = max(dirs, key=os.path.getctime)
        timestamp = os.path.basename(latest_dir)
        print(f"✅ 找到最新的输出目录: {timestamp}")
        return timestamp
    else:
        raise Exception("找不到时间戳目录，请先运行 evidence_builder.py")

def verify():
    timestamp = get_current_timestamp()
    agent_path = f"outputs/{timestamp}/agent_result.json"
    evidence_path = f"outputs/{timestamp}/evidence.json"
    
    # 检查必要文件是否存在
    if not os.path.exists(agent_path):
        raise FileNotFoundError(f"找不到文件: {agent_path}，请确保先运行 agent_analyzer.py")
    if not os.path.exists(evidence_path):
        raise FileNotFoundError(f"找不到文件: {evidence_path}，请确保先运行 evidence_builder.py")
    
    with open(agent_path, "r", encoding="utf-8") as f:
        agent = json.load(f)
    with open(evidence_path, "r", encoding="utf-8") as f:
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

    output_dir = f"outputs/{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    
    filepath = f"{output_dir}/verifier_result.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ Verifier 复核完成：{filepath}，结果：{'通过' if passed else '不通过'}")
    return result

if __name__ == "__main__":
    verify()