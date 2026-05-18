# AI-SOC 实现说明

本文档按数据流顺序，逐一说明每个模块的代码实现、关键逻辑和设计决策。

---

## 整体架构

```
┌────────────────────── 宿主机 ─────────────────────────────────────┐
│                                                                   │
│  Wazuh Agent (系统服务)                                            │
│    │ logcollector 采集 journald/auth.log/syslog                    │
│    │ agentd 连接 Manager:1514                                     │
│    ▼                                                              │
│  ┌──────────────────────────────────────────────────┐            │
│  │ Docker: wazuh-manager                            │            │
│  │   remoted → analysisd (7067规则) → alerts.json    │            │
│  │   bind mount: wazuh_logs/ ← /var/ossec/logs/     │            │
│  └──────────────────────────────────────────────────┘            │
│                                                                   │
│  ┌──────────────┐    NATS     ┌──────────────────────┐           │
│  │ Client App   │──JetStream─>│ Server App            │           │
│  │ (client_app) │<─JetStream──│ (server_app)          │           │
│  │              │             │                       │           │
│  │ SignalWatcher│    Core     │ SignalListener        │           │
│  │ DuckDBEngine │──Pub/Sub──>│ QueryResultListener   │           │
│  │              │  (monitor  │ QueryGateway :8000    │           │
│  └──────────────┘   events)  │ Dashboard :8501       │           │
│                              │ Monitor Dashboard :8502│          │
│                              │ AgentTeam              │           │
│                              └──────────────────────┘           │
│                                                                   │
│  Docker: opensearch (证据底座)                                     │
│  Docker: nats (消息总线)                                           │
└───────────────────────────────────────────────────────────────────┘
```

---

## 模块 1: Wazuh 日志采集链

**文件**: 系统配置 `/var/ossec/etc/ossec.conf`（Agent）、Docker 容器内配置（Manager）

### 1.1 Agent 日志采集

Agent 通过 `logcollector` 进程采集以下日志源：

| 日志源 | ossec.conf 配置 | 说明 |
|--------|----------------|------|
| systemd journal | `<log_format>journald</log_format>` | 所有 systemd 服务的日志 |
| auth.log | 通过 journald 间接采集 | SSH 登录、sudo、PAM 认证 |
| syslog | 通过 journald 间接采集 | 系统通用日志 |
| 命令输出 | `<log_format>command</log_format>` | df, netstat, last 等定时采集 |

### 1.2 Agent → Manager 通信

```
Agent (agentd) ──TCP:1514──> Manager (remoted) ──> analysisd ──> alerts.json
```

- Agent 从 `/var/ossec/etc/client.keys` 读取认证密钥
- 连接地址配置在 `<server><address>127.0.0.1</address></server>`
- 使用 `127.0.0.1` 而非容器 IP（通过 Docker 端口映射 `1514:1514`）

### 1.3 Manager 告警生成

`analysisd` 进程执行：
1. **预解码** — 提取 timestamp, hostname, program_name
2. **解码** — 根据日志类型选择对应 decoder（pam, sshd, sudo 等）
3. **规则匹配** — 遍历 7067 条内置规则（`/var/ossec/ruleset/rules/`）
4. **生成告警** — 匹配成功后写入 `alerts.json`（NDJSON）和 `alerts.log`

### 1.4 告警文件路径

由于 bind mount `./wazuh_logs:/var/ossec/logs`，Manager 写入的 `alerts.json` 直接出现在宿主机的 `wazuh_logs/alerts/alerts.json`。

### 1.5 权限注意事项

- Manager 容器内 `analysisd` 以 UID 999(wazuh) 运行
- 写入宿主机的文件所有者为容器内的 UID 999（宿主机上可能显示为 `lxd` 或其他用户名）
- 文件默认权限 `rw-rw----`（只有 owner 和 group 999 可读写）
- 宿主机的 `admin` 用户（UID 1000）需要读取权限
- **解决方案**: `chmod -R a+rX` + ACL `setfacl -d -m o::r`

---

## 模块 2: Client — SignalWatcher（实时信号监控）

