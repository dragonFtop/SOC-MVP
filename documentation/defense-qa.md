# AI-SOC 技术答辩 Q&A 手册

---

## 一、整体架构

### Q1: 项目的整体架构是怎样的？数据如何流转？

这是一个基于 Data Fabric（数据编织）理念的分布式安全运营平台，核心理念是"物理分布、逻辑统一、按需取证、可信研判"。架构分为边缘侧（Client）和中心侧（Server），两者通过 NATS JetStream 消息总线通信。

完整的 9 阶段数据流：

1. **边缘采集**：Client 进程通过 byte-offset 增量读取的方式持续监控 auth.log 文件
2. **日志解析**：两阶段正则解析——先提取 syslog 头部（时间戳、主机名、进程、PID），再根据进程类型（sshd/sudo/su）用不同的正则提取消息中的关键字段
3. **内存存储**：解析后的结构化事件写入 DuckDB 内存列式数据库的 auth_events 表中
4. **规则检测**：将 YAML 声明的检测规则自动转换为 DuckDB SQL（GROUP BY + HAVING threshold 查询），在本地完成检测
5. **信号发布**：检测触发后生成轻量级信号（约 300 字节），通过 NATS JetStream 发布，只包含 signal_id、node_id、rule_id、src_ip 等元数据
6. **信号消费**：Server 端 SignalListener 订阅信令流，先做 5 分钟过期过滤，再根据信号内容动态构建查询请求下发到 NATS
7. **按需取证**：Client 端 DuckDB Sidecar 收到查询请求后，从 DetectionEngine 的内存表中按条件查询，只返回关键字段（不返回原始日志全文）
8. **证据固化**：Server 端将收到的证据 OCSF 标准化后，落盘 JSON 文件，同时 best-effort 写入 OpenSearch
9. **自动研判**：异步启动 5 步研判流水线——数据就绪度评分 → Agent Team AI 研判 → 复核校验 → 报告生成 → OpenSearch 索引

Client 和 Server 的进程组织方式不同。Server 端在一个 asyncio 事件循环中运行两个 Task（SignalListener 和 QueryResultListener），外加一个独立线程运行 FastAPI Query Gateway，一个独立子进程运行 Streamlit Web Console。Client 端一个事件循环运行两个 Task（DetectionEngine 的 2 秒轮询循环和 DuckDB Sidecar 的消息监听循环），两者共享 DuckDB 连接，协作式调度不产生并发冲突。

---

### Q2: 为什么选择 Data Fabric 架构而不是传统 SIEM？

传统 SIEM 需要将所有日志全量上传到中心，带来三个问题：带宽开销大、存储成本高、原始日志离开边缘节点增加隐私暴露面。

本项目的方案是"平时不传、告警才取"——边缘节点自己完成检测，只在产生告警时向中心发送一条约 300 字节的轻量信令，中心根据信令中的元数据构建查询条件，下发到边缘节点本地查询，边缘只返回结构化的关键字段。原始日志始终留在边缘节点，通过 evidence 中的 raw_ref（节点/日志源/时间戳的引用）可以按需回溯。

数据量对比：一次 SSH 暴力破解可能产生几十 KB 的原始日志，而信令只有约 300 字节，压缩了两个数量级以上。

---

### Q3: 为什么消息总线选 NATS 而不是 Kafka 或 RabbitMQ？

核心原因是部署复杂度。NATS 是单个 15MB 二进制文件，一个 Docker 命令就能启动；Kafka 需要额外管理 KRaft 或 ZooKeeper；RabbitMQ 依赖 Erlang 运行时。本项目的消息量级是每秒数十到数百条，不需要 Kafka 的 TB 级吞吐。NATS JetStream 提供了"刚好够用"的持久化——消息 24 小时自动过期、单 Stream 500MB 上限，对于信令和证据这种短生命周期的数据完全足够。另外 NATS 的延迟在毫秒级以下，比 Kafka 通常的数十毫秒更适合实时检测场景。

---

### Q4: Server 是单进程架构，可靠性怎么保证？

当前确实是单进程，这是 MVP 阶段的取舍。但有三层保障：

