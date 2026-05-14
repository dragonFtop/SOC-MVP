# AI-SOC 智能安全运营中心

AI-SOC (Artificial Intelligence Security Operations Center) 是一个集成化的安全事件分析平台，结合了现代安全工具链和人工智能技术，用于自动化分析安全事件、收集证据并生成响应建议。

## 项目概述

本项目旨在构建一个自动化的安全事件分析系统，能够从多个数据源收集安全事件，对威胁进行分析，并生成相应的响应建议。系统主要由以下几个组件构成：

- **OpenSearch**: 用于存储和检索安全日志数据
- **Wazuh**: 作为SIEM解决方案，提供安全监控和告警功能
- **Logstash**: 数据采集管道，将不同来源的日志传输至OpenSearch
- **AI Agent**: 自定义Python脚本，实现安全事件的智能化分析

## 架构组成

### 核心服务

1. **OpenSearch 3.5.0**
   - 用于存储和索引安全日志数据
   - 提供全文搜索和数据分析功能
   - 端口: `9200`

2. **OpenSearch Dashboards 3.5.0**
   - 提供可视化界面，用于查看和分析安全数据
   - 端口: `5601`

3. **Wazuh Manager 4.14.0**
   - 安全事件和事件管理(SIEM/SOAR)平台
   - 收集和分析来自代理的安全数据
   - 端口: `1514/UDP`, `1515/TCP`, `55000/TCP`

