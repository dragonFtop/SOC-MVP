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

def analyze_event():
    timestamp = get_current_timestamp()
    evidence_path = f"outputs/{timestamp}/evidence.json"
    readiness_path = f"outputs/{timestamp}/readiness.json"
    
    # 检查必要文件是否存在
    if not os.path.exists(evidence_path):
        raise FileNotFoundError(f"找不到文件: {evidence_path}，请确保先运行 evidence_builder.py")
    if not os.path.exists(readiness_path):
        raise FileNotFoundError(f"找不到文件: {readiness_path}，请确保先运行 readiness.py")
    
    with open(evidence_path, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    with open(readiness_path, "r", encoding="utf-8") as f:
        readiness = json.load(f)

    if readiness["score"] < 40:
        return {"summary": "数据不足，无法研判", "confidence": "低", "actions": ["补充日志"]}

    alerts = [ev for ev in evidence if ev["rule_id"] == "5503"]

    if alerts:
        summary = "检测到多次本地登录失败事件，疑似暴力破解尝试。"
        timeline = [f"{ev['timestamp']}：登录失败（{ev['rule_id']}）" for ev in alerts[:3]]
        conclusion = "存在登录爆破风险，需加固认证策略。"
        confidence = "中高"
        actions = ["锁定异常账号", "限制登录次数", "开启二次认证"]
    else:
        summary = "未检测到明显安全事件"
        timeline = []
        conclusion = "系统当前状态正常"
        confidence = "中"
        actions = ["持续监控"]

    result = {
        "summary": summary,
        "timeline": timeline,
        "conclusion": conclusion,
        "confidence": confidence,
        "actions": actions,
        "readiness_score": readiness["score"]
    }

    output_dir = f"outputs/{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    
    filepath = f"{output_dir}/agent_result.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ Agent 研判完成，已生成 {filepath}")
    return result

if __name__ == "__main__":
    analyze_event()