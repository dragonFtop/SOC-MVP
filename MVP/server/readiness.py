import json
import os
from datetime import datetime
from typing import Optional

from config import OUTPUTS_DIR


class DataReadinessChecker:
    """
    数据就绪度检查器 (Data Readiness Agent)
    
    对应实现方案：第六章 - 数据质量门控
    职责：
      1. 字段覆盖度检查（OCSF 标准字段完整性）
      2. 字段完整性检查（非空值比例）
      3. 时序一致性检查（时间顺序、时间跨度）
      4. 唯一性检查（evidence_id 不重复）
      5. 输出：readiness_score、level、allowed_actions、blocked_actions
    """

    # OCSF 必需字段
    REQUIRED_FIELDS = [
        "evidence_id",
        "timestamp",
        "source",
        "src_ip",
        "rule_id",
        "description",
    ]

    # OCSF 推荐字段
    RECOMMENDED_FIELDS = [
        "severity",
        "hostname",
        "raw_log",
        "evidence_id",
    ]

    # 严重度字段映射（支持多种命名）
    SEVERITY_FIELDS = ["severity", "level", "priority"]

    # 时间戳格式白名单
    TIMESTAMP_FORMATS = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ]

    def __init__(self):
        self.score = 100
        self.issues = []
        self.allowed_actions = ["analyze", "report"]
        self.blocked_actions = []

    def check_field_coverage(self, evidence: list) -> None:
        """
        检查字段覆盖度：每条证据是否包含所有必需字段
        
        扣分规则：每个缺失字段扣 10 分
        """
        if not evidence:
            self.score -= 50
            self.issues.append("无证据数据")
            return

        total_missing = 0
        total_checks = len(evidence) * len(self.REQUIRED_FIELDS)

        for ev in evidence:
            for field in self.REQUIRED_FIELDS:
                if not ev.get(field):
                    total_missing += 1

        if total_checks > 0:
            coverage_rate = 1 - (total_missing / total_checks)
            deduction = int((1 - coverage_rate) * 40)
            self.score -= deduction

            if total_missing > 0:
                self.issues.append(
                    f"字段覆盖度不足：{total_missing}/{total_checks} 个必需字段缺失"
                )

    def check_field_integrity(self, evidence: list) -> None:
        """
        检查字段完整性：非空值比例
        
        扣分规则：非空值比例低于 50% 扣 15 分
        """
        if not evidence:
            return

        non_empty_count = 0
        total_values = 0

        for ev in evidence:
            for field in self.REQUIRED_FIELDS + self.RECOMMENDED_FIELDS:
                total_values += 1
                val = ev.get(field)
                if val is not None and val != "" and val != "unknown":
                    non_empty_count += 1

        if total_values > 0:
            integrity_rate = non_empty_count / total_values
            if integrity_rate < 0.5:
                self.score -= 15
                self.issues.append(
                    f"字段完整性不足：非空值比例 {integrity_rate:.0%}"
                )

    def check_temporal_consistency(self, evidence: list) -> None:
        """
        检查时序一致性
        
        检查项：
          1. 时间戳是否可解析
          2. 时间是否有序（无乱序）
          3. 时间跨度是否合理（>= 30 秒）
        
        扣分规则：每项不满足扣 10 分
        """
        timestamps = []
        for ev in evidence:
            ts = ev.get("timestamp")
            if ts:
                parsed = self._parse_timestamp(ts)
                if parsed:
                    timestamps.append(parsed)

        if len(timestamps) < 2:
            self.score -= 10
            self.issues.append(f"时间戳不足 2 条（当前 {len(timestamps)} 条），无法评估时序一致性")
            return

        # 时间顺序检查
        sorted_ts = sorted(timestamps)
        if timestamps != sorted_ts:
            self.score -= 10
            self.issues.append("时间戳存在乱序")

        # 时间跨度检查
        time_span = (sorted_ts[-1] - sorted_ts[0]).total_seconds()
        if time_span < 30:
            self.score -= 10
            self.issues.append(f"时间跨度不足 30 秒（当前 {time_span:.0f} 秒）")

    def check_uniqueness(self, evidence: list) -> None:
        """
        检查 evidence_id 唯一性
        
        扣分规则：重复扣 10 分
        """
        ids = [ev.get("evidence_id") for ev in evidence if ev.get("evidence_id")]
        if len(ids) != len(set(ids)):
            self.score -= 10
            self.issues.append("evidence_id 存在重复，影响溯源完整性")

    def determine_level_and_actions(self) -> tuple:
        """
        根据评分确定等级和允许/阻止的操作
        
        Returns:
            (level, allowed_actions, blocked_actions)
        """
        self.score = max(self.score, 0)

        if self.score >= 80:
            level = "完整可用"
            self.allowed_actions = ["analyze", "report", "persist"]
            self.blocked_actions = []
        elif self.score >= 60:
            level = "基本可用"
            self.allowed_actions = ["analyze", "report"]
            self.blocked_actions = ["persist"]
        elif self.score >= 40:
            level = "数据不足"
            self.allowed_actions = ["analyze"]
            self.blocked_actions = ["report", "persist"]
        else:
            level = "严重不足"
            self.allowed_actions = []
            self.blocked_actions = ["analyze", "report", "persist"]

        return level, self.allowed_actions, self.blocked_actions

    def _parse_timestamp(self, ts_str: str) -> Optional[datetime]:
        """尝试多种格式解析时间戳"""
        for fmt in self.TIMESTAMP_FORMATS:
            try:
                return datetime.strptime(ts_str, fmt)
            except (ValueError, TypeError):
                continue
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    def evaluate(self, evidence: list) -> dict:
        """
        执行完整的就绪度评估

        Args:
            evidence: 标准化证据列表

        Returns:
            readiness_result: 包含评分、等级、操作权限等
        """
        self.score = 100
        self.issues = []

        print("🔍 [DataReadiness] 开始数据就绪度评估...")

        self.check_field_coverage(evidence)
        self.check_field_integrity(evidence)
        self.check_temporal_consistency(evidence)
        self.check_uniqueness(evidence)

        level, allowed, blocked = self.determine_level_and_actions()

        result = {
            "score": self.score,
            "level": level,
            "evidence_count": len(evidence),
            "valid_count": len([ev for ev in evidence if ev.get("evidence_id")]),
            "issues": self.issues,
            "allowed_actions": allowed,
            "blocked_actions": blocked,
            "checks": {
                "field_coverage": "passed" if "字段覆盖度" not in str(self.issues) else "failed",
                "field_integrity": "passed" if "字段完整性" not in str(self.issues) else "failed",
                "temporal_consistency": "passed" if "时间" not in str(self.issues) else "failed",
                "uniqueness": "passed" if "重复" not in str(self.issues) else "failed",
            },
        }

        print(f"✅ [DataReadiness] 评分: {self.score} 分, 等级: {level}")
        if self.issues:
            for issue in self.issues:
                print(f"   ⚠️ {issue}")

        return result


