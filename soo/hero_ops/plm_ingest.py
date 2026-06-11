"""PLM '마일스톤 보고서' → 히어로 마스터 14단계 정규화.

PLM 자동 파이프가 없어, 사람이 PLM에서 받은 '마일스톤 보고서.xlsx'를
무신사 공유 드라이브에 업로드하면 그 파일을 읽어 단계 트리플로 정규화한다.
입력단(로컬 경로 / Drive 파일 ID)만 교체하면 다운스트림(파싱·매칭)은 재사용.

매칭(스타일 → 히어로):
  - 26FW: 수동 상품 리스트 (이미 진척된 상태라 소급)
  - 27SS+: GO-DROP 후 MD가 히어로 STY 직접 입력 (목업 STY 입력 화면)
"""
from __future__ import annotations

import io
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# 무신사 공유드라이브 "히어로 트랙터(마스터앱)" 폴더 (PLM 마일스톤 드롭 위치)
MASTER_APP_FOLDER_ID = "1I5mxlv6pODPVN429QSmvd9BWkSfRD9bt"
MILESTONE_NAME_HINT = "마일스톤 보고서"

# ── 마일스톤 보고서 컬럼 인덱스 → 의미 (2행 헤더, 데이터 R2~) ──
COL: dict[int, str] = {
    0: "season", 1: "brand_line", 2: "cat_l", 3: "cat_m", 4: "cat_s",
    5: "wbs_holder", 6: "wbs", 7: "goal_start", 8: "goal_end", 9: "sched_status",
    10: "스타일생성", 11: "proto_in", 12: "proto_done", 13: "품평",
    14: "컬러확정", 15: "원단확정", 16: "PO전송", 17: "PO발송",
    18: "qc1_in", 19: "qc1_done", 20: "qc2_in", 21: "qc2_done",
    22: "qc3_in", 23: "qc3_done", 24: "QC_APP", 25: "사후원가", 26: "판매가", 27: "입고",
    28: "creator", 29: "md_team", 30: "design_team", 31: "sourcing_team",
    32: "carryover", 33: "plm_status", 34: "style_no",
}
_NAME_TO_IDX = {v: k for k, v in COL.items()}

# ── PLM 마일스톤 → 목업 14단계 번호 ──
# (단계 5 '1차 수량'은 PLM에 없음 = 사람 입력 / 단계 0~2는 PLM 이전 / Proto·1~3차 QC는 더 세분)
MILESTONE_TO_STAGE: dict[str, int] = {
    "품평": 3, "스타일생성": 4, "컬러확정": 6, "원단확정": 7,
    "PO전송": 8, "PO발송": 9, "QC_APP": 10, "사후원가": 11, "판매가": 12, "입고": 13,
}

# PLM 라이프사이클 상태 순서 (현재 단계 추정용)
PLM_STATUS_ORDER = [
    "New", "Proto Approved", "PP Confirmed", "PO Issued",
    "QC Confirmed", "Final Cost Set", "Dropped",
]


@dataclass
class StageCell:
    status: str
    baseline: str | None  # P: 목표일 (PLM WBS 자동일정 — 지연 baseline으론 부적합, MDP baseline 권장)
    actual: str | None    # A: 실제일
    est: str | None       # E: 예상일
    delta_days: int | None  # actual - baseline


@dataclass
class StyleMilestone:
    style_no: str
    name: str
    season: str | None
    brand_line: str | None
    category: str
    carryover: bool | None
    plm_status: str | None
    md_nm: str | None = None       # 기획MD (데이터브릭스버전에만 있음 — 기존 export는 공란)
    ds_nm: str | None = None       # 디자이너
    sc_nm: str | None = None       # 소싱
    plm_po_no: str | None = None
    sap_po_no: str | None = None
    stages: dict[int, StageCell] = field(default_factory=dict)


