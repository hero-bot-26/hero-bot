"""히어로 목표·준비물량 → 스타일(base 품번)별 정규화.

소스: 전사 대시보드(네이티브 Google Sheet)의 '히어로목표(거래량)' 탭. Sheets API로 직접 읽는다.
      (과거엔 ★MSTRD 상품기획 .xlsx를 get_media+openpyxl로 읽었으나, 그 파일은 100MB 초과
       Office 파일이라 편집분이 Drive로 저장되지 않아 신규 히어로 목표가 빠졌다. → 네이티브 탭으로 전환.)

'히어로목표(거래량)' 탭 = 전치(품번이 열) + 채널 2블록 (같은 품번이 양 블록에 등장, TOTAL = ON+OFF):
  - R2 = 채널 라벨 ("Online" / "Offline")
  - R3 = 신품번,  R4 = 품명,  R5 = 준비물량,  R6 = 목표소진율,  R7 = 목표판매량,  R8~ = 월별
  ※ '거래액(GMV)'은 별도 탭이며 보지 않는다 (거래량만).

반환: { base_style_no: {
          target_qty, target_qty_on, target_qty_off,
          prep_qty,  prep_qty_on,  prep_qty_off,
          target_sellthrough } }
  (목표 미설정 스타일은 dict에 없음 → 소비측에서 '목표 미설정' 처리)
"""
from __future__ import annotations

import re

# 전사 대시보드(네이티브) — 매출/재고와 동일 시트. 목표는 '히어로목표(거래량)' 탭.
TARGET_SHEET_ID = "1aAYXjJPFgWCJAmZabc_f-f-wF3z492cIeDE-aVlx-HY"
TARGET_TAB = "히어로목표(거래량)"
TARGET_RANGE = f"'{TARGET_TAB}'!A2:DZ8"   # R2(채널)~R8, 열은 넉넉히 (현재 ~113열까지 사용)
STYLE_RE = re.compile(r"^M[A-Z0-9]{8}$")

# A2부터 읽으므로 rows[] 0-indexed: 0=R2(채널), 1=R3(신품번), 3=R5(준비물량), 4=R6(소진율), 5=R7(목표판매량)
_R_CHANNEL, _R_STYLE, _R_PREP, _R_SELL, _R_TGT = 0, 1, 3, 4, 5


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _base(style) -> str:
    return str(style).strip().split("-")[0]


def parse_targets(sheets, sheet_id=TARGET_SHEET_ID, tab_range=TARGET_RANGE) -> dict:
    res = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=tab_range,
        valueRenderOption="UNFORMATTED_VALUE").execute()
    rows = res.get("values", [])

    def R(i):
        return rows[i] if i < len(rows) else []

    ch, sty = R(_R_CHANNEL), R(_R_STYLE)
    prep, sell, tgt = R(_R_PREP), R(_R_SELL), R(_R_TGT)

    def cell(row, c):
        return row[c] if c < len(row) and row[c] not in (None, "") else None

    on: dict[str, dict] = {}
    off: dict[str, dict] = {}
    for c in range(2, max(len(sty), len(ch))):
        s = str(cell(sty, c) or "").strip()
        if not STYLE_RE.match(s):
            continue
        channel = str(cell(ch, c) or "").strip().lower()
        if channel.startswith("online"):
            block = on
        elif channel.startswith("offline"):
            block = off
        else:
            continue
        block[_base(s)] = {
            "prep": _num(cell(prep, c)),
            "target": _num(cell(tgt, c)),
            "sell": _num(cell(sell, c)),
        }

    out: dict[str, dict] = {}
    for base in set(on) | set(off):
        o, f = on.get(base, {}), off.get(base, {})
        tq_on, tq_off = o.get("target"), f.get("target")
        pq_on, pq_off = o.get("prep"), f.get("prep")
        out[base] = {
            "target_qty_on": tq_on,
            "target_qty_off": tq_off,
            "target_qty": (tq_on or 0) + (tq_off or 0) or None,
            "prep_qty_on": pq_on,
            "prep_qty_off": pq_off,
            "prep_qty": (pq_on or 0) + (pq_off or 0) or None,
            "target_sellthrough": o.get("sell") or f.get("sell"),
        }
    return out


if __name__ == "__main__":   # 단독 실행 = 파싱 + 검증
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from soo.auth import get_credentials, build_services
    ROOT = Path(__file__).resolve().parents[2]
    sheets = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))["sheets"]

    t = parse_targets(sheets)
    print(f"목표 보유 품번 {len(t)}개")
    print(f"{'품번':12} {'목표(ON)':>8} {'목표(OFF)':>8} {'목표TOTAL':>9} {'준비TOTAL':>9} {'소진율':>6}")
    for k, v in sorted(t.items()):
        print(f"{k:12} {(v['target_qty_on'] or 0):8.0f} {(v['target_qty_off'] or 0):8.0f} "
              f"{(v['target_qty'] or 0):9.0f} {(v['prep_qty'] or 0):9.0f} {(v['target_sellthrough'] or 0):6.3f}")
    # 검산: 목표 ≈ 준비 × 소진율
    bad = [k for k, v in t.items() if v["target_qty"] and v["prep_qty"]
           and abs(v["target_qty"] - v["prep_qty"] * (v["target_sellthrough"] or 0)) > max(5, v["target_qty"] * 0.03)]
    print(f"\n검산(목표≈준비×소진율) 불일치: {len(bad)}개 {bad[:5]}")