# ====================== 向后兼容函数 ======================

def calculate_readiness(timestamp: Optional[str] = None) -> dict:
    """
    向后兼容的 calculate_readiness 函数

    Args:
        timestamp: 时间戳（用于文件路径）

    Returns:
        readiness_result: 就绪度评估结果
    """
    if timestamp is None:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    input_path = f"{OUTPUTS_DIR}/{timestamp}/evidence.json"
    output_dir = f"{OUTPUTS_DIR}/{timestamp}"
    os.makedirs(output_dir, exist_ok=True)
    
    with open(input_path, "r", encoding="utf-8") as f:
        evidence = json.load(f)

    checker = DataReadinessChecker()
    result = checker.evaluate(evidence)

    filepath = f"{output_dir}/readiness.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ 数据就绪度已保存到 {filepath}")
    return result


# ====================== 独立运行入口 ======================
if __name__ == "__main__":
    # 读取当前时间戳
    try:
        with open(f"{OUTPUTS_DIR}/current_timestamp.txt", "r") as f:
            ts = f.read().strip()
        calculate_readiness(timestamp=ts)
    except FileNotFoundError:
        print("⚠️ 未找到时间戳文件，请先运行 evidence_builder.py")
        # 使用测试数据
        test_evidence = [
            {"evidence_id": "ev-001", "timestamp": "2026-05-14T12:00:00Z", "source": "wazuh", "src_ip": "10.0.0.1", "rule_id": "5503", "description": "SSH登录失败", "severity": 5},
            {"evidence_id": "ev-002", "timestamp": "2026-05-14T12:01:00Z", "source": "wazuh", "src_ip": "10.0.0.1", "rule_id": "5503", "description": "SSH登录失败", "severity": 5},
            {"evidence_id": "ev-003", "timestamp": "2026-05-14T12:02:00Z", "source": "wazuh", "src_ip": "10.0.0.1", "rule_id": "5503", "description": "SSH登录失败", "severity": 5},
        ]
        checker = DataReadinessChecker()
        result = checker.evaluate(test_evidence)
        print(json.dumps(result, indent=2, ensure_ascii=False))
