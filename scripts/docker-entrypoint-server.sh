#!/bin/bash
# Docker entrypoint — Server 中心侧
set -e
export PYTHONUNBUFFERED=1
echo "AI-SOC Server starting..."
echo "  LLM Provider: ${LLM_PROVIDER:-deepseek}"
echo "  NATS: ${NATS_SERVERS:-nats://nats:4222}"
echo "  OpenSearch: ${OPENSEARCH_HOST:-opensearch}:${OPENSEARCH_PORT:-9200}"

# 等待 NATS 就绪
until curl -s http://nats:8222/healthz > /dev/null 2>&1; do
    echo "  ⏳ Waiting for NATS..."
    sleep 2
done
echo "  ✅ NATS ready"

exec python3 -u MVP/server/server_app.py
