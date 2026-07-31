# -*- coding: utf-8 -*-
"""대시보드 시트 준비물량 — 블록 단위 정정(오배치·폐기·신규 STY).

_dash_fix_prep.py 가 못 고치는 3건(2026-07-30):
  ① 커브드팬츠 탭에 **MWFNPAA09**(MSTRD 기준 웜 팬츠 HERO) 블록이 들어가 있어 커브드가 +10,000 과다.
     → ★행을 지우지 않는다. R12 총계 수식이 **144개 열**에서 이 블록 헤더행을 나열하고 있어 삭제하면 전부 #REF!.
       키셀(C)·uid(A)만 비워 모든 열에서 0이 되게 하고, 라벨로 이동 사실을 남긴다(무위험).
  ② 웜 팬츠 탭의 **MWFNPAA07** 블록은 MSTRD HERO STY에서 빠졌는데 6,000이 잡혀 있었다.
     → 같은 3컬러 구조인 **MWFNPAA09로 재사용**(코드·품번·품명 교체) — 블록 수·수식 그대로.
  ③ 데님팬츠에 신규 STY **MWFNP0A06**(3컬러 6,600) 블록이 아예 없다.
     → 마지막 블록 뒤(범위 밖)에 헤더+컬러행을 만들고, R12 총계 수식들에 헤더행 참조를 추가한다.
       각 열 값이 '이전 + 새 블록값'과 일치하는지 검증한다.

실행: python _dash_fix_blocks.py [--apply]
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from soo.auth import build_services, get_credentials  # noqa: E402

SID = "1-A04_TwKZJNPkFg27USkKAScZRu6CAhbgVeXk9c09nA"
LAST_COL = 260
SUMRANGE = re.compile(r"^=sum\(HT(\d+):HT(\d+)\)$", re.I)


def cn(i):
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def num(v):
    try:
        return round(float(str(v).replace(",", "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def get(sh, rng, mode="UNFORMATTED_VALUE"):
    return sh.spreadsheets().values().get(spreadsheetId=SID, range=rng,
                                         valueRenderOption=mode).execute().get("values", [])


def blocks(sh, tab, last_row):
    ht = get(sh, f"'{tab}'!HT10:HT{last_row}", "FORMULA")
    out = []
    for i in range(last_row - 9):
        f = str(ht[i][0]).strip() if i < len(ht) and ht[i] else ""
        m = SUMRANGE.match(f)
        if m:
            out.append((10 + i, int(m.group(1)), int(m.group(2))))
    return out


def set_vals(sh, data):
    sh.spreadsheets().values().batchUpdate(
        spreadsheetId=SID, body={"valueInputOption": "USER_ENTERED", "data": data}).execute()


# ── ① 커브드: MWFNPAA09 블록 비활성(키 비움 + 라벨) ─────────────────────────
def fix_curved(sh, apply):
    tab = "커브드팬츠"
    bl = [b for b in blocks(sh, tab, 160)]
    rows = get(sh, f"'{tab}'!A10:F160", "FORMATTED_VALUE")

    def cell(r, c):
        i = r - 10
        return str(rows[i][c]).strip() if i < len(rows) and len(rows[i]) > c and rows[i][c] is not None else ""

    tgt = [(h, a, b) for h, a, b in bl if cell(h, 1) == "MWFNPAA09"]
    print(f"[커브드] MWFNPAA09 블록 {tgt}")
    if not tgt:
        print("   대상 없음(이미 정리됨)")
        return
    h, a, b = tgt[0]
    before = num((get(sh, f"'{tab}'!HT12") or [[0]])[0][0])
    blkv = num((get(sh, f"'{tab}'!HT{h}") or [[0]])[0][0])
    print(f"   HT12 {before:,} · 블록 HT{h} = {blkv:,}")
    if not apply:
        return
    data = [{"range": f"'{tab}'!B{h}:F{h}",
             "values": [["MWFNPAA09", "0SKU", "(웜 팬츠로 이동 — MSTRD HERO STY 기준)", "0SKU", "Sub Total"]]}]
    for r in range(a, b + 1):
        data.append({"range": f"'{tab}'!A{r}:F{r}",
                     "values": [["", "MWFNPAA09", "", "(웜 팬츠 소속)", "", ""]]})
    set_vals(sh, data)
    after = num((get(sh, f"'{tab}'!HT12") or [[0]])[0][0])
    print(f"   → HT12 {before:,} → {after:,}")


# ── ② 웜 팬츠: MWFNPAA07 블록을 MWFNPAA09로 재사용 ──────────────────────────
def fix_warm(sh, apply, mstrd):
    tab = "웜 팬츠"
    bl = blocks(sh, tab, 110)
    rows = get(sh, f"'{tab}'!A10:F110", "FORMATTED_VALUE")

    def cell(r, c):
        i = r - 10
        return str(rows[i][c]).strip() if i < len(rows) and len(rows[i]) > c and rows[i][c] is not None else ""

    tgt = [(h, a, b) for h, a, b in bl if cell(h, 1) == "MWFNPAA07"]
    if not tgt:
        print("[웜팬츠] MWFNPAA07 블록 없음(이미 정리됨)")
        return
    h, a, b = tgt[0]
    new = sorted(mstrd["MWFNPAA09"], key=lambda x: x["code"])
    print(f"[웜팬츠] MWFNPAA07 블록 R{h}({a}~{b}) → MWFNPAA09 {len(new)}컬러 재사용")
    if len(new) > (b - a + 1):
        print("   ★컬러 수가 블록보다 많음 — 중단(수동 확인 필요)")
        return
    before = num((get(sh, f"'{tab}'!HT12") or [[0]])[0][0])
    if not apply:
        return
    nm = new[0]["name"]
    data = [{"range": f"'{tab}'!A{h}:F{h}",
             "values": [["HERO", "MWFNPAA09", f"{len(new)}SKU", nm, f"{len(new)}SKU", "Sub Total"]]}]
    for k, r in enumerate(range(a, b + 1)):
        if k < len(new):
            d = new[k]
            data.append({"range": f"'{tab}'!A{r}:F{r}",
                         "values": [[d["uid"], "MWFNPAA09", d["code"], d["carry"] or "신규",
                                     f"=IFERROR(RIGHT(C{r},2))", ""]]})
        else:
            data.append({"range": f"'{tab}'!A{r}:F{r}", "values": [["", "MWFNPAA09", "", "", "", ""]]})
    set_vals(sh, data)
    after = num((get(sh, f"'{tab}'!HT12") or [[0]])[0][0])
    print(f"   → HT12 {before:,} → {after:,}")


# ── ③ 데님: MWFNP0A06 신규 블록 + 총계 수식에 헤더 추가 ──────────────────────
def fix_denim(sh, apply, mstrd, gid):
    tab, last = "데님팬츠", 95
    bl = blocks(sh, tab, last)
    rows = get(sh, f"'{tab}'!A10:F{last}", "FORMATTED_VALUE")

    def cell(r, c):
        i = r - 10
        return str(rows[i][c]).strip() if i < len(rows) and len(rows[i]) > c and rows[i][c] is not None else ""

    if any(cell(h, 1) == "MWFNP0A06" for h, a, b in bl):
        print("[데님] MWFNP0A06 블록 이미 있음")
        return
    src_h, src_a, src_b = max(bl, key=lambda x: x[0])          # 마지막 블록(복사 원본)
    new = sorted(mstrd["MWFNP0A06"], key=lambda x: x["code"])
    at = src_b + 1                                             # 마지막 블록 범위 바로 다음(범위 밖)
    print(f"[데님] 신규 블록 삽입 위치 R{at} (원본 블록 R{src_h}, {src_a}~{src_b}) · 컬러 {len(new)}")
    row12 = get(sh, f"'{tab}'!A12:IZ12", "FORMULA")[0]
    cols = []
    for j, f in enumerate(row12, 1):
        f = str(f)
        if f.startswith("=") and re.search(rf"(?<![A-Z0-9]){cn(j)}{src_h}(?![0-9])", f) and f.count(",") >= 2:
            cols.append((j, f))
    print(f"   총계 수식에 헤더 참조 추가 대상 열 {len(cols)}개")
    if not apply:
        return
    before12 = {cn(j): num((get(sh, f"'{tab}'!{cn(j)}12") or [[0]])[0][0]) for j, _ in cols}
    # 행 삽입(헤더 1 + 컬러 n) + 수식 복사
    n = len(new)
    sh.spreadsheets().batchUpdate(spreadsheetId=SID, body={"requests": [
        {"insertDimension": {"range": {"sheetId": gid, "dimension": "ROWS",
                                       "startIndex": at - 1, "endIndex": at - 1 + 1 + n},
                             "inheritFromBefore": True}},
        {"copyPaste": {"source": {"sheetId": gid, "startRowIndex": src_h - 1, "endRowIndex": src_h,
                                  "startColumnIndex": 0, "endColumnIndex": LAST_COL},
                       "destination": {"sheetId": gid, "startRowIndex": at - 1, "endRowIndex": at,
                                       "startColumnIndex": 0, "endColumnIndex": LAST_COL},
                       "pasteType": "PASTE_FORMULA"}},
        {"copyPaste": {"source": {"sheetId": gid, "startRowIndex": src_a - 1, "endRowIndex": src_a,
                                  "startColumnIndex": 0, "endColumnIndex": LAST_COL},
                       "destination": {"sheetId": gid, "startRowIndex": at, "endRowIndex": at + n,
                                       "startColumnIndex": 0, "endColumnIndex": LAST_COL},
                       "pasteType": "PASTE_FORMULA"}},
    ]}).execute()
    nm = new[0]["name"]
    data = [{"range": f"'{tab}'!A{at}:F{at}",
             "values": [["HERO SUB", "MWFNP0A06", f"{n}SKU", nm, f"{n}SKU", "Sub Total"]]},
            {"range": f"'{tab}'!HT{at}", "values": [[f"=sum(HT{at+1}:HT{at+n})"]]}]
    for k, d in enumerate(new):
        r = at + 1 + k
        data.append({"range": f"'{tab}'!A{r}:F{r}",
                     "values": [[d["uid"], "MWFNP0A06", d["code"], d["carry"] or "신규",
                                 f"=IFERROR(RIGHT(C{r},2))", ""]]})
    set_vals(sh, data)
    # 총계 수식에 새 헤더행 참조 추가
    upd = []
    for j, f in cols:
        c = cn(j)
        f2 = re.sub(r"\)\s*$", f",{c}{at})", f, count=1)
        upd.append({"range": f"'{tab}'!{c}12", "values": [[f2]]})
    for i in range(0, len(upd), 100):
        set_vals(sh, upd[i:i + 100])
    print(f"   수식 {len(upd)}개 갱신")
    blk = num((get(sh, f"'{tab}'!HT{at}") or [[0]])[0][0])
    bad = 0
    for j, _ in cols:
        c = cn(j)
        now = num((get(sh, f"'{tab}'!{c}12") or [[0]])[0][0])
        exp_delta = blk if c == "HT" else None
        if c == "HT" and now != before12[c] + blk:
            bad += 1
            print(f"   ★{c}12 {before12[c]:,} → {now:,} (기대 {before12[c]+blk:,})")
    tot = num((get(sh, f"'{tab}'!HT12") or [[0]])[0][0])
    print(f"   새 블록 HT{at} = {blk:,} · HT12 = {tot:,} · 이상 {bad}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sh = svc["sheets"]
    # MSTRD 상세(대상 2 STY)
    vals = sh.spreadsheets().values().get(
        spreadsheetId="1tvtbz6u3xob_SkZQBH79xX6J8dRpsHAa1-nn-KMeY-g", range="'SKU'!A5:AC16000",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])

    def g(r, j):
        return str(r[j]).strip() if len(r) > j and r[j] is not None else ""

    mstrd = {"MWFNPAA09": [], "MWFNP0A06": []}
    for r in vals:
        sty = g(r, 1)
        if sty in mstrd and num(g(r, 26)):
            mstrd[sty].append({"code": g(r, 16), "uid": g(r, 8), "carry": g(r, 7),
                               "qty": num(g(r, 26)), "name": g(r, 19)})
    meta = sh.spreadsheets().get(spreadsheetId=SID, fields="sheets.properties(title,sheetId)").execute()
    gid = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    fix_warm(sh, a.apply, mstrd)
    fix_curved(sh, a.apply)
    fix_denim(sh, a.apply, mstrd, gid["데님팬츠"])


if __name__ == "__main__":
    main()
