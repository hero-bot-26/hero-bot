# -*- coding: utf-8 -*-
"""IMC 미팅용 — 라이트다운·그리드/메시 플리스·빅토리아 울 입고 물량 한판 정리.

목적(사용자): "마케팅 IMC 관점에서 물량이 잘 준비돼 있는지, 어느 주간에 얼마나 확보되는지".
소스 = 앱 INBOUND_BOARD(생산관리 입고예정 + DBX WMS 실입고) · hero_goods_26fw.json(Main/Sub·캐리/신상).
산출 = 구글시트 2탭(한판 = 주차 매트릭스 / 상세 = 차수 단위).
"""
import collections
import datetime as dt
import json
from pathlib import Path

from soo.auth import build_services, get_credentials
from soo.hero_ops.imc_triggers import load_mutan_release_dates

ROOT = Path(__file__).parent
APP = Path.home() / "hero-master-app" / "public" / "app.html"
TARGETS = ["라이트다운", "그리드/메시 플리스", "빅토리아 울"]


def _blk(html, name):
    i = html.index(f"const {name} = ") + len(f"const {name} = ")
    return json.JSONDecoder().raw_decode(html[i:])[0]


def week_start(d: dt.date) -> dt.date:      # 월요일 시작 주
    return d - dt.timedelta(days=d.weekday())


def build(sheets=None):
    html = APP.read_text(encoding="utf-8")
    B, L = _blk(html, "INBOUND_BOARD"), _blk(html, "LAUNCH_26FW")
    meta = json.loads((ROOT / "hero_goods_26fw.json").read_text(encoding="utf-8"))["styles"]
    today = dt.date.fromisoformat(B["as_of"])
    # STY별 발매일 = 무탠본부 아이템마스터(진실소스, 히어로 단위 launch보다 정밀).
    rel = {}
    if sheets is not None:
        try:
            rel = {k: v for k, v in load_mutan_release_dates(sheets)["rep_first"].items()}
        except Exception as e:
            print(f"[주의] 발매일 로드 실패 — 히어로 발매일로 대체: {type(e).__name__}: {e}")

    rows = []          # STY 단위 집계
    detail = []        # 차수 단위
    weeks = set()
    for name in TARGETS:
        h = next((x for x in B["heroes"] if x["name"] == name), None)
        lh = next((x for x in L["heroes"] if x["name"] == name), {})
        if not h:
            continue
        per_sty = collections.OrderedDict()
        for s in h.get("skus", []):
            k = s["style"]
            r = per_sty.setdefault(k, {"hero": name, "style": k, "name": s.get("name") or k,
                                       "sku": 0, "plan": 0, "recv": 0, "wk": collections.Counter(),
                                       "launch": lh.get("launch")})
            r["sku"] += 1
            r["plan"] += s.get("plan_total", 0) or 0
            r["recv"] += s.get("actual_total", 0) or 0
            for p in s.get("planned", []):
                d = dt.date.fromisoformat(p["date"])
                w = week_start(d)
                weeks.add(w)
                r["wk"][w] += p.get("qty", 0) or 0
                detail.append({"hero": name, "style": k, "name": s.get("name") or "",
                               "sku": s.get("sku"), "color": s.get("color"),
                               "date": p["date"], "week": w.isoformat(),
                               "qty": p.get("qty", 0) or 0, "recv": p.get("recv", 0) or 0})
            for a in s.get("leftover", []):     # 예정 외 입고(차수 매칭 안 된 실입고)
                detail.append({"hero": name, "style": k, "name": s.get("name") or "",
                               "sku": s.get("sku"), "color": s.get("color"),
                               "date": a["date"], "week": week_start(dt.date.fromisoformat(a["date"])).isoformat(),
                               "qty": 0, "recv": a.get("qty", 0) or 0})
        # 발매일은 있는데 입고계획이 아예 없는 STY도 드러낸다(IMC 관점 최대 리스크).
        for k, m0 in meta.items():
            if m0.get("hero") == name and k not in per_sty and k in rel:
                per_sty[k] = {"hero": name, "style": k, "name": m0.get("name") or k,
                              "sku": 0, "plan": 0, "recv": 0, "wk": collections.Counter(),
                              "launch": lh.get("launch")}
        for k, r in per_sty.items():
            m = meta.get(k, {})
            if m.get("name"):
                r["name"] = m["name"]        # 생산관리 '(가명)' 대신 확정 품명
            r["rel"] = rel.get(k)            # STY 발매일(date) — 없으면 None
            g = (m.get("grade") or "").upper()
            r["cls"] = "Sub" if "SUB" in g else ("Main" if "HERO" in g else "?")
            r["carry"] = m.get("carry") or "?"
            r["rel_season"] = m.get("rel_season") or "?"
            # 발매 전 확보량 = 발매일까지 들어오는 예정 물량(+실입고). IMC 관점 핵심 숫자.
            rd = r.get("rel")
            r["pre"] = sum(q for w, q in r["wk"].items() if rd and w <= rd) if rd else 0
            r["post"] = r["plan"] - r["pre"]
            rows.append(r)
    return rows, detail, sorted(weeks), today


