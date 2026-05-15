# AI-SOC 智能安全运营中心

AI-SOC (Artificial Intelligence Security Operations Center) 是一个基于**数据编织 (Data Fabric)** 架构的安全事件自动化研判平台。

核心理念：**物理分布、逻辑统一、按需取证、可信研判**。

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    终端 2: Client (边缘侧)                    │
│                                                             │
│  Wazuh Agent ──► Wazuh Manager ──► alerts.json (NDJSON)     │
│                                       │                     │
│                              SignalWatcher (实时监控)        │
│                                       │                     │
│                              DuckDB Sidecar (按需查询)       │
│                                       │                     │
└───────────────────────────────────────┼─────────────────────┘
                                        │
                                 NATS JetStream
                                        │
┌───────────────────────────────────────┼─────────────────────┐
│                    终端 1: Server (中心侧)                    │
│                                       │                     │
│                              Signal Listener (信令监听)      │
│                                       │                     │
│                              Query Gateway (FastAPI :8000)   │
│                                       │                     │
│                              Agent Team (LLM 多Agent研判)    │
│                                       │                     │
│                              Verifier (复核校验)             │
│                                       │                     │
│                              Dashboard (Streamlit :8501)     │
│                                                             │
│  存储层: OpenSearch + DuckDB                                 │
└─────────────────────────────────────────────────────────────┘
```

### 实时数据流

```
Wazuh Agent 检测异常
  → Wazuh Manager 追加告警到 alerts.json (NDJSON)
  → SignalWatcher 检测新行 (每2秒轮询)
  → 生成轻量级微信号 → NATS soc.signals.*
  → Server Signal Listener 接收 → 触发按需查询
  → NATS soc.query.requests
  → Client DuckDB Sidecar 执行本地查询 → 只返回关键证据字段
  → NATS soc.query.results
  → Server 接收 → OCSF标准化 → 数据质量门控 → Agent Team研判 → 复核 → 持久化
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
- 检查并启动 Docker 基础设施（OpenSearch、NATS、Wazuh Manager）
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
- 检查 Wazuh 告警数据源
- 启动 DuckDB Sidecar（监听查询请求）
- 启动 SignalWatcher（实时监控新告警）
- 发布初始信号批次

两个终端窗口会实时展示 Client ↔ Server 之间的交互过程。

### 3. 触发测试告警

提供两种测试方式，在 Client 和 Server 都运行后使用：

**方式一：直接注入 alerts.json（快速测试 SOC 内部链路）**

向告警文件追加模拟 JSON 行，SignalWatcher 在 2 秒内检测到：

```bash
bash scripts/trigger_test.sh              # 注入 5 条 SSH 暴力破解告警 (5503)
bash scripts/trigger_test.sh 3 5710       # 注入 3 条端口扫描告警
bash scripts/trigger_test.sh 2 random     # 随机规则
```

测试链路：`alerts.json → SignalWatcher → 信号 → NATS → Server → 查询 → 证据 → 研判`

**方式二：系统层真实操作（端到端测试，含 Wazuh Agent）**

在系统中执行真实操作，产生 `auth.log` 日志，被 Wazuh Agent 捕获后触发全链路：

```bash
bash scripts/trigger_syslog.sh ssh        # SSH 暴力破解 (PTY 发送错误密码)
bash scripts/trigger_syslog.sh su         # su/sudo 认证失败 + 提权尝试
bash scripts/trigger_syslog.sh scan       # 端口扫描
bash scripts/trigger_syslog.sh all        # 依次执行以上所有
```

测试链路：`系统操作 → /var/log/auth.log → Wazuh Agent → Manager → alerts.json → SignalWatcher → ...`

