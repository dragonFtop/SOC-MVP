from server import *
from client import *
from datetime import datetime
import os
import json
import asyncio
import traceback
from config import OUTPUTS_DIR, DEFAULT_RULE_ID, DEFAULT_QUERY_LIMIT


def main():
    """
    AI-SOC 主执行脚本

    执行流程（对应实现方案九步）：
      1. 边缘信号生成 (signal_generator)
      2. 信令发布到 NATS (signal_generator.publish_signals_to_nats)
      3. 证据构建与按需查询 (evidence_builder + duckdb_sidecar)
      4. 元数据注册与查询计划 (query_gateway)
      5. 证据固化与OCSF标准化 (evidence_builder + ocsf_mapper)
      6. 数据质量门控 (readiness)
      7. Agent Team 多Agent研判 (agent_team)
      8. 复核校验 (verifier)
      9. 报告生成与可视化 (report_generator + dashboard)
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_dir = f"{OUTPUTS_DIR}/{timestamp}"
    os.makedirs(output_dir, exist_ok=True)

    print(f"=" * 60)
    print(f"  AI-SOC 智能安全运营中心 - 自动研判流程")
    print(f"  启动时间: {datetime.now().isoformat()}")
    print(f"  输出目录: {output_dir}")
    print(f"=" * 60)
    print()

    # ==================== Step 1-2: 信号生成与发布 ====================
    print("\n[Step 1-2] 边缘信号生成与发布...")
    try:
        from client.signal_generator import generate_and_publish
        signals, count = asyncio.run(generate_and_publish(rule_id=DEFAULT_RULE_ID, limit=DEFAULT_QUERY_LIMIT))
        print(f"   OK 生成 {len(signals)} 条微信号，发布 {count} 条到 NATS")
    except ImportError:
        print("   [WARN] signal_generator 模块未安装 (nats-py)，跳过 NATS 发布")
        print("   -> 直接执行本地证据构建")
    except (FileNotFoundError, OSError) as e:
        print(f"   [WARN] 信号生成失败 (数据源不可用): {e}")
        print("   -> 继续执行后续流程")
    except Exception as e:
        print(f"   [WARN] 信号生成异常: {e}")
        traceback.print_exc()
        print("   -> 继续执行后续流程")

    # ==================== Step 3-5: 证据构建与查询 ====================
    print("\n[Step 3-5] 证据构建与按需查询...")
    try:
        build_evidence(limit=DEFAULT_QUERY_LIMIT, timestamp=timestamp)
    except (FileNotFoundError, OSError) as e:
        print(f"   [ERROR] 证据构建失败 (数据源不可用): {e}")
        print("   -> 无法继续，流程终止")
        return
    except ImportError as e:
        print(f"   [ERROR] 证据构建失败 (缺少依赖): {e}")
        print("   -> 请检查: pip install -r requirements.txt")
        return
    except Exception as e:
        print(f"   [ERROR] 证据构建失败: {e}")
        traceback.print_exc()
        print("   -> 无法继续，流程终止")
        return

    # ==================== Step 6: 数据质量门控 ====================
    print("\n[Step 6] 数据质量门控 (Readiness Agent)...")
    calculate_readiness(timestamp=timestamp)

    # ==================== Step 7: Agent Team 多Agent研判 ====================
    print("\n[Step 7] Agent Team 多Agent研判...")
    try:
        from server.agent_team import run_analysis

        evidence_path = f"{OUTPUTS_DIR}/{timestamp}/evidence.json"
        with open(evidence_path, "r", encoding="utf-8") as f:
            evidence = json.load(f)

        draft = run_analysis(evidence, timestamp)
        print(f"   OK Agent Team 研判完成")
        print(f"      - 分诊: {draft['triage']['event_type']} (优先级: {draft['triage']['priority']})")
        print(f"      - 建议操作: {len(draft['suggested_actions'])} 条")

        agent_result = {
            "summary": draft["triage"]["summary"],
            "timeline": [],
            "conclusion": f"{draft['triage']['summary']}。攻击链状态: {draft.get('attack_chain', {}).get('progress', '未分析')}",
            "confidence": "高" if draft["triage"]["confidence"] == "high" else ("中" if draft["triage"]["confidence"] == "medium" else "低"),
            "actions": draft["suggested_actions"],
            "evidence_ref": draft["evidence_ref"],
            "event_type": draft["triage"]["event_type"],
            "priority": draft["triage"]["priority"],
        }

        with open(f"{output_dir}/agent_result.json", "w", encoding="utf-8") as f:
            json.dump(agent_result, f, indent=2, ensure_ascii=False)

    except (ImportError, ModuleNotFoundError) as e:
        print(f"   [WARN] Agent Team 模块导入失败: {e}")
        print("   -> 回退到传统 agent_analyzer")
        analyze_event(timestamp=timestamp)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"   [WARN] 证据文件读取失败: {e}")
        print("   -> 回退到传统 agent_analyzer")
        analyze_event(timestamp=timestamp)
    except Exception as e:
        print(f"   [WARN] Agent Team 研判异常: {e}")
        traceback.print_exc()
        print("   -> 回退到传统 agent_analyzer")
        analyze_event(timestamp=timestamp)

    # ==================== Step 8: 复核校验 ====================
    print("\n[Step 8] 复核校验 (Verifier Agent)...")
    try:
        verify(timestamp=timestamp)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"   [WARN] 复核数据不完整: {e}")
    except Exception as e:
        print(f"   [WARN] 复核校验异常: {e}")
        traceback.print_exc()

    # ==================== 持久化到 OpenSearch ====================
    print("\n[辅助] 证据持久化到 OpenSearch...")
    try:
        load_to_opensearch(timestamp=timestamp)
    except (ConnectionError, ConnectionRefusedError, OSError) as e:
        print(f"   [WARN] OpenSearch 连接失败: {e}")
        print("   -> 数据已本地保存，可稍后重试")
    except Exception as e:
        print(f"   [WARN] OpenSearch 写入异常: {e}")
        traceback.print_exc()
        print("   -> 数据已本地保存，可稍后重试")

    # ==================== Step 9: 报告生成 ====================
    print("\n[Step 9] 研判报告生成...")
    try:
        generate_report(timestamp=timestamp)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"   [WARN] 报告生成失败 (数据不完整): {e}")
    except Exception as e:
        print(f"   [WARN] 报告生成异常: {e}")
        traceback.print_exc()

    print()
    print(f"=" * 60)
    print(f"  OK AI-SOC 研判流程执行完毕")
    print(f"  所有文件已保存到: {output_dir}")
    print(f"  启动 Dashboard 查看: cd MVP && streamlit run server/dashboard.py")
    print(f"=" * 60)


if __name__ == "__main__":
    main()
