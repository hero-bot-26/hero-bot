"""오더시트(무탠본부_오더시트)에서 신품번×컬러별 발주수량(=준비물량) 집계.

컬러행 소진율(=YTD판매수량/발주수량)·달성율(=기간판매수량/안분목표)용.
매출 대시보드의 컬러 키('한글(코드)', sales_rollup.color_display)와 동일 규칙으로 매칭한다.
판매량 가중 매칭 커버리지 ~98% (개수 ~87%; 미매칭은 자투리색·통합UID 복합색).

  MD투입 탭 컬럼(0-base): 0=최종품번(구코드, 매출 style_no와 동일 포맷) · 27=CL(컬러코드)
                          · 29=CL명(국문) · 30=내수 발주수량
  컬러구분 탭: 컬러코드↔한글 크로스워크 (2=CODE_영문약자, 4=한글표기)
"""
from __future__ import annotations

import datetime
import re
from collections import defaultdict

from soo.hero_ops.sales_rollup import _base, color_display, COLOR_KO

ORDER_SHEET_ID = "13R4gcJ7cDlReY-vwjXZf0kMZ7tC4kr2-S7PC9uziVUQ"

# MD투입 탭 컬럼(0-base): 30=내수 발주수량(총) · 34=타겟시즌 · 39=내수 온라인 · 40=내수 오프라인
#   (검증 2026-06-23: col30 == col39+col40 정확히 일치 → t=30, o=39, f=40)
_C_QTY, _C_TGT_SEASON, _C_QTY_ON, _C_QTY_OFF = 30, 34, 39, 40
_SEASON_RE = re.compile(r"(\d{2})(\d{2})(SS|FW)")


def _norm_season(s) -> str:
    """오더시트 타겟시즌 '2026SS' → 앱 라벨 '26SS'. 못 알아보면 '' (집계 제외)."""
    m = _SEASON_RE.match(str(s).strip().upper().replace(" ", ""))
    return (m.group(2) + m.group(3)) if m else ""


def current_season(today) -> str:
    """오늘 → 현재 시즌 라벨 '26SS'/'26FW'. 2~7월=SS, 8~12월=FW(올해), 1월=FW(작년 8월 시즌 연장)."""
    d = today if isinstance(today, datetime.date) else datetime.date.fromisoformat(str(today))
    if 2 <= d.month <= 7:
        yy, ss = d.year, "SS"
    elif d.month >= 8:
        yy, ss = d.year, "FW"
    else:                          # 1월
        yy, ss = d.year - 1, "FW"
    return f"{yy % 100:02d}{ss}"


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


def parse_orders(sheets, code2kor, kor2code, season=None):
    """returns (color_prep, style_prep):
      color_prep[(base, '한글(코드)')] = 발주수량 합 (내수 총; 컬러별 준비물량 = 소진율 분모)
      style_prep[base]                = {'t','o','f'} (스타일 총 준비물량; 채널별, 달성율 안분 분모는 't')
    season 지정 시 타겟시즌(col34)==season 행만 집계(타시즌·미래발주 제외).
    season=None 이면 전 시즌 누적(구 동작 호환). base = _base(최종품번)."""
    color_prep = defaultdict(float)
    style_prep = defaultdict(lambda: {"t": 0.0, "o": 0.0, "f": 0.0})
    for r in _vals(sheets, "'MD투입'!A8:AO26529"):
        base = _base(_g(r, 0))
        if not base:
            continue
        if season is not None and _norm_season(_g(r, _C_TGT_SEASON)) != season:
            continue
        qty = _num(r, _C_QTY)
        if qty <= 0:
            continue
        cl = _g(r, 27)
        if cl.startswith("-"):       # 반품/조정 행 스킵
            continue
        disp = color_display(cl, _g(r, 29), code2kor, kor2code)
        color_prep[(base, disp)] += qty
        sp = style_prep[base]
        sp["t"] += qty
        sp["o"] += _num(r, _C_QTY_ON)
        sp["f"] += _num(r, _C_QTY_OFF)
    return dict(color_prep), dict(style_prep)