**文件**: `MVP/client/signal_watcher.py` → `SignalWatcher` 类
**入口**: `MVP/client/client_app.py` → 组装 `SignalWatcher` + `DuckDBQueryEngine`

### 2.1 核心逻辑

```
┌─────────────┐
│ alerts.json │ (NDJSON, 持续追加)
└──────┬──────┘
       │ 每 2 秒轮询
       ▼
┌──────────────────┐
│ 检查文件大小     │ ← last_offset (byte 偏移)
│ 读取新增字节     │
│ 按 \n 分割       │
│ JSON.parse 每行  │
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ 去重检查         │ ← self.seen_ids (set)
│ alert.id 新?     │
└──────┬───────────┘
       │ 新告警
       ▼
┌──────────────────┐
│ _alert_to_signal │ → 构建微信号
│ 提取:            │    signal_id, node_id,
│   rule.id        │    rule_id, rule_level,
│   rule.level     │    rule_desc, src_ip,
│   rule.desc      │    event_time,
│   agent.name     │    suggested_logs,
│   agent.ip       │    raw_ref
│   timestamp      │
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│ NATS publish     │ → soc.signals.{node_id}
└──────────────────┘
```

### 2.2 关键设计决策

**为什么用 byte offset 而不是 tail -f？**
- 跨平台兼容（不依赖 Linux 的 inotify）
- 可精确追踪读取位置
- 支持文件轮转后重新定位

**为什么用 `alert.id` 去重？**
- 每条 Wazuh 告警有唯一 ID（格式：`{timestamp}.{counter}`）
- 即使文件被重新读取（如进程重启），已处理的告警不会被重复发送

**初始批次策略：**
- 首次启动时扫描全部现有告警
- 只发布最后 20 条（避免海量历史数据冲击服务端）
- 全部告警 ID 加入 `seen_ids` 防止后续重复

### 2.3 运行模式

`SignalWatcher.run_forever()` 是一个无限循环的 asyncio 协程，与 `DuckDBQueryEngine.start_listening()` 并发运行在同一个事件循环中：

```python
sidecar_task = asyncio.create_task(sidecar.start_listening(), name="sidecar")
watcher_task = asyncio.create_task(watcher.run_forever(), name="watcher")
_, pending = await asyncio.wait([sidecar_task, watcher_task],
                                 return_when=asyncio.FIRST_COMPLETED)
```

---

## 模块 3: Client — DuckDBQueryEngine（边缘按需查询）

**文件**: `MVP/client/duckdb_sidecar.py` → `DuckDBQueryEngine` 类
**入口**: `MVP/client/client_app.py` → 组装

### 3.1 查询处理流程

```
NATS soc.query.requests
       │
       ▼
handle_query(msg)
       │
       ├─ 解析 JSON → query_id, sql, source
       │
       ├─ 如果无 SQL 但有 filters → _build_sql(request)
       │     └─ 从 filters 构造 DuckDB SQL:
       │        SELECT * FROM read_json_auto('{path}')
       │        WHERE "rule"."id" = '{rule_id}' LIMIT {limit}
       │
       ├─ DuckDB 执行 → rows
       │
       ├─ 构建轻量级证据 (不传全量日志!)
       │     evidence_id, query_id, node_id,
       │     raw_ref, lineage_id, hash,
       │     timestamp, rule_id, agent_name, src_ip
       │
       └─ NATS soc.query.results → 返回证据
```

### 3.2 SQL 构建逻辑

`_build_sql()` 方法根据查询请求中的 `filters` 和 `source` 字段动态生成 SQL：

```python
sql = f"SELECT {fields_str} FROM read_json_auto('{data_path}')"
if rule_id:
    sql += f" WHERE \"rule\".\"id\" = '{rule_id}'"
sql += f" LIMIT {limit}"
```

### 3.3 数据最小化原则

证据中**不包含** `full_log` 原始字段，只提取元数据（rule_id, timestamp, agent_name, src_ip）。原始日志通过 `raw_ref` 和 `lineage_id` 可追溯。

### 3.4 错误处理

