# -*- coding: utf-8 -*-
"""목표 진실소스 통일 — xlsx `26FW HERO 일자별 목표 셋팅` → 대시보드 `히어로목표(거래량)` 탭.

배경(2026-08-10): 같은 '26FW 목표'를 두 곳이 따로 들고 있었다.
  · 앱   = Drive xlsx `26FW HERO 일자별 목표 셋팅` (담당자가 실제로 관리하는 판)
  · 시트 = 대시보드 `히어로목표(거래량)` 탭 — ★IMPORTRANGE 가 아니라 **손으로 박은 값**
같은 창(8/3~8/9)으로 대조하니 커브드팬츠 WEEK 목표가 3,629 vs 3,636 으로 갈렸다.
담당자가 xlsx 만 고치면 대시보드는 영원히 옛 값을 보여준다. → **xlsx 를 정본으로 고정**하고
이 도구가 시트를 따라오게 만든다.

★안전 규칙 (전부 실제 사고 이력에서 나온 것)
  1. **소문자 `online`/`offline` 블록만** 건드린다. 대문자 `Online`/`Offline` 블록은 2~6월
     26SS 기간 목표라 xlsx 에 없다 — 덮으면 상반기 목표가 통째로 0 이 된다.
  2. **열·행을 추가하거나 지우지 않는다**(열을 끼워 넣으면 품목 탭 SUMPRODUCT 범위가 밀린다).
     ★2026-10-06: 시트에 열이 없는 품번×채널은 **수식 범위 안의 빈 여유 열에 헤더를 써서 자동 배치**한다.
       기존엔 '미배치' 로그 한 줄만 남기고 건너뛰어서, 리커버리 8종·힛탠다드 10종 목표가 9/1 부터
       한 달 넘게 대시보드에서 0 이었다(9/1 MKFUTBK06 에 이은 두 번째). 여유 열이 모자라면
       배치할 수 있는 만큼만 하고 **exit 2** → CI 가 슬랙 DM 을 보낸다(조용히 넘어가지 않게).
     ★수식 범위 끝열은 상수가 아니라 **품목 탭 수식에서 실측**한다(상수와 시트가 갈리면 새 열을 못 읽는다).
  3. 값이 같은 셀은 건드리지 않는다 → 재실행하면 **0건**(멱등).
  4. `--dry` 가 기본. `--apply` 시 직전값을 JSON 으로 백업하고, 쓴 뒤 되읽어 검증한다.

    python _sync_target_raw.py            # 대조만
    python _sync_target_raw.py --apply    # 기입
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from soo.auth import build_services, get_credentials                      # noqa: E402
from soo.hero_ops import target_26fw as T                                 # noqa: E402

DASH_SID = "1-A04_TwKZJNPkFg27USkKAScZRu6CAhbgVeXk9c09nA"   # 26FW 히어로 실적 대시보드
RAW_TAB = "히어로목표(거래량)"
R_CHANNEL, R_STYLE, R_DAILY_FROM, R_DAILY_TO = 2, 3, 14, 378
C_DATE = 2                       # B열 = 일자(시리얼)
FALLBACK_LAST_COL = "HH"         # 실측 실패 시에만 쓰는 값 (2026-09-01 GV→HH, 10-06 HH→JV)
# ★2026-10-06: 상수 대신 품목 탭 수식 `'히어로목표(거래량)'!$C$3:$XX$3` 의 XX 를 실측한다.
#   예전엔 이 상수가 시트 범위보다 좁으면 새 열을 못 읽어 **조용히 '미배치'로 건너뛰었다**.
GOAL_REF_RE = re.compile(r"'히어로목표\(거래량\)'!\$C\$3:\$([A-Z]{1,3})\$3")
LABEL_ALL, LABEL_MAIN = "HERO+SUB", "HERO"   # 품목 탭 A12 / A13 (주간 리포트와 같은 탐지 규칙)
AUTO_NOTE = "(자동배치 {d})"                 # 자동 배치 열의 4행(상품명 자리) 표식
R_NAME = 4


def col_idx(name: str) -> int:
    n = 0
    for ch in name:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def formula_last_col(sheets) -> str:
    """품목 탭(A12='HERO+SUB'·A13='HERO')의 목표 수식이 훑는 끝열을 실측. 탭마다 다르면 중단."""
    meta = sheets.spreadsheets().get(spreadsheetId=DASH_SID, fields="sheets.properties.title").execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]
    lab = sheets.spreadsheets().values().batchGet(
        spreadsheetId=DASH_SID, ranges=[f"'{t}'!A12:A13" for t in titles],
        valueRenderOption="UNFORMATTED_VALUE").execute()["valueRanges"]
    items = []
    for t, vr in zip(titles, lab):
        a = [str(x[0]).strip() if x else "" for x in vr.get("values", [])] + ["", ""]
        if a[0] == LABEL_ALL and a[1] == LABEL_MAIN:
            items.append(t)
    fr = sheets.spreadsheets().values().batchGet(
        spreadsheetId=DASH_SID, ranges=[f"'{t}'!A14:IE40" for t in items],
        valueRenderOption="FORMULA").execute()["valueRanges"]
    ends = {}
    for t, vr in zip(items, fr):
        found = {m for r in vr.get("values", []) for c in r for m in GOAL_REF_RE.findall(str(c))}
        if found:
            ends[t] = found
    allv = set().union(*ends.values()) if ends else set()
    if not allv:
        print(f"[범위] 품목 탭 목표 수식을 못 찾음 — 기본값 {FALLBACK_LAST_COL} 사용")
        return FALLBACK_LAST_COL
    if len(allv) > 1:
        raise RuntimeError(f"품목 탭마다 목표 범위 끝열이 다릅니다 — 범위부터 맞출 것: "
                           f"{ {t: sorted(v) for t, v in ends.items()} }")
    last = allv.pop()
    print(f"[범위] 품목 탭 {len(ends)}개 목표 수식 끝열 = {last} (실측)")
    return last


def col_name(idx0: int) -> str:
    s, i = "", idx0 + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def serial(d: datetime.date) -> int:
    return (d - datetime.date(1899, 12, 30)).days


def xlsx_daily(drive) -> tuple[dict, tuple]:
    """xlsx → {(base, 'online'|'offline'): {date: qty}} + 날짜 범위."""
    import openpyxl
    wb = openpyxl.load_workbook(T._download(drive, T.TARGET_FID), read_only=False, data_only=True)
    ws = wb[T.TARGET_TAB]
    loc = T._locate(ws)
    r_chan, r_style = loc["_R_CHANNEL"], loc["_R_STYLE"]
    c_label, c_data, r_daily = loc["_C_LABEL"], loc["_C_DATA_FROM"], loc["_R_DAILY_FROM"]

    colmeta = {}
    for c in range(c_data, ws.max_column + 1):
        sty = str(ws.cell(r_style, c).value or "").strip()
        if not T.STYLE_RE.match(sty):
            continue
        chan = str(ws.cell(r_chan, c).value or "").strip().lower()
        if chan.startswith("online"):
            colmeta[c] = (T._base(sty), "online")
        elif chan.startswith("offline"):
            colmeta[c] = (T._base(sty), "offline")
    if not colmeta:
        raise ValueError("xlsx 품번×채널 열을 못 찾음 — 레이아웃 변경 의심")

    # ★★2026-08-26 사고: 같은 (품번,채널)이 **여러 열 블록**으로 들어온다(원 물량 + 리오더 추가 물량).
    #   여기서 열마다 `out[key][d] = v` 로 **덮어써서 뒤 블록만 남았고**, 뒤 블록은 10~12월에만
    #   값이 있어 **라이트다운 8월 목표 6,075 가 통째로 0** 이 됐다(대시보드 달성율 공란).
    #   앱이 쓰는 `target_26fw.parse_26fw_targets` 는 처음부터 `+=` 로 누적해 정상이었다
    #   → 같은 xlsx 를 읽는 두 파서가 갈렸다. 일자별로 **합산**이 정답(사용자 확정).
    from collections import defaultdict as _dd
    _grp = _dd(list)
    for c, k in colmeta.items():
        _grp[k].append(c)
    _dups = {k: v for k, v in _grp.items() if len(v) > 1}
    if _dups:
        print(f"[중복열] 같은 (품번,채널)이 여러 열 — 일자별 합산 {len(_dups)}조합:")
        for k, cs in sorted(_dups.items())[:12]:
            print(f"    {k[0]} {k[1]}: 열 {cs}")

    out, dates = {}, []
    year_from = None
    for r in range(r_daily, ws.max_row + 1):
        v = ws.cell(r, c_label).value
        if v is None:
            continue
        if year_from is None:
            year_from = 2026
        d = T._cell_date(v, year_from)
        if not d:
            continue
        dates.append(d)
        for c, key in colmeta.items():
            _o = out.setdefault(key, {})
            # ★덮어쓰기 금지 — 같은 (품번,채널)의 여러 열 블록을 일자별로 더한다(위 주석 참조).
            _o[d] = _o.get(d, 0) + (T._num(ws.cell(r, c).value) or 0)
    return out, (min(dates), max(dates))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    sv = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sheets, drive = sv["sheets"], sv["drive"]

    src, (d_from, d_to) = xlsx_daily(drive)
    styles = sorted({b for b, _ in src})
    print(f"[xlsx] 품번 {len(styles)}개 × 채널 / 일자 {d_from} ~ {d_to}")

    last_col = formula_last_col(sheets)
    width = col_idx(last_col) + 1
    grid = sheets.spreadsheets().values().get(
        spreadsheetId=DASH_SID, range=f"'{RAW_TAB}'!A1:{last_col}{R_DAILY_TO}",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])

    def row(n):
        r = grid[n - 1] if len(grid) >= n else []
        return r + [""] * (width - len(r))

    chan_row, style_row = row(R_CHANNEL), row(R_STYLE)
    # ★소문자 블록만. 대문자는 26SS 상반기라 건드리면 안 된다.
    dest = {}
    for ci, sty in enumerate(style_row):
        s = str(sty).strip()
        if not T.STYLE_RE.match(s):
            continue
        ch = str(chan_row[ci]).strip()
        if ch in ("online", "offline"):
            dest[(T._base(s), ch)] = ci

    row_of_date = {}
    for rn in range(R_DAILY_FROM, R_DAILY_TO + 1):
        v = row(rn)[C_DATE - 1]
        if isinstance(v, (int, float)) and v:
            row_of_date[datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v))] = rn

    # ── 미배치 → 여유 열 자동 배치 ──────────────────────────────────────────
    #   여유 열 = 마지막 품번 열 **뒤** ~ 수식 범위 끝(last_col) 사이에서 2·3행 헤더와 4~378행이 전부 빈 열.
    #   빈 열은 2행(채널)이 비어 SUMPRODUCT 곱이 0 이라, 헤더를 쓰는 순간부터 집계에 들어간다.
    #   열 위치는 안 바뀌므로 다른 품번에는 영향이 없다.
    #   ★중간의 빈 열(예: DJ = 26SS 대문자 블록과 26FW 소문자 블록 사이 구분열)은 쓰지 않는다.
    missing = sorted(k for k in src if k not in dest)
    last_used = max((ci for ci in range(width) if str(style_row[ci]).strip()), default=1)
    spare = [ci for ci in range(last_used + 1, width)
             if not str(chan_row[ci]).strip() and not str(style_row[ci]).strip()
             and all(not str(row(rn)[ci]).strip() for rn in range(R_NAME, R_DAILY_TO + 1))]
    placed = list(zip(missing, spare))
    unplaced = missing[len(placed):]
    headers = []
    if missing:
        today = datetime.date.today().isoformat()
        print(f"[자동배치] 시트에 열이 없는 품번×채널 {len(missing)}건 → 여유 열 {len(spare)}개 중 "
              f"{len(placed)}개에 배치")
        for (b, c), ci in placed:
            print(f"    {col_name(ci):>3}  {b} {c}")
            dest[(b, c)] = ci
            headers.append({"range": f"'{RAW_TAB}'!{col_name(ci)}{R_CHANNEL}:{col_name(ci)}{R_NAME}",
                            "values": [[c], [b], [AUTO_NOTE.format(d=today)]]})
        if unplaced:
            print(f"★[미배치] 여유 열이 모자라 {len(unplaced)}건을 못 넣었습니다 — "
                  f"그리드 열과 품목 탭 수식 범위(${last_col}$)를 넓혀야 합니다:")
            for b, c in unplaced:
                print(f"    {b} {c}")
    print(f"[여유] 배치 후 남은 여유 열 {len(spare) - len(placed)}개 (범위 끝 {last_col})")
    rc = 2 if unplaced else 0

    updates, diffs, same = [], [], 0
    for key, daily in src.items():
        ci = dest.get(key)
        if ci is None:
            continue
        for d, want in daily.items():
            rn = row_of_date.get(d)
            if rn is None:
                continue
            cur = row(rn)[ci]
            cur = float(cur) if isinstance(cur, (int, float)) else 0.0
            if abs(cur - float(want)) < 1e-9:
                same += 1
                continue
            a1 = f"'{RAW_TAB}'!{col_name(ci)}{rn}"
            updates.append({"range": a1, "values": [[want]]})
            diffs.append((key[0], key[1], d.isoformat(), cur, want))

    print(f"\n[대조] 일치 {same:,}셀 / 불일치 {len(diffs):,}셀")
    if diffs:
        by_style = {}
        for b, c, d, cur, want in diffs:
            k = by_style.setdefault(b, {"n": 0, "cur": 0.0, "want": 0.0, "first": d, "last": d})
            k["n"] += 1; k["cur"] += cur; k["want"] += want
            k["first"] = min(k["first"], d); k["last"] = max(k["last"], d)
        print(f"{'품번':12} {'셀':>6} {'현재합':>10} {'xlsx합':>10}   기간")
        for b, v in sorted(by_style.items()):
            print(f"  {b:12} {v['n']:>6} {v['cur']:>10,.0f} {v['want']:>10,.0f}   {v['first']}~{v['last']}")

    if not updates and not headers:
        print("\n[OK] 시트가 이미 xlsx 와 같습니다 (멱등).")
        return rc
    if not args.apply:
        print("\n드라이런입니다. 실제로 기입하려면 --apply 를 붙이세요.")
        return rc

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = ROOT / f"_target_raw_backup_{DASH_SID[:8]}_{stamp}.json"
    bak.write_text(json.dumps({"headers_added": [h["range"] for h in headers],
                               "cells": [{"range": u["range"], "prev": d[3]}
                                         for u, d in zip(updates, diffs)]},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[백업] {bak}")

    if headers:
        sheets.spreadsheets().values().batchUpdate(spreadsheetId=DASH_SID, body={
            "valueInputOption": "RAW", "data": headers}).execute()
        print(f"[적용] 자동배치 헤더 {len(headers)}열 기입")

    # ★사내망(VDI)이 큰 배치에서 소켓 타임아웃을 자주 낸다(실제로 밟음) — 잘게 쪼개고 직접 재시도한다.
    #   멱등이라 중간에 끊겨도 다시 돌리면 남은 것만 쓴다.
    import time
    CHUNK = 1200
    for i in range(0, len(updates), CHUNK):
        part = updates[i:i + CHUNK]
        for attempt in range(5):
            try:
                sheets.spreadsheets().values().batchUpdate(spreadsheetId=DASH_SID, body={
                    "valueInputOption": "RAW", "data": part}).execute()
                break
            except Exception as e:
                if attempt == 4:
                    raise
                wait = 3 * (attempt + 1)
                print(f"    재시도 {attempt + 1}/4 ({type(e).__name__}) — {wait}s 후")
                time.sleep(wait)
        print(f"  기입 {min(i + CHUNK, len(updates)):,}/{len(updates):,}")
    print("[적용] 완료 — 되읽어 검증합니다")

    grid2 = sheets.spreadsheets().values().get(
        spreadsheetId=DASH_SID, range=f"'{RAW_TAB}'!A1:{last_col}{R_DAILY_TO}",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])

    def row2(n):
        r = grid2[n - 1] if len(grid2) >= n else []
        return r + [""] * (width - len(r))

    left = 0
    for key, daily in src.items():
        ci = dest.get(key)
        if ci is None:
            continue
        for d, want in daily.items():
            rn = row_of_date.get(d)
            if rn is None:
                continue
            cur = row2(rn)[ci]
            cur = float(cur) if isinstance(cur, (int, float)) else 0.0
            if abs(cur - float(want)) >= 1e-9:
                left += 1
    print("[검증] 되읽기 불일치:", left, "건")
    if left:
        rc = rc or 1
    if rc == 2:
        print("★ 미배치가 남아 exit 2 — CI 가 슬랙 DM 을 보냅니다.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
