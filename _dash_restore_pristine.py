# -*- coding: utf-8 -*-
"""대시보드 히어로 탭을 **최초 원본**(작업 시작 전 스냅샷)으로 되돌린다.

원본 = scratchpad/dash_backup_formulas.json (2026-07-30 작업 시작 시 A1:IZ 수식 그리드).
행 수까지 원복한다(내가 삽입한 행 삭제). 중복행 삭제로 `=sum(#REF!)`가 생긴 탭 복구용.

실행: python _dash_restore_pristine.py "라이트다운" [...] [--apply]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from soo.auth import build_services, get_credentials  # noqa: E402

SID = "1-A04_TwKZJNPkFg27USkKAScZRu6CAhbgVeXk9c09nA"
SNAP = Path("C:/Users/MUSINSA/AppData/Local/Temp/claude/C--Users-MUSINSA/"
            "fffb54c6-c09d-4c39-9719-4c33eda76020/scratchpad/dash_backup_formulas.json")


def cn(i):
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tabs", nargs="+")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sh = svc["sheets"]
    snap = json.loads(SNAP.read_text(encoding="utf-8"))
    meta = sh.spreadsheets().get(spreadsheetId=SID,
                                fields="sheets.properties(title,sheetId,gridProperties(rowCount))").execute()
    props = {s["properties"]["title"]: s["properties"] for s in meta["sheets"]}
    for tab in a.tabs:
        if tab not in snap:
            print(f"[{tab}] 원본 스냅샷 없음")
            continue
        grid0 = snap[tab]
        n = len(grid0)
        w = max((len(r) for r in grid0), default=0)
        p = props[tab]
        have = p["gridProperties"]["rowCount"]
        print(f"[{tab}] 원본 {n}행×{w}열 · 현재 그리드 {have}행")
        if not a.apply:
            continue
        grid = [(r + [""] * (w - len(r))) for r in grid0]
        sh.spreadsheets().values().update(
            spreadsheetId=SID, range=f"'{tab}'!A1:{cn(w)}{n}",
            valueInputOption="USER_ENTERED", body={"values": grid}).execute()
        if have > n:
            sh.spreadsheets().batchUpdate(spreadsheetId=SID, body={"requests": [
                {"deleteDimension": {"range": {"sheetId": p["sheetId"], "dimension": "ROWS",
                                               "startIndex": n, "endIndex": have}}}]}).execute()
            print(f"   {n+1}~{have}행 삭제")
        chk = sh.spreadsheets().values().get(
            spreadsheetId=SID, range=f"'{tab}'!A1:IZ{n}",
            valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
        refs = sum(str(c).count("#REF!") for row in chk for c in row)
        ht = sh.spreadsheets().values().get(
            spreadsheetId=SID, range=f"'{tab}'!HT12",
            valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [[0]])
        print(f"   복구 완료 · #REF! {refs} · HT12 {ht[0][0] if ht and ht[0] else '-'}")


if __name__ == "__main__":
    main()
