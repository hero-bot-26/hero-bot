# -*- coding: utf-8 -*-
"""26FW 목표를 대시보드 시트 히어로 탭에 채운다 (컬러별 = STY 목표 × 물량비중).

★★2026-08-04 daily CI에서 제외됨 — 수동 도구로만 남긴다.
   이 스크립트는 **시즌 누계(YTD)** 목표를 **MTD 목표 칸(AI/BH)** 에 값으로 써서, 8/1부터
   커브드팬츠 MTD 목표가 7.5배로 부풀었다(7월엔 MTD==시즌누계라 드러나지 않았음).
   또 값 덮어쓰기라 담당자가 `히어로목표(거래량)` 탭을 고쳐도 MTD 칸에 반영되지 않았다.
   현재 대시보드 목표의 진실소스 = `히어로목표(거래량)` 탭 + 각 탭 SUMPRODUCT 수식.
   ⚠재사용한다면 AI/BH에는 반드시 tq["MTD"] 를 쓸 것(tq["YTD"] 는 누적 목표칸 HZ 용).

배경(2026-07-31): 앱은 담당자 시트 `26FW HERO 일자별 목표 셋팅`(.xlsx)을 읽어 26FW 목표를 쓰는데,
대시보드 시트는 자체 `히어로목표(거래량)` 탭(26SS 기준)을 물고 있어 26FW 목표가 0으로 비어 있었다.
목표 파일이 **엑셀(.xlsx)이라 IMPORTRANGE가 안 되므로**, 봇이 매일 읽어 값으로 적어준다.

계산: 목표 시트는 **품번 × 채널(ON/OFF) × 일자** 단위 → 시즌 누계 창(7/1~전일)으로 합산한 뒤,
      대시보드 히어로 탭의 **HW열(물량비중, 블록 내 정규화 = 그 STY 안에서 컬러가 차지하는 비율)** 로 안분.
      컬러행 AI(온라인 목표) · BH(오프라인 목표)에 값으로 기록 → STY행·히어로행은 기존 SUM 수식이 자동 집계.
      ※ 비중은 채널 공통(준비물량 기준)이라 온·오프 컬러 구성이 다르면 근사치.

안전장치: 히어로 탭별로 기존 AI/BH 컬러행 수식을 `_dash_targets_backup_<탭>.json`에 백업.
          목표가 없는 히어로(목표 시트 미세팅)는 건드리지 않는다.

실행: python push_26fw_targets.py [탭 ...] [--apply]
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
from soo.hero_ops.target_26fw import parse_26fw_targets  # noqa: E402

DASH = "1-A04_TwKZJNPkFg27USkKAScZRu6CAhbgVeXk9c09nA"
COL_ON, COL_OFF, COL_SHARE, COL_PREP = "AI", "BH", "HW", "HT"
SUMRANGE = re.compile(r"^=sum\(HT(\d+):HT(\d+)\)$", re.I)
CODE = re.compile(r"^M[A-Z0-9]{8}(-[A-Z0-9]{2})?$")
TABS = ["커브드팬츠", "라이트다운", "빅토리아 울", "웜 팬츠", "그리드/메시 플리스", "에센셜 플리스",
        "슬랙스", "데님팬츠", "스웨트팬츠", "심리스 브라", "양말", "벨트", "힛탠다드", "리커버리", "헤비다운"]


def _g(r, j):
    return str(r[j]).strip() if len(r) > j and r[j] is not None else ""


def _f(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tabs", nargs="*")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sh, drive = svc["sheets"], svc["drive"]

    import datetime
    as_of = (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).date().isoformat()
    tgt = parse_26fw_targets(drive, as_of)
    meta = tgt.pop("_meta", {})
    print(f"목표 시트: 스타일 {len(tgt)} · 시즌창 {meta.get('windows', {}).get('YTD')}")

    for tab in (a.tabs or TABS):
        rng = sh.spreadsheets().values().batchGet(
            spreadsheetId=DASH,
            ranges=[f"'{tab}'!A10:C200", f"'{tab}'!{COL_SHARE}10:{COL_SHARE}200",
                    f"'{tab}'!{COL_PREP}10:{COL_PREP}200"],
            valueRenderOption="UNFORMATTED_VALUE").execute()["valueRanges"]
        ac = rng[0].get("values", [])
        share = rng[1].get("values", [])
        # ★블록(벌) 경계 — 시트는 온라인=통합uid/오프라인=개별uid 합산 때문에 같은 STY를 여러 벌로 깔아둔다.
        #   히어로 총계는 그중 한 벌만 더하므로, 목표는 **벌마다 전액**을 배분해야 한다
        #   (품번 단위로 한 번에 나누면 총계가 1/벌수로 줄어든다 — 2026-07-31 실측 5,713 vs 15,718).
        htf = sh.spreadsheets().values().get(
            spreadsheetId=DASH, range=f"'{tab}'!{COL_PREP}10:{COL_PREP}200",
            valueRenderOption="FORMULA").execute().get("values", [])
        blocks = []
        for i in range(191):
            f = _g(htf[i] if i < len(htf) else [], 0)
            m = SUMRANGE.match(f)
            if m:
                blocks.append((int(m.group(1)), int(m.group(2))))
        rows = []                               # (row, 품번, 컬러코드, 비중)
        for i in range(191):
            r = 10 + i
            code = _g(ac[i] if i < len(ac) else [], 2)
            if not CODE.match(code) or "-" not in code:
                continue
            rows.append((r, code.split("-")[0], code, _f(_g(share[i] if i < len(share) else [], 0))))
        if not rows:
            print(f"[{tab}] 컬러행 없음 — 스킵")
            continue

        # (블록, STY)별로 묶어 전액 배분 — 벌이 3개면 3벌 각각에 같은 목표가 들어간다
        by_sty = defaultdict(list)
        for r, sty, code, sh_ratio in rows:
            blk = next((b for b in blocks if b[0] <= r <= b[1]), None)
            by_sty[(blk, sty)].append((r, code, sh_ratio))
        data, tot_on, tot_off, n_sty = [], 0.0, 0.0, 0
        for (blk, sty), items in by_sty.items():
            t = tgt.get(sty)
            if not t:
                continue                        # 목표 미세팅 STY는 건드리지 않는다
            n_sty += 1
            on, off = t["tq"]["YTD"]["o"], t["tq"]["YTD"]["f"]
            ssum = sum(x[2] for x in items) or 0.0
            for r, code, ratio in items:
                w = (ratio / ssum) if ssum else (1.0 / len(items))
                v_on, v_off = round(on * w), round(off * w)
                tot_on += v_on
                tot_off += v_off
                data.append({"range": f"'{tab}'!{COL_ON}{r}", "values": [[v_on]]})
                data.append({"range": f"'{tab}'!{COL_OFF}{r}", "values": [[v_off]]})
        if not data:
            print(f"[{tab}] 목표 세팅된 STY 없음 — 스킵")
            continue
        print(f"[{tab}] 블록×STY {n_sty}건 · 컬러행 {len(data)//2}개 · 목표 온라인 {tot_on:,.0f} / 오프라인 {tot_off:,.0f}")
        if not a.apply:
            continue
        # 백업(현재 AI/BH 수식)
        bk = sh.spreadsheets().values().batchGet(
            spreadsheetId=DASH, ranges=[f"'{tab}'!{COL_ON}10:{COL_ON}200", f"'{tab}'!{COL_OFF}10:{COL_OFF}200"],
            valueRenderOption="FORMULA").execute()["valueRanges"]
        (ROOT / f"_dash_targets_backup_{tab.replace(' ', '').replace('/', '')}.json").write_text(
            json.dumps(bk, ensure_ascii=False), encoding="utf-8")
        for k in range(0, len(data), 100):
            sh.spreadsheets().values().batchUpdate(
                spreadsheetId=DASH, body={"valueInputOption": "USER_ENTERED", "data": data[k:k + 100]}).execute()
        chk = sh.spreadsheets().values().get(
            spreadsheetId=DASH, range=f"'{tab}'!J12", valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [[0]])
        print(f"   → 기록 완료 · 히어로 목표 판매량(J12) = {chk[0][0] if chk and chk[0] else '-'}")


if __name__ == "__main__":
    main()
