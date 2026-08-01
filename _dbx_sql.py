# -*- coding: utf-8 -*-
"""서버리스 SQL warehouse로 짧은 검증 쿼리를 돌린다 (노트북 잡 3시간 돌리기 전 사전검증용).

사용: DBX_TOKEN=... python _dbx_sql.py "SELECT 1"   또는   python _dbx_sql.py --file q.sql
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
HOST = "https://musinsa-data-ws.cloud.databricks.com"
WAREHOUSE = "c0ee970a9c3ed562"          # Shared 서버리스 SQL warehouse
TOK = os.environ.get("DBX_TOKEN") or (Path.home() / ".databricks_pat").read_text().strip()


def api(method, path, body=None):
    req = urllib.request.Request(HOST + path, data=json.dumps(body).encode() if body else None,
                                 method=method,
                                 headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"_err": e.code, "_body": e.read().decode()[:800]}


def run(sql, wait=300):
    r = api("POST", "/api/2.0/sql/statements", {
        "warehouse_id": WAREHOUSE, "statement": sql, "wait_timeout": "30s",
        "on_wait_timeout": "CONTINUE", "format": "JSON_ARRAY", "disposition": "INLINE"})
    if "_err" in r:
        return r
    sid = r.get("statement_id")
    t0 = time.time()
    while r.get("status", {}).get("state") in ("PENDING", "RUNNING") and time.time() - t0 < wait:
        time.sleep(3)
        r = api("GET", f"/api/2.0/sql/statements/{sid}")
    return r


def main():
    sql = Path(sys.argv[2]).read_text(encoding="utf-8") if sys.argv[1] == "--file" else sys.argv[1]
    r = run(sql)
    st = r.get("status", {})
    if st.get("state") != "SUCCEEDED":
        print("실패:", json.dumps(st, ensure_ascii=False)[:1200] or r)
        return 1
    cols = [c["name"] for c in r["manifest"]["schema"]["columns"]]
    rows = (r.get("result") or {}).get("data_array") or []
    print(" | ".join(cols))
    for row in rows[:200]:
        print(" | ".join("" if v is None else str(v) for v in row))
    print(f"-- {len(rows)}행")
    return 0


if __name__ == "__main__":
    sys.exit(main())
