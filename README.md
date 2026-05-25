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
   NATS JetStream (3个 Stream)
   ├── SIGNALS: soc.signals.*
   ├── QUERY_REQUESTS: soc.query.requests
   └── QUERY_RESULTS: soc.query.results
          │
┌─────────┼───────────────────────────────────────────────────┐
│                    终端 1: Server (中心侧)                    │
│         │                                                   │
│  Signal Listener (信令监听)                                  │
│         │                                                   │
│  Query Gateway (FastAPI :8000)                              │
│         │                                                   │
│  Query Result Listener (结果接收 + 自动研判流水线)             │
│         │                                                   │
│  Web Console (Streamlit :8500)  ← 统一运维面板               │
│                                                             │
│  存储层: OpenSearch (6个索引) + DuckDB (内存)                 │
└─────────────────────────────────────────────────────────────┘
```

### NATS 数据流

```
auth.log → DetectionEngine → 信号 → soc.signals.* → SignalListener
  (tail+解析+YAML检测)        (PUBLISH)  Stream:      (SUBSCRIBE)
                                          SIGNALS           │
                                                            ▼
DuckDB Sidecar ←── soc.query.requests ←────────────── 构建查询请求
(SUBSCRIBE)         Stream: QUERY_REQUESTS              (PUBLISH)
  │
  ▼
query_events() → 证据构建
  │
  ▼
DuckDB Sidecar ──→ soc.query.results ──→ QueryResultListener
(PUBLISH)           Stream:               (SUBSCRIBE)
                    QUERY_RESULTS              │
                                               ▼
                                          evidence.json
                                          OpenSearch 索引
                                          研判流水线 (5步):
                                          ① 数据就绪度
                                          ② Agent Team 研判
                                          ③ 复核校验
                                          ④ 报告生成
                                          ⑤ OpenSearch 索引
```

**重复告警防护**：DetectionEngine 按规则追踪已告警的最大事件 ID（`_last_signaled_max_id`），同一批事件不会重复触发告警。

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
- 检查并启动 Docker 基础设施（OpenSearch、NATS、**OpenSearch Dashboards**）
- 启动 Query Gateway（FastAPI，端口 8000）
- 启动 Signal Listener（监听 NATS 信令 soc.signals.*）
- 启动 Query Result Listener（监听查询结果，自动触发研判流水线 + OpenSearch 索引）
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
# 基础设施 + Server + Web Console + Dashboards
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

## OpenSearch 索引

系统自动创建 6 个索引，全部通过 NATS 数据流自动写入：

| 索引 | 写入时机 | 内容 | 时间字段 |
|------|---------|------|---------|
| `soc-signals` | SignalListener 收到信号 | 检测信号 (signal_id, rule_id, src_ip, ...) | `event_time` (ISO) |
| `soc-evidence` | QueryResultListener 收到证据 | 证据记录 (timestamp, rule_id, src_ip, raw_log, ...) | `timestamp` (ISO) |
| `soc-readiness` | 研判流水线 Step 5 | 数据就绪度评分 (score, level, checks, ...) | `@timestamp` (ISO) |
| `soc-analysis` | 研判流水线 Step 5 | Agent Team 研判结论 (summary, confidence, actions, ...) | `@timestamp` (ISO) |
| `soc-verification` | 研判流水线 Step 5 | 复核校验结果 (verified, issues, checks, ...) | `@timestamp` (ISO) |
| `soc-reports` | 研判流水线 Step 5 | 研判报告全文 (content, node_id, ...) | `@timestamp` (ISO) |

在 OpenSearch Dashboards (`http://localhost:5601`) 中创建 Index Pattern 即可可视化查询所有数据。

## NATS 管理

```bash
# 查看 Stream 状态
python3 scripts/nats_mgmt.py list

# 清空所有 Stream 消息
python3 scripts/nats_mgmt.py purge

# 只清空某个 Stream
python3 scripts/nats_mgmt.py purge SIGNALS

# 删除所有 Stream（下次启动自动重建）
python3 scripts/nats_mgmt.py rm
```

