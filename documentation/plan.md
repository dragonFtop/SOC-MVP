
---
## 一、数据来源层（边缘）
1. 边缘节点（node-web-01）产生 **auth.log**（syslog 格式，包含 sshd/sudo/su 事件）

## 二、边缘采集 & 轻量级信令生成
2. **log_parser**：解析 auth.log syslog 行，提取结构化字段（timestamp, process, src_ip, dst_user 等）
3. **DetectionEngine**：tail auth.log → log_parser 解析 → 写入 DuckDB 内存表 → YAML 规则 SQL threshold 检测
4. **Micro‑signal生成**：
   - 字段：signal_id、node_id、rule_id、src_ip、event_time、suggested_logs、matched_count
   - 轻量级信令，不上传全量日志
5. **NATS JetStream**：信令总线，边缘 ↔ 中心通信

## 三、元数据注册与数据地图查询
6. **Metadata Registry**：
   - 存储 metadata.json
   - 记录：日志位置、查询方式、节点信息
7. **Query Gateway（FastAPI/Python）**：
   - 读取 metadata，生成 DuckDB 查询计划
   - OCSF‑lite 字段标准化映射
   - 输出查询请求：case_id、node_id、source、time_window、filters、fields、limit

## 四、边缘按需查询（DuckDB Sidecar）
8. **DuckDB Sidecar（边缘嵌入式库）**：
   - 本地查询 DetectionEngine 预解析的 auth_events 表
   - 按查询条件过滤，**只返回关键证据，不上传全量日志**
   - 返回 evidence 数据

## 五、中心证据固化 & 标准化
9. **OpenSearch（中心证据底座）**：
   - 存储：signal、norm（标准化事件）、evidence、soc‑case、report、data_readiness
   - evidence 字段：evidence_id、query_id、raw_ref、lineage_id、hash
10. **OCSF‑lite**：统一语义、字段标准化

## 六、数据质量门控
11. **Data Readiness Agent**：
    - 检查：覆盖度、字段完整性、时序一致性
    - 输出：readiness_score、allowed_actions、blocked_actions
    - 防止 AI 幻觉

## 七、Agent Team 简化研判
12. **Agent Team**：
    - Triage（分诊）、Attack Chain（攻击链）、Report（报告草稿）
    - 只读标准字段与 evidence_ref
    - 输出 analysis_draft、evidence_ref

## 八、复核校验
13. **Verifier Agent**：
    - 校验：evidence_ref、raw_ref、query_id、lineage_id
    - 拦截：越权结论、无证报告
    - 输出：verify_result、lineage_status

## 九、可视化展示
14. **Dashboard**：
    - 研判面板（:8501）：Case、Evidence、时间线、审批、verify_result、report
    - 生成 Markdown 报告
15. **Monitor Dashboard**：
    - 实时监控面板（:8502）：全链路事件流实时展示
    - Client 事件：信号发送/查询接收/查询执行/结果发送
    - Server 事件：信号接收/查询下发/结果接收/证据保存

## 十、全链路监控
16. **MonitorEmitter**（`common/monitor_events.py`）：
    - 所有组件发布轻量级事件到 `soc.monitor.events`（NATS Core Pub/Sub）
    - 8 种事件类型覆盖 Client/Server 全链路
    - best-effort 发布，不影响主流程
17. **NATS 共享工具**（`common/nats_utils.py`）：
    - `ensure_stream()` — Stream 创建（幂等，含默认限制）
    - `subscribe_safe()` — 安全订阅（自动处理 Stream 不存在 + Consumer 残留）
    - `safe_ack()` — 带重试限制的 ACK（超限自动丢弃防死信）
    - `MAX_DELIVERY_ATTEMPTS = 3` — 最大投递次数

---
