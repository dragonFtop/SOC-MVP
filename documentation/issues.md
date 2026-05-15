# AI-SOC 常见问题与解决方案

本文档汇总了在开发、部署和运行 AI-SOC 过程中可能遇到的各类问题，以及经过验证的解决方案。

---

## 目录

1. [Wazuh Agent 连接问题](#1-wazuh-agent-连接问题)
2. [告警生成问题](#2-告警生成问题)
3. [文件权限问题](#3-文件权限问题)
4. [NATS 消息总线问题](#4-nats-消息总线问题)
5. [Docker 容器问题](#5-docker-容器问题)
6. [Python/代码问题](#6-python代码问题)
7. [Client/Server 运行问题](#7-clientserver-运行问题)
8. [LLM 研判问题](#8-llm-研判问题)
9. [测试触发问题](#9-测试触发问题)
10. [性能与资源问题](#10-性能与资源问题)

---

## 1. Wazuh Agent 连接问题

### 1.1 Agent 无法连接到 Manager

**现象:**
```
wazuh-agentd: ERROR: (1216): Unable to connect to '[172.18.0.2]:1514/tcp': 'Connection refused'.
wazuh-agentd: WARNING: Unable to connect to any server.
```

**原因分析:**
1. Manager 容器未运行
2. Agent 配置的 Manager IP 地址错误（Docker 容器重启后 IP 可能变化）
3. Manager 端口未正确映射到宿主机
4. 系统防火墙阻止连接

**排查步骤:**
```bash
# 1. 检查 Manager 容器是否运行
docker ps | grep wazuh-manager

# 2. 检查 Manager 端口映射
docker port wazuh-manager
# 应显示: 1514/tcp -> 0.0.0.0:1514

# 3. 测试端口连通性
timeout 3 bash -c "echo >/dev/tcp/127.0.0.1/1514" && echo "端口可达" || echo "端口不可达"

# 4. 查看 Agent 当前配置的 Manager 地址
sudo grep '<address>' /var/ossec/etc/ossec.conf

# 5. 查看 Agent 连接日志
sudo grep 'agentd' /var/ossec/logs/ossec.log | tail -10
```

**解决方案:**
```bash
# 将 Agent 指向 127.0.0.1 (通过 Docker 端口映射连接)
sudo sed -i 's|<address>.*</address>|<address>127.0.0.1</address>|' /var/ossec/etc/ossec.conf
sudo systemctl restart wazuh-agent

# 验证连接
sudo grep 'Connected to the server' /var/ossec/logs/ossec.log
```

### 1.2 Agent 认证失败 (Duplicate agent name)

**现象:**
```
wazuh-agentd: ERROR: Duplicate agent name: 37vwmu3rudbyc0v. Unable to add agent (from manager)
wazuh-authd: WARNING: Duplicate name '37vwmu3rudbyc0v', rejecting enrollment.
```

**原因:** Manager 内部数据库中存在同名 Agent 的残留记录（通常来自之前的注册）。

**解决方案:**
```bash
# 方案A: 重建 Manager 容器（彻底清除内部数据库）
docker compose stop wazuh-manager
docker compose rm -f wazuh-manager
docker compose up -d wazuh-manager

# 方案B: 手动清理 client.keys 让 Agent 重新注册
docker exec wazuh-manager bash -c "echo '' > /var/ossec/etc/client.keys"
docker exec wazuh-manager /var/ossec/bin/wazuh-control restart remoted authd

# 方案C: 使用 manage_agents 工具
docker exec wazuh-manager /var/ossec/bin/manage_agents -l  # 列出已注册 Agent
docker exec wazuh-manager bash -c "echo -e 'R\n001\ny\nQ' | /var/ossec/bin/manage_agents"
```

### 1.3 Agent key 不匹配

**现象:** Agent 连接成功但立即断开，日志显示加密错误。

**原因:** Agent 和 Manager 的 `client.keys` 不一致（Manager 容器重建后 key 丢失）。

**解决方案:**
```bash
# 1. 检查两端 key 是否一致
sudo cat /var/ossec/etc/client.keys                          # Agent 的 key
docker exec wazuh-manager cat /var/ossec/etc/client.keys     # Manager 的 key

# 2. 如果不一致，让 Agent 重新注册
sudo rm /var/ossec/etc/client.keys
sudo systemctl restart wazuh-agent
# Agent 会自动向 Manager:1515 请求新 key
```

---

## 2. 告警生成问题

### 2.1 analysisd 未运行

**现象:**
- Agent 连接正常 (`Connected to the server`)
- 但 `alerts.json` 没有任何新告警写入
- `docker exec wazuh-manager /var/ossec/bin/wazuh-control status` 显示 `wazuh-analysisd not running`

**原因:**
`analysisd` 进程因权限不足无法写入日志文件而启动失败。

**完整排查:**
```bash
# 1. 检查 analysisd 状态
docker exec wazuh-manager /var/ossec/bin/wazuh-control status | grep analysisd

# 2. 查看 analysisd 错误
docker exec wazuh-manager grep 'analysisd.*CRITICAL\|analysisd.*ERROR' /var/ossec/logs/ossec.log

# 3. 手动启动 analysisd 查看具体错误
docker exec wazuh-manager /var/ossec/bin/wazuh-analysisd -f
```

**典型错误:**
```
analysisd: CRITICAL: Error opening logfile: 'logs/archives/2026/May/ossec-archive-15.log':
(13) Permission denied
```

**解决方案:**
```bash
# 修复 wazuh_logs 目录权限
sudo chown -R 1000:999 wazuh_logs/
sudo chmod -R g+w wazuh_logs/
sudo chmod -R a+rX wazuh_logs/

# 重启 Manager
docker restart wazuh-manager

# 确认 analysisd 已启动
docker exec wazuh-manager /var/ossec/bin/wazuh-control status | grep analysisd
# 应显示: wazuh-analysisd is running...
```

### 2.2 Wazuh Agent 未采集指定日志

**现象:** 系统操作已发生（如 SSH 登录失败），但 `alerts.json` 中没有对应告警。

**排查链路（从外到内）:**

```bash
# 第1层: 确认系统日志确实生成了
sudo grep 'Failed password\|authentication failure' /var/log/auth.log | tail -5

# 第2层: 确认 Agent 在采集
sudo systemctl status wazuh-agent
sudo grep 'logcollector.*Analyzing' /var/ossec/logs/ossec.log | tail -5

# 第3层: 确认 Agent 已连接 Manager
sudo grep 'Connected to the server' /var/ossec/logs/ossec.log | tail -1

# 第4层: 确认 Manager analysisd 在运行
docker exec wazuh-manager /var/ossec/bin/wazuh-control status | grep analysisd

# 第5层: 确认 Manager remoted 正在接收事件
docker exec wazuh-manager grep 'remoted.*msg\|remoted.*event' /var/ossec/logs/ossec.log | tail -5

# 第6层: 确认告警文件在更新
ls -la wazuh_logs/alerts/alerts.json
tail -5 wazuh_logs/alerts/alerts.json
```

### 2.3 告警延迟

**现象:** 系统操作后 1-2 分钟才在 alerts.json 中看到告警。

**原因:**
- Wazuh Agent 的 `logcollector` 默认每 2 秒扫描一次日志文件
- Manager 的 `analysisd` 可能有处理队列延迟
- 大量事件时可能排队

**期望延迟:**
- Agent 采集: 2-5 秒
- Agent 传输: <1 秒
- Manager 分析: 1-3 秒
- 总计: **5-15 秒**属于正常范围

---

## 3. 文件权限问题

### 3.1 Client 无法读取 alerts.json

**现象:**
```
[Client] 读取告警文件失败: [Errno 13] Permission denied: '/home/admin/SOC/wazuh_logs/alerts/alerts.json'
```

**原因分析:**
```
文件所有者: lxd:999   (容器内 wazuh 用户, UID 999)
文件权限:   rw-rw---- (owner 可读写, group 999 可读写, others 无权限)
当前用户:   admin     (UID 1000, 不属于 group 999)
→ admin 无法读取
```

**立即修复:**
```bash
# 给所有用户添加读取权限
sudo chmod -R a+rX wazuh_logs/

# 验证
test -r wazuh_logs/alerts/alerts.json && echo "可读" || echo "不可读"
```

**持久化修复（新文件自动可读）:**
```bash
# setfacl 设置默认权限
sudo setfacl -d -m o::r wazuh_logs/alerts/
sudo setfacl -d -m o::r wazuh_logs/

# 查看 ACL
getfacl wazuh_logs/alerts/
```

**已整合到启动脚本:**
`scripts/run_server.sh` 在启动基础设施后自动执行权限修复。

### 3.2 Manager 容器内进程无法写入日志

**现象:**
```
analysisd: CRITICAL: Error opening logfile: ... (13) Permission denied
```

**原因:** 目录属主为 `1000:1000`，但 analysisd 以 `wazuh(999)` 运行，且目录没有 group write 权限。

**解决方案:**
```bash
sudo chown -R 1000:999 wazuh_logs/
sudo chmod -R g+w wazuh_logs/
```

### 3.3 容器重建后权限问题复发

**原因:** 新创建的日志文件继承容器的默认权限（`rw-rw----`）。

**永久解决:** 将以下命令添加到 `run_server.sh` 或系统的 crontab:
```bash
sudo chmod -R a+rX /home/admin/SOC/wazuh_logs/
sudo setfacl -d -m o::r /home/admin/SOC/wazuh_logs/alerts/
```

---

## 4. NATS 消息总线问题

### 4.1 NATS 连接失败

**现象:**
```
[Client] NATS 不可达
[Server] Signal Listener 连接失败
```

**排查:**
```bash
# 1. 确认 NATS 容器运行
docker ps | grep nats

# 2. 确认端口映射
docker port nats

# 3. 测试 HTTP 端点
curl http://localhost:8222/healthz

# 4. 如果容器运行但端口不可达，重启 NATS
docker restart nats
```

### 4.2 Consumer 残留导致订阅失败

**现象:** 启动 Client/Server 时 NATS 报错 "consumer name already in use" 或类似错误。

**原因:** 上次进程异常退出时 durable consumer 未清理，残留在 NATS 服务器上。

**代码中已处理的解决方案:**
`client_app.py` 和 `server_app.py` 中的 `_subscribe_safe()` 方法会在订阅失败时自动尝试删除残留 consumer 后重试。

**手动清理:**
```bash
# 查看所有 consumer
docker exec nats nats consumer ls SIGNALS
docker exec nats nats consumer ls QUERY_REQUESTS
docker exec nats nats consumer ls QUERY_RESULTS

# 删除指定 consumer
docker exec nats nats consumer rm SIGNALS signal-listener-server
docker exec nats nats consumer rm QUERY_REQUESTS sidecar-node-web-01
docker exec nats nats consumer rm QUERY_RESULTS query-result-listener
```

### 4.3 消息未正确投递

**现象:** Client 发布了信号，但 Server 未收到。

**排查:**
```bash
# 1. 检查 Stream 是否存在
docker exec nats nats stream ls

# 2. 检查 Stream 中的消息数
docker exec nats nats stream info SIGNALS

# 3. 检查 Consumer 是否有 pending 消息
docker exec nats nats consumer info SIGNALS signal-listener-server

# 4. 如果 pending 消息很多，说明 Server 没在处理
#    检查 Server 是否在运行，是否有错误日志
```

---

## 5. Docker 容器问题

### 5.1 OpenSearch 反复重启或 OOM

**现象:**
- `docker ps` 显示 OpenSearch 状态为 `Restarting`
- `docker logs opensearch` 显示 OutOfMemoryError

**解决方案:**
```bash
# 方案A: 降低 OpenSearch 内存需求
# 编辑 docker-compose.yml，将:
#   - OPENSEARCH_JAVA_OPTS=-Xms4G -Xmx4G
# 改为:
#   - OPENSEARCH_JAVA_OPTS=-Xms2G -Xmx2G

# 方案B: 增加系统 swap
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 方案C: 如果不需要 OpenSearch（开发模式），可以只启动必要容器
docker compose up -d wazuh-manager nats
```

### 5.2 容器时间不同步

**现象:** 告警时间戳与实际时间差几小时（UTC vs 本地时间）。

**说明:** Wazuh Manager 内部使用 UTC 时间。`alerts.json` 中的时间戳格式为 `2026-05-15T12:43:47.000+00:00`（UTC）。这是正常行为，不影响功能。

### 5.3 容器启动顺序问题

**现象:** Logstash 报错连接 OpenSearch 失败。

**原因:** OpenSearch 启动较慢，Logstash 启动时 OpenSearch 尚未就绪。

**解决:**
```bash
# 先单独启动 OpenSearch，等待就绪后再启动其他
docker compose up -d opensearch
sleep 30  # 等待 OpenSearch 完全启动
docker compose up -d dashboards logstash
```

### 5.4 Manager 容器内部存储丢失

**现象:** 重启/重建 Manager 后 Agent 失联。

**原因:** `client.keys`、WazuhDB 等文件在容器重建时会丢失（只有 `/var/ossec/logs/` 通过 bind mount 持久化）。

**重要文件清单:**
| 文件 | 是否持久化 | 说明 |
|------|-----------|------|
| `/var/ossec/logs/*` | ✅ bind mount | 日志、告警 |
| `/var/ossec/etc/client.keys` | ❌ 容器内 | Agent 认证密钥 |
| `/var/ossec/queue/db/*` | ❌ 容器内 | 内部数据库 |
| `/var/ossec/ruleset/*` | ❌ 容器内 | 规则集（镜像自带） |

**建议:** 如果需要持久化 `client.keys`，可以额外 mount：
```yaml
volumes:
  - ./wazuh_logs:/var/ossec/logs
  - ./wazuh_etc:/var/ossec/etc     # 新增: 持久化配置
```

---

## 6. Python/代码问题

### 6.1 ModuleNotFoundError: No module named 'MVP'

**现象:**
```
ModuleNotFoundError: No module named 'MVP'
```

**原因:** uvicorn 在子线程中运行时，Python 的 `sys.path` 不包含项目根目录。

**已修复:** `server_app.py` 使用 `"server.query_gateway:app"` 替代 `"MVP.server.query_gateway:app"`，因为代码中 `sys.path.insert(0, ...)` 已经将 `MVP/` 目录添加到搜索路径。

### 6.2 导入路径混乱

**问题:** `MVP/` 目录缺少 `__init__.py`，导致 `from MVP.xxx import yyy` 在某些 Python 版本中失败。

**项目约定:**
- `MVP/client/__init__.py` 和 `MVP/server/__init__.py` 存在且包含相应的导入
- `MVP/` 本身**没有** `__init__.py`（依赖 Python 3.3+ 的隐式命名空间包）
- 如需兼容旧版 Python，可添加空的 `MVP/__init__.py`

### 6.3 asyncio 事件循环冲突

**现象:** `RuntimeError: This event loop is already running` 或 `There is no current event loop`

**原因:** 在已经运行的事件循环中尝试创建新的事件循环，或者在非异步上下文中调用 `asyncio.run()`。

**本项目的处理:**
```python
# 创建独立事件循环（避免与现有循环冲突）
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
main_task = loop.create_task(run_client())
loop.run_until_complete(main_task)
```

### 6.4 f-string 引号嵌套错误

**现象:** `SyntaxError: f-string: expecting '}'`

**原因:** f-string 内部使用了相同类型的引号。

**错误示例:**
```python
print(f'user@{'localhost'}:{pw}')    # ❌ 单引号嵌套单引号
```

**正确写法:**
```python
print(f"user@localhost:{pw}")        # ✅ 双引号包含单引号（或反过来）
```

---

## 7. Client/Server 运行问题

### 7.1 启动顺序错误

**正确顺序:**
```bash
# 终端1: 先启动 Server (启动基础设施 + 监听服务)
bash scripts/run_server.sh

# 终端2: 再启动 Client (连接 NATS + 开始监控)
bash scripts/run_client.sh
```

**错误顺序的后果:**
- 先启动 Client → NATS 连接失败 → Client 退出
- 因为 Server 负责启动 Docker 基础设施（包括 NATS）

### 7.2 Client 或 Server 意外退出

**现象:** 终端显示 "所有组件已关闭" 后进程退出。

**常见原因:**
1. NATS 连接断开
2. Python 异常未被正确捕获
3. 收到系统信号 (SIGTERM)

**检查方法:**
- 查看终端中的完整错误输出
- 检查 `wazuh_logs/ossec.log` 中的错误

### 7.3 两个终端窗口的输出不一致

**说明:** Client 和 Server 是独立进程，通过 NATS 异步通信。如果 Client 发布了信号但 Server 没有立即响应，可能是：
- 消息还在 NATS 队列中（正常，延迟 <1 秒）
- Server 的 SignalListener 未运行（检查 Server 终端输出）
- 网络问题（检查 NATS 状态）

---

## 8. LLM 研判问题

### 8.1 API 调用失败

**现象:**
```
[LLM] API 调用失败: ...
Agent Team 回退到规则引擎
```

**原因:**
- `ANTHROPIC_AUTH_TOKEN` 环境变量未设置或已过期
- API 端点不可达（网络问题、防火墙）
- HTTP 代理导致 httpx 连接失败

**排查:**
```bash
# 检查环境变量
echo $ANTHROPIC_AUTH_TOKEN
echo $ANTHROPIC_BASE_URL

# 测试 API 连通性
curl -s https://api.anthropic.com/v1/messages -H "x-api-key: $ANTHROPIC_AUTH_TOKEN"
```

**解决方案:**
```bash
# 设置环境变量
export ANTHROPIC_AUTH_TOKEN="sk-ant-api03-xxxxx"

# 如果有代理问题，清除代理
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy
```

**注意:** LLM 不可用不影响流程——系统自动回退到规则引擎，仍可完成研判。

### 8.2 LLM 返回非 JSON 格式

**现象:** `[LLM] JSON 解析失败: ...`

**原因:** LLM 有时会在 JSON 前后添加额外文字，或者使用不规范的格式。

**代码中的处理:** `_call_llm()` 函数尝试从 ```json``` 代码块中提取 JSON。如果仍失败，返回 `None`，触发规则引擎回退。

### 8.3 System Prompt 被忽略

**现象:** LLM 输出的格式不符合 Prompt 要求。

**建议:**
- 在 Prompt 中使用更强的约束语言："只返回 JSON，不要有任何其他文字"
- 限制 `max_tokens` 防止输出过长
- 如果频繁出现格式问题，考虑使用 `response_format` 参数（需要 API 支持）

---

## 9. 测试触发问题

### 9.1 trigger_syslog.sh 没有生成告警

**完整排查链路:**
```bash
# 步骤1: 确认系统日志生成了
sudo grep 'sshd.*Failed password\|pam_unix.*auth.*fail' /var/log/auth.log | tail -5

# 步骤2: 确认 Wazuh Agent 在运行
sudo systemctl status wazuh-agent

# 步骤3: 确认 Agent 已连接
sudo grep 'Connected to the server' /var/ossec/logs/ossec.log | tail -1

# 步骤4: 确认 Manager analysisd 在运行
docker exec wazuh-manager /var/ossec/bin/wazuh-control status | grep analysisd

# 步骤5: 等待 15-30 秒（正常延迟）
sleep 20

# 步骤6: 检查告警
tail -10 wazuh_logs/alerts/alerts.json | python3 -c "
import sys,json
for l in sys.stdin:
    if l.strip():
        a=json.loads(l)
        print(f\"{a['rule']['id']} {a['rule']['description'][:50]} {a['timestamp'][:19]}\")
"

# 步骤7: 如果仍然没有，降级使用直接注入验证内部链路
bash scripts/trigger_test.sh
```

### 9.2 trigger_test.sh 直接注入也未触发

**排查:**
1. Client 的 SignalWatcher 是否在运行（每 2 秒监控文件变化）
2. `alerts.json` 文件是否可写（`test -w wazuh_logs/alerts/alerts.json`）
3. 写入的 JSON 格式是否正确（必须是单行有效 JSON + 换行符）

---

## 10. 性能与资源问题

### 10.1 整体资源需求

| 组件 | 最低内存 | 推荐内存 |
|------|---------|---------|
| OpenSearch | 2 GB | 4 GB |
| Wazuh Manager | 512 MB | 1 GB |
| NATS | 64 MB | 128 MB |
| Python Client | 128 MB | 256 MB |
| Python Server | 256 MB | 512 MB |
| **总计** | **~3 GB** | **~6 GB** |

### 10.2 磁盘空间监控

```bash
# 检查 wazuh_logs 大小
du -sh wazuh_logs/

# 查看各子目录大小
du -sh wazuh_logs/*/

# 清理旧的 outputs
ls -t MVP/outputs/ | tail -n +20 | xargs -I {} rm -rf MVP/outputs/{}
```

### 10.3 告警文件增长

`alerts.json` 会持续增长。建议定期归档：
```bash
# 归档当前告警
mv wazuh_logs/alerts/alerts.json wazuh_logs/alerts/alerts.$(date +%Y%m%d).json
# Wazuh Manager 会自动创建新的 alerts.json
```

---

## 附录: 快速诊断命令集

```bash
# === Wazuh ===
sudo systemctl status wazuh-agent                                   # Agent 状态
sudo grep 'agentd\|logcollector' /var/ossec/logs/ossec.log | tail -20
docker exec wazuh-manager /var/ossec/bin/wazuh-control status      # Manager 进程
docker exec wazuh-manager cat /var/ossec/etc/client.keys           # 注册的 Agent

# === Docker ===
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'     # 容器状态
docker stats --no-stream                                            # 资源使用

# === NATS ===
curl -s http://localhost:8222/healthz                               # 健康检查

# === 文件 ===
ls -la wazuh_logs/alerts/                                           # 告警目录
wc -l wazuh_logs/alerts/alerts.json                                 # 告警行数
test -r wazuh_logs/alerts/alerts.json && echo "可读" || echo "不可读"

# === Client/Server ===
ps aux | grep -E 'client_app|server_app|duckdb_sidecar|signal_listener|query_gateway'
```