- 文件竞态（Wazuh 写入与 DuckDB 读取冲突）自动重试最多 2 次
- `safe_ack()` 带重试限制的 ACK — 超限自动丢弃防死信阻塞
- `subscribe_safe()` 自动处理 Stream 创建 + Consumer 残留清理

---

## 模块 4: Server — SignalListener + ResultListener

**文件**: `MVP/server/signal_listener.py` → `SignalListener` 类
**文件**: `MVP/server/query_result_listener.py` → `QueryResultListener` 类
**入口**: `MVP/server/server_app.py` → 组装

### 4.1 SignalListener

```
NATS soc.signals.* (通配符订阅)
       │
       ▼
handle_signal(msg)
       │
       ├─ 解析信号 → signal_id, node_id, rule_id, suggested_logs
       ├─ 记录到 OpenSearch (soc-signals 索引)
       ├─ 构建查询请求:
       │     query_id = f"qry-{uuid}"
       │     source = suggested_logs[0]
       │     filters = {"rule.id": signal.rule_id}
       │     limit = 20
       ├─ NATS soc.query.requests → 下发到边缘
       └─ msg.ack() (手动确认)
```

### 4.2 QueryResultListener

```
NATS soc.query.results
       │
       ▼
handle_result(msg)
       │
       ├─ 解析结果 → query_id, evidence_count, execution_time_ms, evidence
       ├─ 持久化到本地 outputs/nats_{timestamp}/evidence.json
       └─ msg.ack()
```

### 4.3 并发模型

```python
# 3 个独立线程（阻塞服务）
gateway_thread = threading.Thread(target=run_gateway, daemon=True)
dashboard_thread = threading.Thread(target=_launch_dashboard, daemon=True)
monitor_thread = threading.Thread(target=_launch_monitor, daemon=True)

# 2 个 asyncio Task（共享事件循环）
signal_task = asyncio.create_task(signal_listener.listen_forever())
result_task = asyncio.create_task(result_listener.listen_forever())
await asyncio.wait([signal_task, result_task], return_when=FIRST_COMPLETED)
```

- Query Gateway、Dashboard、Monitor Dashboard 各自在独立线程中运行
- 两个 Listener 是独立的 asyncio Task，共享同一个事件循环
- Monitor Dashboard（:8502）通过 NATS Core Pub/Sub 订阅 `soc.monitor.events` 实时展示全链路事件

---

## 模块 5: Server — Query Gateway (REST API)

**文件**: `MVP/server/query_gateway.py`

### 5.1 端点定义

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/` | 服务信息（名称、版本、状态） |
| GET | `/health` | 健康检查 |
| GET | `/metadata` | 查看元数据注册表 |
| POST | `/query` | **主查询接口**（见下方） |

### 5.2 POST /query 处理流水线

```
QueryRequest (Pydantic 模型校验)
       │
       ▼
build_query_plan()
  ├─ load_metadata()        → 读取 metadata.json
  ├─ find_source_config()   → 按 node_id + source 匹配数据源
  └─ 构造 DuckDB SQL        → 根据 time_window, filters, fields, limit
       │
       ▼
execute_local_query(sql)
  └─ duckdb.connect() → execute(sql) → fetchall()
       │
       ▼
standardize_evidence(rows)
  ├─ 逐行提取字段 (timestamp, rule, agent, full_log)
  ├─ map_wazuh_to_ocsf()   → OCSF 标准化
  ├─ 增强溯源字段 (evidence_id, query_id, raw_ref, lineage_id, hash)
  └─ 返回 list[EvidenceItem]
       │
       ▼
持久化 (异步, 不阻塞响应)
  ├─ OpenSearchClient.index("soc-evidence", ...)
  └─ 本地文件 outputs/query_{query_id}.json
       │
       ▼
QueryResponse → 返回客户端
```

### 5.3 Pydantic 模型

```python
class QueryRequest(BaseModel):
    case_id: str = "case-soc-001"
    node_id: str = "node-web-01"
    source: str = "wazuh_alerts"
    signal_id: Optional[str] = None
    time_window: Optional[str] = None       # 格式: "start/end"
    filters: dict = {}                       # {"rule.id": "5503"}
    fields: list = ["timestamp", "rule.id", ...]
    limit: int = 20

