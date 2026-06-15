"""히어로 목표·준비물량 → 스타일(base 품번)별 정규화 (기간 누적 목표).

소스: 전사 대시보드(네이티브 Google Sheet)의 '히어로목표(거래량)' 탭. Sheets API로 직접 읽는다.
      (과거엔 ★MSTRD 상품기획 .xlsx를 get_media+openpyxl로 읽었으나, 그 파일은 100MB 초과
       Office 파일이라 편집분이 Drive로 저장되지 않아 신규 히어로 목표가 빠졌다. → 네이티브 탭으로 전환.)

'히어로목표(거래량)' 탭 = 전치(품번이 열) + 채널 2블록 (같은 품번이 양 블록에 등장, TOTAL = ON+OFF):
  - R2 = 채널 라벨 ("Online" / "Offline")
  - R3 = 신품번,  R4 = 품명,  R5 = 준비물량,  R6 = 목표소진율,  R7 = 목표판매량(시즌 총합)
  - R8~R13  = 월별 목표("1월".."6월", B열이 텍스트)  ← 사용 안 함
  - R14~R194 = **일별 목표** (B열 = 날짜 시리얼, C~EI = 품번별 일목표)
  ※ '거래액(GMV)'은 별도 탭이며 보지 않는다 (거래량만).

★ 달성율 정합 (2026-06-15): 전사 per-히어로 탭의 '목표판매량'은 풀시즌 총합(R7)이 아니라
  **일별 목표를 기간 윈도우로 누적**한 값이다 (셀 수식 = SUMPRODUCT(일자 in [start,end]) × 물량비중).
  물량비중은 신품번 단위로 1로 합산되므로 신품번/히어로 레벨에선 무시 가능 → 검증 결과
  커브드 YTD 74,895 / MTD 6,216 으로 전사 탭과 완전 일치.
  기간 경계(전사 탭 B3/B5/B7 = today()-1 / 월초 / 1-1, B4=B3-6):
    YTD = [1/1, as_of-1] · MTD = [월초, as_of-1] · WEEK = [as_of-7, as_of-1] · DAY = [as_of-1]

반환: { base_style_no: {
          tq: { 기간: {t, o, f} },   # 기간 누적 목표판매량 (t=Total, o=Online, f=Offline)
          prep: {t, o, f},           # 준비물량(시즌, 소진율용)
          sellthrough } }
  (목표 미설정 스타일은 dict에 없음 → 소비측에서 '목표 미설정' 처리)
"""
from __future__ import annotations

import datetime
import re

# 전사 대시보드(네이티브) — 매출/재고와 동일 시트. 목표는 '히어로목표(거래량)' 탭.
TARGET_SHEET_ID = "1aAYXjJPFgWCJAmZabc_f-f-wF3z492cIeDE-aVlx-HY"
TARGET_TAB = "히어로목표(거래량)"
TARGET_RANGE = f"'{TARGET_TAB}'!A2:EI194"   # R2(채널)~R194(일별 그리드 끝), 열은 C~EI(품번)
STYLE_RE = re.compile(r"^M[A-Z0-9]{8}$")

PERIODS = ["YTD", "MTD", "WEEK", "DAY"]

# A2부터 읽으므로 rows[] 0-indexed: 0=R2(채널), 1=R3(신품번), 3=R5(준비물량), 4=R6(소진율)
# 5=R7(목표판매량 시즌총합, 미사용), 12~=R14(일별 그리드)
_R_CHANNEL, _R_STYLE, _R_PREP, _R_SELL = 0, 1, 3, 4
_DAILY_START = 12          # rows[12] == R14 (첫 일별 행)
_DATE_COL = 1              # B열 (rows[i][1]) = 날짜 시리얼
_EPOCH = datetime.date(1899, 12, 30)   # Google Sheets 날짜 시리얼 기준


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _base(style) -> str:
    return str(style).strip().split("-")[0]


def _serial_to_date(v):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    try:
        return _EPOCH + datetime.timedelta(days=int(v))
    except (ValueError, OverflowError):
        return None


