"""goods_no → (히어로명, 시즌) 매핑 + 신품번 → 히어로 매핑.

매출/재고/입고는 goods_no 기준인데 히어로 정의(HERO STY)는 신품번 기준이고 style 코드 체계가
달라(매출 style_no=구코드) 직접 조인이 깨진다. → **goods_no 기준** 매핑을 두 소스에서 만든다.

  ① 26SS(현재 판매중) — 전사 대시보드 시트의 히어로별 탭(워셔블수피마 등) A열=goods_no, B열=신품번.
  ② 26FW(8월~ 발매, 중첩) — HERO 마스터 시트의 'SKU' 탭 I열=uid(=goods_no), P열=신품번
       → HERO STY 탭(신품번→시리즈)으로 히어로명 조인. (사용자 제공 경로: uid 매칭)

precedence: 26SS(판매중) 우선, 26FW가 빈 goods 채움. (carryover로 양쪽에 있으면 판매중 라벨 유지)
운영 전환 시 Databricks goods_filter를 (goods_no,hero,season) 라벨드 테이블로 만들면 이 파싱은 불필요.
"""
from __future__ import annotations

import re

from soo.hero_ops.sales_rollup import build_style_to_hero

DEV_SHEET_ID = "1aAYXjJPFgWCJAmZabc_f-f-wF3z492cIeDE-aVlx-HY"      # 전사 대시보드(26SS 판매중)
HERO_SHEET = "1tvtbz6u3xob_SkZQBH79xX6J8dRpsHAa1-nn-KMeY-g"        # HERO 마스터(26FW)
STYLE_RE = re.compile(r"^M[A-Z0-9]{8}$")

# 전사 시트의 판매중(26SS) 히어로 탭 → 표시용 히어로명
SS_HERO_TABS = {
    "워셔블수피마": "워셔블 수피마", "커브드팬츠": "커브드 팬츠", "윈드브레이커": "윈드 브레이커",
    "심리스브라": "심리스 브라", "NEW 티셔츠": "NEW 티셔츠", "쿨탠다드티셔츠": "쿨탠다드 티셔츠",
    "쿨탠다드팬츠": "쿨탠다드 팬츠", "버뮤다 팬츠": "버뮤다 팬츠", "탱크탑": "탱크탑",
}
FW_OVERRIDES: dict[int, str] = {}   # 자동조인 누락분 수기 보정 (필요 시)


def _ss_maps(sheets, sheet_id=DEV_SHEET_ID, tabs=None):
    """전사 히어로 탭 → (goods_no→hero, 신품번base→hero) [26SS]."""
    tabs = tabs or SS_HERO_TABS
    goods, style = {}, {}
    for tab, hero in tabs.items():
        res = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{tab}'!A13:B400",
            valueRenderOption="UNFORMATTED_VALUE").execute()
        for row in res.get("values", []):
            if row:
                try:
                    gid = int(row[0])
                    if gid > 100000:
                        goods.setdefault(gid, hero)
                except (TypeError, ValueError):
                    pass
            if len(row) > 1 and row[1]:
                base = str(row[1]).strip().split("-")[0]
                if STYLE_RE.match(base):
                    style.setdefault(base, hero)
    return goods, style


def _fw_maps(sheets, sheet_id=HERO_SHEET):
    """HERO 마스터 SKU 탭(uid→신품번) + HERO STY(신품번→시리즈) → (goods_no→hero, 신품번→hero) [26FW]."""
    style2hero, _, _ = build_style_to_hero(sheets, sheet_id, "'HERO STY'!A7:M400")
    res = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range="'SKU'!A5:Q15400",
        valueRenderOption="UNFORMATTED_VALUE").execute()
    goods = {}
    for row in res.get("values", []):
        uid = row[8] if len(row) > 8 else None       # I = uid(=goods_no)
        style = row[15] if len(row) > 15 else None    # P = 신품번
        if uid is None or not style:
            continue
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            continue
        base = str(style).strip().split("-")[0]
        if base in style2hero:
            goods.setdefault(uid, style2hero[base])
    return goods, style2hero


def build_maps(sheets):
    """({goods_no: {'hero','season'}}, {신품번base: hero}) 반환. 26SS 우선 + 26FW 보강."""
    ss_goods, ss_style = _ss_maps(sheets)
    fw_goods, fw_style = _fw_maps(sheets)

    goods_to_hero = {g: {"hero": h, "season": "26SS"} for g, h in ss_goods.items()}
    for g, h in fw_goods.items():
        goods_to_hero.setdefault(g, {"hero": h, "season": "26FW"})   # 판매중(26SS) 우선
    for g, h in FW_OVERRIDES.items():
        goods_to_hero[int(g)] = {"hero": h, "season": "26FW"}

    style_to_hero = dict(fw_style)
    style_to_hero.update(ss_style)   # 26SS 우선
    return goods_to_hero, style_to_hero


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from soo.auth import get_credentials, build_services
    from collections import Counter
    ROOT = Path(__file__).resolve().parents[2]
    sheets = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))["sheets"]
    g2h, s2h = build_maps(sheets)
    print(f"goods_no→hero {len(g2h)}개 / 신품번→hero {len(s2h)}개")
    bys = Counter(v["season"] for v in g2h.values())
    print("시즌 분포:", dict(bys))
    for (hero, season), n in sorted(Counter((v["hero"], v["season"]) for v in g2h.values()).items()):
        print(f"  [{season}] {hero:16} {n}")
