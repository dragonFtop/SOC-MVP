#!/bin/bash
# Docker entrypoint — Client 边缘侧
set -e
export PYTHONUNBUFFERED=1

CLIENT_ID="${CLIENT_ID:-gateway-guard}"
echo "AI-SOC Client starting..."
echo "  Client ID: ${CLIENT_ID}"
echo "  NATS: ${NATS_SERVERS:-nats://nats:4222}"

# 等待 NATS 就绪
until curl -s http://nats:8222/healthz > /dev/null 2>&1; do
    echo "  ⏳ Waiting for NATS..."
    sleep 2
done
echo "  ✅ NATS ready"

exec python3 -u MVP/client/client_app.py --client-id "${CLIENT_ID}"
