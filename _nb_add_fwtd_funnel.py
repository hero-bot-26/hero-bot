# -*- coding: utf-8 -*-
"""노트북(`히어로 마스터 앱_실적`) 퍼널 셀에 FWTD(26FW 시즌 누계 7/1~) 기간 추가.

배경: 26FW 대시보드 누계는 FWTD인데 PDP퍼널 탭엔 YTD/MTD/WEEK/DAY만 있어
      기간이 어긋난 유입·전환 대신 '-'를 표시하고 있었음(with_funnel=False).
적용: 2026-07-29, job 334354908178394 재실행(run 220815200814359).
롤백: `_nb_workspace_pre_fwtdfunnel.py`를 같은 경로로 import.

실행: DBX_TOKEN=$(cat ~/.databricks_pat) python _nb_add_fwtd_funnel.py [--run]
"""
import base64
import io
import json
import os
import sys
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HOST = "https://musinsa-data-ws.cloud.databricks.com"
TOK = os.environ["DBX_TOKEN"]
WPATH = "/Users/sooyoung.moon@musinsa.com/히어로 마스터 앱_실적"
JOB_ID = 334354908178394

OLD = '''  _fp = [("YTD", params["start_dt"][0], params["end_dt"][0]), ("MTD", params["start_dt"][2], params["end_dt"][2]),
         ("WEEK", params["start_dt"][4], params["end_dt"][4]), ("DAY", params["start_dt"][6], params["end_dt"][6])]'''
NEW = '''  # ★FWTD(26FW 시즌 누계 7/1~) 추가 — 26FW 대시보드 누계가 FWTD인데 퍼널에 FWTD가 없어 유입·전환을 껐었다.
  #   인덱스 8 = params의 FWTD 슬롯(리스트 맨 뒤 추가 규칙 준수).
  _fp = [("YTD", params["start_dt"][0], params["end_dt"][0]), ("MTD", params["start_dt"][2], params["end_dt"][2]),
         ("WEEK", params["start_dt"][4], params["end_dt"][4]), ("DAY", params["start_dt"][6], params["end_dt"][6]),
         ("FWTD", params["start_dt"][8], params["end_dt"][8])]'''


def api(method, path, body=None):
    req = urllib.request.Request(
        HOST + path,
        data=json.dumps(body).encode() if body else None,
        method=method,
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=90))


def export():
    q = urllib.parse.quote(WPATH)
    return base64.b64decode(api("GET", f"/api/2.0/workspace/export?path={q}&format=SOURCE")["content"]).decode("utf-8")


def main():
    src = export()
    if NEW.splitlines()[-1] in src:
        print("이미 반영돼 있음 — import 스킵")
    else:
        assert src.count(OLD) == 1, f"퍼널 셀 매칭 {src.count(OLD)}건 (1이어야 함)"
        open("_nb_workspace_pre_fwtdfunnel.py", "w", encoding="utf-8").write(src)   # 롤백본
        api("POST", "/api/2.0/workspace/import", {
            "path": WPATH, "format": "SOURCE", "language": "PYTHON", "overwrite": True,
            "content": base64.b64encode(src.replace(OLD, NEW).encode()).decode(),
        })
        assert NEW.splitlines()[-1] in export(), "import 후 반영 확인 실패"
        print("import OK — 퍼널 셀에 FWTD 추가")
    if "--run" in sys.argv:
        print("run_id", api("POST", "/api/2.1/jobs/run-now", {"job_id": JOB_ID})["run_id"])


if __name__ == "__main__":
    main()
