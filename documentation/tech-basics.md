# AI-SOC 技术基础指南

本文档帮助你从零开始理解和掌握本项目涉及的所有技术。

---

## 1. Docker & Docker Compose

### 1.1 什么是 Docker

Docker 是一个容器化平台。你可以把容器理解为"轻量级虚拟机"——它拥有独立的文件系统、进程空间，但与宿主机共享内核。

**关键概念：**

| 概念 | 说明 | 类比 |
|------|------|------|
| **镜像 (Image)** | 只读模板，包含运行环境 | 操作系统的 ISO 文件 |
| **容器 (Container)** | 镜像的运行实例 | 从 ISO 安装好的虚拟机 |
| **Dockerfile** | 构建镜像的脚本 | 安装脚本 |
| **docker-compose.yml** | 多容器编排配置 | Vagrantfile |
| **Volume/Bind Mount** | 宿主机与容器的目录映射 | 共享文件夹 |
| **端口映射** | 宿主机端口 → 容器端口 | 端口转发 |
| **Network** | 容器间的虚拟网络 | 虚拟交换机 |

### 1.2 本项目的 docker-compose.yml 解析

```yaml
services:
  opensearch:
    image: opensearchproject/opensearch:3.5.0    # 使用哪个镜像
    container_name: opensearch                    # 容器名称（固定）
    environment:
      - discovery.type=single-node                # 单节点模式
      - OPENSEARCH_JAVA_OPTS=-Xms4G -Xmx4G        # JVM 内存限制
      - plugins.security.disabled=true            # 禁用安全插件
    ports:
      - "9200:9200"                               # 宿主机:容器
    networks:
      - aisoc-net                                 # 接入的网络

  nats:
    image: nats:2.10-alpine
    command: "--jetstream --http_port 8222"
    ports:
      - "4222:4222"
      - "8222:8222"
    networks:
      - aisoc-net

networks:
  aisoc-net:                                       # 自定义网络定义
```

### 1.3 网络: soc_aisoc-net

Docker Compose 自动创建网络 `<项目名>_<网络名>`。本项目项目名默认为目录名 `soc`，网络名为 `aisoc-net`，因此实际网络名是 `soc_aisoc-net`。

容器间可以通过**服务名**互相访问：
- `opensearch` → 解析为 OpenSearch 容器的 IP
- `nats` → 解析为 NATS 容器的 IP

### 1.4 常用操作速查

```bash
# 启动/停止
docker compose up -d                          # 后台启动所有容器
docker compose stop                           # 停止所有容器
docker compose start                          # 启动已停止的容器

# 单个容器操作
docker restart opensearch                     # 重启
docker compose stop nats                      # 停止
docker compose rm -f nats                     # 删除容器
docker compose up -d nats                     # 重新创建并启动

# 查看状态
docker ps                                     # 运行中的容器
docker ps -a                                  # 所有容器（包括已停止的）
docker stats                                  # 实时资源使用
docker logs opensearch --tail 50              # 查看日志

# 进入容器
docker exec -it nats sh                       # 交互式 shell
```

---

## 2. NATS & JetStream

### 2.1 NATS 是什么

NATS 是一个高性能消息中间件。核心模式：

**发布/订阅 (Pub/Sub):**
```
Publisher ──msg──> Subject "foo.bar" ──msg──> Subscriber 1
                                        ──msg──> Subscriber 2
```
- 发布者发送消息到 subject（主题）
- 所有订阅该 subject 的订阅者都会收到
- **at-most-once**：消息不持久化，订阅者离线时消息丢失

**JetStream（持久化层）:**
- 在 Pub/Sub 之上增加持久化
- 消息存储在 Stream（流）中
- Consumer（消费者）可以 offline 后重新消费
- 支持手动 ACK（确认）

### 2.2 本项目的 NATS 架构

```
Stream: SIGNALS (JetStream)
  Subject: soc.signals.*          (通配符, 匹配 soc.signals.node-web-01 等)
  Consumer: signal-listener        (durable, 手动 ACK)

Stream: QUERY_REQUESTS (JetStream)
  Subject: soc.query.requests
  Consumer: duckdb-sidecar-{node_id} (durable, 手动 ACK)

Stream: QUERY_RESULTS (JetStream)
  Subject: soc.query.results
  Consumer: query-result-listener  (durable, 手动 ACK)

Core Pub/Sub (非 JetStream):
  Subject: soc.monitor.events     → Monitor Dashboard (:8502) 实时消费
```

### 2.3 Python 客户端用法

