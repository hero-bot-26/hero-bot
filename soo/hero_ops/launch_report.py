# -*- coding: utf-8 -*-
"""발매 상품 시간별 판매현황 리포트 — 구글시트 적재 + 슬랙 DM.

2026-08-26 발매 2종(라이트다운 11:00 · 메시 플리스 후디 17:00)을 발매시각부터
실행 시점까지 1시간 단위로 집계해 회의 직전에 손에 쥐어 준다.

★왜 "매시간 적재"가 아닌가
  주문 원장(`musinsa.order_group.order_opt`)에 `ord_date` 가 남아 있어 사후에 조회해도
  시간별 추이가 그대로 복원된다. 매시간 스냅샷을 쌓을 이유가 없다.
  다만 두 가지는 사후 복원이 안 된다 —
    ① 취소·상태변경은 소급 반영된다(그 시각의 값이 아니라 '지금 본' 값이다)
    ② 품절 상태는 CDC 가 현재값만 들고 있어 재입고-재품절이 있으면 이력이 덮인다
       (실제로 라이트그레이 S 의 품절시각이 12:49 → 13:28 로 갱신되는 걸 목격했다)
  그래서 리포트에 '읽은 시각'과 '데이터 기준시각'을 항상 같이 박는다([[CLAUDE 1-4]]).

★배치 타이밍 함정
  운영 DB 미러는 `musinsa_cdc_merge_hourly` 잡이 매시 :10 시작 → :19~:20 커밋하며
  **직전 정시까지만** 넣는다. 09:00 에 실행하면 08:00 까지밖에 못 본다.
  09:00 슬롯(1차)과 09:25 슬롯(2차)을 나눈 이유가 이것이다.

★GitHub schedule 은 이 레포에서 수 시간 밀린다(hourly.yml 주석 참조).
  그래서 워크플로에 슬롯당 cron 을 여러 번 걸고, 여기서 슬롯 판정 + 발송로그로 멱등을 잡는다.

사용:
  python -m soo.hero_ops.launch_report --dry-run
  python -m soo.hero_ops.launch_report --slot 1차
  python -m soo.hero_ops.launch_report --force
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent.parent
KST = timezone(timedelta(hours=9))

SHEET_ID = "1e43H7bd5vkI56mAfyly2LpfDxRq_zDxybRD3dXI3ROY"
TAB_HOURLY, TAB_COLOR, TAB_LOG = "시간별", "컬러별", "_발송로그"

DBX_HOST_DEFAULT = "https://musinsa-data-ws.cloud.databricks.com"
WAREHOUSE = "c0ee970a9c3ed562"

# 대상 발매 상품. goods_no 는 박지 않는다 — style_no 로 마스터에서 찾아 전부 합산한다
# (통합 UID 전환·컬러 추가로 goods_no 는 계속 늘어난다. [[CLAUDE 1-1]])
LAUNCHES = [
    {"style": "MMFDJ9A82", "label": "시어 후디드 라이트 다운 재킷", "launch": "2026-08-26 11:00"},
    {"style": "MMFFE9A81", "label": "메시 플리스 후디드 긴소매 티셔츠", "launch": "2026-08-26 17:00"},
]
WINDOW_START = "2026-08-26 11:00"   # 사용자 요청 시작점
# 8/27 회의용 일회성 리포트다. 이 날짜가 지나면 스케줄이 남아 있어도 조용히 끝낸다
# (워크플로를 지우는 걸 잊어 매일 DM 이 오는 상황을 막는다).
EXPIRE_AFTER = "2026-08-27"


# ── Databricks ───────────────────────────────────────────────────────────────
def _dbx_token() -> str:
    tok = (os.environ.get("DATABRICKS_PAT") or "").strip()
    if tok:
        return tok
    p = Path.home() / ".databricks_pat"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def dbx_sql(sql: str, wait: int = 300) -> list[list]:
    """SQL 실행 후 data_array 반환. 실패하면 예외를 올린다(조용한 0 금지, [[CLAUDE 2-6]])."""
    host = (os.environ.get("DATABRICKS_HOST") or "").strip() or DBX_HOST_DEFAULT
    tok = _dbx_token()
    if not tok:
        raise RuntimeError("DATABRICKS_PAT 없음")

    def api(method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            host + path, data=json.dumps(body).encode() if body else None, method=method,
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"DBX HTTP {e.code}: {e.read().decode()[:400]}") from e

    r = api("POST", "/api/2.0/sql/statements", {
        "warehouse_id": WAREHOUSE, "statement": sql, "wait_timeout": "30s",
        "on_wait_timeout": "CONTINUE", "format": "JSON_ARRAY", "disposition": "INLINE"})
    sid = r.get("statement_id")
    t0 = time.time()
    while r.get("status", {}).get("state") in ("PENDING", "RUNNING") and time.time() - t0 < wait:
        time.sleep(3)
        r = api("GET", f"/api/2.0/sql/statements/{sid}")
    st = r.get("status", {})
    if st.get("state") != "SUCCEEDED":
        raise RuntimeError(f"DBX 실패: {json.dumps(st, ensure_ascii=False)[:400]}")
    return (r.get("result") or {}).get("data_array") or []


def goods_nos(style: str) -> tuple[list[int], list[int]]:
    """(판매실적을 합산할 전체 goods_no, 재고를 볼 판매중 goods_no).

    ★실적은 구 컬러별 UID 까지 전부 더한다(통합 UID 전환 전 주문이 섞인다).
      반면 품절·옵션수는 **판매중인 UID 만** 봐야 한다 — 판매중지된 구 컬러 상품의 옵션이
      전부 out_of_stock=1 로 남아 있어, 섞으면 '품절 31/90개' 같은 헛수가 나온다(실제로 밟음)."""
    rows = dbx_sql(f"""
        SELECT goods_no, sale_stat_cl_nm FROM datamart.datamart.goods
        WHERE style_no = '{style}' OR style_no LIKE '{style}-%'
    """)
    allg = sorted({int(r[0]) for r in rows if r and r[0] is not None})
    live = sorted({int(r[0]) for r in rows if r and r[0] is not None and r[1] == "판매중"})
    return allg, (live or allg)


def data_asof() -> str:
    rows = dbx_sql("""
        SELECT MAX(ord_date) FROM musinsa.order_group.order_opt
        WHERE ord_date >= TIMESTAMP'2026-08-26 00:00:00'
    """)
    return (rows[0][0] if rows and rows[0] else "") or ""


def hourly(gnos: list[int]) -> list[dict]:
    ins = ",".join(str(g) for g in gnos)
    rows = dbx_sql(f"""
        SELECT date_format(ord_date,'yyyy-MM-dd HH') AS h,
               COUNT(DISTINCT CASE WHEN ord_state > 0 THEN ord_no END) AS ord_cnt,
               SUM(CASE WHEN ord_state > 0 THEN qty ELSE 0 END)        AS qty,
               SUM(CASE WHEN ord_state > 0 THEN recv_amt ELSE 0 END)   AS amt,
               COUNT(DISTINCT CASE WHEN ord_state < 0 THEN ord_no END) AS cancel
        FROM musinsa.order_group.order_opt
        WHERE ord_date >= TIMESTAMP'{WINDOW_START}' AND goods_no IN ({ins})
        GROUP BY 1 ORDER BY 1
    """)
    out = []
    for r in rows:
        out.append({"h": r[0], "ord": int(r[1] or 0), "qty": int(r[2] or 0),
                    "amt": int(r[3] or 0), "cancel": int(r[4] or 0)})
    return out


def by_color(gnos: list[int]) -> list[dict]:
    """컬러별 누계. 옵션이 [컬러,사이즈] 2원소인 통합 UID 만 컬러로 가른다 —
    구 컬러별 goods_no 는 옵션이 사이즈뿐이라 [0] 을 컬러로 읽으면 사이즈가 컬러로 둔갑한다."""
    ins = ",".join(str(g) for g in gnos)
    rows = dbx_sql(f"""
        SELECT CASE WHEN goods_option_name LIKE '%","%'
                    THEN split(replace(replace(goods_option_name,'["',''),'"]',''),'","')[0]
                    ELSE '(구 UID·컬러별 상품)' END AS color,
               SUM(qty) AS qty, COUNT(DISTINCT ord_no) AS ord_cnt
        FROM musinsa.order_group.order_opt
        WHERE ord_date >= TIMESTAMP'{WINDOW_START}' AND goods_no IN ({ins}) AND ord_state > 0
        GROUP BY 1 ORDER BY 2 DESC
    """)
    return [{"color": r[0], "qty": int(r[1] or 0), "ord": int(r[2] or 0)} for r in rows]


def soldout(gnos: list[int]) -> list[tuple[str, str]]:
    ins = ",".join(str(g) for g in gnos)
    rows = dbx_sql(f"""
        SELECT managed_code, CAST(ut AS STRING)
        FROM musinsa.bizest.goods_options_items
        WHERE goods_no IN ({ins}) AND Op <> 'delete'
          AND activated = 1 AND out_of_stock = 1
        ORDER BY ut
    """)
    return [(r[0], (r[1] or "")[:16].replace("T", " ")) for r in rows]


def option_total(gnos: list[int]) -> int:
    ins = ",".join(str(g) for g in gnos)
    rows = dbx_sql(f"""
        SELECT COUNT(*) FROM musinsa.bizest.goods_options_items
        WHERE goods_no IN ({ins}) AND Op <> 'delete' AND activated = 1
    """)
    return int(rows[0][0]) if rows else 0


# ── 집계 ─────────────────────────────────────────────────────────────────────
def collect() -> list[dict]:
    out = []
    for spec in LAUNCHES:
        gnos, live = goods_nos(spec["style"])
        if not gnos:
            out.append({**spec, "gnos": [], "hourly": [], "colors": [], "soldout": [],
                        "opt_total": 0, "error": "goods_no 조회 0건"})
            continue
        hrs = hourly(gnos)
        out.append({**spec, "gnos": gnos, "hourly": hrs, "colors": by_color(gnos),
                    "soldout": soldout(live), "opt_total": option_total(live), "error": ""})
    return out


def totals(hrs: list[dict]) -> dict:
    return {"ord": sum(h["ord"] for h in hrs), "qty": sum(h["qty"] for h in hrs),
            "amt": sum(h["amt"] for h in hrs), "cancel": sum(h["cancel"] for h in hrs)}


# ── 출력 ─────────────────────────────────────────────────────────────────────
def _hlabel(h: str) -> str:
    """'2026-08-26 11' → '8/26 11시'"""
    try:
        d = datetime.strptime(h, "%Y-%m-%d %H")
        return f"{d.month}/{d.day} {d.hour:02d}시"
    except Exception:
        return h


def slack_text(data: list[dict], now: datetime, asof: str, slot: str) -> str:
    L = [f"[26FW 발매 시간별 판매현황] {now:%m/%d %H:%M} 발송 ({slot})",
         f"데이터 기준 {asof[:16].replace('T', ' ')} — 미러가 매시 :20 에 직전 정시까지 반영한다", ""]
    for d in data:
        L.append(f"■ {d['style']} {d['label']}  ({d['launch'][5:]} 발매)")
        if d["error"]:
            L.append(f"   ※ {d['error']}")
            L.append("")
            continue
        t = totals(d["hourly"])
        if t["qty"] == 0:
            L.append("   주문 없음")
            L.append("")
            continue
        L.append(f"   누계  {t['ord']:,}건 · {t['qty']:,}장 · {t['amt']:,}원"
                 + (f"  (취소 {t['cancel']}건 제외)" if t["cancel"] else ""))
        L.append("   ```")
        L.append("   시간        주문    수량        결제금액")
        for h in d["hourly"]:
            L.append(f"   {_hlabel(h['h']):<10} {h['ord']:>4}건 {h['qty']:>5}장 {h['amt']:>13,}원")
        L.append("   ```")
        tops = [c for c in d["colors"]][:3]
        if tops:
            share = " · ".join(f"{c['color']} {c['qty']}장({100.0*c['qty']/t['qty']:.0f}%)"
                               for c in tops)
            L.append(f"   컬러 TOP3  {share}")
        if d["soldout"]:
            head = " · ".join(f"{c}({u[5:]})" for c, u in d["soldout"][:5])
            more = f" 외 {len(d['soldout']) - 5}개" if len(d["soldout"]) > 5 else ""
            L.append(f"   품절 {len(d['soldout'])}/{d['opt_total']}개  {head}{more}")
        L.append("")
    L.append(f"시트: https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit")
    return "\n".join(L)


def sheet_rows(data: list[dict], now: datetime, asof: str) -> tuple[list[list], list[list]]:
    hrows: list[list] = [["품번", "상품명", "시간", "주문건수", "판매수량", "결제금액", "취소건수"]]
    crows: list[list] = [["품번", "상품명", "컬러", "판매수량", "비중(%)", "주문건수"]]
    for d in data:
        for h in d["hourly"]:
            hrows.append([d["style"], d["label"], _hlabel(h["h"]),
                          h["ord"], h["qty"], h["amt"], h["cancel"]])
        tq = totals(d["hourly"])["qty"]
        for c in d["colors"]:
            crows.append([d["style"], d["label"], c["color"], c["qty"],
                          round(100.0 * c["qty"] / tq, 2) if tq else 0, c["ord"]])
    stamp = [[f"읽은 시각 {now:%Y-%m-%d %H:%M} KST · 데이터 기준 "
              f"{asof[:16].replace('T', ' ')} · 취소 제외(ord_state>0) · "
              f"소스 musinsa.order_group.order_opt"]]
    return stamp + [[]] + hrows, stamp + [[]] + crows


# ── 시트/슬랙 ────────────────────────────────────────────────────────────────
def _sheets():
    from soo.auth import build_services, get_credentials
    return build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))["sheets"]


def _ensure_tab(sheets, title: str) -> None:
    meta = sheets.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
    if any(s["properties"]["title"] == title for s in meta["sheets"]):
        return
    sheets.spreadsheets().batchUpdate(spreadsheetId=SHEET_ID, body={
        "requests": [{"addSheet": {"properties": {"title": title}}}]}).execute()


def write_sheet(sheets, tab: str, rows: list[list]) -> None:
    _ensure_tab(sheets, tab)
    sheets.spreadsheets().values().clear(spreadsheetId=SHEET_ID, range=f"'{tab}'!A:Z").execute()
    # RAW — 타입추론을 끈다. 컬러명·시간 라벨이 날짜/숫자로 둔갑하는 걸 막는다([[CLAUDE 1-8]])
    sheets.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"'{tab}'!A1",
        valueInputOption="RAW", body={"values": rows}).execute()


def already_sent(sheets, key: str) -> bool:
    _ensure_tab(sheets, TAB_LOG)
    got = sheets.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"'{TAB_LOG}'!A:B").execute().get("values", [])
    return any(r and r[0] == key for r in got)


def mark_sent(sheets, key: str, now: datetime) -> None:
    got = sheets.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"'{TAB_LOG}'!A:B").execute().get("values", [])
    got.append([key, f"{now:%Y-%m-%d %H:%M:%S}"])
    # OVERWRITE — append 의 INSERT_ROWS 는 다른 셀 참조를 밀어낸다([[CLAUDE 1-6]])
    sheets.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"'{TAB_LOG}'!A1",
        valueInputOption="RAW", body={"values": got}).execute()


def send_slack(msg: str) -> bool:
    tok = (os.environ.get("SLACK_BOT_TOKEN") or "").strip()
    if not tok:  # 로컬 검증용 폴백 — CI 는 env 로 들어온다
        try:
            from soo.secrets import load_secrets
            tok = (load_secrets(ROOT / "secrets.yaml").get("slack_bot_token") or "").strip()
        except Exception:
            tok = ""
    if not tok:
        print("SLACK_BOT_TOKEN 없음 — 슬랙 발송 스킵")
        return False
    from soo import persona
    from soo.hero_ops.notify import DEFAULT_TARGET
    target = (os.environ.get("NOTIFY_TARGET") or "").strip() or DEFAULT_TARGET
    ts = persona.send_slack(msg, bot_token=tok, target=target, persona=persona.RANKING_BOT)
    print("슬랙 발송 OK" if ts else "슬랙 발송 실패")
    return bool(ts)


# ── 슬롯 판정 ────────────────────────────────────────────────────────────────
def resolve_slot(now: datetime, override: str = "") -> str:
    """GitHub schedule 지연에 대비해 넓게 잡는다. 밖이면 '수동'."""
    if override:
        return override
    m = now.hour * 60 + now.minute
    if 8 * 60 + 40 <= m < 9 * 60 + 20:
        return "1차"
    if 9 * 60 + 20 <= m < 10 * 60:
        return "2차"
    return "수동"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="시트·슬랙 쓰지 않고 출력만")
    ap.add_argument("--slot", default="", help="슬롯 강제 (1차/2차/수동)")
    ap.add_argument("--force", action="store_true", help="이미 보낸 슬롯도 재발송")
    ap.add_argument("--no-slack", action="store_true", help="시트만 갱신")
    a = ap.parse_args()

    now = datetime.now(KST)
    slot = resolve_slot(now, a.slot)
    key = f"{now:%Y-%m-%d}/{slot}"
    ci = (os.environ.get("LAUNCH_REPORT_CI") or "").strip() == "1"
    print(f"[launch_report] {now:%Y-%m-%d %H:%M} KST · 슬롯 {slot}{' · CI' if ci else ''}")

    if ci and f"{now:%Y-%m-%d}" > EXPIRE_AFTER:
        print(f"만료({EXPIRE_AFTER} 이후) — 종료. 워크플로를 지워도 된다.")
        return 0
    if ci and slot == "수동":
        # GitHub schedule 이 크게 밀려 슬롯 밖에서 깨어난 경우다. 밀린 실행이 여러 개면
        # 중복 발송이 되므로 여기서 끝낸다(수동 확인은 workflow_dispatch 로 --slot 지정).
        print("슬롯 범위 밖 실행(스케줄 지연) — 발송하지 않고 종료")
        return 0

    asof = data_asof()
    data = collect()
    msg = slack_text(data, now, asof, slot)

    if a.dry_run:
        print("--- DRY RUN ---")
        print(msg)
        return 0

    sheets = _sheets()
    if slot != "수동" and not a.force and already_sent(sheets, key):
        print(f"이미 발송됨({key}) — 스킵")
        return 0

    hrows, crows = sheet_rows(data, now, asof)
    write_sheet(sheets, TAB_HOURLY, hrows)
    write_sheet(sheets, TAB_COLOR, crows)
    print(f"시트 적재 OK — 시간별 {len(hrows) - 3}행 · 컬러별 {len(crows) - 3}행")

    ok = True if a.no_slack else send_slack(msg)
    if ok and slot != "수동":
        mark_sent(sheets, key, now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
