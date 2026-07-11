# -*- coding: utf-8 -*-
"""'입고일자별' 탭을 즉시 채우는 경량 임시 노트북 실행 + 검증.
BARCODE = 스타일(9)+컬러코드(2)+사이즈(3) → 품번-컬러 = SUBSTR(1,9)+'-'+SUBSTR(10,2) = 보드 SKU 키.
토큰=dbx_token.txt. 값 출력 안 함."""
import os, base64, json, time, requests
from pathlib import Path

ROOT = Path(__file__).parent
HOST = "https://musinsa-data-ws.cloud.databricks.com"
TOKEN = (ROOT / "dbx_token.txt").read_text(encoding="utf-8").strip()
H = {"Authorization": f"Bearer {TOKEN}"}
JOB = 334354908178394
TMP_PATH = "/Users/sooyoung.moon@musinsa.com/_tmp_inbound_daily"

from _inbound_query import QUERY   # 단일 소스

NB = '''# Databricks notebook source
# MAGIC %pip install gspread
dbutils.library.restartPython()

# COMMAND ----------
import json, os, uuid
from decimal import Decimal
import gspread as gs

FILE_URL = "https://docs.google.com/spreadsheets/d/1iHH2qG8Uj5vmlC3aXkey96usktWODmguDPD_ToT2rfA/edit"
_tmp = "/tmp/_sa_" + uuid.uuid4().hex + ".json"
with open(_tmp, "w") as f:
    json.dump(json.loads(dbutils.secrets.get(scope="29CM_PRODUCT", key="29CM_PRODUCT_GCP_API")), f)
gc = gs.service_account(filename=_tmp); os.remove(_tmp)
_book = gc.open_by_url(FILE_URL)

def _cell(v):
    if v is None: return ""
    if isinstance(v, Decimal): return float(v)
    if isinstance(v, (int, float, str, bool)): return v
    try: return float(v)
    except (TypeError, ValueError): return str(v)

def insert_query_result(sheet_name, sdf, label=""):
    header = list(sdf.columns)
    rows = [[_cell(v) for v in r] for r in sdf.collect()]
    try:
        ws = _book.worksheet(sheet_name)
    except gs.exceptions.WorksheetNotFound:
        ws = _book.add_worksheet(title=sheet_name, rows=10, cols=30)
    ncols = max(len(header), 1)
    ws.clear(); ws.resize(rows=max(len(rows) + 2, 2), cols=ncols)
    ws.update(values=[[label] + [""] * (ncols - 1)] + [header] + rows, value_input_option="RAW")
    return len(rows)

# COMMAND ----------
Q = """__QUERY__"""
sdf = spark.sql(Q)
n = insert_query_result("입고일자별", sdf, label="25.11.01~전일 일자별 실입고 (BARCODE 파싱, 품번-컬러 단위)")

# 검증
import json as _j
v = {}
v["rows"] = n
agg = spark.sql("SELECT COUNT(*) c, SUM(inbound_qty) q, MIN(dt) mn, MAX(dt) mx FROM (" + Q + ")").collect()[0]
v["total_qty"] = int(agg["q"] or 0); v["dt_min"] = agg["mn"]; v["dt_max"] = agg["mx"]
v["brands"] = [r["brd_nm"] for r in spark.sql("SELECT DISTINCT brd_nm FROM (" + Q + ") LIMIT 15").collect()]
# 진단: 브랜드필터 전 base 행수(ORD_TYPE=일반, 바코드 유효)
v["base_rows"] = spark.sql("""SELECT COUNT(*) c FROM pbo.moms.ui_grreport_detail
  WHERE ORD_STATUS NOT IN ('출고취소','입고취소','입고대기') AND ORD_TYPE='일반' AND SPR_NM='MUSINSA'
    AND BARCODE IS NOT NULL AND LENGTH(BARCODE)>=11 AND ACT_DATE>='20251101'""").collect()[0]["c"]
v["ord_types"] = [ {r["ORD_TYPE"]: r["c"]} for r in spark.sql("""SELECT ORD_TYPE, COUNT(*) c FROM pbo.moms.ui_grreport_detail
  WHERE ACT_DATE>='20251101' AND SPR_NM='MUSINSA' GROUP BY ORD_TYPE ORDER BY c DESC LIMIT 6""").collect() ]
# 보드 대조용: 커브드/데님/슬랙스 SKU별 합계
for tag, like in [("curved","MWFPC4A15-%"), ("denim","MWJNP0Z02-%"), ("slacks","MMFPL3C03-%")]:
    v[tag] = [ {k:(str(x) if x is not None else None) for k,x in r.asDict().items()}
        for r in spark.sql("SELECT sku_code, SUM(inbound_qty) q FROM (" + Q + ") WHERE sku_code LIKE '"+like+"' GROUP BY sku_code ORDER BY sku_code").collect() ]
dbutils.notebook.exit(_j.dumps(v, ensure_ascii=False, default=str))
'''.replace("__QUERY__", QUERY)