```python
import nats
import json

# 连接
nc = await nats.connect(servers=["nats://localhost:4222"])
js = nc.jetstream()

# 确保 Stream 存在
await js.add_stream(name="SIGNALS", subjects=["soc.signals.*"])

# 发布
await js.publish("soc.signals.node-web-01", json.dumps(signal).encode())

# 订阅 (durable consumer, 手动 ACK)
sub = await js.subscribe("soc.signals.*", durable="my-consumer", manual_ack=True)

async for msg in sub.messages:
    data = json.loads(msg.data.decode())
    # 处理消息...
    await msg.ack()  # 手动确认

await nc.close()
```

### 2.4 Consumer 残留问题

当客户端异常断开时，durable consumer 可能残留在 NATS 服务器上。再次订阅会报错。

**解决**: `subscribe_safe()` 方法先尝试订阅，失败则删除残留 consumer 后重试：

```python
async def subscribe_safe(js, subject, durable):
    try:
        return await js.subscribe(subject, durable=durable, manual_ack=True)
    except Exception:
        # 自动清理残留 consumer 后重试
        for stream_name in ["SIGNALS", "QUERY_REQUESTS", "QUERY_RESULTS"]:
            try:
                await js.delete_consumer(stream_name, durable)
            except Exception:
                pass
        return await js.subscribe(subject, durable=durable, manual_ack=True)
```

### 2.5 NATS 监控

```bash
# HTTP 监控端点
curl http://localhost:8222/healthz         # 健康检查
curl http://localhost:8222/varz            # 服务器状态
curl http://localhost:8222/jsz             # JetStream 状态

# 命令行工具
docker exec nats nats stream ls            # 列出所有 Stream
docker exec nats nats consumer ls SIGNALS  # 列出 SIGNALS Stream 的 Consumer
```

---

## 3. DuckDB

### 3.1 DuckDB 是什么

DuckDB 是一个嵌入式列式数据库，专为分析查询（OLAP）设计。

**与 SQLite 的区别:**
| 特性 | SQLite | DuckDB |
|------|--------|--------|
| 存储方式 | 行式 (Row-based) | 列式 (Column-based) |
| 优化场景 | 事务处理 (OLTP) | 分析查询 (OLAP) |
| 外部数据 | 需要导入 | 直接查询 JSON/CSV/Parquet |
| 并发 | 单写入者 | 单写入者 |
| 向量化执行 | 否 | 是 |

### 3.2 核心用法

```python
import duckdb

# 方式1: 内存数据库
con = duckdb.connect()
con.execute("SELECT 1").fetchall()

# 方式2: 直接查询 JSON 文件 (无需建表!)
con.execute("""
    SELECT * FROM read_json_auto('/path/to/alerts.json')
    WHERE "rule"."id" = '5503'
    LIMIT 20
""").fetchall()

# 方式3: 查询 CSV
con.execute("SELECT * FROM read_csv_auto('data.csv')").fetchall()

con.close()
```

### 3.3 read_json_auto() 详解

这个函数自动检测 JSON 格式并推断 schema：

- 支持**NDJSON**（每行一个 JSON）
- 支持**JSON 数组**（`[{...}, {...}]`）
- 支持**嵌套结构**（嵌套对象用 `.` 或 `[]` 访问）
- **自动类型推断**：字符串 → VARCHAR，数字 → INTEGER/DOUBLE，布尔 → BOOLEAN

```sql
-- 访问嵌套字段
SELECT "rule"."id", "rule"."level", "agent"."name", "agent"."ip"
FROM read_json_auto('alerts.json')
WHERE "rule"."level" >= 5
ORDER BY "timestamp" DESC
LIMIT 10;
```

### 3.4 在本项目中的角色

- **Client 端**: DetectionEngine 使用 DuckDB 内存表存储解析后的 auth.log 事件，执行 SQL threshold 检测
- **Client 端**: DuckDBQueryEngine 使用 DuckDB 查询内存表或 JSON 文件
- **Server 端**: QueryGateway 使用 DuckDB 做开发/单机模式查询
- **优势**: 不需要安装 MySQL/PostgreSQL，零配置，进程内运行

---

## 4. Python asyncio

### 4.1 为什么需要 asyncio

NATS 客户端是异步的（`async/await`）。这意味着我们不能简单地在同步代码中调用它。

### 4.2 核心概念

```python
import asyncio

# async def 定义协程 (coroutine)
async def fetch_data():
    await asyncio.sleep(1)  # await 暂停协程，让出控制权
    return "done"

# 运行协程
async def main():
    # 并发运行多个协程
    results = await asyncio.gather(
        fetch_data(),
        fetch_data(),
        fetch_data(),
    )

asyncio.run(main())
```