第一，NATS JetStream 持久化——Server 重启后 durable consumer 会从未 ACK 的位置继续投递，消息不会丢失。第二，`safe_ack()` 机制确保消息处理成功才 ACK，失败时 NAK 请求重投（最多 3 次），超过则丢弃防止死信阻塞。第三，证据本地落盘作为 OpenSearch 之外的备份。

演进方向上，SignalListener 和 ResultListener 可以通过 NATS Queue Group 机制水平扩展——多个实例订阅同一 subject，NATS 自动负载均衡分发消息。

---

## 二、边缘检测引擎

### Q5: DetectionEngine 的工作循环是怎样的？

DetectionEngine 是一个无限循环的 asyncio 协程，每 2 秒执行一轮。每轮做六件事：

第一，byte-offset 增量读取 auth.log。打开文件后先用 seek 跳到末尾获取当前文件大小，与上次记录的 offset 比较。如果文件变小（说明被 logrotate 截断了），offset 归零从头读。如果文件大小没变，说明无新内容，跳过本轮。如果有新内容，从上次 offset 位置读取增量部分，更新 offset。这种方式不依赖操作系统特有的文件监控 API，跨平台且精确。

第二，解析和入库。将新增的每一行交给 log_parser 做两阶段解析，解析成功则写入 DuckDB 的 auth_events 表。

第三，运行检测规则。遍历所有 YAML 规则，每条规则自动转换为一条 DuckDB SQL，执行 threshold 查询。SQL 中自动包含了两个去重条件——按事件 ID 排除已告警过的记录，以及 cooldown 时间检查。

第四，发布信号。检测到的每条命中构建一个信号 dict，通过 NATS JetStream 发布到 `soc.signals.{node_id}` 主题，同时通过 MonitorEmitter 发布监控事件。

第五，规则热加载检测。比较规则文件的修改时间，有变化则重新加载并清空 cooldown 和事件 ID 去重状态。

第六，每 30 轮执行一次清理——删除超过 60 分钟的旧事件，清理已过期的 cooldown 条目。

---

### Q6: auth.log 的两阶段解析具体怎么做？

第一阶段用 syslog 头部正则提取骨架信息——月份日期时间、主机名、进程名、PID、消息体。其中时间戳需要特殊处理：syslog 格式不包含年份（如 "May 22 10:15:32"），解析器用月份缩写到数字的映射表加上当前年份，补全为 ISO 8601 格式。

第二阶段根据进程名选择不同的消息解析器。以 sshd 为例，按顺序尝试六个正则："Failed password for ... from ... port ..." 提取目标用户、来源 IP 和端口；"Accepted password/publickey for ... from ... port ..." 提取成功登录信息；"Did not receive identification string from ..." 提取扫描探测的 IP；"Connection closed by ..." 提取连接关闭信息；"authentication failure" 识别 PAM 级别的认证失败；"Received disconnect from ... [preauth]" 识别预认证阶段的断开。

sudo 和 su 各自有 2-3 种消息模式的解析。未匹配的消息仍然保留（标记为 `{进程}_other`），不丢弃数据。最终输出一个包含 timestamp、hostname、process、pid、src_ip、src_port、dst_user、log_type、message、raw_line 的结构化字典。

---

### Q7: YAML 检测规则是怎么自动转换成 SQL 的？

每条 YAML 规则定义了 match（匹配条件）和 threshold（阈值条件）两部分。转换器从 match 中取 process 作为 SQL 的 WHERE 过滤，取 patterns 列表用 LIKE 做 OR 拼接。从 threshold 中取 count 和 window_seconds，分别作为 HAVING COUNT 的阈值和 INTERVAL 的时间窗口。如果有 group_by 字段，自动加上 GROUP BY 子句。

关键的一步是自动注入 `id > last_signaled_max_id` 条件——每个规则维护一个已告警过的最大事件 ID，转换成 SQL 时自动拼接，确保已处理的事件不会再次触发。这个 SQL 模板让新增检测规则只需要写 YAML，不需要写任何代码。

---

### Q8: 重复告警防护的两重机制各起什么作用？

