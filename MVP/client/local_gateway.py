import duckdb
from config import ALERTS_JSON_PATH, DEFAULT_RULE_ID

def query_wazuh(rule_id=None, limit=10):
    path = ALERTS_JSON_PATH

    sql = f"""
    SELECT *
    FROM read_json_auto('{path}')
    """

    params = []
    where = []
    if rule_id:
        where.append('"rule"."id" = ?')
        params.append(rule_id)

    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" LIMIT {limit};"

    con = duckdb.connect()
    print("执行 SQL:\n", sql)
    res = con.execute(sql, params).fetchall()
    con.close()
    return res

if __name__ == "__main__":
    # 查登录失败告警
    result = query_wazuh(rule_id=DEFAULT_RULE_ID, limit=5)
    print("\n结果：")
    for r in result:
        print(r)