### 4.3 本项目中的 asyncio 模式

**创建并发 Task:**
```python
engine_task = asyncio.create_task(engine.run_forever(), name="detection")
sidecar_task = asyncio.create_task(sidecar.start_listening(), name="sidecar")

# 等待任意一个完成 (通常第一个完成的是因为被取消)
done, pending = await asyncio.wait(
    [engine_task, sidecar_task],
    return_when=asyncio.FIRST_COMPLETED
)

# 取消剩余的
for task in pending:
    task.cancel()
```

**监听 NATS 消息（无限循环）:**
```python
async def listen_forever(self):
    sub = await self.js.subscribe("soc.signals.*", durable="x", manual_ack=True)
    async for msg in sub.messages:  # 异步迭代器
        await self.handle_signal(msg)
```

**在线程中运行同步代码（FastAPI）:**
```python
import threading

def run_gateway():
    uvicorn.run(app, host="0.0.0.0", port=8000)

# FastAPI 是同步的，需要在独立线程运行
thread = threading.Thread(target=run_gateway, daemon=True)
thread.start()
```

---

## 5. FastAPI + Uvicorn + Pydantic

### 5.1 什么是 FastAPI

FastAPI 是一个现代 Python Web 框架，特点：
- **自动生成 OpenAPI 文档** (Swagger UI)
- **Pydantic 数据校验**
- **异步支持**
- **类型安全**

### 5.2 最简单的 FastAPI 应用

```python
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class Item(BaseModel):
    name: str
    price: float

@app.get("/")
def root():
    return {"status": "ok"}

@app.post("/items")
def create_item(item: Item):          # Pydantic 自动校验
    return {"name": item.name, "price": item.price * 1.1}
```

### 5.3 运行 FastAPI

```bash
# 命令行方式
uvicorn app:app --host 0.0.0.0 --port 8000

# Python 代码方式
uvicorn.run("module:app", host="0.0.0.0", port=8000)
```

### 5.4 本项目的 API 端点

| 方法 | 路径 | 请求模型 | 响应模型 |
|------|------|----------|----------|
| GET | `/` | — | dict |
| GET | `/health` | — | dict |
| GET | `/metadata` | — | list |
| POST | `/query` | QueryRequest | QueryResponse |

### 5.5 Pydantic 模型示例

```python
class QueryRequest(BaseModel):
    case_id: str = "case-soc-001"          # 默认值
    node_id: str = "node-web-01"
    source: str = "wazuh_alerts"
    signal_id: Optional[str] = None        # 可选字段
    filters: dict = Field(default_factory=dict)
    limit: int = 20
```

---

## 6. Streamlit

### 6.1 什么是 Streamlit

Streamlit 是一个纯 Python 的数据应用框架。不需要写 HTML/CSS/JS，只需要 Python 代码。

### 6.2 基础组件

```python
import streamlit as st

# 文本
st.title("标题")
st.markdown("**Markdown** 文本")
st.code("print('hello')", language="python")

# 数据展示
st.metric("温度", "25°C")          # 指标卡片
st.dataframe(df)                    # 数据表格
st.json({"key": "value"})           # JSON 展示

# 交互
selected = st.selectbox("选择", ["A", "B", "C"])
if st.button("点击"):
    st.success("成功!")

# 布局
col1, col2 = st.columns(2)          # 两列
with st.container(border=True):     # 带边框的容器
    st.write("卡片内容")

with st.expander("点击展开"):        # 折叠面板
    st.write("详细内容")
```

### 6.3 本项目的 Dashboard 布局

```
┌─────────────────────────────────────────────┐
│ 侧边栏 (Sidebar)          │  主区域 (Main)    │
│                           │                  │
│ 📂 历史研判任务            │ 🛡️ 标题          │
│   [下拉选择]              │                  │
│                           │ 📊 卡片1: 就绪度  │
│                           │                  │
│                           │ 🔍 卡片2: 证据   │
│                           │                  │
│                           │ 🧠 卡片3: AI研判 │
│                           │                  │
│                           │ 🛡️ 卡片4: 复核   │
│                           │                  │
│                           │ 📄 卡片5: 报告   │
└─────────────────────────────────────────────┘
```

### 6.4 启动 Dashboard

```bash
streamlit run MVP/server/dashboard.py --server.port 8501 --server.headless true
```

---

## 7. Anthropic API (Claude)

### 7.1 API 基础

```python
from anthropic import Anthropic

client = Anthropic(api_key="sk-ant-...")

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system="你是一个安全分析专家。只输出 JSON，不要其他文字。",
    messages=[
        {"role": "user", "content": "分析以下告警: ..."}
    ],
)

# 提取文本响应 (处理 ThinkingBlock 兼容)
for block in response.content:
    if hasattr(block, "text"):
        print(block.text)
```

