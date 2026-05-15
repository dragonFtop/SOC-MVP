import json
import os
from config import OUTPUTS_DIR

def generate_report(timestamp=None):
    if timestamp is None:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_dir = f"{OUTPUTS_DIR}/{timestamp}"
    os.makedirs(output_dir, exist_ok=True)

    # 读取各个模块结果
    def _load_json(filename):
        filepath = f"{output_dir}/{filename}"
        if not os.path.exists(filepath):
            print(f"   [WARN] 缺少文件: {filename}")
            return {}
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    evidence = _load_json("evidence.json")
    readiness = _load_json("readiness.json")
    agent = _load_json("agent_result.json")
    verifier = _load_json("verifier_result.json")

    # 报告内容
    report_content = f"""
# AI-SOC 安全研判报告
报告时间：{timestamp}

## 1. 数据就绪度评估
- 就绪度评分：{readiness.get('score', 'N/A')} 分
- 评估等级：{readiness.get('level', 'N/A')}
- 有效证据数：{readiness.get('valid_count', 'N/A')}

## 2. 安全证据（标准化）
"""

    if isinstance(evidence, list):
        for idx, ev in enumerate(evidence, 1):
            report_content += f"""
【证据 {idx}】
- 时间：{ev.get('timestamp', '未知')}
- 规则ID：{ev.get('rule_id', '未知')}
- 描述：{ev.get('description', '未知')}
- 源IP：{ev.get('src_ip', '未知')}
- 主机：{ev.get('hostname', '未知')}
"""
    else:
        report_content += "\n无证据数据\n"

    report_content += f"""
## 3. AI 研判结果
- 研判结论：{agent.get('conclusion', 'N/A')}
- 置信度：{agent.get('confidence', 'N/A')}

## 4. 处置建议
"""

    for act in agent.get("actions", []):
        report_content += f"- {act}\n"

    report_content += f"""
## 5. 复核结果（防AI幻觉）
- 复核状态：**{'通过' if verifier.get('verified') else '未通过'}**
- 发现问题：{verifier.get('issues', [])}
- 最终结论：{verifier.get('final_conclusion', 'N/A')}
- 最终置信度：{verifier.get('final_confidence', 'N/A')}

## 6. 总结
本报告基于数据编织架构，通过边缘按需取证、标准化映射、AI研判与复核验证，形成完整可信的安全事件分析结果。
"""

    # 保存报告
    with open(f"{output_dir}/report.md", "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"✅ 报告已生成：{output_dir}/report.md")
    return report_content

if __name__ == "__main__":
    from datetime import datetime
    generate_report(timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"))
