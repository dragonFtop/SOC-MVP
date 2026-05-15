
---

## 一、数据来源层（边缘）
1. 边缘节点（node-web-01）产生 **nginx.log**、**/alerts.json**、Wazuh 告警/本地日志

## 二、边缘采集 & 轻量级信令生成# MVP/server/readiness.py 关键改动

def calculate_readiness(timestamp=None):
    # ... 现有代码 ...

    # ======================
    # 【增强】时序一致性检查
    # ======================
    timestamps = [ev["timestamp"] for ev in evidence if ev.get("timestamp")]
    if len(timestamps) >= 2:
        # 检查时间是否有序
        sorted_ts = sorted(timestamps)
        if timestamps != sorted_ts:
            score -= 10  # 时序乱序扣分

        # 检查时间跨度是否合理（>=30秒）
        from datetime import datetime
        try:
            start = datetime.fromisoformat(sorted_ts[0])
            end = datetime.fromisoformat(sorted_ts[-1
2. **Wazuh Agent**：边缘侧异常检测、规则命中
3. **Micro‑signal生成（signal.json）**：
   - 字段：signal_id、node_id、rule_id、src_ip、event_time、suggested_logs
   - 轻量级信令，不上传全量日志
4. **NATS JetStream**：信令总线，边缘 ↔ 中心通信

## 三、元数据注册与数据地图查询
5. **Metadata Registry**：
   - 存储 metadata.json / SQLite
   - 记录：日志位置、查询方式、节点信息
6. **Query Gateway（FastAPI/Python）**：
   - 读取 metadata，生成 DuckDB 查询计划
   - OCSF‑lite/ECS‑lite 字段标准化映射
   - 输出查询请求：case_id、node_id、source、time_window、filters、fields、limit

## 四、边缘按需查询（DuckDB Sidecar）
7. **DuckDB Sidecar（边缘嵌入式库）**：
   - 本地查询日志（JSON/CSV/Parquet）
   - 按查询条件过滤，**只返回关键证据，不上传全量日志**
   - 返回 evidence 数据

## 五、中心证据固化 & 标准化
8. **OpenSearch（中心证据底座）**：
   - 存储：signal、norm（标准化事件）、evidence、soc‑case、report、data_readiness
   - evidence 字段：evidence_id、query_id、raw_ref、lineage_id、hash
9. **OCSF‑lite/ECS‑lite**：统一语义、字段标准化

## 六、数据质量门控
10. **Data Readiness Agent**：
    - 检查：覆盖度、字段完整性、时序一致性
    - 输出：readiness_score、allowed_actions、blocked_actions
    - 防止 AI 幻觉

## 七、Agent Team 简化研判
11. **简化 Agent Team**：
    - Triage（分诊）、Attack Chain（攻击链）、Report（报告草稿）
    - 只读标准字段与 evidence_ref
    - 输出 analysis_draft、evidence_ref

## 八、复核校验
12. **Verifier Agent**：
    - 校验：evidence_ref、raw_ref、query_id、lineage_id
    - 拦截：越权结论、无证报告
    - 输出：verify_result、lineage_status

## 九、可视化展示
13. **Dashboard**：
    - 展示：Case、Evidence、时间线、审批、verify_result、report、lineage_status
    - 生成 Markdown 报告

---