### 7.2 System Prompt 设计

本项目使用 system prompt 定义 Agent 的角色和输出格式：

```
你是一个 SOC 安全分析团队的分诊 (Triage) 专家。
你需要分析安全告警证据，判断事件类型、优先级和置信度。

请只返回 JSON，不要有任何其他文字：
```json
{
  "priority": "critical|high|medium|low",
  "event_type": "brute_force|scanning|...",
  "summary": "用中文简要描述（1-2句话）",
  "confidence": "high|medium|low"
}
```
```

### 7.3 处理代理环境变量

`httpx` (Anthropic SDK 底层 HTTP 库) 遇到 SOCKS 代理时会崩溃：

```python
# 保存并清除代理环境变量
saved = {}
for key in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", ...):
    saved[key] = os.environ.pop(key, None)

# 创建客户端
client = Anthropic(...)

# 恢复代理环境变量
for key, val in saved.items():
    if val is not None:
        os.environ[key] = val
```

---

## 8. NDJSON 格式

### 8.1 格式定义

**NDJSON (Newline Delimited JSON)** = 每行一个完整的 JSON 对象，行尾有换行符 `\n`。

```
{"id":1,"name":"Alice","score":95}
{"id":2,"name":"Bob","score":87}
{"id":3,"name":"Charlie","score":92}
```

### 8.2 为什么用 NDJSON

- **追加友好**: 无需解析整个文件，直接在末尾 append 一行
- **流式处理**: 可以逐行读取，不需要一次性加载整个文件到内存
- **容错**: 某一行格式错误不影响其他行的解析
- **DuckDB 原生支持**: `read_json_auto()` 可以直接查询

### 8.3 处理 NDJSON 文件

```python
import json

# 追加新记录
with open("alerts.json", "a") as f:
    f.write(json.dumps(new_alert) + "\n")

# 逐行读取
with open("alerts.json", "r") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        alert = json.loads(line)
        process(alert)

# 增量读取 (从指定 offset 开始)
with open("alerts.json", "r") as f:
    f.seek(last_offset)   # 跳到上次读取位置
    new_data = f.read()   # 读取新增内容
    last_offset = f.tell()  # 记录新位置
```

---

## 9. Python 多线程与子进程

### 9.1 threading.Thread

本项目用线程来同时运行多个服务组件：

```python
import threading

# FastAPI 在独立线程中运行 (uvicorn.run 是阻塞的)
gateway_thread = threading.Thread(target=run_query_gateway, daemon=True)
gateway_thread.start()

# Dashboard 也在独立线程中通过 subprocess 启动
dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
dashboard_thread.start()
```

`daemon=True` 表示主线程退出时，这些线程也会被强制终止。

### 9.2 subprocess

Dashboard 通过 `subprocess.run()` 启动：

```python
import subprocess

subprocess.run(
    [sys.executable, "-m", "streamlit", "run",
     "server/dashboard.py",
     "--server.port", "8501"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
```

---

## 10. auth.log Syslog 解析

### 10.1 auth.log 格式

auth.log 是 Linux 系统认证日志，由 rsyslog/systemd-journald 写入。每行格式：

```
May 22 10:15:32 hostname sshd[12345]: Failed password for root from 192.168.1.100 port 22 ssh2
```

结构：`{月份 日期 时间} {主机名} {进程}[{PID}]: {消息}`

### 10.2 项目中的解析器 (log_parser.py)

两阶段解析：
1. **syslog 头部正则** — 提取 timestamp、hostname、process、pid、message
2. **进程特定正则** — 根据 process (sshd/sudo/su) 选择不同的消息解析器

支持的 log_type：
- `ssh_failed_password` — SSH 密码失败
- `ssh_accepted` — SSH 登录成功
- `ssh_scan` — SSH 扫描探测
- `ssh_connection_closed` — SSH 连接关闭
- `ssh_auth_failure` — SSH 认证失败（PAM 错误）
- `ssh_preauth_disconnect` — SSH 预认证断开
- `sudo_auth_failure` — Sudo 认证失败
- `sudo_success` — Sudo 命令执行
- `su_failed` — Su 切换失败
- `su_success` — Su 切换成功

---

## 11. Bash 脚本模式

### 11.1 脚本模板

```bash
#!/bin/bash
set -e  # 遇到错误立即退出

# 获取脚本所在目录 (兼容软链接)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 切换到项目根目录 (脚本在 scripts/ 子目录下)
cd "$SCRIPT_DIR/.."
```

