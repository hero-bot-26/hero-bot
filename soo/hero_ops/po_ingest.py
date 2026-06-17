"""PO 수량(발주수량) → 스타일(최종품번)별 정규화.

소스: 소싱/MD 실무 시트 'MD투입' 탭 (gid 1474972499). 실무자 사용본.
      추후 PLM 직접 연동으로 전환 가능 (현재 PLM엔 PO번호만 있고 발주수량 컬럼이 없음).

'MD투입' 탭 = 스타일-컬러 단위 행 (한 스타일이 컬러/오더차수별로 여러 행):
  - R7 = 헤더, R8~ = 데이터
  - B  = 최종품번 (style_no, M+8 형식)  ← 매칭키
  - AE = 내수 발주수량 (해당 컬러행 합계, = 온라인+오프라인)
  - AN = 내수 온라인 발주,  AO = 내수 오프라인 발주
  - CP = PLM PO 넘버,  CQ = 소싱 담당
  ※ '내수' 기준 (글로벌/차이나 제외) — 매출 대시보드(무탠 내수 온/오프)와 정합.

반환: { style_no: {
          po:  {t, o, f},          # 발주수량 (t=Total=내수, o=Online, f=Offline)
          po_no:    str | None,    # 대표 PO 넘버 (첫 비어있지 않은 값)
          sourcing: str | None } } # 소싱 담당 (첫 비어있지 않은 값)
  (스타일이 여러 컬러행이면 발주수량은 합산)

⚠️ 이 시트는 Drive API(파일 export)로는 대부분 #REF!로 보이지만, Sheets API
   values.get(UNFORMATTED_VALUE)로는 계산된 실제 값이 잡힌다 (사용자 OAuth 기준 검증됨).
"""
from __future__ import annotations

import re

PO_SHEET_ID = "13R4gcJ7cDlReY-vwjXZf0kMZ7tC4kr2-S7PC9uziVUQ"
PO_TAB = "MD투입"

# 컬럼 0-indexed (A=0). batchGet 블록 단위로 읽고 블록 내 오프셋으로 접근.
_COL_STYLE = 1            # B 최종품번
_AE = 30                  # AE 내수 발주수량 (AE:AO 블록의 0)
_AN = 39                  # AN 내수 온라인   (블록의 9)
_AO = 40                  # AO 내수 오프라인 (블록의 10)
# 블록 정의: (range, 시작 0-indexed 컬럼)
_RANGE_STYLE = f"'{PO_TAB}'!B7:B"        # 헤더 R7 + 데이터
_RANGE_QTY   = f"'{PO_TAB}'!AE7:AO"      # AE..AO (11열)
_RANGE_PO    = f"'{PO_TAB}'!CP7:CQ"      # CP(PO넘버), CQ(소싱담당)
_QTY_OFF = {"ae": 0, "on": _AN - _AE, "off": _AO - _AE}   # 블록 내 오프셋

STYLE_RE = re.compile(r"^M[A-Z0-9]{8}$")


def _num(v):
    if v in (None, ""):
        return 0
    try:
        return int(round(float(str(v).replace(",", "").strip())))
    except (TypeError, ValueError):
        return 0


def _txt(v):
    s = str(v).strip() if v not in (None, "") else ""
    return s or None


def parse_po_qty(sheets, sheet_id=PO_SHEET_ID) -> dict:
    res = sheets.spreadsheets().values().batchGet(
        spreadsheetId=sheet_id,
        ranges=[_RANGE_STYLE, _RANGE_QTY, _RANGE_PO],
        valueRenderOption="UNFORMATTED_VALUE").execute()["valueRanges"]
    style_rows = res[0].get("values", [])
    qty_rows = res[1].get("values", [])
    po_rows = res[2].get("values", [])

    # 헤더(R7) 검증 — 컬럼 드리프트 조기 발견
    def h(rows, off=0):
        return str(rows[0][off]).replace("\n", " ").strip() if rows and len(rows[0]) > off else ""
    assert h(style_rows) == "최종품번", f"B7 헤더 불일치: {h(style_rows)!r}"
    assert "발주수량" in h(qty_rows, _QTY_OFF["ae"]), f"AE7 헤더 불일치: {h(qty_rows, _QTY_OFF['ae'])!r}"
    assert "온라인" in h(qty_rows, _QTY_OFF["on"]), f"AN7 헤더 불일치: {h(qty_rows, _QTY_OFF['on'])!r}"
    assert "오프라인" in h(qty_rows, _QTY_OFF["off"]), f"AO7 헤더 불일치: {h(qty_rows, _QTY_OFF['off'])!r}"
    assert "PO" in h(po_rows), f"CP7 헤더 불일치: {h(po_rows)!r}"

    out: dict[str, dict] = {}

    def slot(style):
        return out.setdefault(style, {
            "po": {"t": 0, "o": 0, "f": 0}, "po_no": None, "sourcing": None})

    # 데이터는 인덱스 1부터 (0=헤더). 세 블록은 같은 행에 정렬됨.
    n = len(style_rows)
    for i in range(1, n):
        srow = style_rows[i]
        style = str(srow[0]).strip() if srow else ""
        if not STYLE_RE.match(style):
            continue
        sl = slot(style)
        q = qty_rows[i] if i < len(qty_rows) else []

        def qv(key):
            o = _QTY_OFF[key]
            return _num(q[o]) if o < len(q) else 0
        sl["po"]["o"] += qv("on")
        sl["po"]["f"] += qv("off")
        sl["po"]["t"] += qv("ae")
        p = po_rows[i] if i < len(po_rows) else []
        if sl["po_no"] is None and len(p) > 0:
            sl["po_no"] = _txt(p[0])
        if sl["sourcing"] is None and len(p) > 1:
            sl["sourcing"] = _txt(p[1])

    # AE(t)가 비어있는데 온/오프가 있으면 t = o+f 로 보정
    for sl in out.values():
        if not sl["po"]["t"]:
            sl["po"]["t"] = sl["po"]["o"] + sl["po"]["f"]
    return out


if __name__ == "__main__":   # 단독 실행 = 파싱 + 검증
    import sys
    from pathlib import Path
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from soo.auth import get_credentials, build_services
    ROOT = Path(__file__).resolve().parents[2]
    sheets = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))["sheets"]

    po = parse_po_qty(sheets)
    nz = {k: v for k, v in po.items() if v["po"]["t"]}
    print(f"PO수량 보유 스타일 {len(nz)}개 (전체 {len(po)})")
    print(f"{'style':12} {'발주':>9} {'온':>9} {'오프':>9}  PO넘버 / 소싱")
    for k, v in sorted(nz.items())[:15]:
        p = v["po"]
        print(f"{k:12} {p['t']:9,} {p['o']:9,} {p['f']:9,}  {v['po_no'] or '-'} / {v['sourcing'] or '-'}")