第一重是 cooldown（冷却时间），基于时间维度去重。用一个字典存储 `"规则ID:分组键"` 到过期时间戳的映射。每次检测命中时先查字典，如果当前时间还没到冷却截止时间就跳过。分组的含义是：如果按 src_ip 分组，那么来自不同 IP 的同一类攻击各自独立冷却，互不影响。

第二重是事件 ID 去重，基于数据维度。每次信号发布成功后，记录当时 auth_events 表的最大 ID。下次转换 SQL 时，自动加上 `AND id > {上次最大ID}` 的条件，从物理层面排除已告警的事件。

两重机制互补的原因是：cooldown 在 Client 重启后会丢失（存在内存中），此时事件 ID 去重作为底线保证不会立即重复告警。反过来，如果文件被 rotate 后新写入的事件 ID 重新从 1 开始，cooldown 仍然能防止短时间内重复告警。

---

### Q9: 规则热加载怎么实现？为什么不需要专门的文件监控？

直接在每轮循环中调用操作系统的 stat 获取规则文件的修改时间（mtime），和加载时记录的 mtime 比较。这种轮询方式每 2 秒检查一次，规则变更后最多 2 秒生效。不需要引入 inotify 或 watchdog 等额外的文件监控库，减少了依赖和复杂度。

检测到变化后，除了重新加载规则，还会清空 cooldown 字典和事件 ID 去重状态。原因是规则内容变化后，旧的去重状态（基于旧规则生成的 key 和 ID）对新的规则已无意义。

---

### Q10: DuckDB 在检测引擎中扮演什么角色？

DuckDB 在这里是一个嵌入式列式分析数据库，在 DetectionEngine 进程中直接运行，不需要独立的数据库服务。它管理一张 auth_events 内存表，包含 id、timestamp、hostname、process、pid、message、src_ip、src_port、dst_user、log_type、raw_line、ingested_at 十二个字段。表上建了 src_ip 和 timestamp 两个索引加速常用的检测查询。

选择 DuckDB 而非 SQLite 是因为安全检测的查询模式是 OLAP 类型的——按时间窗口做 COUNT 聚合、GROUP BY 分组、HAVING 条件过滤。DuckDB 的列式存储和向量化执行在这种场景下性能远超行式数据库。此外，DuckDB 还支持直接查询 JSON 文件（通过 read_json_auto 函数），在兼容旧 Wazuh 模式时不需要建表和导入。

---

## 三、消息总线

### Q11: NATS 的 3 个 Stream 和 4 个 Subject 怎么设计的？

系统使用了 3 个 JetStream Stream（持久化通道）和 1 个 Core Pub/Sub 通道。

SIGNALS Stream 承载信令流，订阅主题用通配符 `soc.signals.*`，这样每个 Client 发到自己的子主题（如 `soc.signals.node-web-01`），Server 一条通配符订阅就能收到所有节点的信号。

QUERY_REQUESTS Stream 承载查询请求，Server 发布、Client 订阅。QUERY_RESULTS Stream 承载查询结果，Client 发布、Server 订阅。

三条 Stream 统一配置为 24 小时自动过期、单 Stream 最大 500MB。24 小时足够覆盖"Client 离线积压、Server 重启恢复"等异常场景；500MB 上限防止磁盘被消息撑满。

第四个通道 `soc.monitor.events` 使用 Core Pub/Sub（不持久化），用于实时监控事件。选 Core Pub/Sub 而非 JetStream 的原因：监控事件是 best-effort 的，丢失几条不影响研判，不需要 ACK 管理和持久化开销。Monitor Dashboard 通过这个通道实时展示全链路事件。

---

### Q12: "安全订阅"是怎么处理残留 Consumer 的？

durable consumer 在进程异常退出后会残留在 NATS 服务器上，下次启动时如果尝试创建同名 consumer 会报错。subscribe_safe 函数用了两步重试来自动处理这个问题。

