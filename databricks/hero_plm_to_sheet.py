# Databricks 노트북 — 히어로 PLM 마일스톤 → 구글시트 (매일 자동 갱신)
# ─────────────────────────────────────────────────────────────────────────────
# 목적: PLM 마일스톤 테이블을 매일 구글시트로 내보내 "히어로 마스터 앱"이 자동 갱신되게 함.
#       (기존 수동 단계 = PLM에서 엑셀 받아 드라이브 업로드 → 이 잡이 대체)
# 기반: 사내 "실적 자동화(샘플)" 노트북의 인증/쓰기 패턴 그대로. 데이터만 PLM·날짜형으로 교체.
#
# 등록: Run all 로 1회 수동 실행(시트 채워지는지 확인) → 우측상단 Schedule(시계) → 매일 새벽, Asia/Seoul.
# 인증: 아래 SCOPE/KEY 가 샘플과 동일(29CM_PRODUCT). 팀 전용 SA secret이 따로 있으면 그걸로 교체.
#       그리고 그 서비스계정 이메일을 대상 시트의 "편집자"로 공유해두세요 (1회).
# ─────────────────────────────────────────────────────────────────────────────

# COMMAND ----------
%pip install gspread
dbutils.library.restartPython()

# COMMAND ----------
import json, os, uuid
import gspread as gs
from decimal import Decimal

# ── 인증 (샘플 패턴 동일) ──
SCOPE = "29CM_PRODUCT"            # ← 팀 전용 SA secret scope 있으면 교체
KEY   = "29CM_PRODUCT_GCP_API"    # ← 〃 key
FILE_URL = "https://docs.google.com/spreadsheets/d/1_tZDl-heZyWT4VQYIAT3ZHFeMoQlK2FSOpEMyZjqvm0/edit"  # 히어로 PLM 마일스톤 (자동)
TAB = "데이터"

_tmp = "/tmp/_sa_" + uuid.uuid4().hex + ".json"
with open(_tmp, "w") as f:
    json.dump(json.loads(dbutils.secrets.get(scope=SCOPE, key=KEY)), f)
gc = gs.service_account(filename=_tmp)
os.remove(_tmp)


def insert_query_result(sheet_name, sdf):
    pdf = sdf.toPandas()
    worksheet = gc.open_by_url(FILE_URL).worksheet(sheet_name)
    header_values = [pdf.columns.tolist()]
    # ★ 샘플과 다른 점: 날짜·텍스트 데이터라 전부 '문자열' + 빈값은 '' (0 아님).
    #    생성기 파서가 'YYYY-MM-DD' 텍스트와 빈칸을 기대하므로 RAW 로 그대로 저장.
    pdf = pdf.astype(object).where(pdf.notna(), "")
    row_values = [
        ["" if v == "" else (float(v) if isinstance(v, Decimal) else str(v)) for v in row]
        for row in pdf.values.tolist()
    ]
    worksheet.batch_clear(["A2:AZ"])   # 헤더(1행) 빼고 전부 비우기 (38컬럼=A~AL, 여유롭게 AZ)
    worksheet.update(
        values=header_values + row_values,
        value_input_option="RAW",      # USER_ENTERED 아님 — 날짜 자동변환 방지
    )
    print(f"[OK] {len(row_values)} rows x {len(pdf.columns)} cols -> '{sheet_name}'")


# COMMAND ----------
# PLM 마일스톤 전체 (데이터브릭스버전 탭과 동일 컬럼)
query = "SELECT * FROM team.`brand-strategy`.sourcing_dash_mutandard_mdp_plm"
insert_query_result(TAB, spark.sql(query))
