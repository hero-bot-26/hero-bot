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
KEY_PREFIX = "asn:"            # ① ASN 등록 알림
KEY_PREFIX_RECV = "asnrecv:"   # ② 입고확정 시작 알림 — 확정이 처음 잡힌 그때 1회만
KEY_PREFIX_DAY = "asnday:"     # ③ 금일 입하 예정 브리핑 — 날짜당 1회

# ★실담당자 발송 스위치 — 이 모듈 전용.
#   `triggers.TEST_ONLY` 를 끄면 IMC 단계 알림 등 **다른 발송까지 같이 풀린다**. 그래서 분리했다.
#   True  = 담당 MD·디자이너·소싱에게 각자 담당 건만 발송(사용자 결정 2026-09-09)
#   False = 전부 본인 DM 으로(테스트)
ASN_LIVE = True
# 담당자 Slack ID 가 없는 건은 여기로 모아 보낸다(누락을 조용히 삼키지 않기 위해).
FALLBACK_SLACK_ID = T.TEST_DM_SLACK_ID
# ★전략팀 전체 사본 — 담당자별 발송과 별개로 **전 건을 한 통**으로 더 받는다
#   (사용자 요청 2026-09-09 "당분간 나도 알아야 하니까"). 끄려면 None.
DIGEST_SLACK_ID = T.TEST_DM_SLACK_ID
# ★`?tab=asn` — 이게 없으면 링크를 눌러도 진입 화면(수량·입고 한눈에)이 뜬다.
APP_URL = "https://hero-master-app.vercel.app/inbound?tab=asn"

# 알림에 올릴 최소 수량 — 샘플·소량 보충까지 다 울리면 알림이 무뎌진다.
MIN_QTY = 100
# ★백필 방지 — `_ASN` 탭은 45일치를 담으므로 원장만 보고 "안 보낸 것"을 고르면
#   첫 실행에 718건(=45일 전부)이 한 번에 나간다(실측). 최근 등록분만 대상으로 한다.
SINCE_DAYS = 2
# 메시지 길이 상한 — 넘치면 나머지는 한 줄로 접는다.
MAX_GROUPS = 12


def _last_col() -> str:
    """`_ASN` 탭의 마지막 열 문자 — 수집기 HEADER 길이에서 유도(열이 늘어도 안 밀린다)."""
    from soo.hero_ops.asn_ingest import HEADER
    n = len(HEADER)
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def load_asn_rows(sheets) -> list[dict]:
    vals = sheets.spreadsheets().values().get(
        # ★범위는 헤더 길이에서 유도한다 — 열이 늘 때 여기를 안 고쳐 recv_dates 가 통째로 잘렸다.
        spreadsheetId=APP_SHEET_ID, range=f"'{ASN_TAB}'!A2:{_last_col()}",
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
            "color_nm": str(g("color_nm")).strip(),
            "hero": str(g("hero")).strip(), "name": str(g("name")).strip(),
            "eindt": str(g("eindt")).strip().split(".")[0],
            "qty": qty,
            "supplier": str(g("supplier")).strip(),
            "warehouse": str(g("warehouse")).strip(),
            "ins_at": str(g("ins_at")).strip(),
            "recv": int(_num(g("recv_qty"))),
            "recv_dates": str(g("recv_dates")).strip(),
        })
    return out


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


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
            if len(r) >= 2 and str(r[1]).strip().startswith(("asn:", "asnrecv:", "asnday:"))}


def record_sent_bulk(sheets, as_of: datetime.date, keys: list[str], labels: str) -> None:
    """발송 키를 한 번에 append. ★건별 append 는 신규가 수백 건일 때 그만큼 API 호출이 된다."""
    if not keys:
        return
    d = as_of.isoformat()
    sheets.spreadsheets().values().append(
        spreadsheetId=APP_SHEET_ID, range=f"'{ALARM_LOG_TAB}'!A:D", valueInputOption="RAW",
        insertDataOption="INSERT_ROWS", body={"values": [[d, k, labels, d] for k in keys]}).execute()


def _w(t: str, n: int) -> str:
    """슬랙 코드블록 표 정렬 — 한글은 2칸으로 센다."""
    t = str(t)
    ln = sum(2 if ord(c) > 0x1100 else 1 for c in t)
    return t + " " * max(0, n - ln)


def _fmt_date(ymd: str) -> str:
    return f"{int(ymd[4:6])}/{int(ymd[6:8])}" if len(ymd) == 8 else ymd


def _short_wh(w: str) -> str:
    import re
    return re.sub(r"^무신사\s*물류센터_?", "", w or "") or "물류센터"


