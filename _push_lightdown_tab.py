# -*- coding: utf-8 -*-
"""25FW 라이트다운 주간 시계열 → 미팅 시트 '작년 라이트다운 주간' 탭 갱신(표 + 라인차트).
   ★대상 = 주력 3 STY(Main 후디드 MMDDJAZ01 / Sub1 시어 MMEDJ9A04 / Sub2 우먼즈 MWEDJ9B57) 24 uid.
"""
import collections
import json
from pathlib import Path

from soo.auth import build_services, get_credentials

ROOT = Path(__file__).parent
SID = "1GBnvUyQItjB5H0sowch3VlI8QPPliGBkP9wCO6mVqJ4"
TITLE = "작년 라이트다운 주간"


def main():
    d = json.loads((ROOT / "_lightdown_25fw_weekly.json").read_text(encoding="utf-8"))
    sty_map = {}          # uid -> (sty, label)
    for sty, v in (d.get("sty_uids") or {}).items():
        for u in v["uids"]:
            sty_map[str(u)] = (sty, v["label"])

    byw = collections.defaultdict(lambda: {"Online": [0, 0], "Offline": [0, 0]})
    for r in d["weekly"]:
        byw[r["week_start"]][r["channel"]] = [float(r["gmv"] or 0), int(float(r["qty"] or 0))]
    ks = sorted(byw)

    # STY(주력 3종)별 주간 GMV — '무엇이 끌었나' 분해
    sty_wk = collections.defaultdict(lambda: collections.defaultdict(float))
    for r in d.get("weekly_by_goods", []):
        s = sty_map.get(str(r["goods_no"]), ("기타", "기타"))[0]
        sty_wk[s][r["week_start"]] += float(r["gmv"] or 0)
    stys = [s for s in (d.get("sty_uids") or {}) if s in sty_wk]

    svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sh = svc["sheets"]
    props = [x["properties"] for x in sh.spreadsheets().get(
        spreadsheetId=SID, fields="sheets.properties").execute()["sheets"]]
    p = next((x for x in props if x["title"] == TITLE), None)
    if p is None:
        p = sh.spreadsheets().batchUpdate(spreadsheetId=SID, body={"requests": [
            {"addSheet": {"properties": {"title": TITLE,
                                         "gridProperties": {"rowCount": 90, "columnCount": 20}}}}]}
        ).execute()["replies"][0]["addSheet"]["properties"]
    gid = p["sheetId"]
    # 기존 차트 제거(열 구성이 바뀌므로 다시 그린다)
    full = sh.spreadsheets().get(spreadsheetId=SID, fields="sheets(properties,charts)").execute()
    reqs = []
    for shx in full["sheets"]:
        if shx["properties"]["title"] == TITLE:
            for c in shx.get("charts", []) or []:
                reqs.append({"deleteEmbeddedObject": {"objectId": c["chartId"]}})
    if reqs:
        sh.spreadsheets().batchUpdate(spreadsheetId=SID, body={"requests": reqs}).execute()
    sh.spreadsheets().values().clear(spreadsheetId=SID, range=f"'{TITLE}'!A1:T90").execute()

    hdr = ["주(월요일 시작)", "온라인 GMV", "오프라인 GMV", "합계 GMV",
           "온라인 수량", "오프라인 수량", "합계 수량", "온라인 비중"] + \
          [f"{s} GMV" for s in stys]
    vals = [hdr]
    for k in ks:
        o, f = byw[k]["Online"], byw[k]["Offline"]
        tot = o[0] + f[0]
        vals.append([k, int(o[0]), int(f[0]), int(tot), o[1], f[1], o[1] + f[1],
                     (o[0] / tot if tot else 0)] + [int(sty_wk[s].get(k, 0)) for s in stys])
    on, off = sum(byw[k]["Online"][0] for k in ks), sum(byw[k]["Offline"][0] for k in ks)
    vals.append(["합계", int(on), int(off), int(on + off),
                 sum(byw[k]["Online"][1] for k in ks), sum(byw[k]["Offline"][1] for k in ks),
                 sum(byw[k]["Online"][1] + byw[k]["Offline"][1] for k in ks),
                 (on / (on + off) if on + off else 0)] + [int(sum(sty_wk[s].values())) for s in stys])
    vals.append([])
    lab = " · ".join(f"{s}({(d['sty_uids'][s]['label'])}, {len(d['sty_uids'][s]['uids'])}uid)" for s in d.get("sty_uids", {}))
    vals.append([f"※ 대상 = 25FW 라이트다운 주력 3 STY / 총 {len(sty_map)} uid — {lab}"])
    vals.append(["※ 기간 = 2025-08-04(8월 1주) 시작 주간 버킷 · 판매 없는 주는 행 없음"])
    vals.append(["※ 온라인=orders_merged(무탠 4개 브랜드, musinsa/musinsa_event 제외) · "
                 "오프라인=pos_order_sales(offline·selectshop) — 앱 실적 대시보드와 동일 정의"])
    sh.spreadsheets().values().update(spreadsheetId=SID, range=f"'{TITLE}'!A1",
                                      valueInputOption="RAW", body={"values": vals}).execute()

    n = len(ks) + 1
    ncol = len(hdr)
    req = [
        {"appendDimension": {"sheetId": gid, "dimension": "COLUMNS", "length": 12}},
        {"updateSheetProperties": {"properties": {"sheetId": gid, "gridProperties": {"frozenRowCount": 1}},
                                   "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.12, "green": 0.13, "blue": 0.15},
                                                       "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                                                       "horizontalAlignment": "CENTER", "wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat"}},
        {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 1, "endRowIndex": n + 1,
                                  "startColumnIndex": 1, "endColumnIndex": 7},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
                        "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 1, "endRowIndex": n + 1,
                                  "startColumnIndex": 8, "endColumnIndex": ncol},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
                        "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 1, "endRowIndex": n + 1,
                                  "startColumnIndex": 7, "endColumnIndex": 8},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}},
                        "fields": "userEnteredFormat.numberFormat"}},
        {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": n, "endRowIndex": n + 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                                       "backgroundColor": {"red": 0.95, "green": 0.95, "blue": 0.95}}},
                        "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor"}},
        {"updateDimensionProperties": {"range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": ncol},
                                       "properties": {"pixelSize": 122}, "fields": "pixelSize"}},
    ]

    def chart(title, cols, anchor_row, stacked=False):
        return {"addChart": {"chart": {"spec": {
            "title": title,
            "basicChart": {**({"chartType": "COLUMN", "stackedType": "STACKED"} if stacked
                              else {"chartType": "LINE"}),
                           "legendPosition": "TOP_LEGEND", "headerCount": 1,
                           "axis": [{"position": "BOTTOM_AXIS", "title": "주 (월요일 시작)"},
                                    {"position": "LEFT_AXIS", "title": "GMV"}],
                           "domains": [{"domain": {"sourceRange": {"sources": [
                               {"sheetId": gid, "startRowIndex": 0, "endRowIndex": n,
                                "startColumnIndex": 0, "endColumnIndex": 1}]}}}],
                           "series": [{"series": {"sourceRange": {"sources": [
                               {"sheetId": gid, "startRowIndex": 0, "endRowIndex": n,
                                "startColumnIndex": ci, "endColumnIndex": ci + 1}]}},
                               "targetAxis": "LEFT_AXIS"} for ci in cols]}},
            "position": {"overlayPosition": {"anchorCell": {"sheetId": gid, "rowIndex": anchor_row,
                                                            "columnIndex": ncol + 1},
                                             "widthPixels": 900, "heightPixels": 380}}}}}

    req.append(chart("25FW 라이트다운 — 주간 온·오프라인 GMV", [1, 2], 1))
    if stys:
        req.append(chart("25FW 라이트다운 — 주력 STY별 주간 GMV(누적)",
                         list(range(8, 8 + len(stys))), 22, stacked=True))
    sh.spreadsheets().batchUpdate(spreadsheetId=SID, body={"requests": req}).execute()
    print(f"'{TITLE}' 갱신 — {len(ks)}주 · STY {len(stys)}종 · 총 {int(on + off):,}원 "
          f"(온 {int(on):,} / 오프 {int(off):,})")


if __name__ == "__main__":
    main()
