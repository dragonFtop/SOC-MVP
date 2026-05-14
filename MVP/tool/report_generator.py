import json
import os

def generate_report(timestamp=None):
    output_dir = f"outputs/{timestamp}"
    os.makedirs(output_dir, exist_ok=True)

    # 读取各个模块结果
    with open(f"{output_dir}/evidence.json", "r", encoding="utf-8") as f:
        evidence = json.load(f)

    with open(f"{output_dir}/readiness.json", "r", encoding="utf-8") as f:
        readiness = json.load(f)

    with open(f"{output_dir}/agent_result.json", "r", encoding="utf-8") as f:
        agent = json.load(f)

    with open(f"{output_dir}/verifier_result.json", "r", encoding="utf-8") as f:
        verifier = json.load(f)

    # 报告内容
    report_content = f"""
# AI-SOC 安全研判报告
报告时间：{timestamp}

## 1. 数据就绪度评估
- 就绪度评分：{readiness['score']} 分
- 评估等级：{readiness['level']}
- 有效证据数：{readiness['valid_count']}

## 2. 安全证据（标准化）
"""

    for idx, ev in enumerate(evidence, 1):
        report_content += f"""
【证据 {idx}】
- 时间：{ev.get('timestamp', '未知')}
- 规则ID：{ev.get('rule_id', '未知')}
- 描述：{ev.get('description', '未知')}
- 源IP：{ev.get('src_ip', '未知')}
- 主机：{ev.get('hostname', '未知')}
"""

    report_content += f"""
## 3. AI 研判结果
- 研判结论：{agent['conclusion']}
- 置信度：{agent['confidence']}

## 4. 处置建议
"""

    for act in agent["actions"]:
        report_content += f"- {act}\n"

    # 🔥 这里修复：从 passed → verified
    report_content += f"""
## 5. 复核结果（防AI幻觉）
- 复核状态：**{'通过' if verifier['verified'] else '未通过'}**
- 发现问题：{verifier['issues']}
- 最终结论：{verifier['final_conclusion']}
- 最终置信度：{verifier['final_confidence']}

## 6. 总结
本报告基于数据编织架构，通过边缘按需取证、标准化映射、AI研判与复核验证，形成完整可信的安全事件分析结果。
"""

    # 保存报告
    with open(f"{output_dir}/report.md", "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"✅ 报告已生成：{output_dir}/report.md")
    return report_content

if __name__ == "__main__":
    generate_report()