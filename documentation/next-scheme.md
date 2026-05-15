# AI-SOC 演进方案

本文档规划了下一阶段的两个演进方向：组件可观测化和多节点并行。

---

## 方向一：组件可观测化（端口化 + 监控面板）

### 目标

当前每个组件只在终端打印日志，无法结构化监控。目标是每个组件暴露 HTTP 端口，提供实时状态查询，然后用终端面板或 Web 面板统一展示。

### 端口规划

```
┌─────────────────────────────────────────────────────────┐
│ Server 端                                                │
│                                                          │
│  Query Gateway     :8000   已有 (FastAPI)                │
│  Dashboard         :8501   已有 (Streamlit)              │
│  SignalListener    :8001   新增 — 信令接收统计           │
│  ResultListener    :8002   新增 — 证据接收统计           │
│  Agent Team        :8003   新增 — 研判结果查询           │
│  Health Aggregator :8099   新增 — 汇总所有组件健康状态   │
│                                                          │
├─────────────────────────────────────────────────────────┤
│ Client 端 (每节点独立端口)                                │
│                                                          │
│  node-web-01                                              │
│    SignalWatcher   :8101   新增 — 告警监控状态           │
│    DuckDB Sidecar  :8111   新增 — 查询统计               │
│                                                          │
│  node-web-02                                              │
│    SignalWatcher   :8102   新增                          │
│    DuckDB Sidecar  :8112   新增                          │
│                                                          │
│  node-db-01                                               │
│    SignalWatcher   :8103   新增                          │
│    DuckDB Sidecar  :8113   新增                          │
└─────────────────────────────────────────────────────────┘
```

### 每个端点暴露的数据

**SignalWatcher (:81xx)**
```json
GET /status
{
  "component": "SignalWatcher",
  "node_id": "node-web-01",
  "file": "/home/admin/SOC/wazuh_logs/alerts/alerts.json",
  "file_size_bytes": 468434,
  "last_offset": 468000,
  "seen_alerts": 237,
  "signals_published": 20,
  "last_signal_at": "2026-05-15T20:43:47",
  "last_check_at": "2026-05-15T20:44:02",
  "poll_interval_s": 2,
  "uptime_s": 3600
}
```

**DuckDB Sidecar (:81x1)**
```json
GET /status
{
  "component": "SidecarQueryEngine",
  "node_id": "node-web-01",
  "queries_received": 45,
  "queries_processed": 43,
  "queries_failed": 2,
  "avg_latency_ms": 28.5,
  "last_query_id": "qry-fdb61cbb",
  "last_query_at": "2026-05-15T20:43:48"
}
```

**SignalListener (:8001)**
```json
GET /status
{
  "component": "SignalListener",
  "signals_received": 20,
  "signals_processed": 20,
  "signals_failed": 0,
  "queries_dispatched": 20,
  "last_signal_id": "sig-bbbe6f60",
  "last_signal_at": "2026-05-15T20:43:47"
}
```

**ResultListener (:8002)**
```json
GET /status
{
  "component": "ResultListener",
  "results_received": 18,
  "total_evidence": 270,
  "last_query_id": "qry-fdb61cbb",
  "last_result_at": "2026-05-15T20:43:48"
}
```

**Agent Team (:8003)**
```json
GET /status
{
  "component": "AgentTeam",
  "analyses_completed": 3,
  "last_event_type": "brute_force",
  "last_priority": "high",
  "last_confidence": "medium",
  "mode": "llm"
}
GET /results?limit=10   → 最近10次研判结果列表
```

### 实现方案

每个组件现有代码中已经有 stats dict 或计数器，只需加一个轻量 HTTP 端点。用 `aiohttp` 即可，不需要引入新的框架依赖：

```python
# 在现有 asyncio 事件循环中添加 HTTP 端点
from aiohttp import web

async def handle_status(request):
    return web.json_response({
        "component": "SignalWatcher",
        "node_id": self.node_id,
        "signals_published": self.stats["new"],
        ...
    })

app = web.Application()
app.router.add_get('/status', handle_status)
runner = web.AppRunner(app)
await runner.setup()
site = web.TCPSite(runner, '0.0.0.0', 8101)
await site.start()
```

因为所有组件已经在 asyncio 事件循环中运行，`aiohttp` 的 `TCPSite` 可以和 NATS 订阅在同一循环中共存，不阻塞。

### 监控面板

**方案 A: 终端面板（`rich` 库）**

```python
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.layout import Layout

# 每 2 秒轮询所有组件端口，渲染实时面板
layout = Layout()
layout.split(
    Layout(name="header", size=3),
    Layout(name="body"),
)
layout["body"].split_row(
    Layout(render_signal_panel()),     # 左: 信号流
    Layout(render_query_panel()),      # 中: 查询流
    Layout(render_analysis_panel()),   # 右: 研判流
)
```

