# -*- coding: utf-8 -*-
"""26FW 목표 셋팅(.xlsx) → IMC 운영계획 시트2의 주차별 **목표 판매량** 자동 주입.

원천: `26FW HERO 일자별 목표 셋팅` (박은진님, **.xlsx**) — Drive에 Office 파일로 있다.
      xlsx라 IMPORTRANGE 대상이 안 되므로(구글시트 네이티브만 지원) 매 실행마다
      get_media 로 내려받아 openpyxl 로 캐시값을 읽는다. 즉 **원본 파일을 고치면 다음 실행에 반영**된다.
      (원본을 '새 파일로 업로드'하면 파일 ID가 바뀌어 옛 파일을 계속 읽게 되니 그때만 SRC_FILE_ID 교체.)

타깃: `무신사스탠다드 히어로 IMC 운영 계획 - 26FW` 시트2 — 주차 열 I~AH(마감일 7/5~12/27, 26주)의
      히어로 블록 **`목표 판매량` 행만** 쓴다(R14/19/24/29/34/39/44/49/54).

★ 금액 칸은 절대 건드리지 않는다 (2026-07-29 결정).
  시트2에는 금액 칸이 두 종류 섞여 있다.
    - 히어로 블록의 `목표 매출액` 행(R15/20/…)
    - 오프라인·온라인 `주차별 목표` 블록(R57~68 / R105~115) — 서식 `#,###,,` = **원 단위 저장·백만원 표시**.
      (수량을 넣으면 화면상 0으로 보여 값이 안 들어간 것처럼 됨. 실제로 그 사고를 한 번 겪었다.)
  이 자동화는 수량(pcs)만 다루므로 위 영역은 읽지도 쓰지도 않는다.

★ 원천 두 탭이 일부 어긋난다 (2026-07-29 확인):
    `주차별 목표 셋팅`(→ `목표 그래프`) = 산출 원본. 자연PLC → 매출비 → 조정 주간매출 → 목표 판매량.
    `일자별 목표 셋팅`          = 그 주차 목표를 일자로 배분한 파생물.
  커브드 팬츠 **오프라인**만 7월 1~4주에서 일자별이 주차별보다 636~709 적고, 7월5주 이후로는
  매주 정확히 +84 많다(합계 -762 = 0.5%). 나머지 5개 시리즈는 온·오프 전 주차 일치.
  → 주차 그리드에 넣을 값이므로 **정본인 주차별을 기본(--source weekly)** 으로 쓴다.

★ 주차 정렬 함정: 원천 주차 *라벨*("7월2주")과 시트2 주차 열은 한 칸씩 어긋난다
  (원천 7월1주 = 7/1~7/5 = 시트2 I열, 원천 7월2주 = 7/6~7/12 = 시트2 J열).
  라벨 순서로 붙이면 전 구간이 밀리므로 **주 마감일(끝 날짜)로 매칭**한다.
  시트2의 한 주 = [마감일-6, 마감일]. 원천 12월5주(12/28~12/31)는 시트2 범위 밖이라 버린다.

★ 커버리지: 원천 시리즈는 6종(커브드 팬츠/빅토리아 울/라이트다운/그리드·메시 플리스/에센셜 플리스/웜 팬츠).
  시트2의 **힛탠다드·리커버리는 원천에 없어** 건드리지 않는다(빈칸 유지). TOTAL 행도 6종 합계다.
  또 원천은 시리즈별 **핵심 스타일만** 대상이다(예: 커브드 6스타일·준비 328,731 vs 시트2 신규입고 723,900)
  — 목표수량과 준비물량의 모수가 다르므로 소진율을 이 둘로 계산하면 안 된다.

안전 규칙(0으로 덮어쓰는 사고 방지):
  1. 원천 파싱 결과가 비었거나 전부 0이면 **아무것도 쓰지 않고 중단**한다.
  2. 시트2의 행 라벨(C열 히어로명·D열 지표명)을 매번 검증해 레이아웃이 바뀌었으면 중단한다.
  3. 원천에 없는 히어로 행은 스킵(빈칸 유지) — 0을 채워 '목표 0'처럼 보이게 하지 않는다.
  4. 주차별 탭의 시리즈 집합이 일자별 탭과 다르면 중단(한쪽에만 추가된 시리즈를 놓치지 않기 위함).
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import re
import sys
from pathlib import Path

# ── 원천/타깃 ────────────────────────────────────────────────────────────────
SRC_FILE_ID = "1CB10ouLsOZplJuPoSOkkAXhvzwgtR0zD"     # 26FW HERO 일자별 목표 셋팅 (.xlsx)
SRC_TAB = "일자별 목표 셋팅"
SRC_WEEK_TAB = "목표 그래프"     # 주차별 목표(정본)의 시리즈×주차×ON/OFF 정리본

IMC_SHEET_ID = "1jDRvZncF0D2RoeCGdxNso3wrUz09BQPk4a7JEhs1ElQ"
IMC_TAB = "시트2"

SEASON_YEAR = 2026

# ── 원천 레이아웃 (일자별 목표 셋팅) ─────────────────────────────────────────
SRC_COL0 = 8            # H열부터 스타일×채널 블록
SRC_R_SERIES, SRC_R_LINE, SRC_R_CH, SRC_R_STYLE = 5, 6, 7, 8
SRC_DAILY_ROW0 = 18     # 일자 그리드 시작
SRC_C_WEEK, SRC_C_DATE = 6, 7          # F열 = 주차 라벨, G열 = 날짜("07월 01일" 텍스트)

# ── 타깃 레이아웃 (시트2) ────────────────────────────────────────────────────
WEEK_ROW = 7                    # 마감일 행
WEEK_C0, WEEK_C1 = 9, 34        # I~AH (1-indexed, 26주)

# 히어로 블록: (C열 히어로 라벨, 목표판매량 행). 금액 행(목표 매출액)은 의도적으로 제외.
BLOCKS = [
    ("TOTAL", 14),
    ("커브드팬츠", 19),
    ("라이트다운", 24),
    ("힛탠다드", 29),
    ("빅토리아 울", 34),
    ("그리드/메시 플리스", 39),
    ("에센셜 플리스", 44),
    ("웜 팬츠", 49),
    ("리커버리", 54),
]

# 표기 흔들림 흡수: 공백 제거 후 비교 + 별칭
ALIAS = {"샤기/플러피플리스": "에센셜플리스"}
_EPOCH = dt.date(1899, 12, 30)          # 구글시트 날짜 시리얼 기준
_DATE_RE = re.compile(r"(\d{1,2})\D+(\d{1,2})")


def norm(s) -> str:
    """히어로/시리즈명 정규화 — 공백 전부 제거 후 별칭 치환."""
    k = re.sub(r"\s+", "", str(s or ""))
    return ALIAS.get(k, k)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _to_date(v):
    """원천 G열 → date. '07월 01일' 텍스트와 datetime 둘 다 받는다."""
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    m = _DATE_RE.search(str(v or ""))
    if not m:
        return None
    try:
        return dt.date(SEASON_YEAR, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _col_letter(idx: int) -> str:
    """1-indexed 열 번호 → A1 표기."""
    s = ""
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


# ── 1) 원천 읽기 ─────────────────────────────────────────────────────────────
def load_source(drive):
    """xlsx를 내려받아 (일자별 열, 주차별 목표, 주차 구간) 파싱.

    반환:
      cols     : [{series, line, ch, style, daily:{date: qty}}]        (일자별 탭)
      weekly   : {(series_norm, ch): {주차라벨: qty}}                   (목표 그래프 탭)
      wk_range : {주차라벨: (시작일, 종료일)}                            (일자별 탭 F/G열)
    """
    import openpyxl
    from googleapiclient.http import MediaIoBaseDownload

    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, drive.files().get_media(fileId=SRC_FILE_ID,
                                                          supportsAllDrives=True),
                             chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = dl.next_chunk()
    buf.seek(0)

    wb = openpyxl.load_workbook(buf, data_only=True)
    for tab in (SRC_TAB, SRC_WEEK_TAB):
        if tab not in wb.sheetnames:
            raise RuntimeError(f"원천에 '{tab}' 탭이 없습니다 (탭: {wb.sheetnames})")

    # --- 일자별 탭 ---
    ws = wb[SRC_TAB]
    cols = []
    for c in range(SRC_COL0, ws.max_column + 1):
        series, ch = ws.cell(SRC_R_SERIES, c).value, ws.cell(SRC_R_CH, c).value
        if not series or not ch:
            continue
        cols.append({"c": c, "series": str(series).strip(),
                     "line": str(ws.cell(SRC_R_LINE, c).value or "").strip(),
                     "ch": str(ch).strip().lower(),
                     "style": str(ws.cell(SRC_R_STYLE, c).value or "").strip(),
                     "daily": {}})

    wk_range: dict[str, tuple[dt.date, dt.date]] = {}
    for r in range(SRC_DAILY_ROW0, ws.max_row + 1):
        d = _to_date(ws.cell(r, SRC_C_DATE).value)
        if d is None:
            continue
        wk = str(ws.cell(r, SRC_C_WEEK).value or "").strip()
        if wk:
            a, b = wk_range.get(wk, (d, d))
            wk_range[wk] = (min(a, d), max(b, d))
        for col in cols:
            q = _num(ws.cell(r, col["c"]).value)
            if q:
                col["daily"][d] = col["daily"].get(d, 0.0) + q

    # --- 목표 그래프 탭: [시리즈명] / [주차|ONLINE|OFFLINE|TOTAL] / 주차행... 블록 반복 ---
    g = wb[SRC_WEEK_TAB]
    weekly: dict[tuple[str, str], dict[str, float]] = {}
    for r in range(1, g.max_row + 1):
        name = g.cell(r, 1).value
        if not name or str(g.cell(r + 1, 1).value or "").strip() != "주차":
            continue
        key = norm(name)
        rr = r + 2
        while True:
            wk = str(g.cell(rr, 1).value or "").strip()
            if not wk.endswith("주"):
                break
            weekly.setdefault((key, "online"), {})[wk] = _num(g.cell(rr, 2).value)
            weekly.setdefault((key, "offline"), {})[wk] = _num(g.cell(rr, 3).value)
            rr += 1

    # 두 탭의 시리즈 집합이 어긋나면 중단 (한쪽에만 추가된 시리즈를 조용히 흘리지 않기 위함)
    s_daily = {norm(c["series"]) for c in cols}
    s_week = {k[0] for k in weekly}
    if s_daily != s_week:
        raise RuntimeError(
            f"원천 두 탭의 시리즈가 다릅니다 — 주입 중단.\n"
            f"  일자별만: {sorted(s_daily - s_week)}\n  주차별만: {sorted(s_week - s_daily)}")
    return cols, weekly, wk_range


# ── 2) 주차 경계 (시트2 마감일) ──────────────────────────────────────────────
def load_weeks(sheets) -> list[tuple[dt.date, dt.date]]:
    """시트2 R7의 마감일 → [(주 시작, 주 마감)] 26개. 한 주 = [마감일-6, 마감일]."""
    rng = f"'{IMC_TAB}'!{_col_letter(WEEK_C0)}{WEEK_ROW}:{_col_letter(WEEK_C1)}{WEEK_ROW}"
    row = (sheets.spreadsheets().values().get(
        spreadsheetId=IMC_SHEET_ID, range=rng,
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [[]]) or [[]])[0]

    weeks = []
    for v in row:
        n = _num(v)
        if n <= 0:
            raise RuntimeError(f"시트2 {WEEK_ROW}행 마감일에 날짜가 아닌 값이 있습니다: {v!r}")
        end = _EPOCH + dt.timedelta(days=int(n))
        weeks.append((end - dt.timedelta(days=6), end))
    if len(weeks) != WEEK_C1 - WEEK_C0 + 1:
        raise RuntimeError(f"마감일 개수가 {len(weeks)}개 — {WEEK_C1 - WEEK_C0 + 1}주를 기대했습니다.")
    return weeks


def map_weeks(weeks, wk_range) -> list[str | None]:
    """시트2 주차 → 원천 주차 라벨. 주 마감일(끝 날짜)이 같은 것을 붙인다."""
    by_end = {b: wk for wk, (a, b) in wk_range.items()}
    out = [by_end.get(end) for _, end in weeks]
    if not any(out):
        raise RuntimeError("시트2 주차와 원천 주차가 하나도 매칭되지 않습니다 — 주입 중단.")
    return out


# ── 3) 레이아웃 검증 ────────────────────────────────────────────────────────
def verify_layout(sheets) -> None:
    """행 라벨이 기대와 다르면 즉시 중단 — 엉뚱한 행에 숫자를 쓰는 사고 방지."""
    vals = sheets.spreadsheets().values().get(
        spreadsheetId=IMC_SHEET_ID, range=f"'{IMC_TAB}'!A1:D60").execute().get("values", [])

    def cell(r, c):
        row = vals[r - 1] if r - 1 < len(vals) else []
        return str(row[c - 1]).strip() if c - 1 < len(row) else ""

    bad = []
    for hero, qty_r in BLOCKS:
        if norm(cell(qty_r - 2, 3)) != norm(hero):      # 블록 첫 행(C열)에 히어로명
            bad.append(f"R{qty_r - 2} C열 히어로명 '{cell(qty_r - 2, 3)}' ≠ '{hero}'")
        if cell(qty_r, 4) != "목표 판매량":
            bad.append(f"R{qty_r} D열 '{cell(qty_r, 4)}' ≠ '목표 판매량'")
    if bad:
        raise RuntimeError("시트2 레이아웃이 바뀌었습니다 — 주입 중단:\n  " + "\n  ".join(bad))


# ── 4) 집계 ─────────────────────────────────────────────────────────────────
def aggregate(cols, weekly, wk_map, weeks, mode):
    """{시리즈: 주차별 목표수량 리스트} — 온·오프 합산. 'TOTAL' 키에 전체 합."""
    n = len(weeks)
    series_all = sorted({norm(c["series"]) for c in cols})
    out: dict[str, list[float]] = {}

    for s in series_all:
        arr = [0.0] * n
        for ch in ("online", "offline"):
            for wi, (a, b) in enumerate(weeks):
                if mode == "weekly":
                    lbl = wk_map[wi]
                    arr[wi] += weekly.get((s, ch), {}).get(lbl, 0.0) if lbl else 0.0
                else:
                    arr[wi] += sum(v for col in cols
                                   if norm(col["series"]) == s and col["ch"] == ch
                                   for d, v in col["daily"].items() if a <= d <= b)
        if any(arr):
            out[s] = arr

    total = [0.0] * n
    for arr in out.values():
        for i, v in enumerate(arr):
            total[i] += v
    if any(total):
        out["TOTAL"] = total
    return out


# ── 5) 주입 ─────────────────────────────────────────────────────────────────
def build_updates(qty) -> tuple[list[dict], list[str]]:
    rng0, rng1 = _col_letter(WEEK_C0), _col_letter(WEEK_C1)
    data, skipped = [], []
    for hero, row in BLOCKS:
        key = norm(hero)
        if key not in qty:
            skipped.append(hero)
            continue
        data.append({"range": f"'{IMC_TAB}'!{rng0}{row}:{rng1}{row}",
                     "values": [[round(v) for v in qty[key]]]})
    return data, skipped


def main() -> int:
    from soo.auth import build_services, get_credentials

    # 로컬 콘솔이 cp949라 ⚠ 같은 기호에서 죽는 것 방지 (CI는 UTF-8이라 무해).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="26FW 목표 셋팅 → IMC 시트2 주차별 목표 판매량 주입")
    ap.add_argument("--source", choices=["weekly", "daily"], default="weekly",
                    help="weekly=주차별 목표(정본, 기본) / daily=일자별 목표를 주간 합산")
    ap.add_argument("--dry-run", action="store_true", help="시트에 쓰지 않고 결과만 출력")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[2]
    svc = build_services(get_credentials(root / "credentials.json", root / "token.json"))
    sheets, drive = svc["sheets"], svc["drive"]

    verify_layout(sheets)
    weeks = load_weeks(sheets)
    cols, weekly, wk_range = load_source(drive)
    if not cols:
        raise RuntimeError("원천에서 스타일×채널 열을 하나도 못 읽었습니다 — 주입 중단.")
    if sum(sum(c["daily"].values()) for c in cols) <= 0:
        raise RuntimeError("원천 목표수량 합계가 0입니다 — 주입 중단(0으로 덮어쓰기 방지).")

    wk_map = map_weeks(weeks, wk_range)
    qty = aggregate(cols, weekly, wk_map, weeks, args.source)
    data, skipped = build_updates(qty)

    unmapped = [str(e) for (_, e), m in zip(weeks, wk_map) if m is None]
    dropped = sorted(set(wk_range) - {m for m in wk_map if m})
    print(f"소스 기준: {args.source} · 원천 열 {len(cols)}개 · 주차 {len(weeks)}주 "
          f"({weeks[0][0]}~{weeks[-1][1]}) · 갱신 행 {len(data)}개 (목표 판매량만)")
    if unmapped:
        print(f"  ⚠ 원천 주차를 못 찾은 시트2 주차: {', '.join(unmapped)}")
    if dropped:
        print(f"  · 시트2 범위 밖이라 버린 원천 주차: {', '.join(dropped)}")
    if skipped:
        print(f"  ⚠ 원천에 없어 건드리지 않음: {', '.join(skipped)}")
    for hero, row in BLOCKS:
        key = norm(hero)
        if key in qty:
            arr = qty[key]
            print(f"  R{row:<3} {hero:<16} {sum(arr):>9,.0f} pcs"
                  f"  (첫주 {arr[0]:>6,.0f} / 끝주 {arr[-1]:>6,.0f})")

    if args.dry_run:
        print("\n[dry-run] 시트에 쓰지 않았습니다.")
        return 0

    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=IMC_SHEET_ID,
        body={"valueInputOption": "RAW", "data": data}).execute()
    print(f"\n✅ 시트2 목표 판매량 주입 완료 — {len(data)}개 행 × {len(weeks)}주 (금액 칸 미변경)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
