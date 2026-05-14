import json
import os
from datetime import datetime

def generate_report():
    with open("readiness.json", "r", encoding="utf-8") as f:
        readiness = json.load(f)
    # 获取最新的 agent_result 文件
    import glob
    agent_files = glob.glob("outputs/agent_result_*.json")
    if not agent_files:
        print("❌ 未找到 agent_result 文件")
        return None
        
    latest_agent_file = max(agent_files, key=os.path.getctime)
    with open(latest_agent_file, "r", encoding="utf-8") as f:
        agent = json.load(f)
        
    # 获取最新的 verifier_result 文件
    verifier_files = glob.glob("outputs/verifier_result_*.json")
    if not verifier_files:
        print("❌ 未找到 verifier_result 文件")
        return None
        
    latest_verifier_file = max(verifier_files, key=os.path.getctime)
    with open(latest_verifier_file, "r", encoding="utf-8") as f:
        verifier = json.load(f)
    with open("evidence.json", "r", encoding="utf-8") as f:
        evidence = json.load(f)

    md = f"""
# 安全事件分析报告（数据编织 MVP）

## 一、数据就绪度
- 评分：**{readiness['score']} 分**
- 等级：**{readiness['level']}**
- 有效证据：{readiness['valid_count']} / {readiness['evidence_count']}

## 二、事件概述
{agent['summary']}

## 三、攻击时间线
"""
    for t in agent["timeline"]:
        md += f"- {t}\n"

    md += f"""
## 四、研判结论
> {verifier['final_conclusion']}
- 置信度：**{verifier['final_confidence']}**

## 五、处置建议
"""
    for a in agent["actions"]:
        md += f"- {a}\n"

    md += f"""
## 六、复核结果
- 复核状态：**{'通过' if verifier['passed'] else '未通过'}**
- 复核问题：
"""
    for issue in verifier["issues"]:
        md += f"  - {issue}\n"

    md += f"""
## 七、证据列表（关键）
"""
    for ev in evidence[:3]:
        md += f"""
- 时间：{ev['timestamp']}
- 规则：{ev['rule_id']} - {ev['description']}
- 来源：{ev['agent_ip']}
"""

    # 创建 outputs 目录（如果不存在）
    os.makedirs("outputs", exist_ok=True)
    
    # 生成带时间戳的文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"outputs/report_{timestamp}.md"
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"✅ Markdown 报告已生成：{filename}")
    return md

if __name__ == "__main__":
    generate_report()