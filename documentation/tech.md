# AI-SOC 技术栈总览

## 架构理念

**数据编织 (Data Fabric)** — 核心理念是"物理分布、逻辑统一、按需取证"：

- 边缘节点**不**上传全量日志到中心，只发送轻量级"微信号"（signal_id, node_id, rule_id, src_ip, event_time, suggested_logs）
- 中心根据信令中的 `suggested_logs` 字段，构建 DuckDB 查询计划，下发到边缘 Sidecar
- 边缘 Sidecar 在本地查询 DetectionEngine 预解析事件，只返回**关键证据字段**（不全量上传原始日志）
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

**本项目定义的 4 个 Subject：**

| Subject | 方向 | 内容 |
|---------|------|------|
| `soc.signals.{node_id}` | Client → Server | 微信号 (JetStream) |
| `soc.query.requests` | Server → Client | DuckDB 查询请求 (JetStream) |
| `soc.query.results` | Client → Server | 查询结果 / 轻量级证据 (JetStream) |
| `soc.monitor.events` | All → Monitor | 全链路监控事件 (Core Pub/Sub) |

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

## 2. 边缘检测与查询引擎

### DetectionEngine + log_parser

| 属性 | 说明 |
|------|------|
| **文件** | `MVP/client/detection_engine.py`, `MVP/client/log_parser.py` |
| **规则** | `MVP/client/detection_rules.yaml` |
| **数据源** | `/var/log/auth.log` (syslog) |
| **轮询间隔** | 2 秒 (byte offset 增量读取) |

核心能力：
- **两阶段解析** — syslog 头部提取 + 进程特定消息解析（sshd/sudo/su）
- **DuckDB 内存表** — 解析后事件写入 `auth_events` 表，自动清理 > 60 分钟旧数据
- **YAML → SQL** — 检测规则自动转换为 DuckDB SQL threshold 查询
- **5 条内置规则** — SSH 暴力破解、SSH 扫描、Sudo 失败、Su 失败、SSH 成功登录

### DuckDB

| 属性 | 说明 |
|------|------|
| **版本** | >= 0.9.0 |
| **角色** | 嵌入式 OLAP 引擎 |
| **运行位置** | Client 端（边缘侧） |

关键能力：
- **`read_json_auto()`** — 直接查询 NDJSON 文件，自动推断 schema（兼容旧 Wazuh 模式）
- **内存表查询** — DetectionEngine 的 auth_events 表支持 SQL threshold 检测
- **零配置** — 进程内引擎，`duckdb.connect()` 即可使用，不需要独立服务
- **列式存储** — 查询性能远高于 SQLite（面向 OLAP 分析场景）

在项目中的使用场景：
- `detection_engine.py` — DetectionEngine 内存表 + SQL threshold 检测
- `duckdb_sidecar.py` — Client 端按需证据查询
- `query_gateway.py` — Server 端本地查询（开发/单机模式）

---

## 3. 安全基础设施

### OpenSearch

| 属性 | 说明 |
|------|------|
| **版本** | 3.5.0 |
| **端口** | 9200 (REST API), 5601 (Dashboards) |
| **安全** | 已禁用 SSL/认证（开发模式） |
| **内存** | -Xms4G -Xmx4G |

在本项目中作为**证据底座**，存储索引：
- `soc-signals` — 原始信令
- `soc-evidence` — 标准化证据
- `soc-analysis` — 研判结果
- `soc-verification` — 复核结果

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

两套映射函数：

**map_wazuh_to_ocsf()** — Wazuh 告警 → OCSF（兼容旧模式）：

| Wazuh 字段 | OCSF 字段 |
|-----------|-----------|
| `agent_ip` | `src_ip` |
| `agent_name` | `hostname` |
| `rule_id` | `rule_id` |
| `level` | `severity` |
| `full_log` | `raw_log` |
| `description` | `description` |
| `timestamp` | `timestamp` |

**map_authlog_to_ocsf()** — auth.log 事件 → OCSF：