def build_message(groups: list[dict], as_of: datetime.date, kind: str = "asn") -> str:
    """STY 단위로 묶은 메시지. 컬러는 한 줄에 몰아 쓴다(행이 길면 안 읽힌다).

    kind='asn'  → "ASN 등록됐어요!"   (업체가 보내겠다고 통보)
    kind='recv' → "입고 확정 시작됐어요!" (WMS 에 입고확정이 처음 잡힘 — ★그때 1회만)
    """
    total = sum(i["qty"] for g in groups for i in g["items"])
    if kind == "day":
        lines = [f"*금일 물류센터에 들어올 예정이에요* · {len(groups)}건 · {total:,}장",
                 "_오늘 물류센터에 들어올 예정으로 ASN 통보된 건입니다. 입고처리 빨리 되야하는 STY은 챙겨주세요._",
                 ""]
    elif kind == "recv":
        rtotal = sum(i["recv"] for g in groups for i in g["items"])
        lines = [f"*실물 물류 입고 시작됐어요!* · {len(groups)}건 · 입고 {rtotal:,}장 / ASN 등록완료 {total:,}장",
                 "_물류센터 검수를 거쳐 실물 물류 입고가 잡히기 시작했습니다._",
                 ""]
    else:
        lines = [f"*ASN 등록됐어요!* · {len(groups)}건 · {total:,}장",
                 "_업체가 물류센터로 보내겠다고 ASN 등록완료한 건입니다. 실물 물류 입고 전 단계예요._",
                 ""]
    shown, rest = groups[:MAX_GROUPS], groups[MAX_GROUPS:]
    for g in shown:
        items = sorted(g["items"], key=lambda x: -x["qty"])
        sub = sum(i["qty"] for i in items)
        # 1줄 = 히어로 / 상품코드 / 상품명 / 업체
        head = " / ".join(x for x in [
            f"*{g['hero']}*" if g["hero"] else "", f"`{g['style']}`", g["name"], g["supplier"]] if x)
        lines.append(head)
        lines.append("")
        # 2줄 = 납품일 + 컬러별 수량(코드 + 한글 컬러명)
        if kind == "day":
            # 같은 컬러가 여러 차수에 걸치면 더한다.
            merged: dict[str, dict] = {}
            for i in items:
                m = merged.setdefault(i["color"], {"color": i["color"], "color_nm": i.get("color_nm", ""), "qty": 0})
                m["qty"] += i["qty"]
                m["color_nm"] = m["color_nm"] or i.get("color_nm", "")
            cs = sorted(merged.values(), key=lambda x: -x["qty"])
            n_asn = len(g.get("asns") or [])
            # ★슬랙은 한 줄 나열로 가볍게 간다(사용자 지시) — 컬러별 ASN 대비 물류입고 대조표는
            #   앱 [ASN 등록] 탭에 있다. 슬랙에 표를 넣으면 품번마다 표가 붙어 안 읽힌다.
            colors = " · ".join(
                f"{c['color']} {c['color_nm']} {c['qty']:,}" if c["color_nm"] else f"{c['color']} {c['qty']:,}"
                for c in cs)
            lines.append(colors + (f"  (계 {sub:,})" if len(cs) > 1 else "")
                         + (f"  · {n_asn}차 통보" if n_asn > 1 else ""))
        elif kind == "recv":
            rsub = sum(i["recv"] for i in items)
            colors = " · ".join(
                (f"{i['color']} {i['color_nm']} " if i.get("color_nm") else f"{i['color']} ")
                + f"{i['recv']:,}/{i['qty']:,}" for i in items)
            pct = f"{100 * rsub / sub:.0f}%" if sub else "—"
            lines.append(f"실물 물류 입고 {_fmt_date(g['eindt'])} · 입고 {rsub:,} / ASN {sub:,} ({pct}) · {colors}")
        else:
            colors = " · ".join(
                f"{i['color']} {i['color_nm']} {i['qty']:,}" if i.get("color_nm") else f"{i['color']} {i['qty']:,}"
                for i in items)
            lines.append(f"실물 물류 입고 {_fmt_date(g['eindt'])} 예정 · {colors}"
                         + (f"  (계 {sub:,})" if len(items) > 1 else ""))
        own = g["owners"]
        who = " · ".join(x for x in [
            f"MD {own['md']}" if own.get("md") else "",
            f"디자이너 {own['ds']}" if own.get("ds") else "",
            f"소싱 {own['sc']}" if own.get("sc") else ""] if x)
        lines.append(f"_→ {who}_" if who else "_→ 담당자 미매핑_")
        lines.append("")
    if rest:
        rq = sum(i["qty"] for g in rest for i in g["items"])
        lines.append(f"_외 {len(rest)}개 STY · {rq:,}장 — 앱에서 전체 보기_")
        lines.append("")
    lines.append(f"자세한 내용은 대시보드에서 → <{APP_URL}|컬러별 ASN 등록 · 물류 입고 보기>")
    if not ASN_LIVE:
        lines.append("_※ 테스트 모드 — 실운영 전환 시 담당자에게 직접 발송됩니다._")
    return "\n".join(lines)


