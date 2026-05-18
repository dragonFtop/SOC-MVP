# AI-SOC 技术栈总览

## 架构理念

**数据编织 (Data Fabric)** — 核心理念是"物理分布、逻辑统一、按需取证"：

- 边缘节点**不**上传全量日志到中心，只发送轻量级"微信号"（signal_id, node_id, rule_id, src_ip, event_time, suggested_logs）
- 中心根据信令中的 `suggested_logs` 字段，构建 DuckDB 查询计划，下发到边缘 Sidecar
- 边缘 Sidecar 在本地执行查询，只返回**关键证据字段**（不全量上传原始日志）
- 中心将证据 OCSF 标准化后，进行质量门控 → AI 研判 → 复核校验 → 输出报告

这避免了传统 SIEM 架构中全量日志上传的带宽和存储开销。

---

## 1. 通信层

### NATS JetStream

| 属性 | 说明 |
|------|------|
| **版本** | nats:2.10-alpine (Docker 镜像) |
| **Python 客户端** | nats-py >= 2.8.0 |
| **端口** | 4222 (客户端), 8222 (HTTP 监控) |
| **启动参数** | `--jetstream --http_port 8222` |

在本项目中的角色：Client ↔ Server 之间**所有**消息通信的唯一通道。选择 NATS 而非 RabbitMQ/Kafka 的原因：极简部署（单个二进制）、低延迟、原生 JetStream 持久化。

**本项目定义的 4 个 Stream/Subject：**

| Subject | 方向 | 内容 |
|---------|------|------|
| `soc.signals.{node_id}` | Client → Server | 微信号 |
| `soc.query.requests` | Server → Client | DuckDB 查询请求 |
| `soc.query.results` | Client → Server | 查询结果（轻量级证据） |
| `soc.monitor.events` | All → Monitor | 全链路监控事件（核心Pub/Sub） |

### FastAPI + Uvicorn

| 属性 | 说明 |
|------|------|
| **版本** | FastAPI >= 0.104, Uvicorn >= 0.24 |
| **端口** | 8000 |
| **启动方式** | `uvicorn.run("server.query_gateway:app", host="0.0.0.0", port=8000)` |

Query Gateway 是一个 REST API 服务，提供 4 个端点：`GET /`, `GET /health`, `GET /metadata`, `POST /query`。在 `server_app.py` 中以**独立线程**运行。

### Pydantic

| 属性 | 说明 |
|------|------|
| **版本** | >= 2.5.0 |
| **用途** | FastAPI 请求/响应数据模型校验 |

定义了 `QueryRequest`、`EvidenceItem`、`QueryResponse` 三个模型，确保 API 输入输出的类型安全。

---

## 2. 边缘查询引擎

### DuckDB

| 属性 | 说明 |
|------|------|
| **版本** | >= 0.9.0 |
| **角色** | 嵌入式 OLAP 引擎 |
| **运行位置** | Client 端（边缘侧） |

关键能力：
- **`read_json_auto()`** — 直接查询 NDJSON 文件，自动推断 schema，无需预先定义表结构
- **零配置** — 进程内引擎，`duckdb.connect()` 即可使用，不需要独立服务
- **列式存储** — 查询性能远高于 SQLite（面向 OLAP 分析场景）
- **支持格式** — JSON, CSV, Parquet, 甚至直接读 S3

在项目中的使用场景：
- `local_gateway.py` — 封装 DuckDB 查询，从 `alerts.json` 按 rule_id 筛选
- `duckdb_sidecar.py` — 在 Client 端作为查询执行引擎
- `query_gateway.py` — 在 Server 端执行本地查询（开发/单机模式）

---

## 3. 安全基础设施

### Wazuh

| 组件 | 版本 | 角色 | 端口 |
|------|------|------|------|
| **Wazuh Agent** | 4.9.0 | 宿主机上运行，采集日志 | — |
| **Wazuh Manager** | 4.14.0 | Docker 容器，规则分析 | 1514(tcp+udp), 1515(tcp), 55000(tcp) |

**Manager 内部关键进程：**

| 进程 | 运行用户 | 职责 |
|------|----------|------|
| `wazuh-remoted` | wazuh(999) | 接收 Agent 连接和事件数据 (1514) |
| `wazuh-authd` | root | Agent 注册认证 (1515) |
| `wazuh-analysisd` | wazuh(999) | **核心**：规则匹配、解码日志、生成告警 |
| `wazuh-logcollector` | root | 本地日志文件采集 |
| `wazuh-syscheckd` | root | 文件完整性监控 (FIM) |
| `wazuh-modulesd` | root | 漏洞扫描、SCA 等模块 |
| `wazuh-monitord` | wazuh(999) | Agent 状态监控 |
| `wazuh-db` | wazuh(999) | 内部 SQLite 数据库 |
| `wazuh-execd` | root | 执行主动响应脚本 |
| `wazuh-apid` | root | Wazuh REST API (55000) |