效果：
```
┌─ AI-SOC 实时监控 ───────────────────────── 2026-05-15 20:44:02 ─┐
│                                                                  │
│  📡 信号流                 🔍 查询流              🧠 研判流       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐  │
│  │ node-web-01 ✅   │  │ Sidecar-01 ✅    │  │ 已研判: 3次  │  │
│  │ 信号: 20         │  │ 查询: 15次       │  │ 最新:爆破    │  │
│  │ 最新: 5503/Lv5   │  │ 平均: 28.5ms     │  │ 置信度: 中   │  │
│  │ 规则: PAM login  │  │ 证据: 225条      │  │ 模式: LLM    │  │
│  ├──────────────────┤  ├──────────────────┤  ├──────────────┤  │
│  │ node-web-02 ✅   │  │ Sidecar-02 ✅    │  │ 处置建议:    │  │
│  │ 信号: 15         │  │ 查询: 10次       │  │ 1.锁定源IP   │  │
│  │ 最新: 5710/Lv5   │  │ 平均: 31.2ms     │  │ 2.启用MFA    │  │
│  └──────────────────┘  └──────────────────┘  └──────────────┘  │
│                                                                  │
│  ⚡ 总吞吐: 35 信号 → 25 查询 → 375 证据 → 3 研判               │
└──────────────────────────────────────────────────────────────────┘
```

**方案 B: Web 面板（扩展 Streamlit）**

在现有 Dashboard 基础上加一个 Monitor Tab，用 `st.metric` 网格展示组件状态，通过 `requests` 轮询各组件端口。

### 需要改动的文件

| 文件 | 改动 |
|------|------|
| `MVP/client/client_app.py` | SignalWatcher 和 Sidecar 各加 aiohttp 端点 |
| `MVP/server/server_app.py` | SignalListener 和 ResultListener 各加端点 |
| `MVP/server/agent_team.py` | 加 HTTP 端点暴露研判结果 |
| `MVP/config.py` | 新增端口配置项 |
| 新建 `MVP/server/monitor.py` | 终端面板程序 |

### 风险与注意

- 端口冲突：多 Client 时必须分配不同端口，`run_client.sh` 启动时根据 `node_id` 自动分配
- 安全：这些端口仅在 localhost 监听，不对外开放
- aiohttp 是已有依赖（`requirements.txt` 中已有 `aiohttp>=3.9.0`）

---

## 方向二：多 Client 并行模拟

### 目标

在单机上模拟多个边缘节点同时运行，每个节点独立监控告警、接收查询、返回证据。

### 运行拓扑

```
┌──────────────────────────────────────────────────┐
│ Docker 容器                                       │
│  ┌─────────────┐  ┌──────┐  ┌────────────────┐  │
│  │wazuh-manager│  │ NATS │  │ OpenSearch     │  │
│  └──────┬──────┘  └──┬───┘  └────────────────┘  │
└─────────┼─────────────┼──────────────────────────┘
          │             │
    ┌─────┴─────┐  ┌────┴────┐  ┌──────────┐
    │ Client-A  │  │Client-B │  │ Client-C │
    │ web-01    │  │web-02   │  │ db-01    │
    │ :8101     │  │:8102    │  │:8103     │
    │ :8111     │  │:8112    │  │:8113     │
    └───────────┘  └─────────┘  └──────────┘
```

### 启动方式

```bash
# 终端1: Server (不变)
bash scripts/run_server.sh

# 终端2: Client-A
MVP_CLIENT_NODE=node-web-01 \
MVP_CLIENT_SIGNAL_PORT=8101 \
MVP_CLIENT_SIDECAR_PORT=8111 \
python3 MVP/client/client_app.py

# 终端3: Client-B
MVP_CLIENT_NODE=node-web-02 \
MVP_CLIENT_SIGNAL_PORT=8102 \
MVP_CLIENT_SIDECAR_PORT=8112 \
python3 MVP/client/client_app.py

# 终端4: Client-C
MVP_CLIENT_NODE=node-db-01 \
MVP_CLIENT_SIGNAL_PORT=8103 \
MVP_CLIENT_SIDECAR_PORT=8113 \
python3 MVP/client/client_app.py

# 终端5: 监控面板
python3 MVP/server/monitor.py

# 终端6: 触发测试 — 注入不同节点的告警
bash scripts/trigger_test.sh 5 5503 node-web-01
bash scripts/trigger_test.sh 3 5710 node-web-02
bash scripts/trigger_test.sh 2 5763 node-db-01
```

### 数据源策略

**方案 A: 共享文件 + agent.name 过滤（推荐用于模拟）**

所有 Client 读取同一个 `alerts.json`，通过 DuckDB SQL 中的 `WHERE agent.name = '{node_id}'` 获取各自节点的事件。