def _group(rows, owners, merge_asn: bool = False):
    """STY × 입하일 (× ASN) 로 묶는다.

    merge_asn=True 면 **차수를 합친다**. 업체가 같은 날 물량을 두세 번에 나눠 통보하는 일이 흔한데
    (실측: MKFFJAK90 이 9/7·9/8 두 차수로 통보되어 같은 9/9 입하), '금일 입하' 브리핑에서는
    "오늘 이 품번 몇 장"이 알고 싶은 것이지 차수가 아니다 → 합쳐서 한 줄로 본다.
    반대로 'ASN 등록' 알림은 그 통보 이벤트 자체가 주제라 차수를 살린다.
    """
    gmap: dict[tuple, dict] = {}
    for r in rows:
        k = (r["style"], r["eindt"]) if merge_asn else (r["style"], r["eindt"], r["asn"])
        g = gmap.setdefault(k, {"style": r["style"], "eindt": r["eindt"], "asn": r["asn"],
                                "hero": r["hero"], "name": r["name"], "supplier": r["supplier"],
                                "warehouse": r["warehouse"], "asns": set(),
                                "owners": owners.get(r["style"], {}), "items": []})
        g["items"].append(r)
        g["asns"].add(r["asn"])
    return sorted(gmap.values(), key=lambda g: -sum(i["qty"] for i in g["items"]))


def _recipients(groups):
    want: set[str] = set()
    for g in groups:
        for nm in (g["owners"].get("md"), g["owners"].get("ds"), g["owners"].get("sc")):
            if nm:
                want.add(nm)
    return want, {nm: T.OWNER_SLACK_IDS.get(nm) for nm in sorted(want)}