第一步先尝试直接订阅——如果 consumer 已存在且健康，直接恢复，保留上次 ACK 的位点。如果失败（可能是 Stream 不存在或 consumer 残留冲突），进入第二步：先尝试创建 Stream（幂等，已存在则跳过），再重试订阅。deliver_policy 设为 all 确保 Server 后启动也能收到 Client 先发布的历史消息。

这样无论是 Client 还是 Server 的启动顺序如何，或者上次是否异常退出，都能自动恢复正常订阅，不需要手动清理。

---

### Q13: safe_ack 的消息重试和防死信机制是怎么设计的？

这是一个防死信阻塞的 ACK 管理机制。消息对象上有 metadata 记录了已投递次数。处理流程是：如果投递次数未超过上限（默认 3 次），执行业务处理逻辑，成功则 ACK，失败则 NAK（请求重新投递）；如果已达上限，说明这条消息格式有问题或处理逻辑有 bug，继续 NAK 只会无限循环阻塞 consumer，因此直接强制 ACK 丢弃。

这个设计是一个务实的工程权衡——宁可丢弃一条异常消息，也不能让整个消息处理管道卡死。正常消息通常在第一次就能成功处理，需要重试的场景极少。

---

## 四、按需取证

### Q14: DuckDB Sidecar 怎么处理查询请求？

Sidecar 通过 NATS 订阅 `soc.query.requests` 主题。收到消息后先解析 JSON，提取 query_id、source 和 filters。然后做一个节点 ID 过滤——多 Client 场景下查询请求可能广播到所有节点，每个 Sidecar 只处理 target 为自己的请求。

根据 source 类型走两条分支。如果是 auth_log，委托给 DetectionEngine 的 query_events 方法，该方法将 filter 字典转换为参数化的 DuckDB SQL（支持 src_ip、process、log_type、since 四种过滤条件，使用参数化查询防注入），从内存表中查询匹配事件。如果是 wazuh_alerts（兼容旧模式），则用 DuckDB 的 read_json_auto 函数直接查询 JSON 文件。

查询结果通过 build_evidence_from_auth_events 方法构建证据——不是返回原始日志，而是提取 timestamp、src_ip、log_type、message 等关键字段，加上 evidence_id、query_id、lineage_id、hash 等溯源元数据。对于 wazuh_alerts 源，还有一个文件竞态重试机制：当 DuckDB 读 JSON 文件遇到 "Malformed JSON" 错误时（Wazuh Manager 正在同时写入），等待 0.3 到 0.6 秒后重试，最多 3 次。

---

### Q15: "数据最小化"原则具体怎么体现？

证据中不包含完整的 syslog 原始行。只返回结构化的关键字段：timestamp、hostname、process、src_ip、dst_user、log_type、message 的简短描述。需要追溯原始日志时，通过证据中的 raw_ref 字段定位——格式是 `{节点ID}/{日志源}#{关键字段}#{时间戳}`。通过 lineage_id（格式为 `{query_id}:{sha256哈希前16位}`）可以验证证据由哪次查询产生、内容是否被篡改。原始数据始终留在边缘节点，中心只有结构化的元数据和可追溯的引用。

---

## 五、信号消费与查询下发

### Q16: SignalListener 收到信号后做什么？

SignalListener 收到信号后做四件事。

第一，解析信号 JSON，提取 signal_id、node_id、rule_id、severity、src_ip、event_time、suggested_logs 等字段。

第二，过期过滤。将 event_time 解析为 UTC 时间，与当前 UTC 时间比较，超过 5 分钟的旧信号直接跳过并 ACK。这个机制防止 Client 离线期间积压的旧信号在恢复连接后洪水般涌入 Server——几个小时前的攻击已经没有取证价值了。

第三，OpenSearch 索引。将信号写入 soc-signals 索引，失败不阻塞后续流程。

第四，构建查询请求。从 suggested_logs 中取第一个数据源（通常为 auth_log），然后根据规则类型动态构建 filter 条件。如果有实质性的 src_ip（不是 0.0.0.0），加入 IP 过滤；根据 rule_id 中包含的关键词推导目标进程——包含 "SSH" 则过滤 sshd，包含 "SUDO" 则过滤 sudo，包含 "SU_" 则过滤 su。生成唯一的 query_id，拼装完整的查询请求 JSON，发布到 NATS 的 soc.query.requests 主题。

