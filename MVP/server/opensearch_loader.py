from opensearchpy import OpenSearch, helpers
import json
import os
from config import OPENSEARCH_HOST, OPENSEARCH_PORT, OPENSEARCH_USER, OPENSEARCH_PASS, OUTPUTS_DIR, ROOT_DIR


class OpenSearchClient:
    """OpenSearch 客户端封装"""

    def __init__(self, host=None, port=None):
        self.host = host or OPENSEARCH_HOST
        self.port = port or OPENSEARCH_PORT
        self.client = OpenSearch(
            hosts=[{"host": self.host, "port": self.port}],
            http_auth=(OPENSEARCH_USER, OPENSEARCH_PASS) if OPENSEARCH_USER else None,
            use_ssl=False,
            verify_certs=False
        )

    def ensure_index(self, index_name: str, mapping_path: str = None):
        """确保索引存在并应用 mapping，不存在则创建。仅 soc-evidence 使用显式映射。"""
        if self.client.indices.exists(index=index_name):
            return
        if index_name != "soc-evidence":
            self.client.indices.create(index=index_name)
            print(f"✅ [OpenSearch] 索引 {index_name} 已创建（动态映射）")
            return
        if mapping_path is None:
            mapping_path = os.path.join(ROOT_DIR, "mapping.json")
        if not os.path.exists(mapping_path):
            print(f"⚠️ [OpenSearch] mapping 文件不存在: {mapping_path}，使用动态映射")
            self.client.indices.create(index=index_name)
            return
        with open(mapping_path, "r", encoding="utf-8") as f:
            mapping_body = json.load(f)
        self.client.indices.create(index=index_name, body=mapping_body)
        print(f"✅ [OpenSearch] 索引 {index_name} 已创建（应用 mapping.json）")

    def index(self, index_name: str, document: dict, doc_id: str = None):
        """索引单条文档"""
        self.ensure_index(index_name)
        try:
            response = self.client.index(
                index=index_name,
                body=document,
                id=doc_id
            )
            return response
        except Exception as e:
            print(f"⚠️ [OpenSearch] 索引失败: {e}")
            return None

    def bulk_index(self, index_name: str, documents: list):
        """批量索引文档"""
        if not documents:
            return 0
        self.ensure_index(index_name)
        actions = [
            {
                "_index": index_name,
                "_source": doc
            }
            for doc in documents
        ]
        try:
            success, errors = helpers.bulk(self.client, actions, raise_on_error=False)
            if errors:
                print(f"⚠️ [OpenSearch] 批量索引: {success} 成功, {len(errors)} 失败")
                for err in errors[:3]:  # 只打印前 3 条错误详情
                    print(f"  错误详情: {err}")
            else:
                print(f"✅ [OpenSearch] 批量索引: {success} 成功, 0 失败")
            return success
        except Exception as e:
            print(f"⚠️ [OpenSearch] 批量索引失败: {e}")
            return 0
    
    def search(self, index_name: str, query: dict, size: int = 10):
        """搜索文档"""
        try:
            response = self.client.search(
                index=index_name,
                body=query,
                size=size
            )
            return response['hits']['hits']
        except Exception as e:
            print(f"⚠️ [OpenSearch] 搜索失败: {e}")
            return []


def load_to_opensearch(timestamp: str = None):
    """加载证据到 OpenSearch（向后兼容函数）"""
    client = OpenSearchClient()
    
    if timestamp:
        evidence_path = f"{OUTPUTS_DIR}/{timestamp}/evidence.json"
    else:
        # 查找最新的时间戳目录
        try:
            outputs = sorted(os.listdir(OUTPUTS_DIR), reverse=True)
        except FileNotFoundError:
            print("⚠️ [OpenSearch] 输出目录不存在，请先启动 Server + Client")
            return
        if not outputs:
            print("⚠️ [OpenSearch] 未找到输出目录")
            return
        evidence_path = f"{OUTPUTS_DIR}/{outputs[0]}/evidence.json"
    
    if not os.path.exists(evidence_path):
        print(f"⚠️ [OpenSearch] 证据文件不存在: {evidence_path}")
        return
    
    with open(evidence_path, "r", encoding="utf-8") as f:
        evidence_list = json.load(f)
    
    # 索引证据
    client.bulk_index("soc-evidence", evidence_list)
    
    # 也尝试索引就绪度评估和研判结果
    base_dir = os.path.dirname(evidence_path)
    for filename, index_name in [
        ("readiness.json", "soc-readiness"),
        ("agent_result.json", "soc-analysis"),
        ("verifier_result.json", "soc-verification"),
        ("report.md", "soc-reports")
    ]:
        filepath = os.path.join(base_dir, filename)
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    if filename.endswith(".json"):
                        data = json.load(f)
                        if isinstance(data, dict):
                            data["@timestamp"] = timestamp
                        client.index(index_name, data)
            except Exception as e:
                print(f"⚠️ [OpenSearch] 索引 {filename} 失败: {e}")
    
    print(f"✅ [OpenSearch] 成功写入 {len(evidence_list)} 条证据到 OpenSearch")


if __name__ == "__main__":
    # 测试连接
    try:
        client = OpenSearchClient()
        info = client.client.info()
        print(f"✅ OpenSearch 连接成功: {info['version']['number']}")
    except Exception as e:
        print(f"⚠️ OpenSearch 连接失败: {e}")
        print("  请确认 OpenSearch 服务已启动 (docker-compose up -d opensearch)")

    from datetime import datetime
    load_to_opensearch(timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"))