```python
# Client-A (node-web-01)
SELECT * FROM read_json_auto('alerts.json')
WHERE "agent"."name" = 'node-web-01'

# Client-B (node-web-02)
SELECT * FROM read_json_auto('alerts.json')
WHERE "agent"."name" = 'node-web-02'
```

`trigger_test.sh` 改造：支持指定 `agent.name` 字段，模拟不同节点的告警。

**方案 B: 独立数据文件（接近生产）**

```
wazuh_logs/
  ├── node-web-01/alerts.json
  ├── node-web-02/alerts.json
  └── node-db-01/alerts.json
```

每个 Client 只读自己节点的文件。更接近真实部署（每个 Agent 独立上报）。

**方案 C: 混合模式**

- 信号监控共享 `alerts.json`（用 `agent.name` 过滤）
- DuckDB 查询各自独立的文件
- 兼顾简单性和真实性

### 代码改动

| 文件 | 改动 | 工作量 |
|------|------|--------|
| `MVP/client/client_app.py` | `DEFAULT_NODE_ID` 改为从环境变量读取 | 2 行 |
| `MVP/client/client_app.py` | `_build_sql()` 增加 `WHERE agent.name = ?` | 5 行 |
| `MVP/client/client_app.py` | 端口从环境变量读取，自动分配 | 5 行 |
| `MVP/client/client_app.py` | 多 Client 共享同一个 `alerts.json` 时，NATS consumer 名唯一化 | 已支持 |
| `MVP/metadata.json` | 增加 `node-web-02`、`node-db-01` 的数据源条目 | 10 行 |
| `scripts/trigger_test.sh` | 支持 `--node-id` 参数注入不同节点告警 | 15 行 |
| 新建 `scripts/run_multi_client.sh` | 一键启动 3 个 Client | 30 行 |

### 多 Client 并发无冲突的保证

| 问题 | 为何无冲突 |
|------|-----------|
| DuckDB 读同一文件 | DuckDB 只读不写，多进程共享读没问题 |
| NATS consumer | 每个 Client 用 `{node_id}` 构造唯一 consumer 名 |
| SignalWatcher 去重 | 每个 Client 有自己的 `seen_ids` 集合，互不影响 |
| 端口 | 通过环境变量分配不同端口 |
| 证据输出 | Server 端 `outputs/` 目录用时间戳区分，不冲突 |

### 预期效果

启动 3 个 Client 和 Server 后，触发测试注入告警：

```
Server 终端:
[Server] 收到信号: sig-xxx | 节点=node-web-01 | 规则=5503
[Server] 收到信号: sig-yyy | 节点=node-web-02 | 规则=5710
[Server] 收到信号: sig-zzz | 节点=node-db-01 | 规则=5763
[Server] 已下发查询: qry-001 → node-web-01
[Server] 已下发查询: qry-002 → node-web-02
[Server] 已下发查询: qry-003 → node-db-01
[Server] 收到查询结果: qry-001 | 15 条 | node-web-01
[Server] 收到查询结果: qry-002 | 10 条 | node-web-02
[Server] 收到查询结果: qry-003 | 8 条  | node-db-01

Client-A 终端:
[Client:web-01] 监控到 5 条新告警 → 已发送信号
[Client:web-01] 收到查询: qry-001 → 返回 15 条证据 (28ms)

Client-B 终端:
[Client:web-02] 监控到 3 条新告警 → 已发送信号
[Client:web-02] 收到查询: qry-002 → 返回 10 条证据 (31ms)

Client-C 终端:
[Client:db-01] 监控到 2 条新告警 → 已发送信号
[Client:db-01] 收到查询: qry-003 → 返回 8 条证据 (25ms)
```

---

## 实施优先级

```
Phase 1: 基础可观测
  ├─ SignalWatcher HTTP 端点
  ├─ Sidecar HTTP 端点
  └─ 终端监控面板 (rich)

Phase 2: 多 Client 并行
  ├─ node_id 环境变量配置
  ├─ 多 Client 启动脚本
  └─ trigger_test.sh 支持多节点

Phase 3: 完善可观测
  ├─ Server 端组件 HTTP 端点
  ├─ Agent Team HTTP 端点
  └─ Web 监控面板 (Streamlit Tab)

Phase 4: 生产化
  ├─ 独立数据源目录
  ├─ Health Aggregator (:8099)
  └─ 告警通知集成
```

## 兼容性说明

两个方向**互不冲突**，可以独立实施：
- 方向一（端口化）不改变数据流，只在现有组件上附加 HTTP 端点
- 方向二（多 Client）复用现有 NATS 主题和 consumer 机制，只是增加实例数
- 方向一完成后，方向二的多 Client 监控可以直接看到效果