# 클러스터 스펙
jg = requests.get(f"{HOST}/api/2.1/jobs/get", headers=H, params={"job_id": JOB}, timeout=30).json()
tasks = jg["settings"].get("tasks", [])
jc = jg["settings"].get("job_clusters", [])
cluster = ({"existing_cluster_id": tasks[0]["existing_cluster_id"]} if tasks and tasks[0].get("existing_cluster_id")
           else {"new_cluster": tasks[0]["new_cluster"]} if tasks and tasks[0].get("new_cluster")
           else {"new_cluster": jc[0]["new_cluster"]})

requests.post(f"{HOST}/api/2.0/workspace/import", headers=H, json={
    "path": TMP_PATH, "format": "SOURCE", "language": "PYTHON",
    "content": base64.b64encode(NB.encode("utf-8")).decode("ascii"), "overwrite": True}, timeout=60).raise_for_status()
print("임시 노트북 import 완료")

task = {"task_key": "inbound_daily", "notebook_task": {"notebook_path": TMP_PATH}}; task.update(cluster)
sub = requests.post(f"{HOST}/api/2.1/jobs/runs/submit", headers=H,
                    json={"run_name": "inbound_daily_write", "tasks": [task]}, timeout=30)
print("submit:", sub.status_code); sub.raise_for_status()
run_id = sub.json()["run_id"]; print("run_id:", run_id)

for i in range(150):
    time.sleep(10)
    rg = requests.get(f"{HOST}/api/2.1/jobs/runs/get", headers=H, params={"run_id": run_id}, timeout=30).json()
    life = rg.get("state", {}).get("life_cycle_state")
    if life in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
        print("result:", rg["state"].get("result_state"), rg["state"].get("state_message", "")[:200]); break
    if i % 3 == 0: print(f"  ...{life} ({i*10}s)")

trid = rg["tasks"][0]["run_id"] if rg.get("tasks") else run_id
go = requests.get(f"{HOST}/api/2.1/jobs/runs/get-output", headers=H, params={"run_id": trid}, timeout=30).json()
res = go.get("notebook_output", {}).get("result")
if res:
    d = json.loads(res)
    json.dump(d, open(ROOT / "_inbound_daily_validate.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n=== 입고일자별 탭 작성 완료 ===")
    print(f"행수={d['rows']} 총수량={d['total_qty']:,} 기간={d['dt_min']}~{d['dt_max']}")
    print("브랜드:", d["brands"], "| base행수(브랜드필터전):", d.get("base_rows"))
    print("ORD_TYPE 분포:", d.get("ord_types"))
    for tag in ["curved", "denim", "slacks"]:
        print(f"\n{tag} SKU별 실입고:")
        for r in d.get(tag, []):
            print(f"  {r['sku_code']}  {r['q']}")
else:
    print("output 없음:", json.dumps(go, ensure_ascii=False)[:400])
