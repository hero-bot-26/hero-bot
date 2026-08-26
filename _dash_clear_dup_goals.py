# -*- coding: utf-8 -*-
"""26FW 대시보드 — 중복 '벌' 블록의 목표·달성율 칸 정리 (2026-08-26 신설).

배경(사용자 지적):
  히어로 탭의 한 STY 는 여러 '벌' 블록으로 나뉜다. 실측(커브드팬츠 MMFPC3A15, 8/26 기준)
    r14 HERO     ON 2,218 + OFF 4,521 = 6,739   ← 온·오프 합계 벌
    r22 ★주연     ON     0 + OFF 4,521 = 4,521   ← 오프라인만
    r30 통합 UID  ON 2,218 + OFF     0 = 2,218   ← 온라인만
  그런데 목표 수식은 **품번과 채널로만** 매칭한다(벌 구분 없음):
    AI15 = SUMPRODUCT(... 행2="Online" ... * 행3=$B15 ...) * $HW15
  → STY 전체 목표(5,951)가 세 벌에 그대로 복제돼, 채널 하나치 실적을 전체 목표로 나눈다.
    화면: ★주연 76% · 통합UID 37%   실제: 오프라인 107% · 온라인 129%
  총계행(r12·r13)과 각 채널 블록의 **첫 벌(합계) 행**은 정상이다 — 총계 수식이 첫 벌 행만
  명시적으로 더하기 때문(예: K13=sum(K14,K38,K65,K83,K89,K92)).

조치:
  중복 벌 블록(같은 스타일의 2번째 이후 Sub Total 블록)에서 **목표·달성율 칸만 비운다.**
  채널별 달성율은 첫 벌 행의 Online/Offline 블록에 이미 정확히 있으므로(AK14 129% · BJ14 107%)
  정보 손실이 없다. 실적(GMV·판매수량) 칸은 건드리지 않는다.
  ★행 삽입·삭제는 하지 않는다(#REF! 전례) — 셀 값만 비운다.

사용:
  python _dash_clear_dup_goals.py            # 드라이런(기본) — 무엇을 지울지만 출력
  python _dash_clear_dup_goals.py --apply    # 실제 적용(직전 수식을 JSON 백업 후)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from soo.auth import get_credentials, build_services

SHEET_ID = "1-A04_TwKZJNPkFg27USkKAScZRu6CAhbgVeXk9c09nA"   # 26FW 히어로 실적 대시보드(라이브)

# 기간×채널 블록별 (목표 판매량, 달성율) 열. 행9 헤더로 실측해 고정.
#   MTD   Total J/L · Online AI/AK · Offline BH/BJ
#   WEEK  Total CH/CJ · Online DF/DH · Offline ED/EF
#   DAY   Total FB/FD · Online FZ/GB · Offline GX/GZ
GOAL_COLS = ["J", "L", "AI", "AK", "BH", "BJ",
             "CH", "CJ", "DF", "DH", "ED", "EF",
             "FB", "FD", "FZ", "GB", "GX", "GZ"]

SCAN_FIRST_ROW, SCAN_LAST_ROW = 9, 250
_A1 = re.compile(r"(?<![A-Z0-9$!])\$?([A-Z]{1,2})\$?(\d{1,3})(?![0-9(])")


def _colname(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _hero_tabs(sheets) -> list[str]:
    """히어로 탭 = 데이터/목표 탭을 뺀 나머지. 탭명 하드코딩 금지(개명·추가 대비)."""
    meta = sheets.spreadsheets().get(spreadsheetId=SHEET_ID,
                                     fields="sheets.properties(title)").execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]
    skip = re.compile(r"^(YTD|MTD|WEEK|DAY|FW|전년|잔여재고|입고현황|준비물량|히어로목표|시트\d+|.*상세)")
    return [t for t in titles if not skip.match(t)]


def find_dup_blocks(sheets, tabs: list[str]) -> dict[str, list[tuple[int, int, str]]]:
    """탭별 중복 벌 블록 [(시작행, 끝행, 스타일)] — 같은 스타일의 2번째 이후 Sub Total 블록."""
    res = sheets.spreadsheets().values().batchGet(
        spreadsheetId=SHEET_ID,
        ranges=[f"'{t}'!A{SCAN_FIRST_ROW}:L{SCAN_LAST_ROW}" for t in tabs],
        valueRenderOption="UNFORMATTED_VALUE").execute()["valueRanges"]
    out: dict[str, list[tuple[int, int, str]]] = {}
    for tab, vr in zip(tabs, res):
        rows = vr.get("values", [])
        subs = []
        # ★블록의 마지막 행 = 내용이 있는 마지막 행. 이걸 안 잡으면 **맨 끝 Sub Total 블록의 컬러행**이
        #   통째로 빠진다(초판에서 실제로 빠졌다 — 윈드브레이커 r122 가 컬러 없이 1행만 잡혔다).
        last_row = SCAN_FIRST_ROW
        for i, r in enumerate(rows, start=SCAN_FIRST_ROW):
            if any(str(c).strip() for c in r[:6]):
                last_row = i
            f = str(r[5]) if len(r) > 5 else ""
            if f == "Sub Total":
                subs.append((i, str(r[1]) if len(r) > 1 else ""))
        seen, dup = set(), []
        for k, (rn, sty) in enumerate(subs):
            end = subs[k + 1][0] - 1 if k + 1 < len(subs) else last_row
            if not sty:
                continue          # 스타일이 비면 판정 불가 — 건드리지 않는다
            if sty in seen:
                dup.append((rn, end, sty))
            else:
                seen.add(sty)
        if dup:
            out[tab] = dup
    return out


def guard_no_external_refs(sheets, plan, targets) -> list[tuple]:
    """지울 셀을 '밖에서' 참조하는 수식이 있으면 멈춘다(조용히 #REF! 만드는 것 방지)."""
    res = sheets.spreadsheets().values().batchGet(
        spreadsheetId=SHEET_ID,
        ranges=[f"'{t}'!A{SCAN_FIRST_ROW}:GZ{SCAN_LAST_ROW}" for t in plan],
        valueRenderOption="FORMULA").execute()["valueRanges"]
    bad = []
    for tab, vr in zip(plan, res):
        cells = targets[tab]
        for ri, row in enumerate(vr.get("values", []), start=SCAN_FIRST_ROW):
            for ci, v in enumerate(row):
                s = str(v)
                if not s.startswith("="):
                    continue
                if f"{_colname(ci)}{ri}" in cells:
                    continue      # 지울 셀끼리의 참조는 같이 사라지므로 무관
                for m in _A1.finditer(s):
                    if f"{m.group(1)}{m.group(2)}" in cells:
                        bad.append((tab, f"{_colname(ci)}{ri}", f"{m.group(1)}{m.group(2)}", s[:80]))
                        break
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 적용(기본은 드라이런)")
    a = ap.parse_args()

    root = Path(__file__).parent
    svc = build_services(get_credentials(root / "credentials.json", root / "token.json"))
    sheets = svc["sheets"]

    tabs = _hero_tabs(sheets)
    plan = find_dup_blocks(sheets, tabs)
    if not plan:
        print("[OK] 중복 벌 블록 없음 — 할 일 없음.")
        return 0

    targets = {t: {f"{c}{r}" for rn, end, _ in b for r in range(rn, end + 1) for c in GOAL_COLS}
               for t, b in plan.items()}

    # 현재 값(수식) 읽기 — 백업 + 멱등 판정
    ranges, keys = [], []
    for t, cells in targets.items():
        for cr in sorted(cells, key=lambda x: (int(_A1.match(x).group(2)), _A1.match(x).group(1))):
            ranges.append(f"'{t}'!{cr}")
            keys.append((t, cr))
    cur = {}
    for i in range(0, len(ranges), 400):     # 요청당 범위 수 제한
        res = sheets.spreadsheets().values().batchGet(
            spreadsheetId=SHEET_ID, ranges=ranges[i:i + 400],
            valueRenderOption="FORMULA").execute()["valueRanges"]
        for (t, cr), vr in zip(keys[i:i + 400], res):
            vals = vr.get("values") or [[""]]
            cur[(t, cr)] = (vals[0][0] if vals and vals[0] else "")

    nonempty = {k: v for k, v in cur.items() if str(v).strip() != ""}
    print(f"[대상] 탭 {len(plan)}개 · 중복 벌 블록 {sum(len(b) for b in plan.values())}개 "
          f"· 셀 {len(cur):,}개 (그중 비울 것 {len(nonempty):,}개)")
    for t, blocks in plan.items():
        for rn, end, sty in blocks:
            n = sum(1 for (tt, cr) in nonempty if tt == t and rn <= int(_A1.match(cr).group(2)) <= end)
            print(f"   {t} r{rn}~r{end} {sty} — 비울 셀 {n}")

    if not nonempty:
        print("[OK] 이미 정리돼 있습니다 (멱등 0건).")
        return 0

    bad = guard_no_external_refs(sheets, plan, targets)
    if bad:
        print(f"\n[중단] 지울 셀을 밖에서 참조하는 수식 {len(bad)}건 — 지우면 #REF! 가 난다:")
        for h in bad[:10]:
            print("   ", h)
        return 2
    print("[가드] 외부 참조 0건 — 안전")

    if not a.apply:
        print("\n드라이런입니다. 실제 적용하려면 --apply 를 붙이세요.")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = root / f"_backup_dupgoal_{SHEET_ID[:8]}_{stamp}.json"      # ★파일 ID 포함(사본과 안 섞이게)
    bak.write_text(json.dumps({f"{t}!{cr}": v for (t, cr), v in cur.items()},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[백업] {bak.name} ({len(cur):,}셀)")

    body = {"valueInputOption": "USER_ENTERED",
            "data": [{"range": f"'{t}'!{cr}", "values": [[""]]} for (t, cr) in nonempty]}
    for i in range(0, len(body["data"]), 400):
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": body["data"][i:i + 400]}).execute()
    print(f"[적용] {len(nonempty):,}셀 비움. 재실행하면 0건이어야 합니다(멱등).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
