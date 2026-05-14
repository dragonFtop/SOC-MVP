import json
import os
from datetime import datetime

def analyze_event():
    with open("evidence.json", "r", encoding="utf-8") as f:
        evidence = json.load(f)
    with open("readiness.json", "r", encoding="utf-8") as f:
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

    # 创建 outputs 目录（如果不存在）
    os.makedirs("outputs", exist_ok=True)
    
    # 生成带时间戳的文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"outputs/agent_result_{timestamp}.json"
    
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ Agent 研判完成，已生成 {filename}")
    return result

if __name__ == "__main__":
    analyze_event()