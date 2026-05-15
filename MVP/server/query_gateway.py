# MVP/server/query_gateway.py
"""
Query Gateway - 中心查询网关（FastAPI 服务）
=================================================
职责：
  1. 接收来自 Dashboard / Agent Team / Signal Listener 的查询请求
  2. 读取 Metadata Registry，确定数据源位置和查询方式
  3. 生成 DuckDB 查询计划，下发到边缘 DuckDB Sidecar 执行
  4. 接收查询结果，进行 OCSF‑lite 标准化映射
  5. 将标准化证据持久化到 OpenSearch

对应实现方案：第三章 - 元数据注册与数据地图查询
"""

import json
import uuid
import hashlib
import sys
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from datetime import datetime
from typing import Optional

import duckdb
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from config import METADATA_PATH, OUTPUTS_DIR, DEFAULT_CASE_ID, DEFAULT_NODE_ID, ROOT_DIR
from common import map_wazuh_to_ocsf

app = FastAPI(
    title="AI-SOC Query Gateway",
    version="0.2.0",
    description="按需取证查询网关 - 数据编织核心组件",
)


# ====================== 数据模型 ======================

class QueryRequest(BaseModel):
    """查询请求模型"""
    case_id: str = DEFAULT_CASE_ID
    node_id: str = DEFAULT_NODE_ID
    source: str = "wazuh_alerts"
    signal_id: Optional[str] = None
    time_window: Optional[str] = None
    filters: dict = Field(default_factory=dict)
    fields: list = ["timestamp", "rule.id", "agent.name", "agent.ip", "full_log"]
    limit: int = 20


class EvidenceItem(BaseModel):
    """标准化证据项"""
    evidence_id: str
    query_id: str
    raw_ref: str
    lineage_id: str
    hash: str
    timestamp: str
    source: str
    rule_id: str
    description: str
    src_ip: str
    hostname: str
    severity: Optional[int] = None
    raw_log: Optional[str] = None


class QueryResponse(BaseModel):
    """查询响应模型"""
    query_id: str
    node_id: str
    source: str
    evidence_count: int
    evidence: list[EvidenceItem]
    execution_time_ms: float


# ====================== Metadata Registry ======================

def load_metadata() -> list[dict]:
    """
    加载元数据注册表
    Returns:
        metadata 列表（每个节点+数据源一个条目）
    """
    try:
        with open(METADATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else [data]
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"Metadata 文件未找到: {METADATA_PATH}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Metadata 文件格式错误")


def find_source_config(metadata: list, node_id: str, source: str) -> Optional[dict]:
    """
    在元数据中查找匹配的数据源配置
    """
    for entry in metadata:
        if entry.get("node_id") == node_id and entry.get("source_name") == source:
            return entry
        # 也支持 metadata 作为嵌套结构
        if isinstance(entry, dict) and "sources" in entry:
            if entry.get("node_id") == node_id:
                for src in entry["sources"]:
                    if src.get("source_name") == source:
                        return src
    return None


def build_query_plan(request: QueryRequest) -> tuple[str, dict]:
    """
    根据查询请求和元数据生成 DuckDB 查询计划

    Returns:
        (sql, source_config)
    """
    metadata = load_metadata()
    source_config = find_source_config(metadata, request.node_id, request.source)

    if not source_config:
        raise HTTPException(
            status_code=404,
            detail=f"未找到 node_id='{request.node_id}' 的 '{request.source}' 数据源"
        )

    local_path = source_config.get("local_path")
    if not local_path:
        raise HTTPException(status_code=500, detail="数据源缺少 local_path")

    # 解析相对路径为绝对路径（metadata 中的路径相对于项目根目录 SOC/）
    import os
    if not os.path.isabs(local_path):
        local_path = os.path.normpath(os.path.join(ROOT_DIR, local_path))

    # 构造 DuckDB SQL
    fields_str = ", ".join(request.fields)
    sql = f"""
    SELECT {fields_str}
    FROM read_json_auto('{local_path}')
    """

    where_clauses = []

    # 时间窗口过滤
    if request.time_window:
        parts = request.time_window.split("/")
        if len(parts) == 2:
            where_clauses.append(f"timestamp >= '{parts[0]}' AND timestamp <= '{parts[1]}'")

    # 规则过滤
    rule_id = request.filters.get("rule.id") or request.filters.get("rule_id")
    if rule_id:
        where_clauses.append(f"\"rule\".\"id\" = '{rule_id}'")

    source_filter = request.filters.get("source")
    if source_filter:
        where_clauses.append(f"source = '{source_filter}'")

    if where_clauses:
        sql += " WHERE " + " AND ".join(where_clauses)

    sql += f" LIMIT {request.limit};"

    return sql, source_config


