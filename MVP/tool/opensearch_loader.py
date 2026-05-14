from opensearchpy import OpenSearch
import json


# 填入你的 OpenSearch 容器 IP
client = OpenSearch(
    hosts=[{"host": "172.18.0.2", "port": 9200}],
    use_ssl=False,
    verify_certs=False
)

def load_to_opensearch(timestamp):
    evidence_path = f"outputs/{timestamp}/evidence.json"

    with open(evidence_path, "r", encoding="utf-8") as f:
        evidence_list = json.load(f)

    for ev in evidence_list:
        client.index(
            index="soc-evidence",
            body=ev
        )

    print(f"✅ 成功写入 {len(evidence_list)} 条证据到 OpenSearch")

if __name__ == "__main__":
    with open("current_timestamp.txt") as f:
        ts = f.read().strip()
    load_to_opensearch(ts)