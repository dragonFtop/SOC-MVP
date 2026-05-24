#!/bin/bash
# AI-SOC Client 注册管理脚本
# ===========================
# 用法:
#   bash scripts/register_client.sh add <client-id> --node-id <node-id> [--log-path <path>] [--real] [--desc "描述"]
#   bash scripts/register_client.sh remove <client-id>
#   bash scripts/register_client.sh list
#
# 示例:
#   # 注册模拟节点 (自动创建 simulated_nodes/<node-id>/auth.log)
#   bash scripts/register_client.sh add gateway-guard --node-id node-web-01
#
#   # 注册真机节点 (监控系统 /var/log/auth.log)
#   bash scripts/register_client.sh add real-host --node-id node-prod-01 --real
#
#   # 注册自定义日志路径
#   bash scripts/register_client.sh add custom --node-id node-x --log-path /var/log/custom.log
#
#   # 列出所有客户端
#   bash scripts/register_client.sh list
#
#   # 移除客户端
#   bash scripts/register_client.sh remove gateway-guard

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$PROJECT_DIR/MVP/client_config.yaml"

# ---- 工具函数 ----

ensure_config_file() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "clients: []" > "$CONFIG_FILE"
        echo "   📄 创建配置文件: $CONFIG_FILE"
    fi
}

client_exists() {
    python3 -c "
import yaml
with open('$CONFIG_FILE', 'r') as f:
    config = yaml.safe_load(f)
for c in config.get('clients', []):
    if c.get('client_id') == '$1':
        print('EXISTS')
        break
" 2>/dev/null
}

# ---- 命令实现 ----

