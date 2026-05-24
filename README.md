# AI-SOC 智能安全运营中心

AI-SOC (Artificial Intelligence Security Operations Center) 是一个基于**数据编织 (Data Fabric)** 架构的安全事件自动化研判平台。

核心理念：**物理分布、逻辑统一、按需取证、可信研判**。

## 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    终端 2+: Client (边缘侧)                   │
│                                                             │
│  simulated_nodes/<node>/auth.log 或 /var/log/auth.log       │
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
│  Web Console (Streamlit :8500)  ← 统一运维面板               │
│                                                             │
│  存储层: OpenSearch + DuckDB                                 │
└─────────────────────────────────────────────────────────────┘
```

### 实时数据流

```
系统写入 auth.log
  → DetectionEngine tail + 解析 (每2秒轮询)
  → DuckDB 内存表写入 → YAML 规则 SQL threshold 检测
  → 生成轻量级微信号 → NATS soc.signals.<node_id>
  → Server Signal Listener 接收 (过期信号自动跳过)
  → 触发按需查询 → NATS soc.query.requests
  → Client DuckDB Sidecar 查询 auth_events 内存表 → 只返回关键证据
  → NATS soc.query.results
  → Server 接收 → OCSF 标准化 → Agent Team 研判 → 复核 → 报告

全链路监控:
  → MonitorEmitter → NATS soc.monitor.events (Core Pub/Sub)
  → Web Console Server监控 页面实时展示
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

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入 DeepSeek API Key
```

`.env` 文件示例：
```bash
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-key-here
```

敏感信息已从代码中移除，统一通过 `.env` 文件管理。`.env` 已加入 `.gitignore`，不会被提交到 git。

### 3. 注册客户端

```bash
# 必须先注册节点 (metadata.json)，再注册 Client (client_config.yaml)

# 注册模拟节点
bash scripts/register_client.sh add gateway-guard --node-id node-web-01

# 注册真机节点
bash scripts/register_client.sh add real-host --node-id node-prod-01 --real

# 查看所有已注册客户端
bash scripts/register_client.sh list
```

也可在 Web Console 的「Client注册」和「节点注册」页面进行可视化管理。

### 4. 启动

**Server 和 Client 的启动顺序无要求** — 任意顺序均可正常工作。

**终端 1 — 启动服务端：**

```bash
bash scripts/run_server.sh
```

该脚本会自动：
- 检查并启动 Docker 基础设施（OpenSearch、NATS）
- 启动 Query Gateway（FastAPI，端口 8000）
- 启动 Signal Listener（监听 NATS 信令 soc.signals.*）
- 启动 Query Result Listener（监听查询结果，自动触发研判流水线）
- 启动 Web Console（Streamlit 统一运维面板，端口 8500）

**终端 2+ — 启动客户端：**

```bash
bash scripts/run_client.sh gateway-guard
bash scripts/run_client.sh db-sentinel
```

### 5. 触发安全测试

**方式一：Web Console → 🎯 安全测试**（推荐）

选择节点 → 选择攻击场景 → 设置注入条数 → 点击执行。支持 6 种内置场景 + 自定义场景模板。

**方式二：命令行**

```bash
bash scripts/trigger_authlog.sh --node-id node-web-01 ssh 6
bash scripts/trigger_authlog.sh ssh 6 --node-id node-web-01  # 任意顺序均可
```

### 6. 访问 Web Console

打开浏览器访问 **`http://localhost:8500`**：

| 页面 | 功能 | 自动刷新 |
|------|------|----------|
| 🏠 首页 | 基础设施状态、客户端运行状态、最近研判 | 5s |
| 📊 Server监控 | NATS 实时事件流，按 Client 分开展示 | 2s |
| 🖥️ Client面板 | 选择客户端查看详情、研判记录、日志预览 | 5s |
| 📋 数据查看 | 按节点/类型过滤，浏览证据/AI研判/复核/报告 | 8s |
| 📝 Client注册 | 从已注册节点中选择，注册/删除客户端 | - |
| 🔧 节点注册 | 管理 metadata.json 数据源 | - |
| 🚀 运行控制 | 进程管理（启停 Client）、流水线状态、快捷操作 | 3s |
| 🎯 安全测试 | 场景库、快速测试、自定义攻击场景模板 | 3s |
| 📜 检测规则 | 在线编辑 YAML 规则，保存后 2 秒热加载生效 | - |

首次访问时可设置密码保护：在 `.env` 中配置 `WEB_CONSOLE_PASSWORD=your-password`。

### 7. 停止

```bash
bash scripts/stop_server.sh    # 停止服务端 + 释放端口
bash scripts/stop_client.sh    # 停止所有客户端
```

## Docker 一键部署

```bash
# 基础设施 + Server + Web Console
docker compose up -d

# 全栈（含 2 个 Client）
docker compose --profile full up -d

# 停止
docker compose --profile full down
```

Docker Compose 会从 `.env` 文件自动加载 API Key 等环境变量。

## 服务端口

