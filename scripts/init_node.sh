#!/bin/bash
# AI-SOC Simulated Node 初始化脚本
# 用法: bash scripts/init_node.sh <node-id>
# 示例: bash scripts/init_node.sh node-web-01
set -e

NODE_ID="${1:?Usage: $0 <node-id>}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NODE_DIR="$SCRIPT_DIR/../simulated_nodes/$NODE_ID"
AUTH_LOG="$NODE_DIR/auth.log"

mkdir -p "$NODE_DIR"

TIMESTAMP=$(date '+%b %d %H:%M:%S')
HOSTNAME=$(hostname)

cat > "$AUTH_LOG" << LOGEOF
$TIMESTAMP $HOSTNAME sshd[1001]: Accepted publickey for admin from 192.168.1.10 port 40000 ssh2: RSA SHA256:abc123
$TIMESTAMP $HOSTNAME sshd[1002]: Failed password for root from 10.0.0.5 port 50001 ssh2
$TIMESTAMP $HOSTNAME sshd[1003]: Failed password for root from 10.0.0.5 port 50002 ssh2
$TIMESTAMP $HOSTNAME sshd[1004]: Failed password for root from 10.0.0.5 port 50003 ssh2
$TIMESTAMP $HOSTNAME sshd[1005]: Failed password for root from 10.0.0.5 port 50004 ssh2
$TIMESTAMP $HOSTNAME sshd[1006]: Failed password for admin from 10.0.0.8 port 51001 ssh2
LOGEOF

echo "================================================"
echo "  AI-SOC Simulated Node Initialized"
echo "  Node ID: $NODE_ID"
echo "  Log file: $AUTH_LOG"
echo "  Lines: $(wc -l < "$AUTH_LOG")"
echo "================================================"
echo ""
echo "  Start client: bash scripts/run_client.sh $NODE_ID"
echo "  Trigger:      bash scripts/trigger_authlog.sh ssh 5 --node-id $NODE_ID"