def _windows(as_of):
    """as_of(생성기 TODAY) 기준 기간 윈도우. 종료일 = as_of-1 (전사 탭 B3=today()-1과 정합)."""
    end = datetime.date.fromisoformat(str(as_of)) - datetime.timedelta(days=1)
    return {
        "YTD":  (datetime.date(end.year, 1, 1), end),
        "MTD":  (end.replace(day=1), end),
        "WEEK": (end - datetime.timedelta(days=6), end),   # B4 = B3-6 (최근 7일)
        "DAY":  (end, end),
    }


def parse_targets(sheets, as_of, sheet_id=TARGET_SHEET_ID, tab_range=TARGET_RANGE) -> dict:
    res = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=tab_range,
        valueRenderOption="UNFORMATTED_VALUE").execute()
    rows = res.get("values", [])

    def R(i):
        return rows[i] if i < len(rows) else []

    ch, sty, prep, sell = R(_R_CHANNEL), R(_R_STYLE), R(_R_PREP), R(_R_SELL)

    def cell(row, c):
        return row[c] if c < len(row) and row[c] not in (None, "") else None

    # 열 메타: 품번이 든 열 → (base, 채널키 on/off)
    colmeta: dict[int, tuple[str, str]] = {}
    for c in range(2, max(len(sty), len(ch))):
        s = str(cell(sty, c) or "").strip()
        if not STYLE_RE.match(s):
            continue
        channel = str(cell(ch, c) or "").strip().lower()
        if channel.startswith("online"):
            kch = "on"
        elif channel.startswith("offline"):
            kch = "off"
        else:
            continue
        colmeta[c] = (_base(s), kch)

    # 누적기: base → {prep_on, prep_off, sell, tq:{기간:{on,off}}}
    acc: dict[str, dict] = {}

    def slot(base):
        return acc.setdefault(base, {
            "prep_on": 0.0, "prep_off": 0.0, "sell": None,
            "tq": {p: {"on": 0.0, "off": 0.0} for p in PERIODS},
        })

    # 준비물량/소진율 (시즌, 상단 요약행)
    for c, (base, kch) in colmeta.items():
        sl = slot(base)
        pv = _num(cell(prep, c))
        if pv:
            sl["prep_on" if kch == "on" else "prep_off"] += pv
        if sl["sell"] is None:
            sv = _num(cell(sell, c))
            if sv:
                sl["sell"] = sv

    # 일별 그리드 → 기간 윈도우 누적
    windows = _windows(as_of)
    for r in rows[_DAILY_START:]:
        if len(r) <= _DATE_COL:
            continue
        d = _serial_to_date(r[_DATE_COL])
        if d is None:
            continue
        inper = [p for p, (s, e) in windows.items() if s <= d <= e]
        if not inper:
            continue
        for c, (base, kch) in colmeta.items():
            v = _num(r[c]) if c < len(r) else None
            if not v:
                continue
            tq = acc[base]["tq"]
            for p in inper:
                tq[p][kch] += v

    # 마무리: t/o/f 구조
    out: dict[str, dict] = {}
    for base, sl in acc.items():
        prep_on, prep_off = sl["prep_on"], sl["prep_off"]
        out[base] = {
            "tq": {p: {"t": round(sl["tq"][p]["on"] + sl["tq"][p]["off"]),
                       "o": round(sl["tq"][p]["on"]),
                       "f": round(sl["tq"][p]["off"])} for p in PERIODS},
            "prep": {"t": round(prep_on + prep_off) or None,
                     "o": round(prep_on) or None, "f": round(prep_off) or None},
            "sellthrough": sl["sell"],
        }
    return out


if __name__ == "__main__":   # 단독 실행 = 파싱 + 검증
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from soo.auth import get_credentials, build_services
    ROOT = Path(__file__).resolve().parents[2]
    sheets = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))["sheets"]

    as_of = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    t = parse_targets(sheets, as_of)
    print(f"as_of={as_of} · 목표 보유 품번 {len(t)}개")
    print(f"{'품번':12} {'YTD':>8} {'MTD':>7} {'WEEK':>6} {'DAY':>5} {'준비':>8} {'소진율':>6}")
    for k, v in sorted(t.items()):
        tq = v["tq"]
        print(f"{k:12} {tq['YTD']['t']:8.0f} {tq['MTD']['t']:7.0f} {tq['WEEK']['t']:6.0f} "
              f"{tq['DAY']['t']:5.0f} {(v['prep']['t'] or 0):8.0f} {(v['sellthrough'] or 0):6.3f}")