class EvidenceItem(BaseModel):
    evidence_id: str
    query_id: str
    raw_ref: str
    lineage_id: str
    hash: str
    timestamp: str
    source: str
    rule_id: str
    description: str
    src_ip: str
    hostname: str
    severity: Optional[int] = None
    raw_log: Optional[str] = None

class QueryResponse(BaseModel):
    query_id: str
    node_id: str
    source: str
    evidence_count: int
    evidence: list[EvidenceItem]
    execution_time_ms: float
```

---

## 模块 6: Agent Team（多 Agent LLM 研判）

**文件**: `MVP/server/agent_team.py`

### 6.1 三 Agent 架构

```
证据列表 (list[dict])
       │
       ├──> TriageAgent.analyze(evidence)
       │       ├─ LLM 模式: 调用 Anthropic API
       │       │     system_prompt 定义分诊专家角色
       │       │     输入: 证据摘要 (最多20条)
       │       │     输出: {"priority", "event_type", "summary", "confidence"}
       │       └─ Rule 模式: _rule_based_analyze()
       │             统计 rule_id 频率 → 查 TRIAGE_RULES 表 → 确定优先级
       │             返回: TriageResult
       │
       ├──> AttackChainAgent.analyze(evidence)
       │       ├─ LLM 模式: 映射到 Cyber Kill Chain 7 阶段
       │       └─ Rule 模式: 按 severity 区间映射阶段
       │             severity≤3 → 侦查
       │             severity≤6 → 武器化
       │             severity≤9 → 利用
       │             severity≤12 → C2
       │             severity≤15 → 目标行动
       │             返回: AttackChainResult
       │
       └──> ReportAgent.generate_draft(triage, attack_chain, evidence)
               ├─ LLM 模式: 生成具体可执行的处置建议
               └─ Rule 模式: ACTION_TEMPLATES[event_type]
                     返回: AnalysisDraft
```

### 6.2 LLM 调用细节

```python
def _call_llm(system_prompt: str, user_message: str) -> Optional[dict]:
    client = Anthropic(api_key=ANTHROPIC_API_KEY, base_url=ANTHROPIC_BASE_URL)
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    # 从响应中提取文本 (兼容 TextBlock 和 ThinkingBlock)
    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text
    # 提取 JSON (可能在 ```json 块中)
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    return json.loads(text)
```

**代理处理** — 代码中临时清除 `ALL_PROXY/HTTP_PROXY/HTTPS_PROXY` 环境变量，因为 SOCKS 代理会导致 httpx 崩溃。

### 6.3 Agent Team 协调器

```python
class AgentTeamCoordinator:
    def analyze(self, evidence, timestamp=None) -> AnalysisDraft:
        triage_result = self.triage_agent.analyze(evidence)
        attack_chain_result = self.attack_chain_agent.analyze(evidence)
        draft = self.report_agent.generate_draft(triage_result, attack_chain_result, evidence)
        if timestamp:
            self._save_draft(draft, timestamp)  # → outputs/{ts}/agent_draft.json
        return draft
```

---

## 模块 7: Data Readiness Checker（数据质量门控）

**文件**: `MVP/server/readiness.py` → `DataReadinessChecker`

### 7.1 评分算法

```
初始分: 100

check_field_coverage():
  必需字段 = [evidence_id, timestamp, source, src_ip, rule_id, description]
  for each evidence:
    for each required_field:
      if not ev.get(field): total_missing += 1
  coverage_rate = 1 - (total_missing / (evidence_count * 6))
  deduction = (1 - coverage_rate) * 40
  score -= deduction

check_field_integrity():
  非空值比例 < 50% → score -= 15

check_temporal_consistency():
  时间戳 < 2 条 → score -= 10
  时间戳乱序 → score -= 10
  时间跨度 < 30s → score -= 10

check_uniqueness():
  evidence_id 重复 → score -= 10

