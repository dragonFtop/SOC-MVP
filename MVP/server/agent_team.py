# MVP/server/agent_team.py
"""
Agent Team - 多Agent安全事件分析架构
=========================================
职责：
  1. Triage Agent（分诊）：判断事件类型、紧急程度、优先级
  2. Attack Chain Agent（攻击链）：将证据映射到攻击链条
  3. Report Agent（报告草稿）：整合分析结果，生成研判草稿
  4. 只读标准字段与 evidence_ref，保证数据一致性

支持两种模式：
  - LLM 模式（默认）：调用 DeepSeek API 进行智能研判
  - Rule 模式（回退）：使用硬编码规则引擎

对应实现方案：第七章 - Agent Team 简化研判
"""

import json
import os as _os
import sys as _sys
import uuid
from datetime import datetime
from typing import Optional

# 确保 MVP/ 在 Python path 中（兼容直接运行）
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from config import (
    OUTPUTS_DIR,
    DEFAULT_RULE_ID,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLM_MODEL_TRIAGE,
    LLM_MODEL_ATTACK_CHAIN,
    LLM_MODEL_REPORT,
    ANTHROPIC_API_KEY,
    ANTHROPIC_BASE_URL,
    ANTHROPIC_MODEL,
    LLM_PROVIDER,
)


def _get_llm_client():
    """延迟初始化 LLM 客户端 (DeepSeek 或 Anthropic)。无 API key 时返回 None。"""
    if LLM_PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            return None
        try:
            from anthropic import Anthropic
            import os as _os
            saved = {}
            for key in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
                saved[key] = _os.environ.pop(key, None)
            try:
                client = Anthropic(
                    api_key=ANTHROPIC_API_KEY,
                    base_url=ANTHROPIC_BASE_URL,
                )
            finally:
                for key, val in saved.items():
                    if val is not None:
                        _os.environ[key] = val
            return ("anthropic", client)
        except ImportError:
            return None

    # 默认 DeepSeek (OpenAI 兼容接口)
    if not DEEPSEEK_API_KEY:
        return None
    try:
        from openai import OpenAI
        return ("openai", OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        ))
    except ImportError:
        return None


# ====================== 数据模型 ======================

class TriageResult:
    """分诊结果"""
    def __init__(self, priority: str, event_type: str, summary: str, confidence: str):
        self.priority = priority       # critical / high / medium / low
        self.event_type = event_type   # brute_force / scanning / malware / etc.
        self.summary = summary
        self.confidence = confidence   # high / medium / low

    def to_dict(self) -> dict:
        return {
            "priority": self.priority,
            "event_type": self.event_type,
            "summary": self.summary,
            "confidence": self.confidence,
        }


class AttackChainNode:
    """攻击链节点"""
    def __init__(self, kill_chain_phase: str, evidence_ids: list, description: str):
        self.kill_chain_phase = kill_chain_phase
        self.evidence_ids = evidence_ids
        self.description = description

    def to_dict(self) -> dict:
        return {
            "kill_chain_phase": self.kill_chain_phase,
            "evidence_ids": self.evidence_ids,
            "description": self.description,
        }


class AttackChainResult:
    """攻击链分析结果"""
    def __init__(self, chain: list, progress: str):
        self.chain = chain
        self.progress = progress

    def to_dict(self) -> dict:
        return {
            "chain": [node.to_dict() for node in self.chain],
            "progress": self.progress,
        }


class AnalysisDraft:
    """研判草稿"""
    def __init__(
        self,
        triage: TriageResult,
        attack_chain: Optional[AttackChainResult],
        evidence_ref: list,
        suggested_actions: list,
    ):
        self.triage = triage
        self.attack_chain = attack_chain
        self.evidence_ref = evidence_ref
        self.suggested_actions = suggested_actions

    def to_dict(self) -> dict:
        result = {
            "draft_id": f"draft-{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now().isoformat(),
            "triage": self.triage.to_dict(),
            "evidence_ref": self.evidence_ref,
            "suggested_actions": self.suggested_actions,
        }
        if self.attack_chain:
            result["attack_chain"] = self.attack_chain.to_dict()
        return result


# ====================== LLM 辅助函数 ======================