---

## 六、证据接收与研判流水线

### Q17: QueryResultListener 收到证据后触发什么？

收到查询结果后，首先做 OCSF 标准化——auth_log 源的数据通过 map_authlog_to_ocsf 函数映射为统一格式，包括将 log_type 转换为数值化的 severity、统一字段命名等。标准化后的证据写出到本地 outputs 目录的 evidence.json 文件，同时 best-effort 写入 OpenSearch 的 soc-evidence 索引。

ACK 消息后，以独立的 asyncio Task 启动研判流水线，不阻塞消息循环。

流水线有 5 个步骤。第一步数据就绪度——从 4 个维度给证据质量打分（满分 100），输出评分、等级和操作权限。第二步 Agent Team 研判——三个 AI Agent 依次协作，分别完成分诊、攻击链分析和报告生成。第三步复核校验——5 层检查防止 AI 幻觉导致的误判。第四步报告生成——聚合前面所有输出生成 Markdown 研判报告。第五步 OpenSearch 索引——将就绪度、研判、复核、报告分别写入对应的索引。

每一步都有独立的异常处理，某一步失败不影响后续步骤。

---

## 七、AI 研判

### Q18: Agent Team 的三 Agent 怎么分工？为什么选不同模型？

三个 Agent 模拟 SOC 分析师团队的标准工作流。

Triage Agent 负责分诊——判断这是什么类型的安全事件、优先级多高、置信度如何。它的 System Prompt 定义了 SOC 分诊专家的角色，要求只返回包含 priority、event_type、summary、confidence 四个字段的 JSON。priority 限定在 critical/high/medium/low 四个枚举值内，event_type 限定在 brute_force/reconnaissance/privilege_escalation 等 7 种类型内——这种枚举约束能强制 LLM 输出结构化数据，避免自由发挥导致下游解析失败。

AttackChain Agent 负责攻击链映射——将离散的证据映射到 Lockheed Martin Cyber Kill Chain 的 7 个阶段（侦查、武器化、交付、利用、安装、C2、目标行动）。它的 System Prompt 描述了每个阶段的定义和典型特征，帮助 LLM 准确归类。

Report Agent 负责生成处置建议——综合分诊结果和攻击链分析，输出具体可执行的措施。System Prompt 中明确要求"建议必须具体、可执行、有优先级，不要说'建议加强安全'这类空话"。

三个 Agent 使用不同的模型。Triage 用 deepseek-chat——分诊是快速判断，不需要深度推理，chat 模型延迟低。AttackChain 用 deepseek-reasoner——攻击链推理需要多步逻辑推导，reasoner 的增强推理能力更适合。Report 用 deepseek-chat——中文报告生成看重语言质量，chat 模型中文能力更强且响应更快。每种模型都可以通过环境变量独立覆盖，不需要改代码。

---

### Q19: LLM 调用怎么处理非 JSON 输出和 API 异常？

LLM 的输出经过两层处理。第一层是 JSON 提取——LLM 有时会在 JSON 前后加说明文字（如"好的，以下是我的分析结果："然后才是 JSON），有时会用 markdown 代码块包裹。代码先检查是否有 ```json 标记，有则提取其中的内容；没有则检查是否有 ``` 标记；都没有则当作纯 JSON 解析。如果 JSON 解析仍然失败（JSONDecodeError），返回空值，上层触发规则引擎回退。

第二层是 Provider 适配。通过一个工厂函数 `_get_llm_client()` 统一管理 DeepSeek 和 Anthropic 两个 Provider。DeepSeek 走 OpenAI 兼容接口，Anthropic 走专用 SDK。Anthropic 初始化时会临时清除系统代理环境变量，因为 SOCKS 代理会导致底层 httpx 库崩溃，操作完后恢复。无 API Key 时返回空值，上层自动回退。

整个调用链路是：尝试 LLM → 如果返回有效 JSON → 使用 LLM 结果；如果任何环节失败（API 不可达、Key 无效、返回格式异常）→ 自动回退规则引擎。规则引擎不需要任何外部调用，100% 本地执行，虽然分析和建议不如 LLM 精准，但至少能识别事件类型和优先级。