score = max(score, 0)
```

### 7.2 操作权限门控

| 评分 | 等级 | 允许 | 阻止 |
|------|------|------|------|
| ≥80 | 完整可用 | analyze, report, persist | — |
| ≥60 | 基本可用 | analyze, report | persist |
| ≥40 | 数据不足 | analyze | report, persist |
| <40 | 严重不足 | — | analyze, report, persist |

---

## 模块 8: Verifier Agent（复核校验）

**文件**: `MVP/server/verifier.py` → `VerifierAgent`

### 8.1 5 层校验链

```
verify_evidence_ref(agent_result, evidence)
  → 提取 agent_result 中引用的 evidence_id
  → 与 evidence 中的实际 ID 比对
  → 不存在的引用 → 失败

verify_raw_ref(evidence)
  → 检查每条证据的 raw_ref 字段
  → 格式要求: 包含 "#" 或 "/" (如 "node/source#timestamp")
  → 缺少或格式无效 → 失败

verify_query_id(evidence, agent_result)
  → 收集所有证据的 query_id
  → 应全部一致 (同一次查询)
  → 来自多个查询 → 失败

verify_lineage_id(evidence)
  → 检查每条证据的 lineage_id
  → 格式要求: 包含 ":" (如 "qry-xxx:hash")
  → 缺少或格式无效 → 失败

verify_conclusion(conclusion, evidence_count, readiness_score)
  → 检测绝对化表述 (10 个黑名单短语)
  → 证据 < 3 条但结论详细 → 失败
  → readiness < 60 但高置信度 → 失败
```

### 8.2 绝对化表述黑名单

```
"完全控制", "已被攻陷", "数据泄露", "rootkit", "APT攻击",
"供应链攻击", "国家级攻击者", "已成功入侵", "完全沦陷", "内部威胁确认"
```

这些词语在没有足够证据支撑时不应出现在 AI 生成的结论中。

---

## 模块 9: OCSF Mapper（标准化映射）

**文件**: `MVP/common/ocsf_mapper.py`

### 9.1 核心映射函数

```python
def map_wazuh_to_ocsf(evidence_item: dict) -> dict:
    return {
        "timestamp":  evidence_item.get("timestamp"),
        "severity":   evidence_item.get("level"),        # Wazuh level → OCSF severity
        "rule_id":    evidence_item.get("rule_id"),
        "description":evidence_item.get("description"),
        "src_ip":     evidence_item.get("agent_ip"),     # Wazuh agent_ip → OCSF src_ip
        "hostname":   evidence_item.get("agent_name"),   # Wazuh agent_name → OCSF hostname
        "evidence_id":evidence_item.get("evidence_id"),
        "source":     "wazuh-alerts",
        "raw_log":    evidence_item.get("full_log"),
    }
```

### 9.2 辅助函数

- `extract_severity(evidence)` — 兼容多种 severity 字段名（severity/level/priority），返回 int
- `extract_src_ip(evidence)` — 兼容 src_ip/agent_ip/data.srcip
- `extract_hostname(evidence)` — 兼容 hostname/agent_name/agent.name

---

## 模块 10: Dashboard（可视化面板）

**文件**: `MVP/server/dashboard.py` (研判面板, Streamlit :8501)
**文件**: `MVP/server/monitor_dashboard.py` (实时监控面板, Streamlit :8502)

### 10.1 页面结构

```python
st.set_page_config(layout="wide")   # 宽屏布局
st.markdown("<style>...</style>")   # 深色 SOC 主题 CSS

# 侧边栏: 历史任务选择
tasks = get_all_tasks()              # 扫描 outputs/ 下的时间戳目录
selected = st.selectbox("选择任务", tasks)