def sheet_values(rows, weeks, today):
    """한판 탭 — 히어로 그룹 → STY 행(발매일순), 열 = 실입고 + 발매전/후 + 주차별 예정."""
    hdr = ["히어로", "발매일", "구분", "신상/캐리", "발매시즌", "스타일명", "품번", "SKU",
           "실입고(누적)", "예정 합계", "발매전 확보", "발매후 입고"] + [f"{w.month}/{w.day}주" for w in weeks]
    out = [hdr]
    fut = [w for w in weeks if w >= week_start(today)]
    FAR = dt.date(2099, 1, 1)

    def wk_cells(items):
        return [sum(r["wk"].get(w, 0) for r in items) or "" for w in weeks]

    # 히어로도 '가장 이른 발매일' 순으로
    order = sorted(TARGETS, key=lambda t: min([r["rel"] or FAR for r in rows if r["hero"] == t] or [FAR]))
    for hero in order:
        hr = [r for r in rows if r["hero"] == hero]
        if not hr:
            continue
        hr.sort(key=lambda r: (r["rel"] or FAR, 0 if r["cls"] == "Main" else 1, -r["plan"]))
        rels = [r["rel"] for r in hr if r["rel"]]
        span = f"{rels[0]} ~ {rels[-1]}" if rels and rels[0] != rels[-1] else (str(rels[0]) if rels else "-")
        out.append([f"▣ {hero}", span, "", "", "", "", "", sum(r["sku"] for r in hr),
                    sum(r["recv"] for r in hr), sum(r["plan"] for r in hr),
                    sum(r["pre"] for r in hr), sum(r["post"] for r in hr)] + wk_cells(hr))
        for r in hr:
            note = "" if r["plan"] or r["recv"] else "⚠ 입고계획 없음"
            out.append([" ", (str(r["rel"]) if r["rel"] else "미정"), r["cls"], r["carry"],
                        r["rel_season"], r["name"] + (f"  {note}" if note else ""), r["style"], r["sku"],
                        r["recv"] or "", r["plan"] or "", r["pre"] or "", r["post"] or ""]
                       + [r["wk"].get(w, 0) or "" for w in weeks])
        out.append([""] * len(hdr))
    out.append(["■ 3종 주간 합계", "", "", "", "", "", "", "",
                sum(r["recv"] for r in rows), sum(r["plan"] for r in rows),
                sum(r["pre"] for r in rows), sum(r["post"] for r in rows)] + wk_cells(rows))
    out.append(["■ 누적(주 누계)", "", "", "", "", "", "", "", "", "", "", ""]
               + [sum(sum(r["wk"].get(w2, 0) for r in rows) for w2 in weeks if w2 <= w) for w in weeks])
    out.append([])
    out.append([f"※ 기준 {today.isoformat()} · 입고보드(생산관리 입고예정 + DBX WMS 실입고) · 주는 월요일 시작 · 향후 주차 {len(fut)}개"])
    out.append(["※ 발매일 = 무탠본부 아이템마스터(진실소스, STY 대표품번 최초 발매일) · 구분 Main=HERO / Sub=HERO SUB"])
    out.append(["※ 발매전 확보 = 발매일이 속한 주까지 들어오는 예정 물량 합(실입고 별도 열) — IMC 관점 '발매 시점 판매 가능 물량'"])
    return out


def main():
    import sys
    svc = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))
    sh, dr = svc["sheets"], svc["drive"]
    rows, detail, weeks, today = build(sh)
    sid = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--sid=")), "")
    if not sid:
        title = f"26FW 입고물량 한판 (라이트다운·그리드메시·빅토리아울) {today.isoformat()}"
        sid = sh.spreadsheets().create(body={"properties": {"title": title},
                                             "sheets": [{"properties": {"title": "한판"}},
                                                        {"properties": {"title": "상세"}}]}
                                       ).execute()["spreadsheetId"]
        T0, T1 = "한판", "상세"
    # ★탭 이름은 담당자가 바꿀 수 있다 → 이름이 아니라 '순서'로 참조(0=한판, 1=상세)
    _props = [x["properties"] for x in sh.spreadsheets().get(
        spreadsheetId=sid, fields="sheets.properties").execute()["sheets"]]
    T0, T1 = _props[0]["title"], _props[1]["title"]
    for t in (T0, T1):
        sh.spreadsheets().values().clear(spreadsheetId=sid, range=f"'{t}'!A1:AZ400").execute()
    one = sheet_values(rows, weeks, today)
    det = [["히어로", "발매일", "구분", "스타일명", "품번", "SKU", "컬러", "입고예정일", "주(월요일)", "예정수량", "실입고"]]
    info = {r["style"]: r for r in rows}
    for d in sorted(detail, key=lambda x: (str(info.get(x["style"], {}).get("rel") or "9999"), x["hero"], x["date"], x["style"])):
        i = info.get(d["style"], {})
        det.append([d["hero"], str(i.get("rel") or "미정"), i.get("cls", ""), i.get("name") or d["name"],
                    d["style"], d["sku"], d["color"], d["date"], d["week"], d["qty"] or "", d["recv"] or ""])
    sh.spreadsheets().values().batchUpdate(spreadsheetId=sid, body={
        "valueInputOption": "RAW",
        "data": [{"range": f"'{T0}'!A1", "values": one}, {"range": f"'{T1}'!A1", "values": det}]}).execute()
    dr.permissions().create(fileId=sid, body={"type": "domain", "domain": "musinsa.com",
                                              "role": "writer"}, supportsAllDrives=True).execute()
    print(f"시트: https://docs.google.com/spreadsheets/d/{sid}")
    print(f"한판 {len(one)}행 · 상세 {len(det)}행 · 주차 {len(weeks)}개 "
          f"({weeks[0]} ~ {weeks[-1]})")
    return sid, rows, weeks, today


if __name__ == "__main__":
    main()
