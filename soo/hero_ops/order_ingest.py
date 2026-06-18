"""오더시트(무탠본부_오더시트)에서 신품번×컬러별 발주수량(=준비물량) 집계.

컬러행 소진율(=YTD판매수량/발주수량)·달성율(=기간판매수량/안분목표)용.
매출 대시보드의 컬러 키('한글(코드)', sales_rollup.color_display)와 동일 규칙으로 매칭한다.
판매량 가중 매칭 커버리지 ~98% (개수 ~87%; 미매칭은 자투리색·통합UID 복합색).

  MD투입 탭 컬럼(0-base): 0=최종품번(구코드, 매출 style_no와 동일 포맷) · 27=CL(컬러코드)
                          · 29=CL명(국문) · 30=내수 발주수량
  컬러구분 탭: 컬러코드↔한글 크로스워크 (2=CODE_영문약자, 4=한글표기)
"""
from __future__ import annotations

from collections import defaultdict

from soo.hero_ops.sales_rollup import _base, color_display, COLOR_KO

ORDER_SHEET_ID = "13R4gcJ7cDlReY-vwjXZf0kMZ7tC4kr2-S7PC9uziVUQ"


def _g(r, i):
    return str(r[i]).strip() if i < len(r) and r[i] is not None else ""


def _num(r, i):
    v = r[i] if i < len(r) else None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _vals(sheets, rng):
    return sheets.spreadsheets().values().get(
        spreadsheetId=ORDER_SHEET_ID, range=rng,
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])


def load_color_maps(sheets):
    """컬러 크로스워크 (code2kor, kor2code). COLOR_KO + 오더시트 '컬러구분' 탭(1000여개)."""
    code2kor, kor2code = {}, {}
    for c, k in COLOR_KO.items():
        code2kor.setdefault(c, k)
        kor2code.setdefault(k, c)
    for r in _vals(sheets, "'컬러구분'!A1:G2129")[2:]:    # 1~2행=헤더
        code = _g(r, 2).upper()
        kor = _g(r, 4).replace(" ", "")
        if code and kor:
            code2kor.setdefault(code, kor)
            kor2code.setdefault(kor, code)
    return code2kor, kor2code


def parse_orders(sheets, code2kor, kor2code):
    """returns (color_prep, style_prep):
      color_prep[(base, '한글(코드)')] = 발주수량 합  (컬러별 준비물량)
      style_prep[base]                = 발주수량 합  (스타일 총 준비물량; 달성율 안분 분모)
    base = _base(최종품번). 모든 시즌/오더차수 누적(상시 운영 스타일이라 누적 준비물량)."""
    color_prep = defaultdict(float)
    style_prep = defaultdict(float)
    for r in _vals(sheets, "'MD투입'!A8:AE26529"):
        base = _base(_g(r, 0))
        if not base:
            continue
        qty = _num(r, 30)
        if qty <= 0:
            continue
        cl = _g(r, 27)
        if cl.startswith("-"):       # 반품/조정 행 스킵
            continue
        disp = color_display(cl, _g(r, 29), code2kor, kor2code)
        color_prep[(base, disp)] += qty
        style_prep[base] += qty
    return dict(color_prep), dict(style_prep)
