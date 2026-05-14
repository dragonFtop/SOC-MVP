import json

def verify():
    with open("evidence.json","r",encoding="utf-8") as f:
        evi = json.load(f)
    with open("agent_report.md","r",encoding="utf-8") as f:
        report = f.read()

    issues = []
    if not evi["nginx"]:
        issues.append("缺少nginx日志证据")
    if not evi["wazuh"]:
        issues.append("缺少Wazuh告警证据")
    if "完全控制" in report or "已泄露" in report:
        issues.append("结论过度断言，超出证据范围")

    if not issues:
        return "✅ 复核通过：结论与证据匹配"
    else:
        return "❌ 复核不通过：\n- " + "\n- ".join(issues)

if __name__=="__main__":
    res = verify()
    print(res)
    with open("verifier_result.md","w",encoding="utf-8") as f:
        f.write(res)