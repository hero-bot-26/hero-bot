# -*- coding: utf-8 -*-
"""대시보드 히어로 탭 — MSTRD에 있는데 시트에 없는 **컬러 행을 블록마다 채운다**.

★시트 구조(사용자 설명, 2026-07-30): 한 스타일이 여러 '벌'로 반복된다.
  · 온라인 실적은 **통합 uid**, 오프라인 실적은 **개별(컬러별) uid** 로만 잡히기 때문에
    온·오프 합산을 만들려고 같은 컬러 구성을 벌마다 깔아두고 숨김 처리한 것.
  · 예: 커브드팬츠 MMFPC3A15 → `HERO`(통합uid) / `★주연`(컬러별 실uid) / `통합 UID`(통합uid) 3벌.
  → 따라서 **컬러가 추가되면 그 스타일의 모든 벌에 같은 컬러를 넣어야 한다.**
    (준비물량 총계 R12는 그중 한 벌만 더하므로 3벌에 넣어도 이중계상은 없다.)

행 삽입 규칙(수식 무손상):
  · 블록의 컬러 범위 **안쪽**(첫 컬러행 다음)에 삽입 → `=sum(HTa:HTb)`가 자동 확장.
  · 형제 컬러행에서 PASTE_FORMULA 복사 → 267열 수식 그대로 유지.
  · uid(A열)는 **그 벌의 관례**를 따른다 — 기존 컬러행이 전부 같은 uid면 그 uid(통합), 아니면 MSTRD 컬러별 uid.
  · ★행 삭제는 절대 하지 않는다(반복 벌을 중복으로 오인해 지우면 다른 수식이 #REF!가 된다 — 실제 사고 있었음).

실행: python _dash_fix_colors.py [탭 ...] [--apply]
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from soo.auth import build_services, get_credentials  # noqa: E402

SID = "1-A04_TwKZJNPkFg27USkKAScZRu6CAhbgVeXk9c09nA"
MSTRD = "1tvtbz6u3xob_SkZQBH79xX6J8dRpsHAa1-nn-KMeY-g"
TABS = {"커브드팬츠": 170, "라이트다운": 120, "빅토리아 울": 150, "웜 팬츠": 120, "슬랙스": 130,
        "데님팬츠": 100, "스웨트팬츠": 80, "심리스 브라": 50, "양말": 120, "벨트": 60,
        "힛탠다드": 120, "그리드/메시 플리스": 90, "에센셜 플리스": 110, "리커버리": 60, "헤비다운": 60}
LAST_COL = 260
SUMRANGE = re.compile(r"^=sum\(HT(\d+):HT(\d+)\)$", re.I)
CODE = re.compile(r"^M[A-Z0-9]{8}(-[A-Z0-9]{2})?$")


def _g(r, j):
    return str(r[j]).strip() if len(r) > j and r[j] is not None else ""


def _n(v):
    try:
        return round(float(str(v).replace(",", "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def mstrd_colors(sh, s2h):
    """→ {sty: {code: {qty, uid, carry}}}"""
    vals = sh.spreadsheets().values().get(
        spreadsheetId=MSTRD, range="'SKU'!A5:AC16000",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    out = defaultdict(dict)
    for r in vals:
        sty = _g(r, 1)
        if sty not in s2h:
            continue
        q = _n(_g(r, 26))
        if not q:
            continue
        code = _g(r, 16)
        # ★대표uid행(신품번+컬러에 컬러가 없는 행)은 시트에 넣지 않는다 — 사용자 확정(2026-07-31):
        #   "컬러 없는 건 필요 없다". 준비물량 기준 = 컬러행만(앱 load_26fw_prep도 동일).
        if not code or "-" not in code:
            continue
        out[sty][code] = {"qty": q, "uid": _g(r, 8), "carry": _g(r, 7)}
    return out


def scan(sh, tab, last_row):
    """블록(=sum 범위) + 각 행의 A/C 값."""
    ht = sh.spreadsheets().values().get(
        spreadsheetId=SID, range=f"'{tab}'!HT10:HT{last_row}",
        valueRenderOption="FORMULA").execute().get("values", [])
    ac = sh.spreadsheets().values().get(
        spreadsheetId=SID, range=f"'{tab}'!A10:F{last_row}",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    blocks, rowA, rowC = [], {}, {}
    for i in range(last_row - 9):
        r = 10 + i
        f = _g(ht[i], 0) if i < len(ht) and ht[i] else ""
        m = SUMRANGE.match(f)
        if m:
            blocks.append((r, int(m.group(1)), int(m.group(2))))
        row = ac[i] if i < len(ac) else []
        rowA[r], rowC[r] = _g(row, 0), _g(row, 2)
    return blocks, rowA, rowC


def plan(tab, blocks, rowA, rowC, colors):
    """→ [(hdr, a, b, sty, [code…], uid_mode)]"""
    out = []
    for hdr, a, b in blocks:
        codes = [rowC[r] for r in range(a, b + 1) if CODE.match(rowC.get(r, ""))]
        stys = {c.split("-")[0] for c in codes}
        if len(stys) != 1:
            continue                      # 스타일이 섞인 블록은 건드리지 않는다
        sty = stys.pop()
        want = colors.get(sty, {})
        miss = [c for c in want if c not in set(codes)]
        if not miss:
            continue
        uids = {rowA[r] for r in range(a, b + 1) if rowA.get(r, "").isdigit()}
        uid_mode = uids.pop() if len(uids) == 1 else None    # 한 uid로 통일된 벌 = 통합uid 관례
        out.append((hdr, a, b, sty, sorted(miss), uid_mode))
    return out


def apply_tab(sh, tab, gid, last_row, colors, do):
    blocks, rowA, rowC = scan(sh, tab, last_row)
    jobs = plan(tab, blocks, rowA, rowC, colors)
    before = _n((sh.spreadsheets().values().get(
        spreadsheetId=SID, range=f"'{tab}'!HT12",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values") or [[0]])[0][0])
    print(f"\n=== {tab} · HT12 {before:,} · 블록 {len(blocks)} · 보강 필요 블록 {len(jobs)}")
    for hdr, a, b, sty, miss, uid in jobs:
        print(f"    R{hdr} {sty} ({rowA.get(hdr, '')}) +{len(miss)}컬러 {miss} uid={uid or 'MSTRD 컬러별'}")
    if not do or not jobs:
        return
    for hdr, a, b, sty, miss, uid in sorted(jobs, key=lambda x: -x[0]):   # 아래 블록부터
        at = a + 1
        sh.spreadsheets().batchUpdate(spreadsheetId=SID, body={"requests": [
            {"insertDimension": {"range": {"sheetId": gid, "dimension": "ROWS",
                                           "startIndex": at - 1, "endIndex": at - 1 + len(miss)},
                                 "inheritFromBefore": True}},
            {"copyPaste": {"source": {"sheetId": gid, "startRowIndex": a - 1, "endRowIndex": a,
                                      "startColumnIndex": 0, "endColumnIndex": LAST_COL},
                           "destination": {"sheetId": gid, "startRowIndex": at - 1,
                                           "endRowIndex": at - 1 + len(miss),
                                           "startColumnIndex": 0, "endColumnIndex": LAST_COL},
                           "pasteType": "PASTE_FORMULA"}},
        ]}).execute()
        data = []
        for k, c in enumerate(miss):
            r = at + k
            d = colors[sty][c]
            data.append({"range": f"'{tab}'!A{r}:F{r}",
                         "values": [[uid or d["uid"], sty, c, d["carry"] or "신규",
                                     f"=IFERROR(RIGHT(C{r},2))", ""]]})
        sh.spreadsheets().values().batchUpdate(
            spreadsheetId=SID, body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
        print(f"    R{hdr} {sty}: {len(miss)}행 삽입")
    after = _n((sh.spreadsheets().values().get(
        spreadsheetId=SID, range=f"'{tab}'!HT12",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values") or [[0]])[0][0])
    want = sum(v["qty"] for sty, cs in colors.items() for v in cs.values()
               if True) if False else None
    print(f"    → HT12 {before:,} → {after:,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tabs", nargs="*")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sh = svc["sheets"]
    fw = json.load(open(ROOT / "hero_goods_26fw.json", encoding="utf-8"))
    s2h = fw["style_to_hero"]
    colors = mstrd_colors(sh, s2h)
    meta = sh.spreadsheets().get(spreadsheetId=SID, fields="sheets.properties(title,sheetId)").execute()
    gid = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    for tab in (a.tabs or list(TABS)):
        hero_colors = {sty: cs for sty, cs in colors.items()
                       if s2h[sty].replace(" ", "") == tab.replace(" ", "")}
        apply_tab(sh, tab, gid[tab], TABS[tab], hero_colors, a.apply)


if __name__ == "__main__":
    main()
