# AI-SOC 智能安全运营中心

AI-SOC (Artificial Intelligence Security Operations Center) 是一个基于**数据编织 (Data Fabric)** 架构的安全事件自动化研判平台。

核心理念：**物理分布、逻辑统一、按需取证、可信研判**。

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    终端 2: Client (边缘侧)                    │
│                                                             │
│  /var/log/auth.log (syslog)                                 │
│         │                                                   │
│  DetectionEngine (YAML规则 + DuckDB本地检测)                  │
│         │                                                   │
│  DuckDB Sidecar (按需查询/证据提取)                           │
│         │                                                   │
└─────────┼───────────────────────────────────────────────────┘
          │
   NATS (JetStream + Core)
          │
┌─────────┼───────────────────────────────────────────────────┐
│                    终端 1: Server (中心侧)                    │
│         │                                                   │
│  Signal Listener (信令监听)                                  │
│         │                                                   │
│  Query Gateway (FastAPI :8000)                              │
│         │                                                   │
│  Agent Team (LLM 多Agent研判)                                │
│         │                                                   │
│  Verifier (复核校验)                                         │
│         │                                                   │
│  Dashboard (Streamlit :8501)                                │
│  Monitor Dashboard (:8502)                                  │
│                                                             │
│  存储层: OpenSearch + DuckDB                                 │
└─────────────────────────────────────────────────────────────┘
```

### 实时数据流

```
系统写入 /var/log/auth.log
  → DetectionEngine tail + 解析 (每2秒轮询)
  → DuckDB 内存表写入 → YAML 规则 SQL threshold 检测
  → 生成轻量级微信号 → NATS soc.signals.*
  → Server Signal Listener 接收 → 触发按需查询
  → NATS soc.query.requests
  → Client DuckDB Sidecar 查询 auth_events 内存表 → 只返回关键证据
  → NATS soc.query.results
  → Server 接收 → OCSF 标准化 → OpenSearch 持久化 → 研判 → 报告

兼容旧模式 (Wazuh alerts.json):
  → SignalWatcher 监控 wazuh_logs/alerts/alerts.json
  → 其余链路同上

全链路监控:
  → MonitorEmitter → NATS soc.monitor.events (Core Pub/Sub)
  → Monitor Dashboard (:8502) 实时展示
```

## 快速开始

### 环境要求

- Python 3.10+
- Docker + Docker Compose
- 至少 8GB 可用内存（推荐 16GB）

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动（两个终端窗口）

**终端 1 — 启动服务端（中心侧）：**

```bash
bash scripts/run_server.sh
```

该脚本会自动：
- 检查并启动 Docker 基础设施（OpenSearch、NATS）
- 启动 Query Gateway（FastAPI，端口 8000）
- 启动 Signal Listener（监听 NATS 信令）
- 启动 Query Result Listener（监听查询结果）
- 启动 Dashboard（Streamlit，端口 8501）

**终端 2 — 启动客户端（边缘侧）：**

```bash
bash scripts/run_client.sh
```

该脚本会自动：
- 检查 NATS 连接
- 启动 DuckDB Sidecar（监听查询请求）
- 启动 DetectionEngine（实时监控 auth.log）
- 启动 SignalWatcher（监控告警数据源）
- 发布初始信号批次

两个终端窗口会实时展示 Client ↔ Server 之间的交互过程。

### 3. 触发测试告警

提供两种测试方式，在 Client 和 Server 都运行后使用：

**方式一：直接操作 auth.log（快速测试 SOC 全链路）**

执行真实系统操作或 logger 模拟，写入 /var/log/auth.log，DetectionEngine 在 2 秒内检测到：

```bash
bash scripts/trigger_authlog.sh ssh          # SSH 暴力破解 (PTY 发送错误密码)
bash scripts/trigger_authlog.sh all          # 依次执行 SSH + sudo
bash scripts/trigger_authlog.sh sudo 3       # 3 次错误 sudo 尝试
```

测试链路：`系统操作 → auth.log → DetectionEngine → 信号 → NATS → Server → 查询 → 证据 → 研判`

**方式二：直接注入 alerts.json（兼容旧告警文件测试）**

```bash
bash scripts/trigger_test.sh              # 注入 5 条 SSH 暴力破解告警 (5503)
bash scripts/trigger_test.sh 3 5710       # 注入 3 条端口扫描告警
```

测试链路：`alerts.json → SignalWatcher → 信号 → NATS → Server → 查询 → 证据 → 研判`

### 4. 停止

```bash
bash scripts/stop_server.sh    # 停止服务端
bash scripts/stop_client.sh    # 停止客户端
```

### 5. 访问 Dashboard

打开浏览器访问 `http://localhost:8501` 查看可视化研判面板。

