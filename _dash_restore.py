# -*- coding: utf-8 -*-
"""대시보드 시트 탭 복구 — `_dash_backup_<탭>.json`(수식 그리드)을 그대로 되돌린다.

_dash_fix_prep.py 의 '중복행 삭제'가 위험했다(2026-07-30): 히어로 탭엔 같은 품번-컬러가
여러 섹션에 반복 등장하는 레이아웃이 있어, 이를 중복으로 보고 지우면 다른 섹션 수식이 #REF! 가 된다.
(커브드팬츠 5,508 · 빅토리아 울 1,135건 발생 → 이 스크립트로 원복.)

실행: python _dash_restore.py "커브드팬츠" "빅토리아 울" [--apply]
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
    meta = sh.spreadsheets().get(spreadsheetId=SID,
                                fields="sheets.properties(title,sheetId,gridProperties(rowCount,columnCount))").execute()
    props = {s["properties"]["title"]: s["properties"] for s in meta["sheets"]}
    for tab in a.tabs:
        bkf = ROOT / f"_dash_backup_{tab.replace(' ', '')}.json"
        if not bkf.exists():
            print(f"[{tab}] 백업 없음: {bkf.name}")
            continue
        bk = json.loads(bkf.read_text(encoding="utf-8"))
        need_rows = len(bk)
        width = max((len(r) for r in bk), default=0)
        p = props[tab]
        have = p["gridProperties"]["rowCount"]
        cur = sh.spreadsheets().values().get(spreadsheetId=SID, range=f"'{tab}'!A1:A{have}",
                                            valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
        print(f"[{tab}] 백업 {need_rows}행×{width}열 · 현재 그리드 {have}행 (A열 비어있지 않은 {len(cur)}행)")
        if not a.apply:
            continue
        if have < need_rows:
            sh.spreadsheets().batchUpdate(spreadsheetId=SID, body={"requests": [
                {"appendDimension": {"sheetId": p["sheetId"], "dimension": "ROWS",
                                     "length": need_rows - have}}]}).execute()
            print(f"   행 {need_rows - have}개 추가")
        # 백업 그리드를 그대로 덮어쓰기(빈 셀도 공백으로 맞춤)
        grid = [(r + [""] * (width - len(r))) for r in bk]
        sh.spreadsheets().values().update(
            spreadsheetId=SID, range=f"'{tab}'!A1:{cn(width)}{need_rows}",
            valueInputOption="USER_ENTERED", body={"values": grid}).execute()
        chk = sh.spreadsheets().values().get(spreadsheetId=SID, range=f"'{tab}'!A1:IZ{need_rows}",
                                            valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
        refs = sum(str(c).count("#REF!") for row in chk for c in row)
        ht12 = sh.spreadsheets().values().get(spreadsheetId=SID, range=f"'{tab}'!HT12",
                                             valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [[0]])
        print(f"   복구 완료 · #REF! {refs}개 · HT12 {ht12[0][0] if ht12 and ht12[0] else '-'}")


if __name__ == "__main__":
    main()