| auth.log 字段 | OCSF 字段 |
|--------------|-----------|
| `src_ip` | `src_ip` |
| `hostname` | `hostname` |
| `rule_id` | `rule_id` |
| `severity` | `severity` |
| `log_type` | `raw_log` |
| `description` | `description` |
| `timestamp` | `timestamp` |

### Data Readiness Checker (`server/readiness.py`)

4 维度质量评分（满分 100），确定数据是否足以支撑研判：

| 检查项 | 方法 | 扣分 |
|--------|------|------|
| 字段覆盖度 | 6 个必需字段齐全率 | 缺失按覆盖率扣分，最高 40 分 |
| 字段完整性 | 非空值比例低于 50% | 扣 15 分 |
| 时序一致性 | 时间戳可解析 + 有序 + 跨度 ≥ 30s | 每项不满足扣 10 分 |
| 唯一性 | evidence_id 无重复 | 有重复扣 10 分 |

评分 → 等级：≥80 "完整可用" / ≥60 "基本可用" / ≥40 "数据不足" / <40 "严重不足"

### Verifier Agent (`server/verifier.py`)

防 AI 幻觉的 5 层校验：
1. **evidence_ref 校验** — 研判引用的证据 ID 必须在证据列表中真实存在
2. **raw_ref 校验** — 每条证据必须有可追溯的 raw_ref
3. **query_id 一致性** — 同一批次证据应来自同一次查询
4. **lineage_id 完整性** — 每条证据必须有 `query_id:hash` 格式的 lineage_id
5. **结论合理性** — 检测绝对化表述黑名单（"完全控制"、"已被攻陷"、"APT攻击"等 10 个短语）

### Report Generator (`server/report_generator.py`)

聚合 evidence.json + readiness.json + agent_result.json + verifier_result.json → Markdown 报告

---

## 6. 容器化

### Docker Compose 编排（3 个容器）

| 服务 | 镜像 | 端口映射 |
|------|------|----------|
| `opensearch` | opensearchproject/opensearch:3.5.0 | 9200 |
| `dashboards` | opensearchproject/opensearch-dashboards:3.5.0 | 5601 |
| `nats` | nats:2.10-alpine | 4222, 8222 |

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
2. **标准化证据卡片** — 每条证据以 expander 展开
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
OpenSearch         3.5.0
NATS Server        2.10
```

## 9. 数据格式一览

### auth.log 原始行 (syslog)
```
May 22 10:15:32 hostname sshd[12345]: Failed password for root from 192.168.1.100 port 22 ssh2
```

### 解析后事件 (auth_events 表)
```json
{"timestamp":"2026-05-22T10:15:32","hostname":"hostname","process":"sshd","pid":12345,"src_ip":"192.168.1.100","src_port":22,"dst_user":"root","log_type":"ssh_failed_password"}
```

### 微信号
```json
{"signal_id":"sig-bbbe6f60","node_id":"node-web-01","rule_id":"LOCAL_SSH_BRUTE_FORCE","severity":"high","src_ip":"192.168.1.100","event_time":"2026-05-22T10:15:32Z","suggested_logs":["auth_log"],"raw_ref":"node-web-01/auth_log#192.168.1.100#...","matched_count":5}
```

### 查询请求
```json
{"query_id":"qry-fdb61cbb","case_id":"case-soc-001","node_id":"node-web-01","signal_id":"sig-bbbe6f60","source":"auth_log","filters":{"src_ip":"192.168.1.100"},"limit":20}
```

### 证据 (OCSF-lite)
```json
{"evidence_id":"ev-abc12345","query_id":"qry-fdb61cbb","node_id":"node-web-01","raw_ref":"node-web-01/auth_log#...","lineage_id":"qry-fdb61cbb:abc123","hash":"abc123","timestamp":"...","rule_id":"LOCAL_SSH_BRUTE_FORCE","hostname":"...","src_ip":"192.168.1.100","log_type":"ssh_failed_password"}
```
