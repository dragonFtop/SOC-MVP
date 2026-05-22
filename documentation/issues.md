# AI-SOC 常见问题与解决方案

本文档汇总了在开发、部署和运行 AI-SOC 过程中可能遇到的各类问题，以及经过验证的解决方案。

---

## 目录

1. [DetectionEngine 检测问题](#1-detectionengine-检测问题)
2. [文件权限问题](#2-文件权限问题)
3. [NATS 消息总线问题](#3-nats-消息总线问题)
4. [Docker 容器问题](#4-docker-容器问题)
5. [Python/代码问题](#5-python代码问题)
6. [Client/Server 运行问题](#6-clientserver-运行问题)
7. [LLM 研判问题](#7-llm-研判问题)
8. [测试触发问题](#8-测试触发问题)
9. [性能与资源问题](#9-性能与资源问题)
10. [附录: 快速诊断命令集](#附录-快速诊断命令集)

---

## 1. DetectionEngine 检测问题

### 1.1 auth.log 读取失败

**现象:**
```
[DetectionEngine] 无法打开 /var/log/auth.log: Permission denied
```

**原因:** auth.log 默认权限为 `rw-r-----`（只有 root 和 adm 组可读），当前用户不在 adm 组。

**解决方案:**
```bash
# 方案A: 添加读取权限
sudo chmod o+r /var/log/auth.log

# 方案B: 将用户加入 adm 组（永久解决）
sudo usermod -a -G adm $USER
# 需要重新登录才能生效
```

### 1.2 auth.log 无新事件

**现象:** DetectionEngine 运行正常，但没有检测到任何事件。

**排查:**
```bash
# 1. 确认 auth.log 有新的安全事件写入
sudo tail -5 /var/log/auth.log

# 2. 手动触发测试事件
bash scripts/trigger_authlog.sh ssh

# 3. 确认 DetectionEngine 正在运行（查看 Client 终端输出）
# 应看到: "tail 开始监控 /var/log/auth.log"

# 4. 检查解析统计
# Client 终端应输出: "解析: X 条, 插入: X 条"
```

### 1.3 规则未触发

**现象:** auth.log 有事件，但信号未生成。

**原因:** threshold 条件未满足。

**排查:**
```bash
# 检查规则配置
cat MVP/client/detection_rules.yaml

# 常见原因:
# - LOCAL_SSH_BRUTE_FORCE: 需要同一 src_ip 在 5 分钟内失败 5 次
# - LOCAL_SSH_SCAN: 需要同一 src_ip 在 2 分钟内触发 3 次
# - LOCAL_SUDO_FAILURES: 需要同一 dst_user 在 2 分钟内失败 3 次
```

**解决方案:** 降低 threshold 测试，或多次执行触发脚本：
```bash
# 快速触发多次失败
for i in 1 2 3 4 5; do
  bash scripts/trigger_authlog.sh ssh
  sleep 2
done
```

### 1.4 DuckDB 内存表事件过期

**现象:** 事件插入后很快消失，规则检测不到。

**原因:** `EVENT_RETENTION_MINUTES = 60`（默认 60 分钟清理），事件超过保留时间被自动删除。

**解决方案:** 在 `MVP/config.py` 中调整：
```python
EVENT_RETENTION_MINUTES = 120  # 延长至 2 小时
```

### 1.5 src_ip 显示为 "0.0.0.0"

**现象:** 信号中 `src_ip` 字段值为 `0.0.0.0`。

**原因:** 这是设计行为：
- `group_by: src_ip` 的规则 → src_ip 为实际 IP
- `group_by: dst_user` 的规则（如 LOCAL_SUDO_FAILURES）→ src_ip = `"0.0.0.0"`（因为 OpenSearch ip 类型要求有效 IP，不能为 null）
- `ssh_auth_failure` 等无法从消息中提取 IP 的日志类型 → src_ip = `"0.0.0.0"`

这是正常的，不需要修复。

---

## 2. 文件权限问题

### 2.1 Client 无法读取 auth.log

**现象:**
```
[Errno 13] Permission denied: '/var/log/auth.log'
```

**立即修复:**
```bash
sudo chmod o+r /var/log/auth.log
```

**持久化修复:**
```bash
sudo usermod -a -G adm $USER
# 重新登录后生效
```

### 2.2 outputs 目录写入失败

**现象:** 证据文件保存失败。

**解决方案:**
```bash
mkdir -p MVP/outputs
chmod 755 MVP/outputs
```

---

## 3. NATS 消息总线问题

### 3.1 NATS 连接失败

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

### 3.2 Consumer 残留导致订阅失败

**现象:** 启动 Client/Server 时 NATS 报错 "consumer name already in use"。

**原因:** 上次进程异常退出时 durable consumer 未清理。

**代码中已处理:** `common/nats_utils.py` 中的 `subscribe_safe()` 函数会自动清理残留 consumer 后重试。

**手动清理:**
```bash
# 查看所有 consumer
docker exec nats nats consumer ls SIGNALS
docker exec nats nats consumer ls QUERY_REQUESTS
docker exec nats nats consumer ls QUERY_RESULTS

# 删除指定 consumer
docker exec nats nats consumer rm SIGNALS signal-listener
docker exec nats nats consumer rm QUERY_REQUESTS duckdb-sidecar-node-web-01
docker exec nats nats consumer rm QUERY_RESULTS query-result-listener
```

### 3.3 消息未正确投递

**现象:** Client 发布了信号，但 Server 未收到。

**排查:**
```bash
# 1. 检查 Stream 是否存在
docker exec nats nats stream ls

# 2. 检查 Stream 中的消息数
docker exec nats nats stream info SIGNALS

# 3. 检查 Consumer 是否有 pending 消息
docker exec nats nats consumer info SIGNALS signal-listener
```

### 3.4 监控事件未显示

**现象:** Monitor Dashboard (:8502) 无事件展示。

**原因:** Monitor 事件通过 NATS Core Pub/Sub（非 JetStream）发布，无持久化。

**排查:**
```bash
# 确认 Monitor Dashboard 正在运行
curl -s http://localhost:8502 | head -5

# 使用自检按钮验证管道（在 Dashboard 页面点击"自检"）
```

---

## 4. Docker 容器问题

### 4.1 OpenSearch 反复重启或 OOM

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

# 方案B: 如果不需要 OpenSearch（开发模式），只启动 NATS
docker compose up -d nats
```

### 4.2 容器时间不同步

**现象:** 时间戳与实际时间差几小时（UTC vs 本地时间）。

**说明:** Docker 容器内部默认使用 UTC 时间。这是正常行为，不影响功能。

### 4.3 容器启动顺序问题

**现象:** OpenSearch Dashboards 报错连接 OpenSearch 失败。

**原因:** OpenSearch 启动较慢，Dashboards 启动时尚未就绪。

**解决:**
```bash
docker compose up -d opensearch
sleep 30  # 等待 OpenSearch 完全启动
docker compose up -d dashboards
```

---

## 5. Python/代码问题

### 5.1 ModuleNotFoundError: No module named 'MVP'

**现象:**
```
ModuleNotFoundError: No module named 'MVP'
```

**原因:** uvicorn 在子线程中运行时，Python 的 `sys.path` 不包含项目根目录。

**已修复:** `server_app.py` 使用 `sys.path.insert(0, ...)` 将 MVP/ 目录添加到搜索路径。

### 5.2 导入路径混乱

**问题:** `MVP/` 目录缺少 `__init__.py`，导致 `from MVP.xxx import yyy` 在某些 Python 版本中失败。

**项目约定:**
- `MVP/client/__init__.py` 和 `MVP/server/__init__.py` 存在
- `MVP/` 本身**没有** `__init__.py`（依赖 Python 3.3+ 的隐式命名空间包）

### 5.3 asyncio 事件循环冲突

**现象:** `RuntimeError: This event loop is already running`

**本项目的处理:**
```python
# 创建独立事件循环（避免与现有循环冲突）
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
main_task = loop.create_task(run_client())
loop.run_until_complete(main_task)
```

### 5.4 f-string 引号嵌套错误

**现象:** `SyntaxError: f-string: expecting '}'`

**错误示例:**
```python
print(f'user@{'localhost'}:{pw}')    # ❌ 单引号嵌套单引号
```

**正确写法:**
```python
print(f"user@localhost:{pw}")        # ✅ 双引号包含单引号
```

---

## 6. Client/Server 运行问题

### 6.1 启动顺序错误

**正确顺序:**
```bash
# 终端1: 先启动 Server (启动基础设施 + 监听服务)
bash scripts/run_server.sh

# 终端2: 再启动 Client (连接 NATS + 开始检测)
bash scripts/run_client.sh
```

**错误顺序的后果:**
- 先启动 Client → NATS 连接失败 → Client 退出
- 因为 Server 负责启动 Docker 基础设施（包括 NATS）

### 6.2 Client 或 Server 意外退出

**现象:** 终端显示 "所有组件已关闭" 后进程退出。

**常见原因:**
1. NATS 连接断开
2. Python 异常未被正确捕获
3. 收到系统信号 (SIGTERM)

**检查方法:**
- 查看终端中的完整错误输出

### 6.3 两个终端窗口的输出不一致

**说明:** Client 和 Server 是独立进程，通过 NATS 异步通信。如果 Client 发布了信号但 Server 没有立即响应，可能是：
- 消息还在 NATS 队列中（正常，延迟 <1 秒）
- Server 的 SignalListener 未运行（检查 Server 终端输出）

---

## 7. LLM 研判问题

### 7.1 API 调用失败

**现象:**
```
[LLM] API 调用失败: ...
Agent Team 回退到规则引擎
```

**原因:**
- `ANTHROPIC_AUTH_TOKEN` 环境变量未设置或已过期
- API 端点不可达（网络问题）
- HTTP 代理导致 httpx 连接失败

**排查:**
```bash
# 检查环境变量
echo $ANTHROPIC_AUTH_TOKEN
echo $ANTHROPIC_BASE_URL
```

**解决方案:**
```bash
export ANTHROPIC_AUTH_TOKEN="sk-ant-api03-xxxxx"

# 如果有代理问题，清除代理
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy
```

**注意:** LLM 不可用不影响流程——系统自动回退到规则引擎，仍可完成研判。

### 7.2 LLM 返回非 JSON 格式

**现象:** `[LLM] JSON 解析失败: ...`

**原因:** LLM 有时会在 JSON 前后添加额外文字。

**代码中的处理:** `_call_llm()` 函数尝试从 ```json``` 代码块中提取 JSON。如果仍失败，返回 `None`，触发规则引擎回退。

### 7.3 System Prompt 被忽略

**建议:**
- 在 Prompt 中使用更强的约束语言："只返回 JSON，不要有任何其他文字"
- 限制 `max_tokens` 防止输出过长

---

## 8. 测试触发问题

### 8.1 trigger_authlog.sh 没有生成告警

**完整排查链路:**
```bash
# 步骤1: 确认 auth.log 有新事件写入
sudo tail -5 /var/log/auth.log

# 步骤2: 确认 DetectionEngine 在运行（Client 终端）
# 应看到 tail 监控日志

# 步骤3: 等待 2-5 秒（DetectionEngine 轮询间隔）

# 步骤4: 查看 Client 终端输出
# 应看到解析事件和可能的信号输出

# 步骤5: 查看 Monitor Dashboard (:8502) 是否有 signal.sent 事件
```

### 8.2 trigger_test.sh 直接注入也未触发

**注意:** trigger_test.sh 是兼容旧 Wazuh 模式的测试脚本。当前默认模式是 DetectionEngine + auth.log。

**排查:**
1. 确认 signal_watcher.py 兼容模块是否已激活（通常不需要）
2. 新架构下请使用 `trigger_authlog.sh` 进行测试

---

## 9. 性能与资源问题

### 9.1 整体资源需求

| 组件 | 最低内存 | 推荐内存 |
|------|---------|---------|
| OpenSearch | 2 GB | 4 GB |
| NATS | 64 MB | 128 MB |
| Python Client | 128 MB | 256 MB |
| Python Server | 256 MB | 512 MB |
| **总计** | **~2.5 GB** | **~5 GB** |

### 9.2 磁盘空间监控

```bash
# 检查 outputs 大小
du -sh MVP/outputs/

# 清理旧的 outputs（保留最近 20 个）
ls -t MVP/outputs/ | tail -n +20 | xargs -I {} rm -rf MVP/outputs/{}
```

### 9.3 DuckDB 内存使用

DetectionEngine 的 DuckDB 内存表会随事件累积而增长。默认 `EVENT_RETENTION_MINUTES=60` 自动限制。如果内存压力大：

```python
# 在 config.py 中缩短保留时间
EVENT_RETENTION_MINUTES = 30
```

---

## 附录: 快速诊断命令集

```bash
# === Docker ===
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'     # 容器状态
docker stats --no-stream                                            # 资源使用

# === NATS ===
curl -s http://localhost:8222/healthz                               # 健康检查
docker exec nats nats stream ls                                     # Stream 列表

# === auth.log ===
tail -5 /var/log/auth.log                                           # 最近事件
test -r /var/log/auth.log && echo "可读" || echo "不可读"           # 权限检查

# === Client/Server ===
ps aux | grep -E 'client_app|server_app'

# === OpenSearch ===
curl -s http://localhost:9200/_cat/indices                          # 索引列表
```