def _send_one(msg: str, target: str, tok: str) -> bool:
    from soo import persona
    ts = persona.send_slack(msg, bot_token=tok, target=target, persona=persona.RANKING_BOT)
    print(f"    발송 {target}: {'OK' if ts else '실패'}")
    return bool(ts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="실제 발송(기본은 드라이런)")
    ap.add_argument("--all", action="store_true", help="원장 무시하고 전건 재발송(수동 복구용)")
    ap.add_argument("--min-qty", type=int, default=MIN_QTY)
    ap.add_argument("--with-recv", action="store_true",
                    help="'입고 확정 시작' 알림도 보낸다(기본 꺼짐 — 사용자 결정 2026-09-09: "
                         "알림은 'ASN 등록'과 '금일 입하' 둘로)")
    ap.add_argument("--since-days", type=int, default=SINCE_DAYS,
                    help="최근 N일 안에 등록/확정된 건만 대상(백필 방지). 0=제한 없음")
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

    # ① ASN 등록 — 통보가 처음 뜬 건
    fresh = [r for r in rows
             if f"{KEY_PREFIX}{r['asn']}:{r['sku']}" not in sent
             and r["qty"] >= args.min_qty
             and (not since or (r["ins_at"] or "")[:8] >= since)]
    # ② 입고 확정 시작 — 확정이 처음 잡힌 건. ★그때 1회만 보내고 이후 진행·완료는 알리지 않는다
    #   (사용자 지시 2026-09-09). 기준일은 등록일이 아니라 **실입고일**(recv_dates 최댓값).
    def _last_recv(r):
        ds = [d.strip() for d in (r.get("recv_dates") or "").split(",") if d.strip()]
        return max(ds) if ds else ""
    recvd = [] if not args.with_recv else [
             r for r in rows
             if r["recv"] > 0
             and f"{KEY_PREFIX_RECV}{r['asn']}:{r['sku']}" not in sent
             and r["qty"] >= args.min_qty
             and (not since or _last_recv(r) >= since)]


    # ③ 금일 입하 예정 — 오늘 들어올 것 전체를 한 번. ★날짜당 1회(키에 날짜를 쓴다).
    #   등록 알림(①)과 절반쯤 겹치지만 역할이 다르다 — ①은 개별 이벤트, ③은 그날 전체 그림.
    tkey = as_of.strftime("%Y%m%d")
    today_rows = ([] if f"{KEY_PREFIX_DAY}{tkey}" in sent else
                  [r for r in rows if r["eindt"] == tkey and r["qty"] >= args.min_qty])

    print(f"[ASN알림] 전체 {len(rows)}행 · 기발송키 {len(sent)} "
          f"· 신규 통보 {len(fresh)}건 · 확정 시작 {len(recvd)}건 · 금일 입하 {len(today_rows)}건 "
          f"(최소수량 {args.min_qty} · {since or '전체'} 이후)")
    if not fresh and not recvd and not today_rows:
        print("[ASN알림] 보낼 것 없음")
        return 0

    tok = ""
    if args.send:
        import os
        tok = os.environ.get("SLACK_BOT_TOKEN", "").strip() or (T._slack_token() or "")
        if not tok:
            print("[ASN알림] SLACK_BOT_TOKEN 없음 — 발송 스킵(원장도 기록하지 않는다)")
            return 0

    rc = 0
    # ★두 알림은 따로 나간다(사용자 지시) — 성격이 다르고, 한쪽 실패가 다른 쪽 원장을 오염시키지 않는다.
    for kind, items, prefix, label in (
            ("day",  today_rows, KEY_PREFIX_DAY,  "금일 입하 예정"),
            ("asn",  fresh,      KEY_PREFIX,      "ASN 등록"),
            ("recv", recvd,      KEY_PREFIX_RECV, "입고 확정 시작")):
        if not items:
            continue
        groups = _group(items, owners, merge_asn=(kind == "day"))
        want, ids = _recipients(groups)

        # ★수신자별로 '자기 담당 건만' 담아 보낸다 — 한 통에 전 품목을 담으면 남의 상품까지 보게 된다.
        by_person: dict[str, list] = {}
        orphan = []
        for g in groups:
            own = g["owners"]
            names = [n for n in (own.get("md"), own.get("ds"), own.get("sc")) if n]
            sids = {n: T.OWNER_SLACK_IDS.get(n) for n in names}
            if not any(sids.values()):
                orphan.append(g)
                continue
            for n, sid in sids.items():
                if sid:
                    by_person.setdefault(sid, []).append(g)
        print(f"  [{label}] 그룹 {len(groups)} · 수신자 {len(by_person)}명"
              + (f" · 담당자 미매핑 {len(orphan)}그룹" if orphan else ""))
        for sid, gs in sorted(by_person.items()):
            who = next((n for n, v in T.OWNER_SLACK_IDS.items() if v == sid), sid)
            print(f"      {who} ({sid}) ← {len(gs)}건")

        if not args.send:
            # 미리보기는 가장 많이 받는 사람 기준으로 한 통만 찍는다(전부 찍으면 로그가 길다).
            if by_person:
                top = max(by_person.items(), key=lambda kv: len(kv[1]))
                who = next((n for n, v in T.OWNER_SLACK_IDS.items() if v == top[0]), top[0])
                print("-" * 60)
                print(f"[미리보기] {who} 에게 가는 {len(top[1])}건")
                print(build_message(top[1], as_of, kind))
                print("-" * 60)
            continue

        ok_any = False
        for sid, gs in sorted(by_person.items()):
            tgt = sid if ASN_LIVE else T.TEST_DM_SLACK_ID
            if _send_one(build_message(gs, as_of, kind), tgt, tok):
                ok_any = True
        # 전략팀 전체 사본 — 담당자에게 쪼개 보낸 것과 별개로 전 건을 한 통에.
        if DIGEST_SLACK_ID:
            dmsg = build_message(groups, as_of, kind) + chr(10) + "_전체 사본 · 담당자에게는 각자 담당 건만 갑니다._"
            if _send_one(dmsg, DIGEST_SLACK_ID, tok):
                ok_any = True
        if orphan:
            # 담당자를 못 찾은 건은 조용히 버리지 않고 전략팀으로 보낸다.
            tail = "_※ 담당자 Slack ID 미매핑 — `담당자매핑` 탭에 채우면 자동으로 붙습니다._"
            msg = build_message(orphan, as_of, kind) + chr(10) + tail
            if _send_one(msg, FALLBACK_SLACK_ID, tok):
                ok_any = True
        if not ok_any:
            print(f"  [{label}] 전 수신자 실패 — 원장에 기록하지 않는다(다음 실행에서 재시도)")
            rc = 1
            continue
        labels = ("LIVE · " if ASN_LIVE else "TEST→본인DM · ") + label + " · 수신: " + (", ".join(sorted(want)) or "미매핑")
        # ③은 날짜 하나가 키다(건별로 남기면 다음 날 같은 건이 또 통과한다).
        keys = ([f"{prefix}{tkey}"] if kind == "day"
                else [f"{prefix}{r['asn']}:{r['sku']}" for r in items])
        record_sent_bulk(sheets, as_of, keys, labels)
        print(f"  [{label}] 원장 기록 {len(keys)}건")

    if not args.send:
        print("[ASN알림] 드라이런 — 보내려면 --send")
    return rc


if __name__ == "__main__":
    sys.exit(main())
