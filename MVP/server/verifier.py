import json
import os
from typing import Optional

from config import OUTPUTS_DIR


class VerifierAgent:
    """
    复核校验 Agent (Verifier)

    对应实现方案：第八章 - 复核校验
    职责：
      1. 校验 evidence_ref 是否在证据列表中真实存在
      2. 校验 raw_ref 是否可追溯
      3. 校验 query_id 是否有效
      4. 校验 lineage_id 是否完整
      5. 拦截：越权结论（无证据支撑的高风险结论）
      6. 拦截：无证报告（没有证据引用的推测性结论）

    输出：verify_result, lineage_status
    """

    # 绝对化表述黑名单
    ABSOLUTE_WORDS = [
        "完全控制",
        "已被攻陷",
        "数据泄露",
        "rootkit",
        "APT攻击",
        "供应链攻击",
        "国家级攻击者",
        "已成功入侵",
        "完全沦陷",
        "内部威胁确认",
    ]

    def __init__(self):
        self.issues = []
        self.verified = True
        self.lineage_status = {}

    def verify_evidence_ref(self, agent_result: dict, evidence: list) -> bool:
        """
        校验研判结果引用的 evidence_id 是否真实存在

        Args:
            agent_result: AI 研判结果
            evidence: 证据列表

        Returns:
            bool: 校验是否通过
        """
        evidence_ids_in_agent = []

        # 从研判结果中提取证据引用
        timeline = agent_result.get("timeline", [])
        for item in timeline:
            # 检查 timeline 中是否包含 evidence_id
            if isinstance(item, dict) and item.get("evidence_id"):
                evidence_ids_in_agent.append(item["evidence_id"])

        # 从总结中提取（如果存在 evidence_ref 字段）
        evidence_ref = agent_result.get("evidence_ref", [])
        if evidence_ref:
            evidence_ids_in_agent.extend(evidence_ref)

        # 从 evidence 中提取所有证据 ID
        actual_evidence_ids = [ev.get("evidence_id", "") for ev in evidence]

        # 校验引用有效性
        invalid_refs = []
        for ref in evidence_ids_in_agent:
            if ref and ref not in actual_evidence_ids:
                invalid_refs.append(ref)

        if invalid_refs:
            self.issues.append(f"研判引用了不存在的 evidence_id: {invalid_refs}")
            return False

        return True

    def verify_raw_ref(self, evidence: list) -> bool:
        """
        校验证据的 raw_ref 是否可追溯（包含必要溯源信息）

        Args:
            evidence: 证据列表

        Returns:
            bool: 校验是否通过
        """
        for ev in evidence:
            raw_ref = ev.get("raw_ref", "")
            if not raw_ref:
                self.issues.append(f"证据 {ev.get('evidence_id', 'unknown')} 缺少 raw_ref")
                return False

            # 校验 raw_ref 格式（应包含节点、来源和时间戳）
            if "#" not in raw_ref and "/" not in raw_ref:
                self.issues.append(f"证据 {ev.get('evidence_id')} 的 raw_ref 格式无效: {raw_ref}")
                return False

        return True

    def verify_query_id(self, evidence: list, agent_result: dict) -> bool:
        """
        校验 query_id 的一致性

        所有证据的 query_id 应一致（来自同一次查询）
        """
        query_ids = set()
        for ev in evidence:
            qid = ev.get("query_id")
            if qid:
                query_ids.add(qid)

        if len(query_ids) > 1:
            self.issues.append(f"证据来自多个查询: {query_ids}")
            return False

        return True

    def verify_lineage_id(self, evidence: list) -> bool:
        """
        校验 lineage_id 的完整性

        Args:
            evidence: 证据列表

        Returns:
            bool: 校验是否通过
        """
        for ev in evidence:
            lineage_id = ev.get("lineage_id", "")
            evidence_id = ev.get("evidence_id", "unknown")

            if not lineage_id:
                self.issues.append(f"证据 {evidence_id} 缺少 lineage_id")
                self.lineage_status[evidence_id] = "missing"
                return False

            # 校验 lineage_id 格式（应包含 query_id 和 hash）
            if ":" not in lineage_id:
                self.issues.append(f"证据 {evidence_id} 的 lineage_id 格式无效: {lineage_id}")
                self.lineage_status[evidence_id] = "invalid_format"
                return False

            self.lineage_status[evidence_id] = "valid"

        return True

    def verify_conclusion(self, conclusion: str, evidence_count: int, readiness_score: int) -> bool:
        """
        校验结论的合理性

        1. 高置信度必须有足够证据
        2. 结论不能绝对化
        3. 数据就绪度低于60不能给出高置信度结论

        Args:
            conclusion: 研判结论
            evidence_count: 证据数量
            readiness_score: 数据就绪度评分

        Returns:
            bool: 校验是否通过
        """
        passed = True

        # 1. 绝对化表述检测
        for word in self.ABSOLUTE_WORDS:
            if word in conclusion:
                self.issues.append(f"结论包含绝对化表述: 「{word}」，风险过高，需要更多证据支撑")
                passed = False

        # 2. 高置信度证据不足
        if evidence_count < 3 and len(conclusion) > 10:
            self.issues.append(f"证据仅 {evidence_count} 条，但结论过于详细，置信度存疑")
            passed = False

        # 3. 就绪度门控
        if readiness_score < 60 and evidence_count >= 3:
            self.issues.append(f"数据就绪度 {readiness_score} 分低于 60，结论需谨慎")
            passed = False

        return passed

    def verify(self, timestamp: Optional[str] = None) -> dict:
        """
        执行完整的复核校验流程

        Args:
            timestamp: 时间戳

        Returns:
            verify_result: 复核结果
        """
        self.issues = []
        self.verified = True
        self.lineage_status = {}

        print("🔍 [Verifier] 开始复核校验...")

        # 1. 读取所有输入文件
        input_dir = f"{OUTPUTS_DIR}/{timestamp}" if timestamp else f"{OUTPUTS_DIR}/latest"

        agent_path = f"{input_dir}/agent_result.json"
        evidence_path = f"{input_dir}/evidence.json"
        readiness_path = f"{input_dir}/readiness.json"

        with open(agent_path, "r", encoding="utf-8") as f:
            agent = json.load(f)
        with open(evidence_path, "r", encoding="utf-8") as f:
            evidence = json.load(f)
        with open(readiness_path, "r", encoding="utf-8") as f:
            readiness = json.load(f)

        evidence_count = len(evidence)
        readiness_score = readiness.get("score", 0)

        # 2. 执行各项校验
        checks = {}

        # evidence_ref 校验
        ref_ok = self.verify_evidence_ref(agent, evidence)
        checks["evidence_ref"] = "passed" if ref_ok else "failed"
        if not ref_ok:
            self.verified = False

        # raw_ref 校验
        raw_ok = self.verify_raw_ref(evidence)
        checks["raw_ref"] = "passed" if raw_ok else "failed"
        if not raw_ok:
            self.verified = False

        # query_id 校验
        query_ok = self.verify_query_id(evidence, agent)
        checks["query_id"] = "passed" if query_ok else "failed"
        if not query_ok:
            self.verified = False

        # lineage_id 校验
        lineage_ok = self.verify_lineage_id(evidence)
        checks["lineage_id"] = "passed" if lineage_ok else "failed"
        if not lineage_ok:
            self.verified = False

        # 结论合理性校验
        conclusion_ok = self.verify_conclusion(
            agent.get("conclusion", ""),
            evidence_count,
            readiness_score,
        )
        checks["conclusion"] = "passed" if conclusion_ok else "failed"
        if not conclusion_ok:
            self.verified = False

        # 3. 生成结果
        result = {
            "verified": self.verified,
            "issues": self.issues,
            "checks": checks,
            "lineage_status": self.lineage_status,
            "evidence_count": evidence_count,
            "readiness_score": readiness_score,
            "final_confidence": "高" if (self.verified and evidence_count >= 5)
            else ("中" if self.verified else "低"),
            "final_conclusion": agent.get("conclusion", "") if self.verified
            else "证据有限或校验未通过，结论谨慎使用",
        }

        # 4. 保存结果
        output_dir = f"{OUTPUTS_DIR}/{timestamp}" if timestamp else f"{OUTPUTS_DIR}/latest"
        os.makedirs(output_dir, exist_ok=True)

        filepath = f"{output_dir}/verifier_result.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print(f"✅ [Verifier] 复核完成: {'通过' if self.verified else '不通过'}")
        if self.issues:
            for issue in self.issues:
                print(f"   ⚠️ {issue}")
        print(f"📄 [Verifier] 结果已保存: {filepath}")

        return result


# ====================== 向后兼容函数 ======================

def verify(timestamp: Optional[str] = None) -> dict:
    """
    向后兼容的 verify 函数

    Args:
        timestamp: 时间戳

    Returns:
        dict: 复核结果
    """
    checker = VerifierAgent()
    return checker.verify(timestamp)


# ====================== 独立运行入口 ======================
if __name__ == "__main__":
    import sys

    timestamp = sys.argv[1] if len(sys.argv) > 1 else None

    if not timestamp:
        try:
            with open(f"{OUTPUTS_DIR}/current_timestamp.txt", "r") as f:
                timestamp = f.read().strip()
        except FileNotFoundError:
            print("⚠️ 请提供时间戳参数或先运行 evidence_builder.py")
            sys.exit(1)

    result = verify(timestamp)
    print(json.dumps(result, indent=2, ensure_ascii=False))