cmd_add() {
    local client_id="$1"
    shift

    local node_id=""
    local log_path=""
    local description=""
    local is_real=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --node-id)
                node_id="$2"
                shift 2
                ;;
            --log-path)
                log_path="$2"
                shift 2
                ;;
            --desc)
                description="$2"
                shift 2
                ;;
            --real)
                is_real=true
                shift
                ;;
            *)
                echo "❌ 未知参数: $1"
                exit 1
                ;;
        esac
    done

    if [ -z "$client_id" ] || [ -z "$node_id" ]; then
        echo "用法: $0 add <client-id> --node-id <node-id> [--log-path <path>] [--real] [--desc 描述]"
        exit 1
    fi

    ensure_config_file

    # 检查 node 是否已在 metadata.json 中注册 (需有 auth_log 数据源)
    METADATA_FILE="$PROJECT_DIR/MVP/metadata.json"
    if [ ! -f "$METADATA_FILE" ]; then
        echo "❌ metadata.json 不存在，请先注册节点"
        echo "   方式1: bash scripts/register_client.sh add <client-id> --node-id <node-id> --real"
        echo "   方式2: 在 Web Console → 节点注册 页面添加"
        exit 1
    fi
    NODE_EXISTS=$(python3 -c "
import json
with open('$METADATA_FILE') as f:
    data = json.load(f)
found = any(e.get('node_id') == '$node_id' and e.get('source_name') == 'auth_log' for e in data)
print('yes' if found else 'no')
" 2>/dev/null)
    if [ "$NODE_EXISTS" != "yes" ]; then
        echo "❌ 节点 '$node_id' 未在 metadata.json 中注册 (需要 auth_log 数据源)"
        echo ""
        echo "   请先在 metadata.json 中注册节点，或在 Web Console → 节点注册 页面添加"
        echo "   或者使用 --real 自动注册真机节点"
        if [ "$is_real" = true ]; then
            echo ""
            echo "   🔧 自动注册真机节点 '$node_id' ..."
            python3 << PYEOF
import json
node_entry = {
    "node_id": "$node_id",
    "source_name": "auth_log",
    "source_type": "syslog",
    "local_path": "/var/log/auth.log",
    "format": "syslog",
    "query_engine": "duckdb-sidecar-authlog",
    "retention_days": 7
}
with open('$METADATA_FILE', 'r') as f:
    data = json.load(f)
data.append(node_entry)
with open('$METADATA_FILE', 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print(f"   ✅ 已将节点 '$node_id' 注册到 metadata.json")
PYEOF
        else
            echo "   或使用: python3 -c \"
import json
with open('MVP/metadata.json') as f:
    data = json.load(f)
data.append({'node_id': '$node_id', 'source_name': 'auth_log', 'source_type': 'syslog',
    'local_path': 'simulated_nodes/$node_id/auth.log', 'format': 'syslog',
    'query_engine': 'duckdb-sidecar-authlog', 'retention_days': 7})
with open('MVP/metadata.json', 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print('节点已注册')
\""
            exit 1
        fi
    fi

    # 检查是否已存在
    if [ "$(client_exists "$client_id")" = "EXISTS" ]; then
        echo "❌ client_id='$client_id' 已注册，请先 remove"
        exit 1
    fi

    # 确定日志路径
    if [ -n "$log_path" ]; then
        :
    elif [ "$is_real" = true ]; then
        log_path="/var/log/auth.log"
    else
        log_path="simulated_nodes/$node_id/auth.log"
    fi

    # 自动创建模拟节点目录
    if [ "$is_real" != true ] && [ -z "$log_path" ] || [[ "$log_path" == simulated_nodes/* ]]; then
        local sim_dir="$PROJECT_DIR/simulated_nodes/$node_id"
        mkdir -p "$sim_dir"

        # 如果 auth.log 不存在，创建样本日志
        local auth_log="$sim_dir/auth.log"
        if [ ! -f "$auth_log" ]; then
            local hostname_str
            hostname_str=$(hostname)
            local ts
            ts=$(date '+%b %d %H:%M:%S')
            cat > "$auth_log" << LOGEOF
$ts $hostname_str sshd[1001]: Accepted publickey for admin from 192.168.1.10 port 40000 ssh2: RSA SHA256:abc123
$ts $hostname_str sshd[1002]: Failed password for root from 10.0.0.5 port 50001 ssh2
$ts $hostname_str sshd[1003]: Failed password for root from 10.0.0.5 port 50002 ssh2
$ts $hostname_str sshd[1004]: Failed password for root from 10.0.0.5 port 50003 ssh2
$ts $hostname_str sshd[1005]: Failed password for root from 10.0.0.5 port 50004 ssh2
$ts $hostname_str sshd[1006]: Failed password for admin from 10.0.0.8 port 51001 ssh2
LOGEOF
            echo "   ✅ 创建样本日志: $auth_log ($(wc -l < "$auth_log") 行)"
        fi
    fi

    if [ "$is_real" = true ] && [ ! -f "$log_path" ]; then
        echo "   ⚠️  真机日志路径不存在: $log_path (将在客户端启动时等待)"
    fi

    # 写入 YAML
    python3 << PYEOF
import yaml
from pathlib import Path

config_file = Path('$CONFIG_FILE')
with open(config_file, 'r') as f:
    config = yaml.safe_load(f)

if config is None:
    config = {"clients": []}

new_client = {
    "client_id": "$client_id",
    "node_id": "$node_id",
    "log_path": "$log_path",
}

desc = "$description"
if desc:
    new_client["description"] = desc

config["clients"].append(new_client)

with open(config_file, 'w') as f:
    yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

print(f"   ✅ Client 已注册: $client_id → $node_id")
if desc:
    print(f"   📝 描述: $description")
print(f"   📂 日志: $log_path")
PYEOF

    echo ""
    echo "   启动命令: bash scripts/run_client.sh $client_id"
    echo "   触发测试: bash scripts/trigger_authlog.sh --node-id $node_id ssh 5"
}

cmd_remove() {
    local client_id="$1"

    if [ -z "$client_id" ]; then
        echo "用法: $0 remove <client-id>"
        exit 1
    fi

    if [ ! -f "$CONFIG_FILE" ]; then
        echo "❌ 配置文件不存在"
        exit 1
    fi

    if [ "$(client_exists "$client_id")" != "EXISTS" ]; then
        echo "❌ client_id='$client_id' 未注册"
        exit 1
    fi

    python3 << PYEOF
import yaml
with open('$CONFIG_FILE', 'r') as f:
    config = yaml.safe_load(f)
config["clients"] = [c for c in config["clients"] if c.get("client_id") != "$client_id"]
with open('$CONFIG_FILE', 'w') as f:
    yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
print(f"   ✅ Client '$client_id' 已移除")
PYEOF
}

cmd_list() {
    ensure_config_file

    echo ""
    echo "=========================================="
    echo "  AI-SOC 已注册客户端"
    echo "=========================================="
    echo ""

    python3 << PYEOF
import yaml
with open('$CONFIG_FILE', 'r') as f:
    config = yaml.safe_load(f)
clients = config.get("clients", [])
if not clients:
    print("  （无已注册客户端）")
else:
    for c in clients:
        cid = c.get('client_id', '?')
        nid = c.get('node_id', '?')
        lp = c.get('log_path', '?')
        desc = c.get('description', '')
        print(f"  {cid:<24} | {nid:<20} | {lp}")
        if desc:
            print(f"  {'':24} | {'':20} | {desc}")
        print()
PYEOF
    echo "=========================================="
}

# ---- 主入口 ----

case "${1:-}" in
    add)
        shift
        cmd_add "$@"
        ;;
    remove|rm|delete)
        shift
        cmd_remove "$@"
        ;;
    list|ls)
        cmd_list
        ;;
    *)
        echo "AI-SOC Client 注册管理"
        echo ""
        echo "用法:"
        echo "  bash scripts/register_client.sh add <client-id> --node-id <node-id> [选项]"
        echo "  bash scripts/register_client.sh remove <client-id>"
        echo "  bash scripts/register_client.sh list"
        echo ""
        echo "选项:"
        echo "  --node-id ID    节点标识 (必填)"
        echo "  --log-path PATH 日志路径 (默认: simulated_nodes/<node-id>/auth.log)"
        echo "  --real          注册真机节点，监控 /var/log/auth.log"
        echo "  --desc TEXT     客户端描述信息"
        echo ""
        echo "示例:"
        echo "  bash scripts/register_client.sh add gateway-guard --node-id node-web-01"
        echo "  bash scripts/register_client.sh add real-host --node-id node-prod-01 --real"
        echo "  bash scripts/register_client.sh add custom --node-id node-x --log-path /opt/logs/auth.log"
        exit 1
        ;;
esac