### 6. 单次分析（可选）

如果不需要实时监控，也可以运行一次性分析流程：

```bash
cd MVP && python main.py
```

## 服务端口

| 服务 | 地址 |
|------|------|
| Query Gateway | `http://localhost:8000` |
| Dashboard | `http://localhost:8501` |
| Monitor Dashboard | `http://localhost:8502` |
| OpenSearch | `http://localhost:9200` |
| OpenSearch Dashboards | `http://localhost:5601` |
| NATS Monitoring | `http://localhost:8222` |

## 项目结构

```
SOC/
├── scripts/                       # 启动/停止/测试 脚本
│   ├── run_server.sh              # 启动服务端（终端1）
│   ├── run_client.sh              # 启动客户端（终端2）
│   ├── stop_server.sh             # 停止服务端
│   ├── stop_client.sh             # 停止客户端
│   ├── trigger_authlog.sh         # 触发 auth.log 真实安全事件
│   ├── trigger_test.sh            # 注入模拟告警 (兼容旧告警文件模式)
│
├── MVP/
│   ├── main.py                    # 单次分析入口（9步流水线）
│   ├── config.py                  # 全局配置
│   ├── metadata.json              # 数据源注册表
│   │
│   ├── client/                    # 边缘侧模块
│   │   ├── client_app.py          # 客户端统一入口（DetectionEngine + Sidecar）
│   │   ├── detection_engine.py    # 本地检测引擎 (DetectionEngine)
│   │   ├── detection_rules.yaml   # YAML 检测规则定义
│   │   ├── log_parser.py          # auth.log syslog 解析器
│   │   ├── duckdb_sidecar.py      # DuckDB 边缘查询引擎 (DuckDBQueryEngine)
│   │   ├── signal_watcher.py      # 实时信号监控器 (SignalWatcher, 兼容旧 Wazuh 模式)
│   │   ├── signal_generator.py    # 微信号生成与 NATS 发布（批量模式）
│   │   ├── local_gateway.py       # 本地 DuckDB 查询封装
│   │   ├── evidence_builder.py    # 证据构建与固化
│   │   └── agent_analyzer.py      # 规则引擎分析器（回退用）
│   │
│   ├── server/                    # 中心侧模块
│   │   ├── server_app.py          # 服务端统一入口（Listener + Gateway + Dashboard + Monitor）
│   │   ├── signal_listener.py     # NATS 信令监听器 (SignalListener)
│   │   ├── query_result_listener.py # NATS 查询结果监听器 (QueryResultListener)
│   │   ├── query_gateway.py       # FastAPI 查询网关
│   │   ├── agent_team.py          # 多Agent LLM 研判（Triage/AttackChain/Report）
│   │   ├── readiness.py           # 数据质量门控
│   │   ├── verifier.py            # 复核校验（防AI幻觉）
│   │   ├── report_generator.py    # Markdown 研判报告
│   │   ├── opensearch_loader.py   # OpenSearch 数据持久化
│   │   ├── dashboard.py           # Streamlit 可视化面板 (:8501)
│   │   └── monitor_dashboard.py   # 实时监控面板 (:8502)
│   │
│   ├── common/                    # 共享模块
│   │   ├── ocsf_mapper.py         # OCSF-lite 字段标准化
│   │   ├── nats_utils.py          # NATS 连接/Stream/订阅/ACK 工具
│   │   └── monitor_events.py      # 监控事件发射器 (MonitorEmitter)
│   │
│   └── outputs/                   # 分析结果（按时间戳组织）
│       └── 20260515_144330/
│           ├── evidence.json
│           ├── readiness.json
│           ├── agent_result.json
│           ├── agent_draft.json
│           ├── verifier_result.json
│           └── report.md
│
├── wazuh_logs/                    # 告警日志数据目录
│   └── alerts/alerts.json        # 告警数据 (NDJSON)
│
├── documentation/                 # 项目文档
│   ├── tech.md                    # 技术栈总览
│   ├── realise.md                 # 各模块实现说明
│   ├── tech-basics.md             # 技术基础指南
│   ├── issues.md                  # 常见问题与解决方案
│   ├── plan.md                    # 实现方案
│   └── next-scheme.md             # 演进方案
│
├── docker-compose.yml             # 基础设施编排
├── Dockerfile                     # Python 服务镜像
├── requirements.txt               # Python 依赖
└── README.md
```

## 研判流程（9步流水线）