支持的检测规则：`5503`(爆破)、`2501`(认证失败)、`5710`(扫描)、`5501`(登录)、`5502`(会话)、`5712`(侦查)、`5763`(提权)、`5715`(横向移动)

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
│   └── trigger_test.sh            # 注入模拟告警，触发研判链路
│
├── MVP/
│   ├── main.py                    # 单次分析入口（9步流水线）
│   ├── config.py                  # 全局配置
│   ├── metadata.json              # 数据源注册表
│   │
│   ├── client/                    # 边缘侧模块
│   │   ├── client_app.py          # 客户端统一入口（Sidecar + Watcher）
│   │   ├── duckdb_sidecar.py      # DuckDB 边缘查询引擎
│   │   ├── signal_generator.py    # 微信号生成与 NATS 发布
│   │   ├── local_gateway.py       # 本地 DuckDB 查询封装
│   │   ├── evidence_builder.py    # 证据构建与固化
│   │   └── agent_analyzer.py      # 规则引擎分析器（回退用）
│   │
│   ├── server/                    # 中心侧模块
│   │   ├── server_app.py          # 服务端统一入口（Listener + Gateway + Dashboard）
│   │   ├── signal_listener.py     # NATS 信令监听器
│   │   ├── query_gateway.py       # FastAPI 查询网关
│   │   ├── agent_team.py          # 多Agent LLM 研判（Triage/AttackChain/Report）
│   │   ├── readiness.py           # 数据质量门控
│   │   ├── verifier.py            # 复核校验（防AI幻觉）
│   │   ├── report_generator.py    # Markdown 研判报告
│   │   ├── opensearch_loader.py   # OpenSearch 数据持久化
│   │   └── dashboard.py           # Streamlit 可视化面板
│   │
│   ├── common/                    # 共享模块
│   │   └── ocsf_mapper.py         # OCSF-lite 字段标准化
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
├── wazuh_logs/                    # Wazuh 日志挂载目录
│   └── alerts/alerts.json        # 告警数据 (NDJSON)
│
├── documentation/                 # 项目文档
│   ├── tech.md                    # 技术栈总览
│   ├── realise.md                 # 各模块实现说明
│   ├── tech-basics.md             # 技术基础指南
│   └── issues.md                  # 常见问题与解决方案
│
├── docker-compose.yml             # 基础设施编排
├── Dockerfile                     # Python 服务镜像
├── requirements.txt               # Python 依赖
└── README.md
```

## 研判流程（9步流水线）

| 步骤 | 模块 | 职责 |
|------|------|------|
| 1-2 | `SignalWatcher` / `signal_generator` | 实时监控告警 → 生成微信号 → NATS 发布 |
| 3-5 | `DuckDB Sidecar` + `ocsf_mapper` | 按需查询 → OCSF 标准化 |
| 6 | `readiness` | 数据质量门控（覆盖度/完整性/时序/唯一性） |
| 7 | `agent_team` | LLM 多Agent研判（分诊 → 攻击链 → 报告草稿） |
| 8 | `verifier` | 复核校验（evidence_ref/raw_ref/lineage_id 溯源） |
| 9 | `report_generator` + `dashboard` | 报告生成 + 可视化展示 |

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

## 支持的检测规则

| 规则ID | 类型 | 描述 |
|--------|------|------|
| 5503 | brute_force | SSH/RDP 暴力破解 |
| 5710 | scanning | 端口扫描 |
| 5501 | malware_detected | 恶意软件检测 |
| 5712 | reconnaissance | 信息侦查 |
| 5763 | privilege_escalation | 权限提升 |
| 5715 | lateral_movement | 横向移动 |
| 5502 | data_exfiltration | 数据外泄 |

## 配置说明

主要配置项在 `MVP/config.py` 中：

```python
# 数据路径
WAZUH_LOGS_DIR       # Wazuh 日志目录
ALERTS_JSON_PATH     # 告警数据文件路径
OUTPUTS_DIR          # 分析结果输出目录

# 服务连接
OPENSEARCH_HOST      # OpenSearch 地址 (默认 127.0.0.1:9200)
NATS_SERVERS         # NATS 服务器列表

# LLM 分析
ANTHROPIC_API_KEY    # 从环境变量 ANTHROPIC_AUTH_TOKEN 读取
ANTHROPIC_MODEL      # 模型名称 (默认 claude-sonnet-4-6)

# 实时监控
WATCH_INTERVAL       # 告警文件轮询间隔 (默认 2秒, 在 client_app.py 中)
```

数据源注册在 `MVP/metadata.json` 中，支持多节点、多数据源的灵活配置。

## 故障排除

| 问题 | 解决方法 |
|------|----------|
| Docker 容器无法启动 | 检查内存 >= 8GB，`docker compose logs` 查看日志 |
| LLM API 调用失败 | 检查 `ANTHROPIC_AUTH_TOKEN` 环境变量，系统自动回退规则引擎 |
| OpenSearch 连接失败 | `docker compose up -d opensearch`，数据已本地保存可稍后重试 |
| NATS 连接失败 | 确认 Docker 基础设施已启动，先运行 `scripts/run_server.sh` |
| DuckDB 查询失败 | 确认 `wazuh_logs/alerts/alerts.json` 存在且为 NDJSON 格式 |
| 无实时信号 | 确认 Wazuh Agent 已连接 Manager，alerts.json 有新告警写入 |

## 许可证

MIT License
