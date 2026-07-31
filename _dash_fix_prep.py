# -*- coding: utf-8 -*-
"""26FW 히어로 대시보드 시트의 '준비물량' 컬러행을 MSTRD SKU 기준으로 맞추는 멱등 수정기.

배경(2026-07-30): 대시보드 시트(1-A04_TwK…)의 히어로 탭은 컬러 한 줄씩 사람이 깔아둔 표라
MSTRD 상품MAP `SKU` 탭에 컬러/스타일이 추가돼도 자동 반영되지 않는다. 그래서 앱(=MSTRD 전건 합산)과
총 준비물량이 히어로마다 조금씩 달랐다(빅토리아울 415,092 vs 415,382 등).

수정 방식 — ★수식은 건드리지 않는다:
  · 각 STY 블록의 컬러행 범위 **안쪽**에 행을 삽입하면 블록 소계 `=sum(HTa:HTb)`가 자동 확장되고,
    히어로 총계(블록 헤더행 합)도 자동으로 맞는다. 267열 수식 재배치가 없어 리맵 사고 위험이 없다.
  · 삽입 행은 형제 컬러행에서 **PASTE_FORMULA**로 복사 → 실적·재고·입고 등 모든 열 수식이 그대로 산다.
  · 식별 열만 채운다: A=uid(실적 SUMIFS 키) · B=품번 · C=품번-컬러(준비물량 SUMIFS 키) · D=캐리오버/신규 ·
    E=`=IFERROR(RIGHT(C{r},2))`(컬러코드 자동).
  · 중복 컬러행(같은 코드 2줄)은 **삭제**한다 — 준비물량뿐 아니라 uid 기반 실적까지 두 번 잡힌다.
  · 블록 헤더의 'nSKU' 라벨(C·E열)은 실제 컬러행 수로 갱신.

멱등: 이미 맞는 탭은 "변경 없음"으로 끝난다. 실행 = `python _dash_fix_prep.py [탭명 ...] [--apply]`
(--apply 없으면 계획만 출력). 백업은 실행 시 `_dash_backup_<탭>.json`(수식 그리드)으로 저장.
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

DASH_SID = "1-A04_TwKZJNPkFg27USkKAScZRu6CAhbgVeXk9c09nA"
MSTRD_SID = "1tvtbz6u3xob_SkZQBH79xX6J8dRpsHAa1-nn-KMeY-g"
TABS = {"커브드팬츠": 160, "라이트다운": 110, "빅토리아 울": 140, "웜 팬츠": 110, "슬랙스": 110,
        "데님팬츠": 90, "스웨트팬츠": 70, "심리스 브라": 40, "양말": 110, "벨트": 50}
LAST_COL = 260          # 복사 대상 열 범위(A..IZ 앞쪽 실사용 구간)
SUMRANGE = re.compile(r"^=sum\(HT(\d+):HT(\d+)\)$", re.I)
SUMIFS_KEY = re.compile(r"SUMIFS\('준비물량\(SKU\)'!\$L:\$L,'준비물량\(SKU\)'!\$B:\$B,\$?([A-Z]+)(\d+)\)", re.I)


def _g(r, j):
    return str(r[j]).strip() if len(r) > j and r[j] is not None else ""


def _n(v):
    try:
        return round(float(str(v).replace(",", "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def mstrd_prep(sh, style_to_hero):
    """→ {code: {sty, hero, qty, uid, carry}} (준비물량>0인 SKU/대표행)."""
    vals = sh.spreadsheets().values().get(
        spreadsheetId=MSTRD_SID, range="'SKU'!A5:AC16000",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    out = {}
    for r in vals:
        sty = _g(r, 1)
        hero = style_to_hero.get(sty)
        if not hero:
            continue
        q = _n(_g(r, 26))
        if not q:
            continue
        out[_g(r, 16) or sty] = {"sty": sty, "hero": hero, "qty": q, "uid": _g(r, 8), "carry": _g(r, 7)}
    return out


def read_tab(sh, tab, last_row):
    """→ {'hdrs': [(hdr_row, a, b)], 'codes': {row: code}, 'key_col': {row: col}}"""
    ht = sh.spreadsheets().values().get(
        spreadsheetId=DASH_SID, range=f"'{tab}'!HT10:HT{last_row}",
        valueRenderOption="FORMULA").execute().get("values", [])
    hdrs, key_col = [], {}
    for i in range(last_row - 9):
        f = _g(ht[i], 0) if i < len(ht) and ht[i] else ""
        r = 10 + i
        if not f:
            continue
        m = SUMRANGE.match(f)
        if m:
            hdrs.append((r, int(m.group(1)), int(m.group(2))))
            continue
        m2 = SUMIFS_KEY.search(f)
        if m2:
            key_col[r] = m2.group(1)
    # ★탭 전체(A:F)에 등장하는 품번-컬러 코드 — '이미 있는지' 판정은 이 넓은 집합으로 한다.
    #   SUMIFS 키셀만 보면 다른 섹션에 이미 깔린 코드를 '누락'으로 오판해 중복 삽입된다(슬랙스 +6,702 사고).
    af = sh.spreadsheets().values().get(
        spreadsheetId=DASH_SID, range=f"'{tab}'!A10:F{last_row}",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    anywhere = {str(c).strip() for row in af for c in row
                if re.match(r"^M[A-Z0-9]{8}(-[A-Z0-9]{2})?$", str(c).strip())}
    rows = sorted(key_col)
    codes = {}
    if rows:
        got = sh.spreadsheets().values().batchGet(
            spreadsheetId=DASH_SID, ranges=[f"'{tab}'!{key_col[r]}{r}" for r in rows],
            valueRenderOption="FORMATTED_VALUE").execute()["valueRanges"]
        for r, gv in zip(rows, got):
            v = (gv.get("values") or [[""]])[0]
            codes[r] = str(v[0]).strip() if v else ""
    return {"hdrs": hdrs, "codes": codes, "key_col": key_col, "anywhere": anywhere}


def plan_tab(tab, st, prep):
    """→ (missing: {code: (블록 hdr, a, b)}, dups: [row], stale: [(row, code)])"""
    hero_codes = {c: d for c, d in prep.items() if d["hero"].replace(" ", "") == tab.replace(" ", "")}
    blocks = {}                       # sty → (hdr, a, b)
    for hdr, a, b in st["hdrs"]:
        stys = {c.split("-")[0] for r, c in st["codes"].items() if a <= r <= b and c}
        for s in stys:
            blocks.setdefault(s, (hdr, a, b))
    seen = defaultdict(list)
    for r, c in st["codes"].items():
        if c:
            seen[c].append(r)
    dups = sorted(r for c, rs in seen.items() if len(rs) > 1 for r in sorted(rs)[1:])
    # 잔존행 = 그 히어로 STY 자체가 MSTRD 26FW 히어로 매핑에 없는 행(준비물량 0인 컬러는 정상이라 제외)
    hero_stys = {d["sty"] for d in hero_codes.values()}
    stale = sorted((r, c) for r, c in st["codes"].items()
                   if c and c.split("-")[0] not in hero_stys)
    missing = {}
    for c, d in hero_codes.items():
        if c in seen or c in st.get("anywhere", ()):    # 탭 어디든 이미 있으면 삽입하지 않는다
            continue
        blk = blocks.get(d["sty"])
        if blk:
            missing[c] = blk
    noblock = sorted({d["sty"] for c, d in hero_codes.items()
                      if c not in seen and c not in st.get("anywhere", ()) and d["sty"] not in blocks})
    return missing, dups, stale, noblock, blocks


def apply_tab(sh, tab, gid, last_row, prep, apply=False):
    st = read_tab(sh, tab, last_row)
    missing, dups, stale, noblock, blocks = plan_tab(tab, st, prep)
    hero_total = _n((sh.spreadsheets().values().get(
        spreadsheetId=DASH_SID, range=f"'{tab}'!HT12",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values") or [[0]])[0][0])
    want = sum(d["qty"] for d in prep.values() if d["hero"].replace(" ", "") == tab.replace(" ", ""))
    print(f"\n=== {tab} · 현재 HT12 {hero_total:,} / MSTRD {want:,} (차 {hero_total-want:+,})")
    print(f"    누락 {len(missing)} · 중복 {len(dups)} · 타히어로/폐기 {len(stale)} · 블록없는STY {noblock or '-'}")
    for c, (hdr, a, b) in sorted(missing.items()):
        print(f"      + {c} {prep[c]['qty']:>7,} → 블록 R{hdr}(범위 {a}~{b})")
    for r in dups:
        print(f"      - 중복행 R{r} {st['codes'][r]}")
    for r, c in stale:
        print(f"      ! 잔존행 R{r} {c} (MSTRD 히어로 매핑 밖)")
    if not apply:
        return
    if not missing:
        print("    변경 없음")
        return

    # ① ★중복행은 절대 삭제하지 않는다 — 히어로 탭엔 같은 품번-컬러가 여러 섹션(통합UID·채널별 표)에
    #    반복 등장하는 레이아웃이 있어, 지우면 다른 섹션 수식이 `=sum(#REF!)`로 깨진다
    #    (2026-07-30 커브드 5,508·빅토리아울 1,135·라이트다운 1,805건 사고 → 원본 복구).
    #    이중계상이 의심되면 사람이 확인하도록 보고만 한다.
    if dups:
        print(f"    [보고] 중복 의심 {len(dups)}행 — 자동 삭제하지 않음(레이아웃상 정상일 수 있음)")

    # ② 누락 컬러행 삽입 — 블록별로 아래에서 위로(행번호 밀림 방지)
    by_block = defaultdict(list)
    for c, (hdr, a, b) in missing.items():
        by_block[(hdr, a, b)].append(c)
    for (hdr, a, b) in sorted(by_block, key=lambda x: -x[0]):
        cs = sorted(by_block[(hdr, a, b)])
        at = a + 1                        # 블록 첫 컬러행 다음 = 범위 안쪽(자동 확장)
        sh.spreadsheets().batchUpdate(spreadsheetId=DASH_SID, body={"requests": [
            {"insertDimension": {"range": {"sheetId": gid, "dimension": "ROWS",
                                           "startIndex": at - 1, "endIndex": at - 1 + len(cs)},
                                 "inheritFromBefore": True}},
            {"copyPaste": {"source": {"sheetId": gid, "startRowIndex": a - 1, "endRowIndex": a,
                                      "startColumnIndex": 0, "endColumnIndex": LAST_COL},
                           "destination": {"sheetId": gid, "startRowIndex": at - 1, "endRowIndex": at - 1 + len(cs),
                                           "startColumnIndex": 0, "endColumnIndex": LAST_COL},
                           "pasteType": "PASTE_FORMULA"}},
        ]}).execute()
        data = []
        for k, c in enumerate(cs):
            r = at + k
            d = prep[c]
            data.append({"range": f"'{tab}'!A{r}:E{r}",
                         "values": [[d["uid"], d["sty"], c, d["carry"] or "신규", f"=IFERROR(RIGHT(C{r},2))"]]})
        sh.spreadsheets().values().batchUpdate(
            spreadsheetId=DASH_SID, body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
        print(f"    블록 R{hdr}: {len(cs)}행 삽입 ({', '.join(cs)})")

    # ③ 헤더 'nSKU' 라벨 갱신
    st = read_tab(sh, tab, last_row)
    data = []
    for hdr, a, b in st["hdrs"]:
        cnt = sum(1 for r, c in st["codes"].items() if a <= r <= b and c)
        if not cnt:
            continue
        data.append({"range": f"'{tab}'!C{hdr}", "values": [[f"{cnt}SKU"]]})
        data.append({"range": f"'{tab}'!E{hdr}", "values": [[f"{cnt}SKU"]]})
    if data:
        sh.spreadsheets().values().batchUpdate(
            spreadsheetId=DASH_SID, body={"valueInputOption": "USER_ENTERED", "data": data}).execute()
    after = _n((sh.spreadsheets().values().get(
        spreadsheetId=DASH_SID, range=f"'{tab}'!HT12",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values") or [[0]])[0][0])
    print(f"    → HT12 {hero_total:,} → {after:,} (MSTRD {want:,}, 차 {after-want:+,})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tabs", nargs="*", help="대상 탭(미지정=전체 점검)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sh = svc["sheets"]
    fw = json.load(open(ROOT / "hero_goods_26fw.json", encoding="utf-8"))
    prep = mstrd_prep(sh, fw["style_to_hero"])
    meta = sh.spreadsheets().get(spreadsheetId=DASH_SID, fields="sheets.properties(title,sheetId)").execute()
    gid = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    targets = a.tabs or list(TABS)
    for tab in targets:
        if tab not in TABS:
            print(f"[스킵] 알 수 없는 탭: {tab}")
            continue
        if a.apply:   # 탭 단위 백업(수식 그리드)
            bk = sh.spreadsheets().values().get(
                spreadsheetId=DASH_SID, range=f"'{tab}'!A1:IZ{TABS[tab]}",
                valueRenderOption="FORMULA").execute().get("values", [])
            (ROOT / f"_dash_backup_{tab.replace(' ', '')}.json").write_text(
                json.dumps(bk, ensure_ascii=False), encoding="utf-8")
        apply_tab(sh, tab, gid[tab], TABS[tab], prep, apply=a.apply)


if __name__ == "__main__":
    main()
