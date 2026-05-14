import duckdb
import json

def query_wazuh(rule_id=None, limit=10):
    # 直接写你真实的 Wazuh 路径
    path = "/home/admin/SOC/wazuh_logs/alerts/alerts.json"

    sql = f"""
    SELECT *
    FROM read_json_auto('{path}')
    """

    where = []
    if rule_id:
        where.append(f"\"rule\".\"id\" = '{rule_id}'")

    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" LIMIT {limit};"

    con = duckdb.connect()
    print("执行 SQL:\n", sql)
    res = con.execute(sql).fetchall()
    con.close()
    return res

if __name__ == "__main__":
    # 查登录失败告警
    result = query_wazuh(rule_id="5503", limit=5)
    print("\n结果：")
    for r in result:
        print(r)