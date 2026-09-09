"""ASN(입하 통보) 신규 건 슬랙 알림 — 물건이 물류센터로 갔다는 걸 입고확정보다 먼저 알린다.

왜
--
앱 입고확정은 WMS → DBX 잡(1일 1회) → 시트 → 앱 생성(1일 1회)을 거쳐 D+1~2 에 닿는다.
ASN 은 그보다 먼저 뜨므로 "물건은 왔는데 화면은 아직 미입고"인 창을 메운다.
화면(`/inbound` [입하 통보] 탭)은 보러 가야 보이고, 이 알림은 찾아간다.

수신자 (사용자 확정 2026-09-09)
------------------------------
그 아이템의 **상품MD · 디자이너 · 소싱** 3인. 상품 건이라 전략팀은 넣지 않는다.
STY → 담당자는 PLM `데이터` 탭의 `md_nm` / `ds_nm` / `sc_nm`,
이름 → Slack ID 는 `담당자매핑` 탭(triggers.load_owner_map 재사용).

★안전 하드락 — `triggers.TEST_ONLY`
  True 인 동안 **실제 담당자에게 보내지 않고 전부 본인 DM 으로** 나간다(의도한 수신자는 메시지에 라벨로 표기).
  사용자 지시(2026-06-16 "슬랙 실제 실행은 절대 돌리지않고 나한테만 테스트로")를 그대로 따른다.
  실운영 전환은 그 상수를 False 로 바꾸는 명시적 행위로만.

★중복 발송 방지 — 이게 이 모듈의 핵심 위험이다
  발송 키 = `asn:{ASN번호}:{품번-컬러}` 로 `알람발송로그` 탭에 남긴다. **날짜가 아니라 건 단위**다
  (날짜로 잡으면 같은 ASN 이 매일 새 건으로 통과한다 — 주간 리포트가 catch-up 으로 같은 주차를
  두 번 쏜 전례가 있다). 기록은 **발송에 성공했을 때만**(실패를 적으면 영영 안 나간다).
  드라이런은 원장을 건드리지 않는다.

사용법
  python -m soo.hero_ops.asn_notify              # 드라이런 — 보낼 내용만 출력
  python -m soo.hero_ops.asn_notify --send       # 실제 발송(+원장 기록)
  python -m soo.hero_ops.asn_notify --send --all # 원장 무시하고 전건 재발송(수동 복구용)
"""
from __future__ import annotations

import argparse
import datetime
import sys
from collections import defaultdict
from pathlib import Path

from soo.auth import get_credentials, build_services
from soo.hero_ops import triggers as T
from soo.hero_ops.asn_ingest import APP_SHEET_ID, TAB as ASN_TAB

HERO = Path(__file__).resolve().parents[2]
PLM_TAB = "데이터"                 # style_no ↔ md_nm/ds_nm/sc_nm
ALARM_LOG_TAB = "알람발송로그"
KEY_PREFIX = "asn:"
APP_URL = "https://hero-master-app.vercel.app/inbound"

# 알림에 올릴 최소 수량 — 샘플·소량 보충까지 다 울리면 알림이 무뎌진다.
MIN_QTY = 100
# ★백필 방지 — `_ASN` 탭은 45일치를 담으므로 원장만 보고 "안 보낸 것"을 고르면
#   첫 실행에 718건(=45일 전부)이 한 번에 나간다(실측). 최근 등록분만 대상으로 한다.
SINCE_DAYS = 2
# 메시지 길이 상한 — 넘치면 나머지는 한 줄로 접는다.
MAX_GROUPS = 12