**数据卷挂载：**
```yaml
volumes:
  - ./wazuh_logs:/var/ossec/logs   # Manager 的整个日志目录映射到宿主机
```

这意味着 Manager 容器内的 `/var/ossec/logs/alerts/alerts.json` 实际上就是宿主机上的 `wazuh_logs/alerts/alerts.json`。

**Agent 配置关键点：**
```xml
<client>
  <server>
    <address>127.0.0.1</address>   <!-- 通过 Docker 端口映射连接 Manager -->
    <port>1514</port>
    <protocol>tcp</protocol>
  </server>
</client>
<localfile>
  <log_format>journald</log_format> <!-- 采集 systemd journal -->
</localfile>
```

### OpenSearch

| 属性 | 说明 |
|------|------|
| **版本** | 3.5.0 |
| **端口** | 9200 (REST API), 5601 (Dashboards) |
| **安全** | 已禁用 SSL/认证（开发模式） |
| **内存** | -Xms4G -Xmx4G |

在本项目中作为**证据底座**，存储 4 个索引：
- `soc-signals` — 原始信令
- `soc-evidence` — 标准化证据
- `soc-analysis` — 研判结果
- `soc-verification` — 复核结果

### Logstash

| 属性 | 说明 |
|------|------|
| **版本** | opensearch-project/logstash-oss-with-opensearch-output-plugin:8.9.0 |
| **配置** | logstash.conf |
| **角色** | 日志管道（当前预留，未激活使用） |

---

## 4. AI 研判

### Anthropic API (Claude)

| 属性 | 说明 |
|------|------|
| **默认模型** | `claude-sonnet-4-6` |
| **Python SDK** | `anthropic >= 0.40.0` |
| **认证** | 环境变量 `ANTHROPIC_AUTH_TOKEN` |
| **端点** | 环境变量 `ANTHROPIC_BASE_URL` (默认 api.anthropic.com) |

在 `agent_team.py` 中实现了 3 个 Agent：

| Agent | System Prompt 约束 | 输出 |
|-------|-------------------|------|
| **TriageAgent** | 分析安全告警，判断事件类型、优先级、置信度 | `{"priority", "event_type", "summary", "confidence"}` |
| **AttackChainAgent** | 将证据映射到 Lockheed Martin Cyber Kill Chain 的 7 个阶段 | `{"phases", "progress"}` |
| **ReportAgent** | 基于分诊和攻击链分析，生成处置建议 | `{"suggested_actions", "risk_assessment"}` |

### 规则引擎（回退）

LLM 不可用时自动回退。硬编码了 7 种事件类型的特征和处置模板：

```
brute_force → 锁定源IP + MFA + 限频 + 审计
scanning → WAF规则 + 封禁IP + 入侵检测
malware_detected → 隔离主机 + 全盘扫描 + 应急响应
reconnaissance → 加强监控 + 加固服务 + 审计日志
privilege_escalation → 审查权限 + 重置密码 + 审计特权账号
lateral_movement → 隔离网段 + 检查横向连接 + 应急响应
data_exfiltration → 阻断外连 + 检查传输 + 数据泄露应急
```

---

## 5. 数据流水线

### OCSF Mapper (`common/ocsf_mapper.py`)

Wazuh 原始字段 → OCSF-lite 标准字段的映射：

| Wazuh 字段 | OCSF 字段 |
|-----------|-----------|
| `agent_ip` | `src_ip` |
| `agent_name` | `hostname` |
| `rule_id` | `rule_id` |
| `level` | `severity` |
| `full_log` | `raw_log` |
| `description` | `description` |
| `timestamp` | `timestamp` |

### Data Readiness Checker (`server/readiness.py`)

4 维度质量评分（满分 100），确定数据是否足以支撑研判：

| 检查项 | 方法 | 扣分 |
|--------|------|------|
| 字段覆盖度 | 6 个必需字段齐全率 | 缺失扣 10 分/字段 |
| 字段完整性 | 非空值比例低于 50% | 扣 15 分 |
| 时序一致性 | 时间戳可解析 + 有序 + 跨度 ≥ 30s | 每项不满足扣 10 分 |
| 唯一性 | evidence_id 无重复 | 有重复扣 10 分 |

评分 → 等级：≥80 "完整可用" / ≥60 "基本可用" / ≥40 "数据不足" / <40 "严重不足"

### Verifier Agent (`server/verifier.py`)

防 AI 幻觉的 5 层校验：
1. **evidence_ref 校验** — 研判引用的证据 ID 必须在证据列表中真实存在
2. **raw_ref 校验** — 每条证据必须有可追溯的 raw_ref（格式：`{node}/{source}#{timestamp}`）
3. **query_id 一致性** — 同一批次证据应来自同一次查询
4. **lineage_id 完整性** — 每条证据必须有 `query_id:hash` 格式的 lineage_id
5. **结论合理性** — 检测绝对化表述黑名单（"完全控制"、"已被攻陷"、"APT攻击"等 10 个短语）