| 步骤 | 模块 | 职责 |
|------|------|------|
| 1-2 | `DetectionEngine` / `log_parser` | tail auth.log → 解析 → DuckDB 内存表 → YAML SQL 检测 → 信号 → NATS |
| 3-5 | `DuckDB Sidecar` + `ocsf_mapper` | 按需查询 auth_events 内存表 → 轻量级证据 → OCSF 标准化 |
| 6 | `readiness` | 数据质量门控（覆盖度/完整性/时序/唯一性） |
| 7 | `agent_team` | LLM 多Agent研判（分诊 → 攻击链 → 报告草稿） |
| 8 | `verifier` | 复核校验（evidence_ref/raw_ref/lineage_id 溯源） |
| 9 | `report_generator` + `dashboard` | 报告生成 + 可视化展示 |
| — | `MonitorEmitter` → `monitor_dashboard` | 全链路监控事件实时展示 |

## Agent Team 研判

支持两种模式：

### LLM 模式（默认）
调用 Anthropic API 进行 AI 分析。3个Agent协作：
- **Triage Agent** — 分诊：判断事件类型、优先级、置信度
- **Attack Chain Agent** — 攻击链：映射证据到 Kill Chain 阶段
- **Report Agent** — 报告：生成具体可执行的处置建议

### 规则模式（回退）
LLM 不可用时自动回退。覆盖 7 种事件类型：
`brute_force` / `scanning` / `malware_detected` / `reconnaissance` / `privilege_escalation` / `lateral_movement` / `data_exfiltration`

## 本地检测规则 (detection_rules.yaml)

| 规则ID | 类型 | 描述 | 严重度 |
|--------|------|------|--------|
| LOCAL_SSH_BRUTE_FORCE | brute_force | SSH 暴力破解 (5次/5分钟) | high |
| LOCAL_SSH_SCAN | reconnaissance | SSH 扫描检测 (3次/2分钟) | medium |
| LOCAL_SUDO_FAILURES | privilege_escalation | Sudo 认证失败 (3次/2分钟) | medium |
| LOCAL_SU_FAILURES | privilege_escalation | Su 认证失败 (3次/2分钟) | medium |
| LOCAL_SSH_ACCEPTED | normal | SSH 成功登录 (通知) | low |

### 旧版告警规则 (兼容)

| 规则ID | 类型 | 描述 |
|--------|------|------|
| 5503 | brute_force | SSH/RDP 暴力破解 |
| 5710 | scanning | 端口扫描 |
| 5712 | reconnaissance | 信息侦查 |
| 5763 | privilege_escalation | 权限提升 |
| 5715 | lateral_movement | 横向移动 |

## 配置说明

主要配置项在 `MVP/config.py` 中：

```python
# 数据路径
AUTH_LOG_PATH         # 系统 auth.log 路径 (默认 /var/log/auth.log)
WAZUH_LOGS_DIR        # 告警日志目录 (兼容旧模式)
ALERTS_JSON_PATH      # 告警数据文件路径 (兼容旧模式)
OUTPUTS_DIR           # 分析结果输出目录

# 检测引擎
DETECTION_RULES_PATH  # YAML 规则文件路径
WATCH_INTERVAL        # auth.log 轮询间隔 (默认 2秒)
EVENT_RETENTION_MINUTES # 内存中事件保留时间 (默认 60分钟)

# 服务连接
OPENSEARCH_HOST       # OpenSearch 地址 (默认 127.0.0.1:9200)
NATS_SERVERS          # NATS 服务器列表

# LLM 分析
ANTHROPIC_API_KEY     # 从环境变量 ANTHROPIC_AUTH_TOKEN 读取
ANTHROPIC_MODEL       # 模型名称 (默认 claude-sonnet-4-6)
```

数据源注册在 `MVP/metadata.json` 中，支持多节点、多数据源的灵活配置。

## 故障排除

| 问题 | 解决方法 |
|------|----------|
| Docker 容器无法启动 | 检查内存 >= 8GB，`docker compose logs` 查看日志 |
| LLM API 调用失败 | 检查 `ANTHROPIC_AUTH_TOKEN` 环境变量，系统自动回退规则引擎 |
| OpenSearch 连接失败 | `docker compose up -d opensearch`，数据已本地保存可稍后重试 |
| NATS 连接失败 | 确认 Docker 基础设施已启动，先运行 `scripts/run_server.sh` |
| auth.log 不可读 | `sudo chmod o+r /var/log/auth.log`，确保 DetectionEngine 有读取权限 |
| 无实时信号 | 确认 auth.log 有新的安全事件写入，运行 `scripts/trigger_authlog.sh ssh` 测试 |
| DuckDB 查询失败 | 确认 DetectionEngine 已启动并解析了 auth.log 事件 |
| 检测规则未触发 | 检查 `MVP/client/detection_rules.yaml`，确保 threshold 条件满足 |

## 许可证

MIT License