def _build_evidence_summary(evidence: list) -> str:
    """将证据列表压缩为 LLM 可处理的文本摘要"""
    lines = []
    for ev in evidence[:20]:  # 限制最多20条，防止上下文溢出
        lines.append(
            f"[{ev.get('evidence_id', '?')}] "
            f"time={ev.get('timestamp', '?')} "
            f"rule={ev.get('rule_id', '?')} "
            f"severity={ev.get('severity', '?')} "
            f"src_ip={ev.get('src_ip', '?')} "
            f"host={ev.get('hostname', '?')} "
            f"desc={ev.get('description', '?')}"
        )
    return "\n".join(lines)


def _call_llm(system_prompt: str, user_message: str, max_tokens: int = 1024, model: str = None) -> Optional[dict]:
    """
    调用 LLM API 进行推理，返回 JSON 结果。

    Args:
        model: 覆盖默认模型 (DeepSeek) 或 Anthropic 模型名
    """
    client_info = _get_llm_client()
    if client_info is None:
        return None

    provider, client = client_info

    try:
        if provider == "anthropic":
            response = client.messages.create(
                model=model or ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
        else:
            # DeepSeek / OpenAI 兼容接口
            response = client.chat.completions.create(
                model=model or DEEPSEEK_MODEL,
                max_tokens=max_tokens,
                temperature=0.1,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            text = response.choices[0].message.content or ""

        if not text:
            return None
        # 提取 JSON（可能在 ```json 块中）
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        return json.loads(text)
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"   [LLM] JSON 解析失败: {e}")
        return None
    except Exception as e:
        print(f"   [LLM] API 调用失败: {e}")
        return None


# ====================== Triage Agent ======================

# 规则引擎的分诊配置（LLM 不可用时的回退）
TRIAGE_RULES = {
    # 新版 DetectionEngine 规则
    "LOCAL_SSH_BRUTE_FORCE": {"event_type": "brute_force", "priority": "high"},
    "LOCAL_SSH_SCAN": {"event_type": "reconnaissance", "priority": "medium"},
    "LOCAL_SUDO_FAILURES": {"event_type": "privilege_escalation", "priority": "high"},
    "LOCAL_SU_FAILURES": {"event_type": "privilege_escalation", "priority": "medium"},
    "LOCAL_SSH_ACCEPTED": {"event_type": "unknown", "priority": "low"},
    # 旧版 Wazuh 规则 (兼容)
    DEFAULT_RULE_ID: {"event_type": "brute_force", "priority": "high"},
    "5710": {"event_type": "scanning", "priority": "medium"},
    "5501": {"event_type": "malware_detected", "priority": "critical"},
    "5712": {"event_type": "reconnaissance", "priority": "medium"},
    "5763": {"event_type": "privilege_escalation", "priority": "critical"},
    "5715": {"event_type": "lateral_movement", "priority": "high"},
    "5502": {"event_type": "data_exfiltration", "priority": "critical"},
}

TRIAGE_SYSTEM_PROMPT = """你是一个 SOC 安全分析团队的分诊 (Triage) 专家。你需要分析安全告警证据，判断事件类型、优先级和置信度。

请只返回 JSON，不要有任何其他文字：
```json
{
  "priority": "critical|high|medium|low",
  "event_type": "brute_force|scanning|malware_detected|reconnaissance|privilege_escalation|lateral_movement|data_exfiltration|unknown",
  "summary": "用中文简要描述检测到的安全事件（1-2句话）",
  "confidence": "high|medium|low"
}
```

分析要点：
- 多条相同 rule_id 的告警通常意味着自动化攻击（如爆破、扫描）
- 严重度(severity)越高，优先级越高
- 证据数量越多，置信度越高
- event_type 根据 rule_id 描述和实际日志内容判断"""


class TriageAgent:
    """分诊Agent：判断事件类型、优先级、紧急程度"""

    def analyze(self, evidence: list) -> TriageResult:
        if not evidence:
            return TriageResult(
                priority="low", event_type="unknown",
                summary="无证据，无法分诊", confidence="low",
            )

        # 优先尝试 LLM (Triage 用轻量模型: deepseek-chat)
        evidence_text = _build_evidence_summary(evidence)
        llm_result = _call_llm(
            TRIAGE_SYSTEM_PROMPT,
            f"请分析以下安全告警证据：\n\n{evidence_text}",
            model=LLM_MODEL_TRIAGE,
        )

        if llm_result:
            return TriageResult(
                priority=llm_result.get("priority", "medium"),
                event_type=llm_result.get("event_type", "unknown"),
                summary=llm_result.get("summary", "LLM 分析完成"),
                confidence=llm_result.get("confidence", "medium"),
            )

        # 规则引擎回退
        return self._rule_based_analyze(evidence)

    def _rule_based_analyze(self, evidence: list) -> TriageResult:
        """规则引擎回退分析"""
        rule_counts = {}
        for ev in evidence:
            rid = ev.get("rule_id") or ev.get("rule.id", "unknown")
            rule_counts[rid] = rule_counts.get(rid, 0) + 1

        max_priority = 0
        best_event = None
        best_rule = None
        priority_map = {"critical": 4, "high": 3, "medium": 2, "low": 1}

        for rid, count in rule_counts.items():
            info = TRIAGE_RULES.get(rid)
            if info:
                p = priority_map.get(info["priority"], 1)
                if p > max_priority or (p == max_priority and count > rule_counts.get(best_rule, 0)):
                    max_priority = p
                    best_event = info
                    best_rule = rid

        if best_event:
            total_count = sum(rule_counts.values())
            confidence = "high" if total_count >= 5 else ("medium" if total_count >= 3 else "low")
            return TriageResult(
                priority=best_event["priority"],
                event_type=best_event["event_type"],
                summary=f"检测到 {total_count} 条告警，主要类型: {best_event['event_type']}（规则ID: {best_rule}）",
                confidence=confidence,
            )

        total = len(evidence)
        return TriageResult(
            priority="medium" if total >= 5 else "low",
            event_type="unknown",
            summary=f"检测到 {total} 条未分类告警",
            confidence="low",
        )


# ====================== Attack Chain Agent ======================

ATTACK_CHAIN_SYSTEM_PROMPT = """你是一个攻击链 (Kill Chain) 分析专家。你需要将安全告警证据映射到 Lockheed Martin Cyber Kill Chain 的 7 个阶段。

可用的阶段：
1. 侦查(Reconnaissance) - 信息收集、端口扫描、漏洞探测
2. 武器化(Weaponization) - 准备攻击工具、生成payload
3. 交付(Delivery) - 钓鱼邮件、恶意文件投递
4. 利用(Exploitation) - 漏洞利用、暴力破解、代码执行
5. 安装(Installation) - 恶意软件安装、后门植入
6. 命令与控制(C2) - 建立远程控制通道
7. 目标行动(Actions on Objectives) - 数据窃取、权限提升、横向移动

请只返回 JSON：
```json
{
  "phases": [
    {"phase": "利用(Exploitation)", "evidence_ids": ["ev-xxx"], "description": "检测到SSH暴力破解"},
    {"phase": "侦查(Reconnaissance)", "evidence_ids": ["ev-yyy"], "description": "检测到端口扫描"}
  ],
  "progress": "攻击链当前处于「利用」阶段，暂未发现后续横向移动痕迹"
}
```"""


class AttackChainAgent:
    """攻击链分析Agent：将证据映射到攻击链阶段"""

    def analyze(self, evidence: list) -> AttackChainResult:
        if not evidence:
            return AttackChainResult(chain=[], progress="无证据，无法分析攻击链")

        # 优先尝试 LLM (AttackChain 用推理模型: deepseek-reasoner)
        evidence_text = _build_evidence_summary(evidence)
        llm_result = _call_llm(
            ATTACK_CHAIN_SYSTEM_PROMPT,
            f"请将以下证据映射到攻击链阶段：\n\n{evidence_text}",
            model=LLM_MODEL_ATTACK_CHAIN,
        )

        if llm_result:
            chain_nodes = []
            for p in llm_result.get("phases", []):
                chain_nodes.append(AttackChainNode(
                    kill_chain_phase=p.get("phase", "未知"),
                    evidence_ids=p.get("evidence_ids", []),
                    description=p.get("description", ""),
                ))
            return AttackChainResult(
                chain=chain_nodes,
                progress=llm_result.get("progress", "攻击链分析完成"),
            )

        # 规则引擎回退
        return self._rule_based_analyze(evidence)

    def _rule_based_analyze(self, evidence: list) -> AttackChainResult:
        """按 severity 区间简单映射攻击链阶段"""
        chain_nodes = {}
        for ev in evidence:
            severity = _parse_severity(ev)
            rid = ev.get("rule_id", "")
            eid = ev.get("evidence_id", "")
            description = ev.get("description", "")
            phase = self._map_to_kill_chain_phase(severity, rid)
            if phase:
                if phase not in chain_nodes:
                    chain_nodes[phase] = AttackChainNode(
                        kill_chain_phase=phase,
                        evidence_ids=[],
                        description=description or f"规则 {rid} 触发的告警",
                    )
                chain_nodes[phase].evidence_ids.append(eid)

        chain = list(chain_nodes.values())
        phases_found = [n.kill_chain_phase for n in chain]
        return AttackChainResult(
            chain=chain,
            progress=f"攻击链进展: {', '.join(phases_found) if phases_found else '尚未确定'}",
        )

    @staticmethod
    def _map_to_kill_chain_phase(severity: int, rule_id: str) -> str:
        if severity <= 3:
            return "侦查(Reconnaissance)"
        elif severity <= 6:
            return "武器化(Weaponization)"
        elif severity <= 9:
            return "利用(Exploitation)"
        elif severity <= 12:
            return "命令与控制(C2)"
        elif severity <= 15:
            return "目标行动(Actions on Objectives)"
        # 按规则ID补充映射
        rule_phase_map = {
            # 新版 DetectionEngine 规则
            "LOCAL_SSH_BRUTE_FORCE": "利用(Exploitation)",
            "LOCAL_SSH_SCAN": "侦查(Reconnaissance)",
            "LOCAL_SUDO_FAILURES": "目标行动(Actions on Objectives)",
            "LOCAL_SU_FAILURES": "目标行动(Actions on Objectives)",
            "LOCAL_SSH_ACCEPTED": "利用(Exploitation)",
            # 旧版 Wazuh 规则
            DEFAULT_RULE_ID: "利用(Exploitation)",
            "5710": "侦查(Reconnaissance)",
            "5501": "目标行动(Actions on Objectives)",
        }
        return rule_phase_map.get(rule_id, "侦查(Reconnaissance)")


# ====================== Report Agent ======================

REPORT_SYSTEM_PROMPT = """你是一个 SOC 安全报告撰写专家。你需要根据分诊结果和攻击链分析，生成具体的处置建议。

请只返回 JSON：
```json
{
  "suggested_actions": [
    "具体可执行的处置措施1",
    "具体可执行的处置措施2"
  ],
  "risk_assessment": "用中文评估当前风险等级和影响范围（1-2句）"
}
```

注意：建议必须具体、可执行、有优先级。不要说"建议加强安全"这类空话。"""

# 规则引擎的回退建议模板
ACTION_TEMPLATES = {
    "brute_force": [
        "锁定异常源IP地址",
        "启用多因素认证(MFA)",
        "限制登录频率（如 5次/分钟）",
        "审计相关用户账号",
    ],
    "scanning": [
        "添加WAF规则拦截扫描IP",
        "封禁扫描来源IP段",
        "启用入侵检测规则",
    ],
    "malware_detected": [
        "立即隔离受影响主机",
        "全盘扫描检测恶意软件",
        "上报应急响应团队",
        "检查横向移动迹象",
    ],
    "reconnaissance": [
        "加强网络监控",
        "加固公开服务",
        "审计访问日志",
    ],
    "privilege_escalation": [
        "立即审查权限变更",
        "重置管理员密码",
        "审计所有特权账号",
    ],
    "lateral_movement": [
        "隔离受影响网段",
        "检查所有横向连接",
        "启动应急响应流程",
    ],
    "data_exfiltration": [
        "立即阻断外连流量",
        "检查所有数据传输",
        "启动数据泄露应急响应",
    ],
    "unknown": [
        "持续监控告警趋势",
        "收集更多上下文信息",
        "更新规则库",
    ],
}


class ReportAgent:
    """报告生成Agent：整合分诊结果和攻击链分析，生成研判草稿"""

    def generate_draft(
        self,
        triage: TriageResult,
        attack_chain: AttackChainResult,
        evidence: list,
    ) -> AnalysisDraft:
        evidence_ref = [ev.get("evidence_id", "") for ev in evidence if ev.get("evidence_id")]

        # 优先尝试 LLM (Report 用 chat 模型, tokens 扩容以容纳详细建议)
        llm_result = _call_llm(
            REPORT_SYSTEM_PROMPT,
            f"事件类型: {triage.event_type}\n"
            f"优先级: {triage.priority}\n"
            f"分诊总结: {triage.summary}\n"
            f"攻击链: {attack_chain.progress}\n"
            f"证据数量: {len(evidence)}",
            max_tokens=2048,
            model=LLM_MODEL_REPORT,
        )

        if llm_result:
            actions = llm_result.get("suggested_actions", [])
        else:
            actions = ACTION_TEMPLATES.get(triage.event_type, ACTION_TEMPLATES["unknown"])

        return AnalysisDraft(
            triage=triage,
            attack_chain=attack_chain,
            evidence_ref=evidence_ref,
            suggested_actions=actions,
        )


# ====================== Team 协调器 ======================

class AgentTeamCoordinator:
    """Agent Team 协调器：编排多Agent协作流程"""

    def __init__(self):
        self.triage_agent = TriageAgent()
        self.attack_chain_agent = AttackChainAgent()
        self.report_agent = ReportAgent()

    def analyze(self, evidence: list, timestamp: Optional[str] = None) -> AnalysisDraft:
        print("🔄 [AgentTeam] 开始多Agent协同研判...")

        # Step 1: 分诊
        triage_result = self.triage_agent.analyze(evidence)
        print(f"   ✅ Triage: {triage_result.event_type} (优先级: {triage_result.priority})")

        # Step 2: 攻击链分析
        attack_chain_result = self.attack_chain_agent.analyze(evidence)
        print(f"   ✅ Attack Chain: {attack_chain_result.progress}")

        # Step 3: 生成报告草稿
        draft = self.report_agent.generate_draft(triage_result, attack_chain_result, evidence)
        print(f"   ✅ Report Draft: {len(draft.evidence_ref)} 条证据引用, {len(draft.suggested_actions)} 条建议")

        if timestamp:
            self._save_draft(draft, timestamp)

        return draft

    def _save_draft(self, draft: AnalysisDraft, timestamp: str):
        output_path = f"{OUTPUTS_DIR}/{timestamp}/agent_draft.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(draft.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"📄 [AgentTeam] 研判草稿已保存: {output_path}")


# ====================== 便捷函数 ======================

def run_analysis(evidence: list, timestamp: Optional[str] = None) -> dict:
    """一键运行 Agent Team 分析"""
    coordinator = AgentTeamCoordinator()
    draft = coordinator.analyze(evidence, timestamp)
    return draft.to_dict()


# ====================== 工具函数 ======================

def _parse_severity(evidence_item: dict) -> int:
    """从证据中解析严重度数值"""
    severity = evidence_item.get("severity") or evidence_item.get("level", 0)
    try:
        return int(severity)
    except (ValueError, TypeError):
        return 0


# ====================== 独立运行入口 ======================
if __name__ == "__main__":
    test_path = f"{OUTPUTS_DIR}/test_evidence.json" if len(_sys.argv) < 2 else _sys.argv[1]
    try:
        with open(test_path, "r", encoding="utf-8") as f:
            evidence = json.load(f)
    except FileNotFoundError:
        print(f"⚠️ 未找到证据文件: {test_path}")
        print("  使用测试数据...")
        evidence = [
            {"evidence_id": "ev-test-1", "rule_id": "5503", "severity": 5, "description": "SSH登录失败", "timestamp": "2026-05-14T12:00:00Z"},
            {"evidence_id": "ev-test-2", "rule_id": "5503", "severity": 5, "description": "SSH登录失败", "timestamp": "2026-05-14T12:01:00Z"},
            {"evidence_id": "ev-test-3", "rule_id": "5503", "severity": 5, "description": "SSH登录失败", "timestamp": "2026-05-14T12:02:00Z"},
        ]

    result = run_analysis(evidence)
    print("\n=== 研判结果 ===")
    print(json.dumps(result, indent=2, ensure_ascii=False))
