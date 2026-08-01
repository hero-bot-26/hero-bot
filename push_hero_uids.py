# -*- coding: utf-8 -*-
"""히어로 uid 목록을 실적 시트 `_히어로UID` 탭에 매일 밀어넣는다 (DBX 노트북이 이걸 읽어 필터로 씀).

배경(2026-07-31): 노트북 `GOODS_FILTER`가 **수기 uid 목록**이라 MSTRD에 새 컬러/스타일 uid가 생기면
그 상품 매출이 앱 원천에서 통째로 빠졌다(라이트다운 uid 2개 = 1,678,800원 과소집계, 26FW 60 uid 누락).
→ 매일 CI가 이 탭을 갱신하고, 노트북은 하드코딩 대신 **이 탭을 읽어 필터를 만든다**(탭이 비면 기존 목록 폴백).

소스: MSTRD `HERO STY`(26FW 라이브) + `hero_goods_26ss.json`(26SS 확정 매핑).
탭 포맷: 1행 라벨 · 2행 헤더(uid/hero/season) · 3행~ 데이터 — 다른 raw 탭과 동일 규약.

실행: python push_hero_uids.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from soo.auth import build_services, get_credentials  # noqa: E402
from soo.hero_ops.sales_rollup import SALES_SHEET_ID  # noqa: E402

TAB = "_히어로UID"


def main():
    svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sheets = svc["sheets"]
    rows = []

    # 26FW — MSTRD HERO STY 라이브(발매/컬러 추가가 바로 반영)
    try:
        from soo.hero_ops.imc_triggers import load_26fw_hero_goods
        fw = load_26fw_hero_goods(sheets)
        for uid, hero in (fw.get("goods_to_hero") or {}).items():
            rows.append([str(uid), hero, "26FW"])
        print(f"26FW uid {len(fw.get('goods_to_hero') or {})}")
    except Exception as e:
        print(f"[주의] 26FW 매핑 로드 실패 — 스냅샷 사용: {type(e).__name__}: {e}")
        snap = json.load(open(ROOT / "hero_goods_26fw.json", encoding="utf-8"))
        for uid, hero in snap["goods_to_hero"].items():
            rows.append([str(uid), hero, "26FW"])

    # 26SS — 확정 매핑(시트39 기준)
    try:
        ss = json.load(open(ROOT / "hero_goods_26ss.json", encoding="utf-8"))
        for uid, hero in (ss.get("goods_to_hero") or {}).items():
            rows.append([str(uid), hero, "26SS"])
        print(f"26SS uid {len(ss.get('goods_to_hero') or {})}")
    except Exception as e:
        print(f"[주의] 26SS 매핑 로드 실패: {type(e).__name__}: {e}")

    # ★중복 제거 키 = (uid, season) — 2026-08-01 변경.
    #   예전엔 uid 하나만 남겨(26FW 우선) 캐리오버 uid의 26SS 행이 통째로 사라졌다(26SS 양말 15→0).
    #   노트북 GOODS_FILTER는 uid를 DISTINCT로 다시 접으므로 필터엔 영향이 없고,
    #   히어로 단위 경로 집계(v_hero_map)에서 시즌 레인이 제대로 갈린다.
    seen, out = set(), []
    for uid, hero, season in rows:
        if not uid.isdigit() or (uid, season) in seen:
            continue
        seen.add((uid, season))
        out.append([uid, hero, season])
    out.sort(key=lambda r: int(r[0]))
    if len(out) < 500:      # 안전장치 — 비정상적으로 적으면 시트를 덮지 않는다
        print(f"[중단] uid {len(out)}개뿐 — 탭을 덮지 않음(매핑 로드 실패 의심)")
        return 1

    body = [[f"히어로 uid 목록 (26FW MSTRD 라이브 + 26SS 확정) · {len(out)}개 · 노트북 GOODS_FILTER 소스"],
            ["uid", "hero", "season"]] + out
    meta = sheets.spreadsheets().get(spreadsheetId=SALES_SHEET_ID,
                                     fields="sheets.properties(title,sheetId)").execute()
    titles = {s["properties"]["title"] for s in meta["sheets"]}
    if TAB not in titles:
        sheets.spreadsheets().batchUpdate(spreadsheetId=SALES_SHEET_ID, body={"requests": [
            {"addSheet": {"properties": {"title": TAB, "gridProperties": {"rowCount": len(body) + 50, "columnCount": 3}}}}]}).execute()
        print(f"'{TAB}' 탭 생성")
    sheets.spreadsheets().values().clear(spreadsheetId=SALES_SHEET_ID, range=f"'{TAB}'!A:C").execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=SALES_SHEET_ID, range=f"'{TAB}'!A1",
        valueInputOption="RAW", body={"values": body}).execute()
    print(f"'{TAB}' 갱신 완료 — uid {len(out)}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
