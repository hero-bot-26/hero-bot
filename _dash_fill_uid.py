# -*- coding: utf-8 -*-
"""26FW 대시보드 A열 uid 자동 채움 + 노트북 GOODS_FILTER 동기화 (매일).

왜 필요했나(2026-07-31):
  · 미발매 스타일은 대시보드 A열(uid)이 비어 있고, 발매 시점에 채워야 실적이 잡힌다.
    그동안 이걸 **손으로** 했고, 안 채운 사이 실적이 조용히 새고 있었다
    (MWFNP0A06 데님 4.8M · MWDUR0Z17-UM 1.65M · MMFDJ9A82 1.68M · MWFUR0C03 온라인 6.2M).
  · ★기존 daily(`_dash_fix_colors.py`)가 uid를 **MSTRD SKU I열**에서 가져오는데,
    신규 SKU는 그 칸이 **비어 있다**(기획MD가 나중에 채움). 살아있는 소스는
    **ItemMaster '무탠' 탭(C=uid, D=운영품번)** 이다. → 이 스크립트는 무탠을 본다.

두 관문을 같이 처리한다(하나만 하면 숫자가 안 들어온다):
  ① 대시보드 A열 채움 — 컬러행 중 A가 비었거나 숫자가 아닌 행.
  ② `_UID_FILTER` 탭 기록 — 26FW **데이터시트**에 대시보드가 참조하는 uid 전량을 쓴다.
     Databricks 노트북이 이 탭을 읽어 GOODS_FILTER 에 **합집합**으로 더한다(줄어들지 않음).
     노트북은 SA로 데이터시트만 열 수 있어서(조직 정책상 새 파일 SA 공유 불가) 이 경로가 유일하다.

행 종류는 **G열 수식 모양**으로 판별한다(A/D 값이 비어 있어도 안전):
  · `=sum(Gx,Gy)`      → 표시행. A는 라벨일 뿐 수식이 안 씀 → 건드리지 않는다.
  · `…,$U:$U,$D{r}`    → 통합(온라인)행. 통합uid + **옵션코드**가 세트라 옵션코드 없이는 못 채움 → 보고만.
  · `…!$B:$B,$A{r}`    → 개별(오프라인/심플)행 → **여기만 채운다.**

★행 삽입·삭제·수식 변경은 하지 않는다. A열 값만 쓴다(멱등).
실행: python _dash_fill_uid.py [--apply]
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from soo.auth import build_services, get_credentials  # noqa: E402

DASH = "1-A04_TwKZJNPkFg27USkKAScZRu6CAhbgVeXk9c09nA"   # 26FW 히어로 실적 대시보드
DATA = "1O78bMnJZq-U6zO2mZLHV84573uKM9DU2wpgzeGDBIk0"   # 26FW 데이터시트(노트북이 쓰는 곳)
ITEM = "1rVbq1UVwKAdNApYovVDPF9ALwoE-v1KhNZUyHtf_bn4"   # ItemMaster
FILTER_TAB = "_UID_FILTER"

TABS = ["커브드팬츠", "라이트다운", "힛탠다드", "빅토리아 울", "그리드/메시 플리스",
        "에센셜 플리스", "웜 팬츠", "리커버리", "헤비다운", "슬랙스",
        "데님팬츠", "스웨트팬츠", "심리스 브라", "양말", "벨트"]
COLOR_CODE = re.compile(r"^M[A-Z0-9]{8}-[A-Z0-9]{2,3}$")
HARDCODED_UID = re.compile(r"!\$B:\$B,(\d{6,9})")     # 수식에 박힌 통합uid (심리스브라 등)


def _g(row, j):
    return str(row[j]).strip() if row and len(row) > j and row[j] is not None else ""


def item_master(sh):
    """ItemMaster '무탠'(C=UID, D=운영품번, E=대표품번, F=컬러코드) → ({운영품번: uid}, {대표품번: 스타일uid}).

    ★★컬러행/스타일행 판별은 **D열에 '-' 가 있는가**로 한다. **F열(컬러코드)로 판별하면 안 된다** —
      컬러코드가 비어 있는 컬러행이 124건 있다(예: MMFWCAB12-BK, uid 6949329). F 기준으로 짰다가
      그 행을 스타일행으로 오분류해 "uid 미발급" 으로 흘려보냈다(2026-07-31, 사용자 지적으로 발견).
    ★D가 대표품번과 다른 계열인 행(예: D=MMFPK8A69-TA, E=MMFPK8B70)은 **이벤트/통합 매핑행**이라
      대표품번의 스타일uid 후보로만 쓴다. 진짜 스타일행(D==E)이 있으면 그쪽이 우선.
    """
    vals = sh.spreadsheets().values().get(
        spreadsheetId=ITEM, range="'무탠'!A12:N30000",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    by_pum, by_style, alt = {}, {}, {}
    for r in vals:
        uid = _g(r, 2)
        if not uid.isdigit():
            continue
        pum, rep = _g(r, 3), _g(r, 4)
        if not pum:
            continue
        if pum == rep and "-" not in pum:
            by_style.setdefault(pum, int(uid))
        elif rep and pum.startswith(rep + "-"):
            by_pum.setdefault(pum, int(uid))
        elif "-" not in pum:
            by_style.setdefault(pum, int(uid))
        elif rep:
            alt.setdefault(rep, int(uid))
    for k, u in alt.items():
        by_style.setdefault(k, u)
    return by_pum, by_style


def scan(sh, tab):
    """→ (rows, formulas). rows = [[A,B,C,D,E,F]…] 1-based 인덱스는 +1."""
    res = sh.spreadsheets().values().batchGet(
        spreadsheetId=DASH, ranges=[f"'{tab}'!A1:F400", f"'{tab}'!G1:G400"],
        valueRenderOption="FORMULA").execute()["valueRanges"]
    return res[0].get("values", []), [(_g(x, 0) if x else "") for x in res[1].get("values", [])]


def _norm(name):
    """컬러명 비교용 정규화 — 공백·슬래시 제거, 소문자. ('클라우디 블루' == '클라우디블루')"""
    return re.sub(r"[\s/]+", "", str(name)).lower()


def sales_optmap(sh):
    """26FW 데이터시트 YTD 실적 → {통합uid: {정규화컬러명: '01'}}.

    goods_opt 는 `01.클라우디 블루^L` 꼴. **통합uid 로 파는 스타일만** 옵션코드가 붙는다
    (개별uid 는 `L` 처럼 사이즈만) → 앞 2자가 숫자인 행만 취한다.
    한 컬러명이 두 코드에 걸리면(개편 등) 그 스타일은 통째로 버린다 = 잘못 채우느니 보류.
    """
    vals = sh.spreadsheets().values().get(
        spreadsheetId=DATA, range="'YTD'!B2:S40000",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    seen, bad = {}, set()
    for r in vals:
        uid, opt = _g(r, 0), _g(r, 17)
        if not uid.isdigit() or "." not in opt or "^" not in opt:
            continue
        code, rest = opt[:2], opt[opt.find(".") + 1:opt.find("^")]
        if not code.isdigit():
            continue
        k = (int(uid), _norm(rest))
        if seen.get(k, code) != code:
            bad.add(int(uid))
        seen[k] = code
    out = {}
    for (uid, name), code in seen.items():
        if uid not in bad:
            out.setdefault(uid, {})[name] = code
    return out


def kind(formula, row):
    f = formula.replace(" ", "")
    if not f.startswith("="):
        return None
    if re.match(r"^=sum\(G\d+,G\d+\)$", f, re.I):
        return "표시"
    if f"$U:$U,$D{row}" in f:
        return "통합"
    if re.search(r"!\$B:\$B,\$A%d\b" % row, f):
        return "개별"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    creds = get_credentials(ROOT / "credentials.json", ROOT / "token.json")
    sh = build_services(creds)["sheets"]

    by_pum, by_style = item_master(sh)
    print(f"ItemMaster 무탠: 컬러uid {len(by_pum)} · 스타일uid {len(by_style)}")

    optmap = sales_optmap(sh)
    print(f"실적 goods_opt 옵션코드 맵: 통합uid {len(optmap)}건")

    fills, dfills, pending, all_uid = [], [], [], set()
    for tab in TABS:
        rows, forms = scan(sh, tab)
        for i, row in enumerate(rows):
            r = i + 1
            a_val, pum = _g(row, 0), _g(row, 2)
            if a_val.isdigit():
                all_uid.add(int(a_val))
            if not COLOR_CODE.match(pum):
                continue
            k = kind(forms[i] if i < len(forms) else "", r)
            if k == "개별" and not a_val.isdigit():
                uid = by_pum.get(pum)
                if uid:
                    fills.append((tab, r, uid, pum, a_val))
                    all_uid.add(uid)
                else:
                    pending.append((tab, r, pum, "ItemMaster 미발급"))
            elif k == "통합" and not a_val.isdigit():
                sty = _g(row, 1)
                uid = by_style.get(sty)
                if not uid:
                    pending.append((tab, r, pum, "통합uid 미발급"))
                    continue
                all_uid.add(uid)          # ★먼저 필터에 태워야 실적이 들어오고, 그래야 옵션코드가 생긴다
                code = optmap.get(uid, {}).get(_norm(_g(row, 5)))
                if code:
                    fills.append((tab, r, uid, pum, a_val))
                    dfills.append((tab, r, code))
                else:
                    pending.append((tab, r, pum, f"통합uid {uid} · 옵션코드 대기(판매 전)"))
        for f in forms:                                  # 수식에 박힌 통합uid 회수
            all_uid.update(int(x) for x in HARDCODED_UID.findall(f or ""))

    print(f"\n★A열 채울 행 {len(fills)}건")
    for t, r, uid, pum, old in fills:
        print(f"   [{t}] R{r} {pum} → {uid}" + (f"  (기존값 '{old}' 덮어씀)" if old else ""))
    print(f"보류 {len(pending)}건 (uid 미발급 또는 옵션코드 대기)")
    for t, r, pum, why in pending[:8]:
        print(f"   [{t}] R{r} {pum} — {why}")
    if len(pending) > 8:
        print(f"   … 외 {len(pending) - 8}건")
    print(f"\n_UID_FILTER 대상 uid {len(all_uid)}건")

    if not a.apply:
        print("\n(dry-run) --apply 로 실제 기록")
        return

    if fills:
        sh.spreadsheets().values().batchUpdate(spreadsheetId=DASH, body={
            "valueInputOption": "RAW",
            "data": [{"range": f"'{t}'!A{r}", "values": [[uid]]} for t, r, uid, _, _ in fills],
        }).execute()
        print(f"A열 {len(fills)}건 기록 완료")
    if dfills:
        # ★D열 옵션코드는 반드시 TEXT — USER_ENTERED 로 쓰면 '03'→숫자 3 이 되어
        #   문자열인 MTD!U 와 매칭 실패, 조용히 0 이 된다(과거 벨트 사고).
        sids = {s["properties"]["title"]: s["properties"]["sheetId"]
                for s in sh.spreadsheets().get(spreadsheetId=DASH,
                                               fields="sheets(properties(sheetId,title))").execute()["sheets"]}
        sh.spreadsheets().batchUpdate(spreadsheetId=DASH, body={"requests": [
            {"repeatCell": {"range": {"sheetId": sids[t], "startRowIndex": r - 1, "endRowIndex": r,
                                      "startColumnIndex": 3, "endColumnIndex": 4},
                            "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT"}}},
                            "fields": "userEnteredFormat.numberFormat"}} for t, r, _ in dfills]}).execute()
        sh.spreadsheets().values().batchUpdate(spreadsheetId=DASH, body={
            "valueInputOption": "RAW",
            "data": [{"range": f"'{t}'!D{r}", "values": [[code]]} for t, r, code in dfills],
        }).execute()
        print(f"D열 옵션코드 {len(dfills)}건 기록 완료 (TEXT)")

    # _UID_FILTER 탭 (없으면 생성) — 노트북이 GOODS_FILTER 에 합집합으로 더한다.
    meta = sh.spreadsheets().get(spreadsheetId=DATA, fields="sheets(properties(sheetId,title))").execute()
    titles = {s["properties"]["title"] for s in meta["sheets"]}
    if FILTER_TAB not in titles:
        sh.spreadsheets().batchUpdate(spreadsheetId=DATA, body={"requests": [
            {"addSheet": {"properties": {"title": FILTER_TAB, "gridProperties": {"rowCount": 3000, "columnCount": 2}}}}]}).execute()
        print(f"'{FILTER_TAB}' 탭 생성")
    sh.spreadsheets().values().clear(spreadsheetId=DATA, range=f"'{FILTER_TAB}'!A:B").execute()
    sh.spreadsheets().values().update(
        spreadsheetId=DATA, range=f"'{FILTER_TAB}'!A1",
        valueInputOption="RAW",
        body={"values": [["goods_no"]] + [[u] for u in sorted(all_uid)]}).execute()
    print(f"'{FILTER_TAB}' {len(all_uid)}건 기록 완료 — 다음 09:30 잡부터 자동 반영")


if __name__ == "__main__":
    main()