| 服务 | 地址 |
|------|------|
| **Web Console** (统一面板) | **`http://localhost:8500`** |
| Query Gateway | `http://localhost:8000` |
| OpenSearch | `http://localhost:9200` |
| OpenSearch Dashboards | `http://localhost:5601` |
| NATS Monitoring | `http://localhost:8222` |

## 多客户端架构

```
simulated_nodes/
├── node-web-01/auth.log    ← Client: gateway-guard
├── node-db-01/auth.log     ← Client: db-sentinel
└── node-app-01/auth.log    ← Client: app-watcher
```

规则：**一个 Client 管理一个 Node，Node 必须在 metadata.json 中先注册。**

客户端配置集中在 `MVP/client_config.yaml`：

```yaml
clients:
  - client_id: "gateway-guard"
    node_id: "node-web-01"
    log_path: "simulated_nodes/node-web-01/auth.log"  # 相对路径 → 模拟
    description: "Web网关日志监控"

  - client_id: "real-host"
    node_id: "node-prod-01"
    log_path: "/var/log/auth.log"                     # 绝对路径 → 真机
    description: "生产环境实机监控"
```

安全测试场景配置在 `MVP/test_scenarios.yaml`，支持自定义日志模板和变量。

## 项目结构

```
SOC/
├── .env                            # 环境变量（敏感信息，不提交 git）
├── .env.example                    # 环境变量模板（可提交）
├── scripts/                        # 启动/停止/测试/管理 脚本
│   ├── run_server.sh               # 启动服务端
│   ├── run_client.sh               # 启动客户端 (run_client.sh <client-id>)
│   ├── stop_server.sh              # 停止服务端 + 释放端口
│   ├── stop_client.sh              # 停止所有客户端
│   ├── register_client.sh          # Client 注册管理 (add/remove/list)
│   ├── init_node.sh                # 初始化模拟节点目录
│   ├── trigger_authlog.sh          # 触发安全事件 (支持 --node-id 任意位置)
│   ├── trigger_test.sh             # 注入模拟告警 (兼容旧模式)
│   ├── docker-entrypoint-server.sh # Docker Server 入口
│   └── docker-entrypoint-client.sh # Docker Client 入口
│
├── simulated_nodes/                # 模拟节点日志目录
│   ├── node-web-01/auth.log
│   ├── node-db-01/auth.log
│   └── node-app-01/auth.log
│
├── MVP/
│   ├── config.py                   # 全局配置 + 启动校验
│   ├── client_config.yaml          # Client 注册配置
│   ├── test_scenarios.yaml         # 安全测试场景配置
│   ├── metadata.json               # 数据源注册表
│   │
│   ├── client/                     # 边缘侧模块
│   │   ├── client_app.py           # 客户端统一入口 (--client-id <id>)
│   │   ├── detection_engine.py     # 本地检测引擎 (规则热加载)
│   │   ├── detection_rules.yaml    # YAML 检测规则
│   │   ├── log_parser.py           # auth.log syslog 解析器
│   │   ├── duckdb_sidecar.py       # DuckDB 边缘查询引擎
│   │   └── __init__.py
│   │
│   ├── server/                     # 中心侧模块
│   │   ├── server_app.py           # 服务端统一入口
│   │   ├── signal_listener.py      # NATS 信令监听 (过期信号过滤)
│   │   ├── query_result_listener.py # NATS 查询结果监听 + 研判流水线
│   │   ├── query_gateway.py        # FastAPI 查询网关
│   │   ├── agent_team.py           # 多Agent LLM 研判 (DeepSeek/Anthropic)
│   │   ├── readiness.py            # 数据质量门控
│   │   ├── verifier.py             # 复核校验 (防AI幻觉)
│   │   ├── report_generator.py     # Markdown 研判报告
│   │   ├── opensearch_loader.py    # OpenSearch 数据持久化
│   │   └── __init__.py
│   │
│   ├── web_console/                # 统一运维控制台 (端口 8500)
│   │   ├── 🏠_首页.py
│   │   ├── auth.py                 # Web Console 认证
│   │   └── pages/
│   │       ├── 1_📊_Server监控.py
│   │       ├── 2_🖥️_Client面板.py
│   │       ├── 3_📋_数据查看.py
│   │       ├── 4_📝_Client注册.py
│   │       ├── 5_🔧_节点注册.py
│   │       ├── 6_🚀_运行控制.py
│   │       ├── 7_📜_检测规则.py
│   │       └── 8_🎯_安全测试.py
│   │
│   ├── common/                     # 共享模块
│   │   ├── ocsf_mapper.py          # OCSF-lite 字段标准化
│   │   ├── nats_utils.py           # NATS Stream/订阅/ACK 工具
│   │   ├── monitor_events.py       # 监控事件发射器
│   │   └── __init__.py
│   │
│   └── outputs/                    # 分析结果 + 审计日志
│       ├── audit.log               # 操作审计记录
│       └── 20260524_181218/
│           ├── evidence.json
│           ├── readiness.json
│           ├── agent_result.json
│           ├── agent_draft.json
│           ├── verifier_result.json
│           └── report.md
│
├── docker-compose.yml              # 基础设施 + 应用服务编排
├── Dockerfile                      # Python 服务镜像
├── requirements.txt                # Python 依赖
└── README.md
```