---

### Q20: 规则引擎回退的具体逻辑是怎么做的？

Triage Agent 的回退逻辑：统计证据中每种 rule_id 的出现次数，然后查一个预定义的映射表（TRIAGE_RULES），表里记录了每种规则 ID 对应的事件类型和优先级。选出优先级最高的那个规则作为判断依据，根据总告警数量判断置信度——5 条以上为高，3-5 条为中，少于 3 条为低。

AttackChain Agent 的回退更直接：按 severity 数值区间映射 Kill Chain 阶段——severity 小于等于 3 对应侦查阶段，小于等于 6 对应武器化，小于等于 9 对应利用，小于等于 12 对应 C2，小于等于 15 对应目标行动。同时还有一个 rule_id 到阶段的精准映射表（如 LOCAL_SSH_BRUTE_FORCE 直接映射到"利用"阶段）。

Report Agent 的回退使用预设模板：根据事件类型从 ACTION_TEMPLATES 字典中取出对应的处置建议列表。比如 brute_force 类型的模板是"锁定异常源 IP、启用多因素认证、限制登录频率、审计相关用户账号"。

---

## 八、复核校验

### Q21: 5 层防 AI 幻觉校验怎么逐层工作？

第一层 evidence_ref 校验：从 AI 研判结果中提取它引用的所有 evidence_id，然后与 evidence.json 中实际存在的证据 ID 做比对。如果 AI 引用了一个不存在的 evidence_id，说明它可能产生了幻觉，校验不通过。

第二层 raw_ref 校验：检查每条证据是否有 raw_ref 字段，以及该字段的格式是否合法。raw_ref 格式要求包含斜杠或井号（如 "node-web-01/auth_log#192.168.1.100#2026-05-22T10:15:32"），不满足则说明证据缺乏可追溯的数据来源。

第三层 query_id 校验：收集所有证据的 query_id 字段，应该全部一致（同一批证据来自同一次查询）。如果出现多个不同的 query_id，说明证据来源混乱，可能存在数据混入。

第四层 lineage_id 校验：检查每条证据是否有 lineage_id，以及格式是否合法。lineage_id 格式要求包含冒号（如 "qry-xxx:abc123"），即 query_id 加哈希值。缺失或格式错误说明缺乏可验证的血缘关系。

第五层结论校验：检查三项——AI 结论中是否包含绝对化表述（预定义了 10 个黑名单短语，如"已被攻陷""APT 攻击""完全沦陷"等）、证据不足 3 条但结论过于详细（说明 AI 可能在"脑补"）、数据就绪度低于 60 分但给出高置信度结论（数据质量不足以支撑判断）。

最终输出一个 verifier_result.json，包含 overall 是否通过、发现的具体问题、5 个检查项各自的通过状态、根据校验结果修正后的最终置信度和结论。

---

## 九、数据就绪度

### Q22: 就绪度评分算法是怎么设计的？

满分 100 分，4 个维度扣分。

字段覆盖度最高扣 40 分。定义 6 个必需字段（evidence_id、timestamp、source、src_ip、rule_id、description），遍历每条证据的每个必需字段，统计缺失数占总数量的比例，按比例从 40 分中扣减。

字段完整性最高扣 15 分。统计所有必需和推荐字段（共 10 个字段）的非空值比例，低于 50% 则一次性扣 15 分。

时序一致性最高扣 30 分，分 3 个子项各 10 分：可用时间戳少于 2 条扣 10 分；时间戳存在乱序（不是单调递增）扣 10 分；第一条和最后一条的时间跨度不足 30 秒扣 10 分。

唯一性最高扣 10 分。检查 evidence_id 是否有重复，有则扣 10 分。

评分映射到 4 个等级和对应的操作权限：大于等于 80 分为"完整可用"，允许分析、报告和持久化；60 到 80 分为"基本可用"，允许分析和报告但阻止持久化；40 到 60 分为"数据不足"，只允许分析；低于 40 分为"严重不足"，全部阻止。关键设计是：即使数据质量差，仍然允许生成分析结果帮助分析师了解情况，只是阻止低质量数据进入持久化存储。

