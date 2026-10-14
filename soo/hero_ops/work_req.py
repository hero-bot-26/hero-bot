# -*- coding: utf-8 -*-
"""시즌 작업의뢰(★ MS_{시즌}_작업의뢰 '기획시트') → STY별 단계 실적.

★2026-10-14 (사용자 지시) 보드 신호등(컬러확정~입고)의 실적 원천을 PLM 에서 작업의뢰로 바꾼다.
  PLM 데이터가 부정확하다는 현업 의견. 27SS 는 sched_27ss.py 가 같은 원리로 이미 이 시트를 읽는다.

원천이 구글시트일 수도, Drive 의 .xlsx 일 수도 있다(26FW 는 xlsx). 헤더 행은 라벨로 찾는다
(27SS·27FW=5행, 26FW=7행 — 행 번호를 박지 않는다).

판정 규칙
  - 진행 칸이 날짜면 완료(그 날짜), '완료' 글자면 완료(날짜 미기록), 그 외('미완료'·빈칸·쓰레기)는 미완료
  - SKU 여러 행 → STY 는 **전 SKU 가 끝나야** 완료, 날짜는 가장 늦은 날
  - 바닥선: 현업이 뒤 칸만 채우고 앞 칸을 '미완료'로 두는 일이 많다 → 후속 완료면 선행 완료로 추정
"""
from __future__ import annotations

import datetime as dt
import io
import re

TAB = "기획시트"
KEY_COL = "신품번"
DONE_MARK = "완료"
NOT_DONE = {"미완료", "누락", "-", "N/A", "#N/A", "미정"}

# 앱 단계 → 진행 칸(26FW 보드 기준 — PLM 이 주던 단계 전부). 11·12 는 날짜 칸이 없어 금액 유무로 본다.
STAGE_COLS: dict[int, str] = {
    3: "품평 (진행일)", 4: "스타일 생성", 6: "컬러 확정", 7: "원단 확정",
    8: "PO 발행 (작지투입)", 9: "PO 발행 (작지투입)",   # 원천에 PO 칸이 하나뿐 — 8/9 같이 닫힌다
    10: "테크팩 확정 (APP)", 13: "입고 완료",
}
VALUE_COLS: dict[int, str] = {11: "사후 원가V-", 12: "확정 TAG"}
EXTRA_COLS = ("원단 발주", "오더시트 실입고일")
# 바닥선 — (근거 단계/칸, 완료로 추정할 단계, 사유)
FLOOR_RULES = [
    ("입고", (3, 4, 6, 7, 8, 9, 10), "입고 완료"),
    ("PO", (3, 4, 6, 7), "PO 발행 완료"),
    ("원단 발주", (7,), "원단 발주 완료"),
]


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        return (v.date() if isinstance(v, dt.datetime) else v).isoformat()
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).replace("\n", " ").strip()


def norm_date(raw: str) -> dt.date | None:
    s = (raw or "").strip()
    if not s or s in NOT_DONE:
        return None
    m = re.match(r"^(\d{2}|\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    if y < 100:
        y += 2000
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def _is_done(raw: str) -> bool:
    return bool(norm_date(raw)) or (raw or "").strip() == DONE_MARK


def load_rows(sheets, drive, file_id: str, tab: str = TAB) -> tuple[list[str], list[list[str]]]:
    """작업의뢰 기획시트 → (헤더, 데이터행). 구글시트·xlsx 자동 판별. 헤더는 라벨로 찾는다."""
    mime = drive.files().get(fileId=file_id, fields="mimeType",
                             supportsAllDrives=True).execute().get("mimeType", "")
    if mime == "application/vnd.google-apps.spreadsheet":
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=file_id, range=f"'{tab}'!A1:GZ").execute().get("values", [])
        grid = [[_cell_str(x) for x in r] for r in vals]
    else:
        import openpyxl
        from googleapiclient.http import MediaIoBaseDownload
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, drive.files().get_media(fileId=file_id, supportsAllDrives=True),
                                 chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
        buf.seek(0)
        wb = openpyxl.load_workbook(buf, read_only=True, data_only=True)
        grid = [[_cell_str(x) for x in r] for r in wb[tab].iter_rows(values_only=True)]
    hi = next((i for i, r in enumerate(grid[:15]) if KEY_COL in r and "원단 확정" in r), None)
    if hi is None:
        raise ValueError(f"작업의뢰 '{tab}' 헤더(신품번·원단 확정) 못 찾음")
    return grid[hi], grid[hi + 1:]


def _merge_done(raws: list[str]) -> str:
    if not raws or not all(_is_done(x) for x in raws):
        return "미완료"
    ds = [norm_date(x) for x in raws]
    ds = [d for d in ds if d]
    return max(ds).isoformat() if ds else DONE_MARK


def by_style(hdr: list[str], rows: list[list[str]], only: set[str] | None = None) -> dict[str, dict]:
    """STY → {칸: 값}. 진행 칸은 SKU 전 행 병합, 나머지는 첫 행."""
    H: dict[str, int] = {}
    for i, h in enumerate(hdr):
        if h and h not in H:                              # 같은 라벨이 여럿이면 첫 매치(진척 블록이 앞)
            H[h] = i
    missing = [c for c in set(STAGE_COLS.values()) if c not in H]
    if missing:
        raise ValueError(f"작업의뢰 헤더 불일치 — 없는 칸: {missing}")
    grouped: dict[str, list] = {}
    for r in rows:
        k = r[H[KEY_COL]].strip() if len(r) > H[KEY_COL] else ""
        if k and not (only and k not in only):
            grouped.setdefault(k, []).append(r)
    get = lambda r, c: (r[H[c]] if c in H and len(r) > H[c] else "")   # noqa: E731
    done_cols = set(STAGE_COLS.values()) | {c for c in EXTRA_COLS if c in H}
    out = {}
    for k, rs in grouped.items():
        rec = {c: get(rs[0], c) for c in H}
        for c in done_cols:
            rec[c] = _merge_done([get(r, c) for r in rs])
        for c in VALUE_COLS.values():
            vals = [get(r, c).replace(",", "") for r in rs]
            rec[c] = "완료" if vals and all(re.match(r"^\d+(\.\d+)?$", v) and float(v) > 0 for v in vals) else "미완료"
        out[k] = rec
    return out


def stage_actuals(rec: dict) -> dict[int, tuple[str | None, str]]:
    """STY 1건 → {단계: (실적일 'YYYY-MM-DD' | None, 출처문구)} — 완료인 단계만 담는다."""
    out: dict[int, tuple[str | None, str]] = {}
    for n, c in {**STAGE_COLS, **VALUE_COLS}.items():
        raw = rec.get(c, "")
        if n == 13 and not _is_done(raw):
            raw = rec.get("오더시트 실입고일", "")
        if _is_done(raw):
            d = norm_date(raw)
            out[n] = (d.isoformat() if d else None, "작업의뢰")
    anchors = {"입고": 13 in out, "PO": 8 in out, "원단 발주": _is_done(rec.get("원단 발주", ""))}
    for key, stages, why in FLOOR_RULES:
        if anchors.get(key):
            for n in stages:
                out.setdefault(n, (None, f"완료 추정 — {why}"))
    return out