def calculate_hash(row: tuple) -> str:
    """
    计算证据行的哈希值，用于溯源
    """
    raw = "|".join([str(field) for field in row])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def execute_local_query(sql: str) -> list[tuple]:
    """
    本地执行 DuckDB 查询（用于开发/单机模式）

    在分布式模式下，此函数会通过 NATS 下发到边缘 DuckDB Sidecar
    """
    con = duckdb.connect()
    try:
        print(f"🔍 [QueryGateway] 执行 SQL:\n{sql}")
        result = con.execute(sql).fetchall()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询执行失败: {str(e)}")
    finally:
        con.close()


def standardize_evidence(
    rows: list[tuple],
    query_id: str,
    request: QueryRequest,
) -> list[EvidenceItem]:
    """
    将原始查询结果标准化为 OCSF‑lite 格式
    """
    evidence_list = []

    for row in rows:
        timestamp_field = row[0].isoformat() if hasattr(row[0], 'isoformat') else str(row[0])
        rule = row[1] if len(row) > 1 and isinstance(row[1], dict) else {"id": "", "description": ""}
        agent = row[2] if len(row) > 2 and isinstance(row[2], dict) else {"name": "", "ip": ""}
        full_log = row[4] if len(row) > 4 else ""

        evidence_raw = {
            "timestamp": timestamp_field,
            "source": request.source,
            "agent_name": agent.get("name", ""),
            "agent_ip": agent.get("ip", ""),
            "rule_id": rule.get("id", ""),
            "description": rule.get("description", ""),
            "full_log": full_log,
        }

        # OCSF 标准化
        ocsf = map_wazuh_to_ocsf(evidence_raw)

        # 增强字段
        evidence_item = EvidenceItem(
            evidence_id=ocsf.get("evidence_id", f"ev-{uuid.uuid4().hex[:8]}"),
            query_id=query_id,
            raw_ref=f"{request.node_id}/{request.source}#{timestamp_field}",
            lineage_id=f"{query_id}:{hashlib.sha256(str(row).encode()).hexdigest()[:8]}",
            hash=calculate_hash(row),
            timestamp=timestamp_field,
            source=request.source,
            rule_id=ocsf.get("rule_id", ""),
            description=ocsf.get("description", ""),
            src_ip=ocsf.get("src_ip", ""),
            hostname=ocsf.get("hostname", ""),
            severity=ocsf.get("severity"),
            raw_log=ocsf.get("raw_log"),
        )
        evidence_list.append(evidence_item)

    return evidence_list


# ====================== FastAPI 端点 ======================

@app.get("/")
def root():
    return {
        "service": "AI-SOC Query Gateway",
        "version": "0.2.0",
        "status": "running",
    }


@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.get("/metadata", response_model=list)
def get_metadata():
    """查看元数据注册表"""
    return load_metadata()


@app.post("/query", response_model=QueryResponse)
async def execute_query(request: QueryRequest):
    """
    主查询接口

    接收查询请求 → 生成查询计划 → 执行本地/边缘查询 → 标准化 → 返回
    """
    import time
    start_time = time.time()

    # 1. 生成查询 ID
    query_id = f"qry-{uuid.uuid4().hex[:8]}"

    # 2. 构建查询计划
    try:
        sql, source_config = build_query_plan(request)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询计划生成失败: {str(e)}")

    # 3. 执行查询
    try:
        rows = execute_local_query(sql)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询执行失败: {str(e)}")

    # 4. 标准化证据
    evidence_list = standardize_evidence(rows, query_id, request)

    # 5. 持久化到 OpenSearch（异步）
    try:
        from .opensearch_loader import OpenSearchClient
        os_client = OpenSearchClient()
        for ev in evidence_list:
            os_client.index("soc-evidence", ev.dict())
    except Exception as e:
        print(f"⚠️ [QueryGateway] OpenSearch 写入失败: {e}")

    # 6. 保存到本地文件
    output_path = f"{OUTPUTS_DIR}/query_{query_id}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump([ev.dict() for ev in evidence_list], f, indent=2, ensure_ascii=False)

    execution_time = (time.time() - start_time) * 1000

    return QueryResponse(
        query_id=query_id,
        node_id=request.node_id,
        source=request.source,
        evidence_count=len(evidence_list),
        evidence=evidence_list,
        execution_time_ms=round(execution_time, 2),
    )


# ====================== 独立运行入口 ======================
if __name__ == "__main__":
    import uvicorn
    from config import QUERY_GATEWAY_HOST, QUERY_GATEWAY_PORT

    uvicorn.run(
        "MVP.server.query_gateway:app",
        host=QUERY_GATEWAY_HOST,
        port=QUERY_GATEWAY_PORT,
        reload=True,
    )