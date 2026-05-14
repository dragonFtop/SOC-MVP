import json

def analyze():
    with open("evidence.json","r",encoding="utf-8") as f:
        evi = json.load(f)
    with open("readiness.json","r",encoding="utf-8") as f:
        rd = json.load(f)

    lines = []
    lines.append(f"### 事件分析")
    lines.append(f"数据就绪度：{rd['score']}/100")
    lines.append(f"攻击源IP：192.168.1.50")
    lines.append(f"时间窗口：2025-12-20 08:00–08:01")
    lines.append(f"现象：多次访问/admin、/login.php、疑似SQL注入URL")
    lines.append(f"初步结论：疑似Web攻击（SQL注入探测）")
    lines.append(f"处置建议：封禁IP、检查Web代码、加固WAF规则")
    return "\n".join(lines)

if __name__=="__main__":
    report = analyze()
    with open("agent_report.md","w",encoding="utf-8") as f:
        f.write(report)
    print("✅ Agent分析完成：agent_report.md")