def load_asn_rows(sheets) -> list[dict]:
    vals = sheets.spreadsheets().values().get(
        spreadsheetId=APP_SHEET_ID, range=f"'{ASN_TAB}'!A2:S",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    if len(vals) < 2:
        return []
    hdr = [str(c).strip() for c in vals[0]]
    idx = {h: i for i, h in enumerate(hdr)}
    out = []
    for row in vals[1:]:
        def g(k, d=""):
            i = idx.get(k)
            return row[i] if i is not None and i < len(row) else d
        sku = str(g("sku")).strip()
        if not sku:
            continue
        try:
            qty = int(float(g("qty") or 0))
        except (TypeError, ValueError):
            qty = 0
        out.append({
            "asn": str(g("asn_no")).strip(), "sku": sku,
            "style": str(g("style")).strip(), "color": str(g("color")).strip(),
            "hero": str(g("hero")).strip(), "name": str(g("name")).strip(),
            "eindt": str(g("eindt")).strip().split(".")[0],
            "qty": qty,
            "supplier": str(g("supplier")).strip(),
            "warehouse": str(g("warehouse")).strip(),
            "ins_at": str(g("ins_at")).strip(),
        })
    return out


def load_owners(sheets) -> dict[str, dict]:
    """PLM `데이터` 탭 → {style_no: {md, ds, sc}}. 같은 style 이 여러 행이면 담당자가 찬 행을 남긴다."""
    vals = sheets.spreadsheets().values().get(
        spreadsheetId=APP_SHEET_ID, range=f"'{PLM_TAB}'!A1:AL",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    if not vals:
        return {}
    hdr = [str(c).strip() for c in vals[0]]
    idx = {h: i for i, h in enumerate(hdr)}
    need = ("style_no", "md_nm", "ds_nm", "sc_nm")
    if any(k not in idx for k in need):
        print(f"[ASN알림] `{PLM_TAB}` 헤더에 담당자 열이 없다 {hdr[:40]} → 담당자 없이 진행")
        return {}
    out: dict[str, dict] = {}
    for row in vals[1:]:
        def g(k):
            i = idx[k]
            return str(row[i]).strip() if i < len(row) and row[i] is not None else ""
        st = g("style_no")
        if not st:
            continue
        cur = {"md": g("md_nm"), "ds": g("ds_nm"), "sc": g("sc_nm")}
        prev = out.get(st)
        # 담당자가 더 많이 채워진 행을 남긴다(빈 행이 뒤에 와서 덮는 걸 막는다).
        if prev is None or sum(bool(v) for v in cur.values()) > sum(bool(v) for v in prev.values()):
            out[st] = cur
    return out


def load_sent_keys(sheets) -> set[str]:
    """`알람발송로그` 전체에서 ASN 발송 키 집합. ★날짜 무관 — 한 번 보낸 건은 다시 안 보낸다."""
    try:
        res = sheets.spreadsheets().values().get(
            spreadsheetId=APP_SHEET_ID, range=f"'{ALARM_LOG_TAB}'!A2:B").execute()
    except Exception as e:
        # 원장을 못 읽으면 중복 발송 위험이 있으므로 멈춘다(조용히 다 보내는 게 최악).
        raise RuntimeError(f"발송 원장을 읽지 못했다 — 중복 발송 방지 불가: {e}") from e
    return {str(r[1]).strip() for r in res.get("values", [])
            if len(r) >= 2 and str(r[1]).strip().startswith(KEY_PREFIX)}


def record_sent_bulk(sheets, as_of: datetime.date, keys: list[str], labels: str) -> None:
    """발송 키를 한 번에 append. ★건별 append 는 신규가 수백 건일 때 그만큼 API 호출이 된다."""
    if not keys:
        return
    d = as_of.isoformat()
    sheets.spreadsheets().values().append(
        spreadsheetId=APP_SHEET_ID, range=f"'{ALARM_LOG_TAB}'!A:D", valueInputOption="RAW",
        insertDataOption="INSERT_ROWS", body={"values": [[d, k, labels, d] for k in keys]}).execute()


def _fmt_date(ymd: str) -> str:
    return f"{int(ymd[4:6])}/{int(ymd[6:8])}" if len(ymd) == 8 else ymd


def _short_wh(w: str) -> str:
    import re
    return re.sub(r"^무신사\s*물류센터_?", "", w or "") or "물류센터"


def build_message(groups: list[dict], as_of: datetime.date) -> str:
    """STY 단위로 묶은 메시지. 컬러는 한 줄에 몰아 쓴다(행이 길면 안 읽힌다)."""
    n_sku = sum(len(g["items"]) for g in groups)
    total = sum(i["qty"] for g in groups for i in g["items"])
    lines = [f"*입하 통보* · 신규 {n_sku}건 · {total:,}장",
             "_업체가 물류센터로 보낸 건입니다. WMS 입고확정 전 단계라 앱 '입고확정'에는 아직 안 잡힙니다._",
             ""]
    shown, rest = groups[:MAX_GROUPS], groups[MAX_GROUPS:]
    for g in shown:
        items = sorted(g["items"], key=lambda x: -x["qty"])
        sub = sum(i["qty"] for i in items)
        head = f"*{g['hero']}* · `{g['style']}` {g['name']}" if g["hero"] else f"`{g['style']}` {g['name']}"
        lines.append(head)
        lines.append(f"납품 {_fmt_date(g['eindt'])} · {g['supplier']} → {_short_wh(g['warehouse'])}")
        colors = " · ".join(f"{i['color']} {i['qty']:,}" for i in items)
        lines.append(f"• {colors}" + (f"  (계 {sub:,})" if len(items) > 1 else ""))
        own = g["owners"]
        who = " · ".join(x for x in [
            f"MD {own['md']}" if own.get("md") else "",
            f"디자이너 {own['ds']}" if own.get("ds") else "",
            f"소싱 {own['sc']}" if own.get("sc") else ""] if x)
        lines.append(f"→ {who}" if who else "→ _담당자 미매핑_")
        lines.append("")
    if rest:
        rq = sum(i["qty"] for g in rest for i in g["items"])
        lines.append(f"_외 {len(rest)}개 STY · {rq:,}장 — 앱에서 전체 보기_")
        lines.append("")
    lines.append(f"<{APP_URL}|앱에서 보기 — 입하 통보 탭>")
    if T.TEST_ONLY:
        lines.append("_※ 테스트 모드 — 실운영 전환 시 위 담당자에게 직접 발송됩니다._")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="실제 발송(기본은 드라이런)")
    ap.add_argument("--all", action="store_true", help="원장 무시하고 전건 재발송(수동 복구용)")
    ap.add_argument("--min-qty", type=int, default=MIN_QTY)
    ap.add_argument("--since-days", type=int, default=SINCE_DAYS,
                    help="최근 N일 안에 등록된 ASN 만 대상(백필 방지). 0=제한 없음")
    args = ap.parse_args()

    as_of = datetime.date.today()
    sheets = build_services(get_credentials(HERO / "credentials.json", HERO / "token.json"))["sheets"]

    rows = load_asn_rows(sheets)
    if not rows:
        print("[ASN알림] `_ASN` 탭이 비었다 — asn_ingest 를 먼저 돌릴 것")
        return 0
    sent = set() if args.all else load_sent_keys(sheets)
    owners = load_owners(sheets)
    T.load_owner_map(sheets, APP_SHEET_ID)      # 이름 → Slack ID

    since = ""
    if args.since_days > 0:
        since = (as_of - datetime.timedelta(days=args.since_days)).strftime("%Y%m%d")
    fresh = [r for r in rows
             if f"{KEY_PREFIX}{r['asn']}:{r['sku']}" not in sent
             and r["qty"] >= args.min_qty
             and (not since or (r["ins_at"] or "")[:8] >= since)]
    print(f"[ASN알림] 전체 {len(rows)}행 · 기발송 {len(sent)} · 신규 {len(fresh)}건 "
          f"(최소수량 {args.min_qty} · 등록일 {since or '전체'} 이후)")
    if not fresh:
        print("[ASN알림] 새 통보 없음 — 발송 안 함")
        return 0

    # STY × 납품일 × ASN 으로 묶는다(같은 STY라도 차수가 다르면 따로 알린다).
    gmap: dict[tuple, dict] = {}
    for r in fresh:
        k = (r["style"], r["eindt"], r["asn"])
        g = gmap.setdefault(k, {"style": r["style"], "eindt": r["eindt"], "asn": r["asn"],
                                "hero": r["hero"], "name": r["name"], "supplier": r["supplier"],
                                "warehouse": r["warehouse"],
                                "owners": owners.get(r["style"], {}), "items": []})
        g["items"].append(r)
    groups = sorted(gmap.values(), key=lambda g: (-sum(i["qty"] for i in g["items"])))

    msg = build_message(groups, as_of)
    # 의도한 수신자(담당자 3인) — 하드락이 풀리면 이 목록으로 나간다.
    want: set[str] = set()
    for g in groups:
        for nm in (g["owners"].get("md"), g["owners"].get("ds"), g["owners"].get("sc")):
            if nm:
                want.add(nm)
    ids = {nm: T.OWNER_SLACK_IDS.get(nm) for nm in sorted(want)}
    print(f"[ASN알림] 그룹 {len(groups)} · 의도 수신자 {len(want)}명 "
          f"(Slack ID 있음 {sum(1 for v in ids.values() if v)})")
    for nm, sid in ids.items():
        print(f"    {nm}: {sid or '— 미매핑'}")

    if not args.send:
        print("-" * 60)
        print(msg)
        print("-" * 60)
        print("[ASN알림] 드라이런 — 보내려면 --send")
        return 0

    import os
    tok = os.environ.get("SLACK_BOT_TOKEN", "").strip() or (T._slack_token() or "")
    if not tok:
        print("[ASN알림] SLACK_BOT_TOKEN 없음 — 발송 스킵(원장도 기록하지 않는다)")
        return 0
    from soo import persona
    targets = [T.TEST_DM_SLACK_ID] if T.TEST_ONLY else sorted({v for v in ids.values() if v})
    ok = False
    for tgt in targets:
        ts = persona.send_slack(msg, bot_token=tok, target=tgt, persona=persona.RANKING_BOT)
        print(f"[ASN알림] 발송 {tgt}: {'OK' if ts else '실패'}")
        ok = ok or bool(ts)

    if not ok:
        print("[ASN알림] 전 수신자 실패 — 원장에 기록하지 않는다(다음 실행에서 재시도)")
        return 1
    labels = ("TEST→본인DM · 의도: " if T.TEST_ONLY else "의도: ") + (", ".join(sorted(want)) or "미매핑")
    record_sent_bulk(sheets, as_of, [f"{KEY_PREFIX}{r['asn']}:{r['sku']}" for r in fresh], labels)
    print(f"[ASN알림] 원장 기록 {len(fresh)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
