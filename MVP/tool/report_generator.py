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

def generate_report():
    timestamp = get_current_timestamp()
    readiness_path = f"outputs/{timestamp}/readiness.json"
    agent_path = f"outputs/{timestamp}/agent_result.json"
    verifier_path = f"outputs/{timestamp}/verifier_result.json"
    evidence_path = f"outputs/{timestamp}/evidence.json"
    
    # 检查所有必要文件是否存在
    for path, script in [(readiness_path, "readiness.py"), 
                         (agent_path, "agent_analyzer.py"), 
                         (verifier_path, "verifier.py"), 
                         (evidence_path, "evidence_builder.py")]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"找不到文件: {path}，请确保先运行 {script}")
    
    with open(readiness_path, "r", encoding="utf-8") as f:
        readiness = json.load(f)
    with open(agent_path, "r", encoding="utf-8") as f:
        agent = json.load(f)
    with open(verifier_path, "r", encoding="utf-8") as f:
        verifier = json.load(f)
    with open(evidence_path, "r", encoding="utf-8") as f:
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

    output_dir = f"outputs/{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    
    filepath = f"{output_dir}/report.md"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"✅ Markdown 报告已生成：{filepath}")
    return md

if __name__ == "__main__":
    generate_report()