def parse_cell(s) -> StageCell | None:
    """'완료P:2026-02-16A:2026-02-10E:...' → StageCell. 빈 셀은 None."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s)
    status = re.split(r"[PAE]:", s)[0].strip()

    def grab(tag: str) -> str | None:
        m = re.search(tag + r":(\d{4}-\d{2}(?:-\d{2})?)", s)
        return m.group(1) if m else None

    baseline, actual, est = grab("P"), grab("A"), grab("E")
    delta = None
    if baseline and actual and len(baseline) == 10 and len(actual) == 10:
        delta = (pd.Timestamp(actual) - pd.Timestamp(baseline)).days
    return StageCell(status=status, baseline=baseline, actual=actual, est=est, delta_days=delta)


def parse_milestone_report(path: str | Path) -> list[StyleMilestone]:
    """마일스톤 보고서 xlsx → StyleMilestone 리스트. 스타일코드 없는 행(미생성)은 제외."""
    raw = pd.read_excel(path, sheet_name=0, header=None, skiprows=2, engine="openpyxl")
    out: list[StyleMilestone] = []
    for _, r in raw.iterrows():
        def g(name: str):
            i = _NAME_TO_IDX[name]
            return None if i >= len(r) or pd.isna(r.iloc[i]) else r.iloc[i]

        code = g("style_no")
        if code is None or str(code).strip() == "":
            continue
        holder = str(g("wbs_holder") or "")
        name = holder.rsplit(",", 1)[0].strip() if "," in holder else holder.strip()
        rec = StyleMilestone(
            style_no=str(code).strip(),
            name=name,
            season=g("season"),
            brand_line=g("brand_line"),
            category="/".join(str(g(c)) for c in ("cat_l", "cat_m", "cat_s") if g(c)),
            carryover=bool(g("carryover")) if g("carryover") is not None else None,
            plm_status=g("plm_status"),
        )
        for mcol, stage_n in MILESTONE_TO_STAGE.items():
            cell = parse_cell(g(mcol))
            if cell:
                rec.stages[stage_n] = cell
        out.append(rec)
    return out


# ════════════════════════════════════════════════════════════════════
# 데이터브릭스버전 (team.`brand-strategy`.sourcing_dash_mutandard_mdp_plm)
#   원본 'Sheet' 탭(패킹 "완료P:..A:..E:.." 문자열, 위치기반)과 달리
#   ① 마일스톤 셀 = 실제일(actual) 단일 날짜  ② 헤더명 기준(컬럼 순서 무관)
#   ③ 담당자 실명(md_nm/ds_nm/sc_nm)·PO번호 포함 — 원본 export엔 공란이던 것.
#   baseline(P)은 여기에도 없음 → 생성기는 MDP BASELINE 사용(기존과 동일).
#   per-단계 est(E)는 없고, 전체 WBS 추정완료일(스케줄상태의 E:)을 입고(13)에 부착.
# ════════════════════════════════════════════════════════════════════
DBX_SHEET = "데이터브릭스버전"
DBX_MILESTONE_TO_STAGE: dict[str, int] = {
    "품평": 3, "스타일생성": 4, "컬러확정": 6, "원단확정": 7,
    "PO전송": 8, "PO발송": 9, "QCAPP": 10, "사후원가확정": 11,
    "판매가확정": 12, "입고": 13,
}
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _dbx_clean(v) -> str | None:
    """셀 → 정리된 문자열. '', 'null', NaN, NaT 류는 None."""
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in ("", "null", "none", "nan", "nat") else s


def _dbx_date(v) -> str | None:
    """셀에서 YYYY-MM-DD 추출 ('2026-02-02', '2026-02-02 00:00:00', Timestamp 모두)."""
    s = _dbx_clean(v)
    if not s:
        return None
    m = _DATE_RE.search(s)
    return m.group(0) if m else None


def _dbx_records(header: list[str], rows) -> list[StyleMilestone]:
    """(헤더, 행들) → StyleMilestone 리스트. xlsx/구글시트 공통 코어."""
    idx = {name: i for i, name in enumerate(header)}

    def get(row, name):
        i = idx.get(name)
        return _dbx_clean(row[i]) if (i is not None and i < len(row)) else None

    def raw(row, name):
        i = idx.get(name)
        return row[i] if (i is not None and i < len(row)) else None

    out: list[StyleMilestone] = []
    for row in rows:
        code = get(row, "style_no")
        if not code:
            continue
        rel = get(row, "release_type")
        # 전체 WBS 추정완료일 = 스케줄상태의 E: → 입고(13) est로
        m_est = re.search(r"E:(\d{4}-\d{2}-\d{2})", str(raw(row, "스케줄상태") or ""))
        est13 = m_est.group(1) if m_est else None
        rec = StyleMilestone(
            style_no=code,
            name="",  # 깔끔한 품명은 HERO STY 시트에서 조인 (생성기), 여기선 미사용
            season=get(row, "시즌"),
            brand_line=get(row, "브랜드라인"),
            category="/".join(x for x in (get(row, "대복종"), get(row, "중복종"), get(row, "소복종")) if x),
            carryover=(rel.lower() == "true") if rel is not None else None,
            plm_status=get(row, "style_status"),
            md_nm=get(row, "md_nm"), ds_nm=get(row, "ds_nm"), sc_nm=get(row, "sc_nm"),
            plm_po_no=get(row, "plm_po_no"), sap_po_no=get(row, "sap_po_no"),
        )
        for hdr, stage_n in DBX_MILESTONE_TO_STAGE.items():
            actual = _dbx_date(raw(row, hdr))
            est = est13 if stage_n == 13 else None
            if actual or est:
                rec.stages[stage_n] = StageCell(
                    status=("완료" if actual else ""),
                    baseline=None, actual=actual, est=est, delta_days=None)
        out.append(rec)
    return out


def parse_milestone_dbx(path: str | Path) -> list[StyleMilestone]:
    """'데이터브릭스버전' 탭(xlsx) → StyleMilestone 리스트 (헤더명 기준)."""
    df = pd.read_excel(path, sheet_name=DBX_SHEET, header=0, engine="openpyxl", dtype=object)
    return _dbx_records(list(df.columns), df.values.tolist())


# 데이터브릭스 잡이 매일 쓰는 구글시트 (databricks/hero_plm_to_sheet.py 가 채움)
DBX_SHEET_ID = "1_tZDl-heZyWT4VQYIAT3ZHFeMoQlK2FSOpEMyZjqvm0"
DBX_SHEET_TAB = "데이터"


def parse_milestone_dbx_from_sheet(sheets, sheet_id: str = DBX_SHEET_ID,
                                   tab: str = DBX_SHEET_TAB) -> list[StyleMilestone]:
    """구글시트(데이터브릭스 잡 출력) → StyleMilestone 리스트. 1행 헤더, 2행~ 데이터."""
    res = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{tab}'!A1:AZ",
        valueRenderOption="UNFORMATTED_VALUE").execute()
    vals = res.get("values", [])
    if len(vals) < 2:
        raise ValueError(f"구글시트 '{tab}'에 데이터 없음 (헤더만 있거나 비어있음) — Databricks 잡 실행 확인")
    return _dbx_records([str(c) for c in vals[0]], vals[1:])


def find_latest_milestone_file(drive, folder_id: str = MASTER_APP_FOLDER_ID,
                               name_hint: str = MILESTONE_NAME_HINT) -> dict | None:
    """폴더에서 이름에 name_hint 포함된 최신(수정시각) 파일 메타 반환."""
    res = drive.files().list(
        q=f"'{folder_id}' in parents and name contains '{name_hint}' and trashed=false",
        orderBy="modifiedTime desc",
        fields="files(id,name,modifiedTime,mimeType)",
        includeItemsFromAllDrives=True, supportsAllDrives=True, pageSize=10,
    ).execute()
    files = res.get("files", [])
    return files[0] if files else None


def parse_milestone_from_drive(drive, folder_id: str = MASTER_APP_FOLDER_ID):
    """Drive 폴더의 최신 마일스톤 보고서를 받아 파싱. (파일메타, records) 반환."""
    meta = find_latest_milestone_file(drive, folder_id)
    if not meta:
        raise FileNotFoundError(f"'{MILESTONE_NAME_HINT}' 파일이 폴더 {folder_id}에 없음")
    return meta, parse_milestone_report(_download_milestone(drive, meta))


def _download_milestone(drive, meta) -> Path:
    """Drive 파일 메타 → 로컬 임시경로로 다운로드."""
    tmp = Path(tempfile.gettempdir()) / meta["name"]
    req = drive.files().get_media(fileId=meta["id"], supportsAllDrives=True)
    from googleapiclient.http import MediaIoBaseDownload
    with io.FileIO(tmp, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
    return tmp


def parse_milestone_dbx_from_drive(drive, folder_id: str = MASTER_APP_FOLDER_ID):
    """Drive 폴더 최신 파일의 '데이터브릭스버전' 탭을 파싱. (파일메타, records) 반환."""
    meta = find_latest_milestone_file(drive, folder_id)
    if not meta:
        raise FileNotFoundError(f"'{MILESTONE_NAME_HINT}' 파일이 폴더 {folder_id}에 없음")
    return meta, parse_milestone_dbx(_download_milestone(drive, meta))


def group_by_hero(
    records: list[StyleMilestone], style_to_hero: dict[str, str]
) -> dict[str, list[StyleMilestone]]:
    """스타일→히어로 매핑으로 히어로별 묶기. 매핑에 없는 스타일은 버림(=히어로 아님)."""
    grouped: dict[str, list[StyleMilestone]] = {}
    for rec in records:
        hero = style_to_hero.get(rec.style_no)
        if hero:
            grouped.setdefault(hero, []).append(rec)
    return grouped


if __name__ == "__main__":
    import sys
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Downloads" / "마일스톤 보고서.xlsx"
    recs = parse_milestone_report(src)
    print(f"정규화 스타일 {len(recs)}건  (소스: {src.name})")
    by_status: dict[str, int] = {}
    stage_done: dict[int, int] = {}
    for r in recs:
        by_status[r.plm_status] = by_status.get(r.plm_status, 0) + 1
        for sn, c in r.stages.items():
            if c.actual:
                stage_done[sn] = stage_done.get(sn, 0) + 1
    print("PLM 상태별:", dict(sorted(by_status.items(), key=lambda x: -x[1])))
    print("단계별 actual 완료:", dict(sorted(stage_done.items())))
