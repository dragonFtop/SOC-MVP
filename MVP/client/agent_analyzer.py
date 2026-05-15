import json
import os
from config import OUTPUTS_DIR, DEFAULT_RULE_ID

def analyze_event(timestamp=None):

    if timestamp is None:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    evidence_path = f"{OUTPUTS_DIR}/{timestamp}/evidence.json"
    readiness_path = f"{OUTPUTS_DIR}/{timestamp}/readiness.json"

    if not os.path.exists(evidence_path):
        print(f"   [WARN] 证据文件不存在: {evidence_path}")
        return {"summary": "证据文件不存在，无法研判", "confidence": "低", "actions": []}
    if not os.path.exists(readiness_path):
        print(f"   [WARN] 就绪度文件不存在: {readiness_path}")
        readiness = {"score": 0}
    else:
        with open(readiness_path, "r", encoding="utf-8") as f:
            readiness = json.load(f)

    with open(evidence_path, "r", encoding="utf-8") as f:
        evidence = json.load(f)

    if readiness["score"] < 40:
        return {"summary": "数据不足，无法研判", "confidence": "低", "actions": ["补充日志"]}

    alerts = [ev for ev in evidence if ev.get("rule_id") == DEFAULT_RULE_ID]

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

    output_dir = f"{OUTPUTS_DIR}/{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    
    filepath = f"{output_dir}/agent_result.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ Agent 研判完成，已生成 {filepath}")
    return result

if __name__ == "__main__":
    from datetime import datetime
    analyze_event(timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"))