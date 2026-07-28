import pathlib, requests, time, json, sys
PAT=(pathlib.Path.home()/'.databricks_pat').read_text().strip()
H={"Authorization":f"Bearer {PAT}"}
RUN=int(sys.argv[1])
while True:
    d=requests.get("https://musinsa-data-ws.cloud.databricks.com/api/2.1/jobs/runs/get",
                   headers=H, params={"run_id":RUN}, timeout=30).json()
    st=d.get("state",{})
    if st.get("life_cycle_state") not in ("PENDING","RUNNING","QUEUED"):
        print(json.dumps(st, ensure_ascii=False)); break
    time.sleep(300)