三个 Stream 均有 24 小时 / 500MB 的自动清理策略，正常情况下无需手动干预。

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
│   ├── trigger_authlog.sh          # 触发安全事件
│   ├── trigger_test.sh             # 注入模拟告警 (兼容旧模式)
│   ├── nats_mgmt.py                # NATS Stream 管理工具 (list/purge/rm)
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
│   ├── mapping.json                # OpenSearch soc-evidence 索引映射
│   │
│   ├── client/                     # 边缘侧模块
│   │   ├── client_app.py           # 客户端统一入口 (--client-id <id>)
│   │   ├── detection_engine.py     # 本地检测引擎 (规则热加载 + 重复告警防护)
│   │   ├── detection_rules.yaml    # YAML 检测规则
│   │   ├── log_parser.py           # auth.log syslog 解析器
│   │   ├── duckdb_sidecar.py       # DuckDB 边缘查询引擎
│   │   └── __init__.py
│   │
│   ├── server/                     # 中心侧模块
│   │   ├── server_app.py           # 服务端统一入口 (含 Dashboards 自动启动)
│   │   ├── signal_listener.py      # NATS 信令监听
│   │   ├── query_result_listener.py # NATS 查询结果监听 + 研判流水线 + OS索引
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
│       ├── client_db-sentinel.log  # Client 运行日志
│       └── 20260525_184707/
│           ├── evidence.json
│           ├── readiness.json
│           ├── agent_result.json
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
Step 1: DetectionEngine tail auth.log
  → log_parser 解析 syslog 行
  → DuckDB 内存表 auth_events
  → YAML SQL threshold 检测
  → 重复告警防护 (_last_signaled_max_id)
  → 信号 → NATS soc.signals.<node_id>

Step 2: SignalListener 接收信号
  → 过期信号过滤 (>5分钟自动跳过)
  → OpenSearch 信令索引 (soc-signals)
  → 查询请求 → NATS soc.query.requests

Step 3: DuckDB Sidecar 按需查询
  → auth_log source → DetectionEngine.query_events()
  → 轻量级证据构建 (evidence_id, lineage_id, hash)
  → 结果返回 → NATS soc.query.results

Step 4: QueryResultListener 接收证据
  → OCSF 标准化 → 写入 evidence.json
  → OpenSearch 证据索引 (soc-evidence)

Step 5: 自动研判流水线
  → ① readiness 数据质量门控
  → ② agent_team LLM 多Agent研判 (Triage → AttackChain → Report)
  → ③ verifier 复核校验 (防AI幻觉)
  → ④ report_generator Markdown 报告
  → ⑤ OpenSearch 索引 (soc-readiness / soc-analysis / soc-verification / soc-reports)
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

| 规则ID | 类型 | 描述 | 时间窗口 | 冷却 | 分组 |
|--------|------|------|----------|------|------|
| LOCAL_SSH_BRUTE_FORCE | brute_force | SSH 暴力破解 | 300s (5次) | 30s | src_ip |
| LOCAL_SSH_SCAN | reconnaissance | SSH 扫描检测 | 120s (3次) | 30s | src_ip |
| LOCAL_SUDO_FAILURES | privilege_escalation | Sudo 认证失败 | 120s (3次) | 30s | dst_user |
| LOCAL_SU_FAILURES | privilege_escalation | Su 认证失败 | 120s (3次) | 30s | dst_user |
| LOCAL_SSH_ACCEPTED | normal | SSH 成功登录 | 1s (1次) | 0s | — |

**时间窗口 vs 冷却时间**：窗口决定"多久内的相关事件算同一波攻击"，冷却决定"告警后多久不再重复报告"。DetectionEngine 内置 `_last_signaled_max_id` 机制防止同一批事件重复触发，即使冷却短于窗口也不会产生重复证据。

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
| NATS 消息积压 | `python3 scripts/nats_mgmt.py list` 查看，`purge` 清空 |
| Server 启动后不停产生空证据 | 旧信号积压导致：`python3 scripts/nats_mgmt.py purge` 清空 Stream |
| 触发测试后无反应 | 确认 Client 在运行；检查 NATS Stream 是否有积压旧信号 |
| 同一批事件重复触发告警 | 检查 Client 是否重启过（内存 DuckDB 重置）；规则冷却是否过短 |
| OpenSearch 索引无数据 | 确认 Server + Client 都在运行；检查 `_cat/indices` 确认索引存在 |
| Dashboards 时间字段无法识别 | 确认索引中时间字段为 ISO 8601 格式（含时区如 +08:00） |
| Client 未在列表 | `bash scripts/register_client.sh list` 或 Web Console Client注册页 |
| Web Console 打不开 | 确认端口 8500 未被占用 |

## 许可证

MIT License