---

## 十、数据标准化与存储

### Q23: OCSF 映射怎么统一两种数据源？

系统有两套映射函数，对应两个数据源。auth.log 源通过 map_authlog_to_ocsf 处理——用预定义的 log_type 到 severity 数值映射表（如 ssh_failed_password 对应 severity 5、ssh_accepted 对应 severity 1），将 log_type 填为 rule_id，message 填为 description，source 固定为 "auth_log"。Wazuh 告警通过 map_wazuh_to_ocsf 处理——将 Wazuh 的 agent_ip 映射为 src_ip、agent_name 映射为 hostname、level 映射为 severity、full_log 映射为 raw_log。

两种映射输出完全相同的字段结构，上层研判和存储不感知差异。时间戳统一做标准化处理——处理 datetime 对象自动附加本地时区，处理字符串将 +08:00 格式转为 +0800 以兼容 OpenSearch 的 date 类型要求。

---

### Q24: OpenSearch 6 个索引的设计和管理方式？

soc-signals 在 SignalListener 收到信号时写入，使用信号自身的 event_time 作为时间字段。soc-evidence 在 QueryResultListener 收到证据时写入，使用证据的 timestamp。soc-readiness、soc-analysis、soc-verification、soc-reports 四个索引在研判流水线第 5 步批量写入，使用流水线生成的时间戳（从目录名解析为 ISO 格式并附加本地时区偏移）。

索引的创建采用幂等设计——ensure_index 方法先检查索引是否已存在，存在则跳过。soc-evidence 使用项目根目录的 mapping.json 做显式字段类型映射（timestamp 为 date 类型支持多种格式、severity 为 integer、rule_id 为 keyword 精确匹配、src_ip 为 ip 类型），其他 5 个索引使用 OpenSearch 的动态映射自动推断字段类型。所有写入都 best-effort，失败不阻塞主流程。

---

## 十一、Web Console

### Q25: 首页的基础设施状态检查是怎么做的？

首页用三种方式检测基础设施状态。NATS 用 socket 直连 4222 端口测连通性。OpenSearch 用 HTTP 请求访问 `/_cluster/health` 接口，解析返回的 status 字段（green/yellow/red）。Docker 容器用 subprocess 执行 `docker ps` 命令，检查 opensearch、nats、dashboards 三个容器名是否在运行列表中。

Client 运行状态通过 `ps aux` 命令查找 `client_app.py --client-id` 的进程，不是依赖 PID 文件，更可靠。还提供最近 10 条研判任务的摘要列表——扫描 outputs 目录下的时间戳子目录，读取每个目录中的 agent_result.json 提取节点和事件类型。

9 个页面的设计逻辑是从高频操作到低频管理排列。前面是监控和查看类页面（自动刷新），后面是注册和配置类页面（手动操作）。刷新频率与信息变化速度匹配——Server 监控事件变化最快用 2 秒，首页和 Client 面板用 5 秒，数据查看变化最慢用 8 秒。

---

### Q26: 检测规则编辑页面怎么实现在线编辑和热加载？

规则编辑页面从文件系统读取 detection_rules.yaml 的完整内容，渲染在一个 Streamlit 的 text_area 组件中，支持直接在线编辑 YAML。点击保存按钮后，将编辑后的内容写回文件。由于 DetectionEngine 每 2 秒轮询检查文件的 mtime，保存后最多 2 秒就能自动加载新规则，不需要重启 Client 进程。这也是选择文件 mtime 检测方案而非内存配置方案的额外好处——在线编辑和热加载天然打通。

---

## 十二、安全测试

### Q27: 测试场景的日志注入是怎么工作的？

test_scenarios.yaml 预定义了 6 种攻击场景，每个场景有三要素：日志格式模板、变量定义、默认注入条数。日志格式模板是一个包含占位符的 syslog 行字符串，变量定义中固定值直接填充（如攻击 IP 固定为 10.0.99.1），列表值每次随机选取一个（如用户名从 8 个候选值中随机选）。