4. **Logstash 8.9.0**
   - 日志收集和预处理组件
   - 将多种日志源(包括Wazuh告警、系统日志等)聚合到OpenSearch
   - 配置文件位于 [logstash.conf](file:///home/admin/SOC/logstash.conf)

### AI分析模块

位于 [MVP](file:///home/admin/SOC/MVP) 目录下，包含以下核心组件：

- **[tool](file:///home/admin/SOC/MVP/tool) 包**: 包含所有AI分析功能的模块化工具集合
  - **[evidence_builder.py](file:///home/admin/SOC/MVP/tool/evidence_builder.py)**: 证据收集器，从Wazuh告警中提取标准化证据
  - **[agent_analyzer.py](file:///home/admin/SOC/MVP/tool/agent_analyzer.py)**: AI分析引擎，根据收集的证据生成安全事件分析报告
  - **[verifier.py](file:///home/admin/SOC/MVP/tool/verifier.py)**: 验证器，验证分析结果与证据的一致性
  - **[readiness.py](file:///home/admin/SOC/MVP/tool/readiness.py)**: 评估数据完整性和可用性的准备度检查器
  - **[report_generator.py](file:///home/admin/SOC/MVP/tool/report_generator.py)**: 生成最终的分析报告
  - **[query_gateway.py](file:///home/admin/SOC/MVP/tool/query_gateway.py)**: 查询网关，提供与数据库交互的接口
  - **[ocsf_mapper.py](file:///home/admin/SOC/MVP/tool/ocsf_mapper.py)**: OCSF标准映射器，将安全事件映射到通用安全遥测框架标准
  - **[opensearch_loader.py](file:///home/admin/SOC/MVP/tool/opensearch_loader.py)**: OpenSearch加载器，负责从OpenSearch中加载数据

- **[main.py](file:///home/admin/SOC/MVP/main.py)**: 主执行脚本，协调执行整个分析流程
- **[metadata.json](file:///home/admin/SOC/MVP/metadata.json)**: 项目元数据配置文件

## 功能特性

- **多源日志收集**: 支持Wazuh告警、系统日志、SSH认证日志等多种数据源
- **自动证据收集**: 自动从安全告警中提取和标准化证据
- **智能事件分析**: 利用AI技术对安全事件进行分析，识别攻击类型和影响范围
- **OCSF标准支持**: 支持通用安全遥测框架(OCSF)标准，提供标准化安全事件格式
- **风险评估**: 对检测到的安全事件进行风险评分
- **响应建议**: 提供针对特定安全事件的处置建议
- **文件管理**: 生成的所有文件均带有时间戳并存储在 [outputs](file:///home/admin/SOC/MVP/outputs) 目录中，以时间戳子目录组织

## 快速开始

### 环境要求

- Docker
- Docker Compose
- 至少8GB可用内存（推荐16GB）

### 启动步骤

1. 克隆项目到本地环境：

   ```bash
   git clone <your-repo-url>
   cd SOC
   ```

2. 启动所有服务：

   ```bash
   docker-compose up -d
   ```

3. 等待所有容器启动完成：

   ```bash
   # 检查服务状态
   docker ps
   ```

4. 访问各服务界面：
   - OpenSearch Dashboard: http://localhost:5601
   - Wazuh Web Interface: http://localhost:55000 (默认用户名admin，默认密码admin)

5. 运行AI分析：

   ```bash
   cd MVP
   python main.py
   ```

## 依赖项安装

在运行AI分析模块之前，请确保安装必要的Python依赖项：

```bash
# 进入MVP目录
cd MVP

# 安装Python依赖项（如果有的话）
pip install -r requirements.txt  # 如果存在requirements.txt文件
```

注意：如果这是首次运行，可能需要安装Python库如requests、pandas、json等，它们是处理数据和API请求所必需的。

## 使用说明

### 数据流程

1. **数据采集**: Logstash从多个源（Wazuh告警、系统日志、SSH日志等）采集数据
2. **数据存储**: 所有日志被发送到OpenSearch进行存储和索引
3. **证据构建**: [evidence_builder.py](file:///home/admin/SOC/MVP/tool/evidence_builder.py) 从Wazuh告警中提取标准化证据，并创建以时间戳命名的输出目录
4. **就绪度评估**: [readiness.py](file:///home/admin/SOC/MVP/tool/readiness.py) 评估证据的完整性和可用性
5. **AI分析**: [agent_analyzer.py](file:///admin/SOC/MVP/tool/agent_analyzer.py) 对收集的证据进行分析并生成结果
6. **结果验证**: [verifier.py](file:///home/admin/SOC/MVP/tool/verifier.py) 检查分析结果与证据的一致性
7. **报告生成**: [report_generator.py](file:///home/admin/SOC/MVP/tool/report_generator.py) 生成最终的分析报告

### 执行顺序

脚本必须按照以下顺序执行，以确保每个步骤都有其所需的输入文件：

1. [evidence_builder.py](file:///home/admin/SOC/MVP/tool/evidence_builder.py) - 构建证据并创建时间戳目录
2. [readiness.py](file:///home/admin/SOC/MVP/tool/readiness.py) - 评估数据就绪度
3. [agent_analyzer.py](file:///home/admin/SOC/MVP/tool/agent_analyzer.py) - 进行AI分析
4. [verifier.py](file:///home/admin/SOC/MVP/tool/verifier.py) - 验证结果
5. [report_generator.py](file:///home/admin/SOC/MVP/tool/report_generator.py) - 生成最终报告

### 文件组织结构

所有输出文件都保存在 [outputs](file:///home/admin/SOC/MVP/outputs) 目录中，按时间戳创建子目录，例如：

```
outputs/
└── 20260514_120024/
    ├── evidence.json
    ├── readiness.json
    ├── agent_result.json
    ├── verifier_result.json
    └── report.md
```

## 项目结构

```
SOC/
├── MVP/                    # AI分析模块
│   ├── main.py            # 主执行脚本
│   ├── tool/              # AI分析工具包
│   │   ├── __init__.py    # 包初始化文件
│   │   ├── evidence_builder.py # 证据收集器
│   │   ├── agent_analyzer.py # AI分析引擎
│   │   ├── query_gateway.py # 查询接口
│   │   ├── verifier.py    # 结果验证器
│   │   ├── readiness.py   # 数据准备度检查
│   │   ├── report_generator.py # 报告生成器
│   │   ├── ocsf_mapper.py # OCSF标准映射器
│   │   └── opensearch_loader.py # OpenSearch数据加载器
│   ├── outputs/           # 存放生成的带时间戳的文件
│   ├── data/              # 示例数据目录
│   │   └── node-web-01/   # 示例节点数据
│   │       ├── nginx_access.json # Nginx访问日志
│   │       └── wazuh_alerts.json # Wazuh告警示例
│   └── metadata.json      # 项目元数据配置文件
├── docker-compose.yml     # Docker容器编排配置
├── logstash.conf         # Logstash数据管道配置
├── wazuh_logs/           # Wazuh日志目录
│   ├── alerts/           # 告警日志
│   │   ├── 2026/         # 年份目录
│   │   └── alerts.json   # 告警示例
│   └── logs/             # 原始日志
├── filebeat/             # Filebeat配置目录
│   └── filebeat.yml      # Filebeat配置文件
├── initial_code/         # 初始代码备份
│   ├── agent_analyzer.py # 初始AI分析代码
│   ├── evidence_builder.py # 初始证据收集代码
│   ├── query_gateway.py  # 初始查询网关代码
│   ├── readiness.py      # 初始就绪度检查代码
│   ├── report_generator.py # 初始报告生成代码
│   ├── verifier.py       # 初始验证器代码
│   └── metadata.json     # 初始元数据配置
└── README.md             # 项目说明文档
```

## 安全告警类型

当前系统可检测多种安全事件，包括但不限于：

- Web攻击（SQL注入、XSS等）
- 异常登录行为
- 恶意扫描活动
- 权限提升尝试
- 系统异常行为

## 维护和扩展

- 可以通过修改 [logstash.conf](file:///home/admin/SOC/logstash.conf) 添加新的日志源
- AI分析模块可以扩展以支持更多类型的攻击检测
- Wazuh规则可以根据实际需求进行定制
- 输出文件的时间戳格式可根据需要进行调整
- 支持OCSF标准，可以扩展更多安全事件标准化映射

## 故障排除

常见问题及解决方法：

1. **容器无法启动**：
   - 检查Docker和Docker Compose是否正确安装
   - 确认有足够的内存资源（至少8GB）
   - 查看Docker日志以获取详细错误信息

2. **AI分析模块无法连接到OpenSearch**：
   - 检查OpenSearch服务是否正在运行
   - 确认网络连接和端口可达性
   - 验证配置文件中的连接参数

3. **执行脚本顺序错误**：
   - 确保按照正确顺序执行各个脚本
   - 检查时间戳目录是否存在所需的输入文件

4. **Python环境问题**：
   - 确保Python版本兼容（Python 3.x）
   - 安装所有必要的依赖库

## 注意事项

- 在生产环境中部署前，请确保更新Wazuh的访问凭据
- 根据实际硬件资源调整OpenSearch的JVM内存设置
- 定期备份重要安全数据
- 生成的所有文件均位于 [outputs](file:///home/admin/SOC/MVP/outputs) 目录中，按时间戳子目录组织
- 执行脚本时必须遵循正确的顺序，以确保每个步骤都有其所需的输入文件
- 确保AI分析模块的Python环境正确配置
- 保持Docker容器资源充足，特别是内存分配

## 许可证

本项目采用 MIT 许可证。详情请参见 [LICENSE](LICENSE) 文件。

## 支持

如果您有任何问题或需要技术支持，请通过以下方式联系我们：

- 项目邮箱：<your-project-email@example.com>
- GitHub Issues: https://github.com/your-repo/issues
- 文档：有关详细信息，请参阅项目文档

## 贡献者

- 安全分析师团队
- AI开发工程师
- DevOps工程师