## 研判流程

```
Step 1-2: DetectionEngine tail auth.log
  → log_parser 解析 syslog 行
  → DuckDB 内存表 auth_events
  → YAML SQL threshold 检测
  → 信号 → NATS soc.signals.<node_id>

Step 3-5: SignalListener 接收信号 (过期信号自动跳过)
  → 查询请求 → NATS soc.query.requests
  → DuckDB Sidecar 按需查询
  → 轻量级证据 → NATS soc.query.results
  → ocsf_mapper 字段标准化 (保留 raw_ref/lineage_id/query_id)

Step 6-9: QueryResultListener 接收证据
  → readiness 数据质量门控
  → agent_team LLM 多Agent研判 (Triage → AttackChain → Report)
  → verifier 5层复核校验 (防AI幻觉)
  → report_generator Markdown 报告
  → OpenSearch 持久化
```

## Agent Team 研判

支持两种 LLM Provider，通过 `LLM_PROVIDER` 环境变量切换：

### DeepSeek（默认）

```bash
export DEEPSEEK_API_KEY="sk-xxx"
export LLM_PROVIDER="deepseek"
```

| Agent | 模型 | 用途 |
|-------|------|------|
| Triage | `deepseek-chat` | 快速事件分类 |
| Attack Chain | `deepseek-reasoner` | 深度攻击链推理 |
| Report | `deepseek-chat` | 中文报告生成 |

### Anthropic（备选）

```bash
export ANTHROPIC_AUTH_TOKEN="sk-ant-xxx"
export LLM_PROVIDER="anthropic"
```

LLM 不可用时自动回退规则引擎。

## 检测规则

定义在 `MVP/client/detection_rules.yaml`，支持热加载：

| 规则ID | 类型 | 描述 | 阈值 | 冷却 |
|--------|------|------|------|------|
| LOCAL_SSH_BRUTE_FORCE | brute_force | SSH 暴力破解 | 5次/5分钟, src_ip | 30s |
| LOCAL_SSH_SCAN | reconnaissance | SSH 扫描检测 | 3次/2分钟, src_ip | 30s |
| LOCAL_SUDO_FAILURES | privilege_escalation | Sudo 认证失败 | 3次/2分钟, dst_user | 30s |
| LOCAL_SU_FAILURES | privilege_escalation | Su 认证失败 | 3次/2分钟, dst_user | 30s |
| LOCAL_SSH_ACCEPTED | normal | SSH 成功登录 | 1次 | 0s |

通过 Web Console → 📜 检测规则 在线编辑，保存后 2 秒内自动热加载生效，无需重启 Client。

## 安全测试场景

预置 6 种可配置攻击场景 (`MVP/test_scenarios.yaml`)：

| 场景 | 分类 | 触发规则 |
|------|------|----------|
| SSH 暴力破解 | brute_force | LOCAL_SSH_BRUTE_FORCE |
| SSH 端口扫描 | reconnaissance | LOCAL_SSH_SCAN |
| Sudo 提权失败 | privilege_escalation | LOCAL_SUDO_FAILURES |
| Su 切换用户失败 | privilege_escalation | LOCAL_SU_FAILURES |
| SSH 成功登录 | normal | LOCAL_SSH_ACCEPTED |
| 混合攻击 | custom | BRUTE_FORCE + SUDO |

支持在 Web Console → 🎯 安全测试 页面自定义场景模板和变量。

## 配置说明

敏感信息统一在 `.env` 文件管理：

```bash
# LLM
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com

# OpenSearch
OPENSEARCH_USER=admin
OPENSEARCH_PASS=admin

# Web Console 认证（可选，为空则跳过）
WEB_CONSOLE_PASSWORD=
```

`MVP/config.py` 启动时自动加载 `.env`，并执行配置校验（缺失关键变量给出警告）。

## 故障排除

| 问题 | 解决方法 |
|------|----------|
| Docker 容器无法启动 | 检查内存 >= 8GB，`docker compose logs` 查看日志 |
| LLM API 调用失败 | 检查 `.env` 中 `DEEPSEEK_API_KEY` 是否正确 |
| OpenSearch 连接失败 | `docker compose up -d opensearch` |
| NATS 连接失败 | `docker compose up -d nats` |
| Server 启动后不停产生数据 | NATS 积压旧信号：`docker compose down && docker compose up -d` 重置 |
| 触发测试后无反应 | 确认 Client 在运行；同 IP 同规则有 30s cooldown |
| 日志文件无换行 | 通过 Web Console 安全测试页面注入（已内置换行修复） |
| Client 未在列表 | `bash scripts/register_client.sh list` 或 Web Console Client注册页 |
| Web Console 打不开 | 确认端口 8500 未被占用 |


## 许可证

MIT License