# 主区域: 5 个卡片
st.container(border=True)            # 卡片 1: 数据就绪度
st.container(border=True)            # 卡片 2: 标准化证据列表
st.container(border=True)            # 卡片 3: AI 研判结论
st.container(border=True)            # 卡片 4: 复核结果
st.container(border=True)            # 卡片 5: 完整研判报告
```

### 10.2 数据读取

每个卡片从 `outputs/{timestamp}/` 目录读取对应的 JSON 文件：
- `readiness.json` → 卡片 1
- `evidence.json` → 卡片 2（每条证据一个 expander）
- `agent_result.json` 或 `agent_draft.json` → 卡片 3
- `verifier_result.json` → 卡片 4
- `report.md` → 卡片 5（Markdown 渲染）

### 10.3 Monitor Dashboard（实时监控面板）

**文件**: `MVP/server/monitor_dashboard.py`

通过 NATS 核心 Pub/Sub 订阅 `soc.monitor.events`，实时展示全链路事件：

```
页面结构:
┌─ 顶栏 ───────────────────────────────────────────┐
│ NATS 连接状态 | 事件总数 | Client/Server 分类计数   │
└───────────────────────────────────────────────────┘
┌─ 左列 (Client 事件) ──┐ ┌─ 右列 (Server 事件) ──┐
│ 📤 信号已发送          │ │ 📥 信号已接收          │
│ 📥 查询已接收          │ │ 📤 查询已下发          │
│ 🔍 查询已执行          │ │ 📥 结果已接收          │
│ 📤 结果已发送          │ │ 💾 证据已保存          │
└────────────────────────┘ └────────────────────────┘
┌─ 实时事件日志 (最新50条, 可折叠) ──────────────────┐
└───────────────────────────────────────────────────┘
```

- 使用 `st.cache_resource` 保持 NATS 连接单例跨 Streamlit re-run 存活
- 后台线程持续收集事件到 `deque(maxlen=1000)`
- `streamlit-autorefresh` 每 2 秒自动刷新面板
- 自检按钮通过 NATS 发送测试事件验证管道

---

## 模块 11: Monitor Emitter（监控事件发射器）

**文件**: `MVP/common/monitor_events.py` → `MonitorEmitter` 类

### 11.1 设计原则

所有 Client/Server 组件通过 `MonitorEmitter` 发布轻量级结构化事件到 NATS 核心 Pub/Sub（非 JetStream），供 Monitor Dashboard 消费：
- **best-effort** — 发布失败不影响主流程
- **即发即弃** — 无需 ACK，无需持久化
- **轻量级** — 每个事件仅包含关键元数据

### 11.2 8 种事件类型

| 事件类型 | 来源 | 发射组件 |
|---------|------|---------|
| `signal.sent` | Client | SignalWatcher |
| `signal.received` | Server | SignalListener |
| `query.sent` | Server | SignalListener |
| `query.received` | Client | DuckDBQueryEngine |
| `query.executed` | Client | DuckDBQueryEngine |
| `result.sent` | Client | DuckDBQueryEngine |
| `result.received` | Server | QueryResultListener |
| `evidence.saved` | Server | QueryResultListener |

### 11.3 事件结构

```json
{
  "event_id": "evt-a1b2c3d4",
  "event_type": "signal.sent",
  "source": "client",
  "component": "SignalWatcher",
  "node_id": "node-web-01",
  "timestamp": "20:43:47",
  "payload": {
    "signal_id": "sig-bbbe6f60",
    "rule_id": "5503",
    "rule_level": 5,
    "node_id": "node-web-01"
  }
}
```

---

## 模块 12: NATS 共享工具

**文件**: `MVP/common/nats_utils.py`

### 12.1 提供的工具函数

| 函数 | 职责 |
|------|------|
| `get_nats()` | 延迟导入 nats 模块（使 NATS 成为可选依赖） |
| `ensure_stream(js, name, subjects)` | 创建 JetStream Stream（幂等，带默认限制） |
| `subscribe_safe(js, subject, durable)` | 安全订阅（自动处理 Stream 不存在、Consumer 残留） |
| `safe_ack(msg, on_success)` | 带重试限制的 ACK 处理（超限自动丢弃防死信） |

### 12.2 Stream 默认配置

```python
STREAM_CONFIG = {
    "max_age":  24 * 3600,          # 24 小时后自动过期
    "max_bytes": 500 * 1024 * 1024, # 单 Stream 最多 500 MB
}
MAX_DELIVERY_ATTEMPTS = 3           # 每条消息最大重试次数
```

### 12.3 Docker 文件竞态处理

`DuckDBQueryEngine.execute_query()` 遇到 "Malformed JSON" 或 "unexpected end of data" 错误时自动重试（最多 2 次），处理 Wazuh Manager 写入与 DuckDB 读取的竞态条件。