### Report Generator (`server/report_generator.py`)

聚合 evidence.json + readiness.json + agent_result.json + verifier_result.json → Markdown 报告

---

## 6. 容器化

### Docker Compose 编排（5 个容器）

| 服务 | 镜像 | 端口映射 |
|------|------|----------|
| `opensearch` | opensearchproject/opensearch:3.5.0 | 9200 |
| `dashboards` | opensearchproject/opensearch-dashboards:3.5.0 | 5601 |
| `wazuh-manager` | wazuh/wazuh-manager:4.14.0 | 1514, 1515, 55000 |
| `nats` | nats:2.10-alpine | 4222, 8222 |
| `logstash` | opensearch-project/logstash-oss... | — |

**网络**: 所有容器接入 `aisoc-net`（Docker Compose 自动加项目名前缀 → `soc_aisoc-net`）

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "MVP/main.py"]
```

---

## 7. 可视化

### Streamlit Dashboard (`server/dashboard.py`)

| 属性 | 说明 |
|------|------|
| **版本** | >= 1.28.0 |
| **端口** | 8501 |
| **主题** | 深色 SOC 风格 (自定义 CSS) |

5 个功能卡片：
1. **数据就绪度卡片** — 评分、等级、有效证据数
2. **标准化证据卡片** — 每条证据以 expander 展开，显示 ID/时间/级别/IP/主机/日志
3. **AI 研判结论卡片** — 结论、置信度、事件类型、优先级、处置建议
4. **复核结果卡片** — 通过/未通过、发现的问题
5. **完整研判报告卡片** — Markdown 渲染

### Monitor Dashboard (`server/monitor_dashboard.py`)

| 属性 | 说明 |
|------|------|
| **版本** | >= 1.28.0 |
| **端口** | 8502 |
| **刷新** | streamlit-autorefresh (每2秒) |

实时监控全链路事件流：
- **Client 事件**: 信号发送 → 查询接收 → 查询执行 → 结果发送
- **Server 事件**: 信号接收 → 查询下发 → 结果接收 → 证据保存
- 双列面板布局，顶栏显示 NATS 连接状态和事件总数
- 自检按钮验证事件管道

---

## 8. 关键技术版本汇总

```
Python             3.10+
DuckDB             0.9+
NATS-py            2.8+
FastAPI            0.104+
Uvicorn            0.24+
Anthropic          0.40+
OpenSearch-py      2.4+
Streamlit          1.28+
streamlit-autorefresh 1.0+
Pydantic           2.5+
aiohttp            3.9+
httpx              0.25+
Wazuh Agent        4.9.0
Wazuh Manager      4.14.0
OpenSearch         3.5.0
NATS Server        2.10
```

## 9. 数据格式一览

### 告警 (NDJSON)
```json
{"timestamp":"2026-05-15T12:43:47.000+00:00","rule":{"level":5,"description":"PAM: User login failed.","id":"5503",...},"agent":{"id":"001","name":"37vwmu3rudbyc0v","ip":"127.0.0.1"},"manager":{"name":"0f98319a2063"},"id":"1778687135.725724","full_log":"May 15...","location":"journald"}
```

### 微信号
```json
{"signal_id":"sig-bbbe6f60","node_id":"37vwmu3rudbyc0v","rule_id":"5502","src_ip":"127.0.0.1","event_time":"...","suggested_logs":["wazuh_alerts","auth.log"],"raw_ref":"wazuh-alerts#...#37vwmu3rudbyc0v"}
```

### 查询请求
```json
{"query_id":"qry-fdb61cbb","case_id":"case-soc-001","node_id":"37vwmu3rudbyc0v","signal_id":"sig-bbbe6f60","source":"wazuh_alerts","filters":{"rule.id":"5503"},"limit":20}
```

### 证据 (OCSF-lite)
```json
{"evidence_id":"ev-abc12345","query_id":"qry-fdb61cbb","node_id":"node-web-01","raw_ref":"node-web-01/wazuh_alerts#...","lineage_id":"qry-fdb61cbb:12345","hash":"abc123","timestamp":"...","rule_id":"5503","agent_name":"...","src_ip":"127.0.0.1","rows_returned":15}
```

### NATS 主题
```
soc.signals.{node_id}     → 微信号，每个边缘节点独立主题 (JetStream)
soc.query.requests        → 查询请求，所有节点共享 (JetStream)
soc.query.results         → 查询结果，所有节点共享 (JetStream)
soc.monitor.events        → 监控事件，best-effort 发布 (Core Pub/Sub)
```