### 11.2 条件判断

```bash
if docker ps | grep -q opensearch; then
    echo "OpenSearch 已运行"
else
    echo "OpenSearch 未运行"
fi

if [ -f "/var/log/auth.log" ]; then
    echo "auth.log 存在"
fi

if [ -r "/var/log/auth.log" ]; then
    echo "auth.log 可读"
fi
```

### 11.3 Python 内联脚本

```bash
python3 << 'PYEOF'  # 'PYEOF' 加引号防止 bash 变量展开
import json
data = {"key": "value"}
print(json.dumps(data))
PYEOF
```

### 11.4 捕获命令输出

```bash
# 命令替换
NOW=$(date '+%Y-%m-%d %H:%M:%S')

# 命令退出码
if curl -s http://localhost:8222/healthz > /dev/null 2>&1; then
    echo "NATS OK"
fi
```

---

## 12. 项目目录结构速查

```
SOC/                               # 项目根目录
├── documentation/                  # 项目文档
├── scripts/                        # 启动/停止/测试脚本
│   ├── run_server.sh               # 启动服务端
│   ├── run_client.sh               # 启动客户端
│   ├── stop_server.sh              # 停止服务端
│   ├── stop_client.sh              # 停止客户端
│   ├── trigger_authlog.sh          # 测试: 触发真实安全事件 (SSH/sudo/su)
│   └── trigger_test.sh             # 测试: 直接注入 JSON 告警 (兼容旧模式)
├── MVP/                            # Python 代码
│   ├── main.py                     # 单次分析入口
│   ├── config.py                   # 全局配置
│   ├── metadata.json               # 数据源注册表
│   ├── client/                     # 边缘侧
│   │   ├── client_app.py           # ★ 客户端主入口
│   │   ├── detection_engine.py     # DetectionEngine (tail auth.log + YAML检测)
│   │   ├── detection_rules.yaml    # YAML 检测规则
│   │   ├── log_parser.py           # auth.log syslog 解析器
│   │   ├── duckdb_sidecar.py       # DuckDB 查询引擎 (DuckDBQueryEngine)
│   │   ├── signal_watcher.py       # 实时信号监控 (兼容旧 Wazuh 模式)
│   │   ├── signal_generator.py     # 信号生成 (批量模式)
│   │   ├── local_gateway.py        # DuckDB 查询封装
│   │   ├── evidence_builder.py     # 证据构建
│   │   └── agent_analyzer.py       # 规则分析器 (回退)
│   ├── server/                     # 中心侧
│   │   ├── server_app.py           # ★ 服务端主入口
│   │   ├── signal_listener.py      # NATS 信令监听
│   │   ├── query_result_listener.py # NATS 结果监听
│   │   ├── query_gateway.py        # FastAPI 查询网关
│   │   ├── agent_team.py           # LLM 多Agent研判
│   │   ├── readiness.py            # 数据质量门控
│   │   ├── verifier.py             # 复核校验
│   │   ├── report_generator.py     # 报告生成
│   │   ├── opensearch_loader.py    # OpenSearch 持久化
│   │   ├── dashboard.py            # Streamlit 研判面板 (:8501)
│   │   └── monitor_dashboard.py   # Streamlit 实时监控 (:8502)
│   ├── common/                     # 共享模块
│   │   ├── ocsf_mapper.py          # OCSF 标准化
│   │   ├── nats_utils.py           # NATS 工具函数
│   │   └── monitor_events.py       # 监控事件发射器
│   └── outputs/                    # 分析结果 (按时间戳)
├── wazuh_logs/                     # 告警日志数据 (兼容旧模式)
│   └── alerts/alerts.json          # 告警数据 (NDJSON)
├── docker-compose.yml              # 容器编排
├── Dockerfile                      # Python 镜像
└── requirements.txt                # Python 依赖
```

---

## 学习路径建议

1. **先理解数据流**: auth.log → log_parser → DetectionEngine → 信号 → NATS → SignalListener → 查询 → DuckDBQueryEngine → 证据 → Monitor Events
2. **掌握容器化**: docker compose up/down/logs/exec
3. **理解 auth.log 解析**: syslog 格式 → 正则匹配 → 结构化事件
4. **熟悉 NATS**: Pub/Sub 模式 → JetStream 持久化 → subject 设计 → monitor events
5. **读懂 Python 代码**: client_app.py → server_app.py → agent_team.py → nats_utils.py → monitor_events.py
6. **学会故障排查**: Monitor Dashboard 实时诊断 → 检查 auth.log 权限 → 验证 NATS 连通性