trigger_authlog.sh 脚本读取场景配置，逐条填充模板生成 syslog 行，追加写入目标节点的 auth.log 文件。混合攻击场景（multi_vector）使用 multi_line 标记，交替生成 SSH 暴力破解行和 Sudo 提权失败行，模拟完整的攻击链。

DetectionEngine 在下一次 2 秒轮询中检测到新增行，解析后插入 DuckDB，规则 SQL 检测到 threshold 条件满足（比如同一 IP 的 Failed password 在 300 秒内出现 6 次达到 COUNT >= 5 的阈值），触发信号。整个过程从注入到信号产生通常在 5 秒内完成。

---

## 十三、全链路监控

### Q28: 监控事件系统怎么做到不干扰主流程？

所有组件共用一个 MonitorEmitter 实例，它内部封装了向 NATS Core Pub/Sub 发布事件的逻辑。8 种事件类型覆盖了从信号发送到证据保存的完整链路。事件发布采用 try/except 静默吞异常的方式——如果 NATS 发布失败（比如网络抖动），异常被捕获但不抛出，不影响 DetectionEngine 的检测循环或 SignalListener 的消息处理。

选择 Core Pub/Sub 而非 JetStream 的几个原因：监控事件是 best-effort 的，丢失几条不影响实际的安全研判；Core Pub/Sub 即发即弃不需要 ACK，延迟更低；不需要管理额外的 JetStream Consumer。Monitor Dashboard 通过 `soc.monitor.events` 主题订阅这些事件，后台线程持续收集到固定大小的双端队列中，每 2 秒自动刷新展示。

---

## 十四、证据溯源

### Q29: 证据的"血缘追踪"怎么设计？

系统中有三级 ID 形成完整的追溯链。第一级是 signal_id，由 DetectionEngine 在检测命中时生成（格式为 `sig-` 加 8 位随机十六进制）。第二级是 query_id，由 SignalListener 在收到信号后生成（格式为 `qry-` 加 8 位随机十六进制）。第三级是 evidence_id，由 DuckDB Sidecar 在构建证据时生成（格式为 `ev-` 加 8 位随机十六进制）。

每条证据还包含两个核心溯源字段。lineage_id 格式为 `{query_id}:{sha256哈希前16位}`，将证据内容做 SHA256 哈希后截取前 16 位，与产生该证据的查询 ID 拼接。这样既能验证证据由哪次查询产生，又能验证内容是否被篡改——如果内容变了，哈希就对不上。raw_ref 格式为 `{节点ID}/{数据源}#{关键字段}#{时间戳}`，提供了到边缘节点原始日志的物理路径引用。

通过 signal_id → query_id → evidence_id → raw_ref 这条链路，可以从最终研判报告一路追溯到边缘节点的原始日志行。

---

## 十五、关键设计决策总结

### Q30: 当前架构有哪些已知局限及其应对？

DuckDB 内存模式导致 Client 重启丢失所有历史事件。解决方案是将 DUCKDB_PATH 配置项从 None 改为磁盘路径，DuckDB 自动切换为持久化模式，不需要改任何代码。

Server 单点故障——但 NATS JetStream 持久化使 Server 重启后可恢复未处理的消息。SignalListener 和 ResultListener 可以通过 NATS Queue Group 水平扩展。

检测规则只有 5 条覆盖范围窄——但框架本身设计就是通过 YAML 扩展的，新增规则只需编辑 YAML 文件即可，添加 50 条规则也不需要写额外代码。

LLM 单次调用失败直接回退规则引擎——可以加 exponential backoff 重试提高 LLM 成功率。但回退机制本身保证了系统在 LLM 完全不可用的情况下仍能正常运行。

Web Console 认证只有单密码——对于 MVP 阶段的开发和演示场景足够，生产环境需要对接企业 SSO。

src_ip 字段在无法提取 IP 的场景下显示为 "0.0.0.0"——这是有意设计，因为 OpenSearch 的 ip 类型不接受 null，0.0.0.0 在安全行业语义中表示"未知"。
