"""ASN(입하 통보) 수집 — 업체가 "이 날 보낸다"고 통보한 건을 앱 시트 `_ASN` 탭에 적재한다.

왜 필요한가
-----------
앱 입고 보드의 파이프라인은 3단계인데 가운데가 비어 있었다.
  ① 오더시트 생산관리 AK/AL  = MD가 계획한 입고일          (앱: 입고예정)
  ② **ASN**                 = 업체가 확정 통보한 날짜·수량   (앱: 비어 있었음)
  ③ WMS ui_grreport_detail  = 도착·전수검사·정상입고        (앱: 입고확정)

②는 ③보다 먼저 뜨는데다 **공급사·입고창고**를 들고 있다(오더시트에도 앱에도 없는 정보).
게다가 ③은 DBX 잡(1일 1회) → 시트 → 앱 생성(1일 1회)을 거쳐 화면에 D+1~2로 닿는 반면
ASN 원천은 실시간에 가깝게 적재된다. 2026-09-09 실측에서 **히어로 ASN 42건 145,925장이
앱 입고확정에 아직 없었고**, 그날 '미입고(빨강)' 29건 중 5건은 ASN 상 이미 검수까지 끝나 있었다.

★원천 선택 (2026-09-09)
  담당자가 알려준 `pbo.erp.supplier_order_asn` 은 **SELECT 권한이 없다**(PERMISSION_DENIED).
  같은 SAP 원형인 `pbo.moms.iif_bam_asn` 으로 대체했다 — 우리가 입고에 이미 쓰는 스키마다.
  히어로 152품번 7~8월로 WMS 실입고와 대조: SKU 439개 중 ASN에만 18·WMS에만 12(**커버리지 96%**),
  수량은 EINQTY 기준 +4.2%, SKU 단위 정확일치 69%. 키(DELVNO+DELVSEQ+PO+바코드) 중복은 0건.

★수량 컬럼 (실측으로 고른 것)
  - `EINQTY`  = 이번 납품 수량. WMS 실입고와 가장 잘 맞는다 → **이걸 쓴다**.
  - `MENGE`   = PO 라인 총수량. 같은 PO가 여러 ASN에 반복 등장해 합치면 **3.2배로 부푼다**. 표시용으로만.
  - `CMENGE`  = 잔량으로 보이나 `MENGE = EINQTY + CMENGE` 가 54%에서만 성립 → 참고값.
  세 필드의 정확한 정의는 원본 테이블(`supplier_order_asn`) 권한을 받아 대조해야 확정된다.

★남은 한계 — 취소 ASN
  준 쿼리의 `delete_flag NOT IN ('X')` 에 해당하는 컬럼이 `iif_bam_asn` 에는 없다.
  대안으로 찾은 `gspread.musinsastandard.mutandard_asn_deleted` 는 asn_no 체계가 달라
  (그쪽은 `MUTA`·`NIKE` 류, 이쪽은 `TX-INB…`) 7~8월 매칭이 0건이었다. 지금은 못 거른다.
  매 실행마다 전 구간을 다시 읽어 덮으므로(멱등) 취소분이 사라지면 자연히 빠지지만,
  '취소됐는데 원천에 남는' 경우는 잡히지 않는다.

ZZ_BARCODE 파싱
  바코드 숫자가 아니라 `품번(9)+컬러(2)+사이즈` 문자열이다(`MWFPCAA12GR027`).
  길이는 12~14가 섞이지만 앞 11자는 항상 품번+컬러라 substr 로 우리 SKU 키가 그대로 나온다.

사용법
  python -m soo.hero_ops.asn_ingest            # 드라이런(기본) — 읽고 요약만
  python -m soo.hero_ops.asn_ingest --apply    # `_ASN` 탭 기입
"""
from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from soo.auth import get_credentials, build_services
from soo.hero_ops.inbound_board import build_pumbon2hero
from soo.hero_ops.launch_report import dbx_sql

HERO = Path(__file__).resolve().parents[2]
APP_SHEET_ID = "1_tZDl-heZyWT4VQYIAT3ZHFeMoQlK2FSOpEMyZjqvm0"   # 히어로 PLM 마일스톤(자동) = 앱 시트
TAB = "_ASN"

# 며칠치를 담을까 — 화면은 최근 것만 보지만, 뒤늦게 확정되는 건이 있어 넉넉히 본다.
LOOKBACK_DAYS = 45

HEADER = ["asn_no", "po_no", "po_cnt", "sku", "style", "color", "color_nm", "hero", "name",
          "eindt", "qty", "po_qty", "remain", "supplier", "warehouse",
          "sts", "ins_at", "upd_at", "recv_qty", "recv_dates"]


def fetch_asn(styles: list[str], lookback: int = LOOKBACK_DAYS) -> list[list]:
    """히어로 품번의 ASN + 같은 SKU 의 WMS 실입고(±3일)를 한 번에 읽는다.

    WMS 를 EINDT 정확일치가 아니라 ±3일 창으로 붙이는 이유 = 통보일과 입고확정일이
    같은 날인 게 89% 지만 나머지는 하루이틀 밀린다. 정확일치로 잡으면 '확정대기'가 과다해진다.
    """
    if not styles:
        raise RuntimeError("히어로 품번이 비었다 — HERO STY 매핑부터 확인할 것")
    in_styles = ",".join(f"'{s}'" for s in styles)
    sql = f"""
WITH asn AS (
  -- 그레인 = ASN번호 × 품번-컬러. ★PO라인(EBELP)은 사이즈마다 갈리므로 GROUP BY 에 넣으면
  --   같은 SKU 가 5~10행으로 쪼개진다(실측: 넣었을 때 6,550행 → 뺐을 때 1,000행대).
  -- ★무컬러 상품(벨트·양말 등 ACC)은 바코드 10~11자리가 컬러가 아니라 **사이즈**다
  --   (`ME0BE0Z6159028` = 품번 + 사이즈59 + 028). 그대로 품번-사이즈를 SKU 로 쓰면
  --   WMS(STL_NO=품번, 사이즈는 GDS_OPT)와 영영 안 맞아 '확정대기'로 굳는다 — 실제로 밟았다
  --   (8/11 ME0BE0Z61 통보 1,810 / 화면 확정 0인데 WMS 엔 품번 단위로 1,810 이 정확히 들어와 있었다).
  --   ★SKU 를 품번으로 접어서 맞추려던 시도는 전부 실패했다(실측으로 폐기) —
  --     ①컬러 마스터에 없는 코드로 판정 → `LG`·`SW`·`AH`·`KH`·`BR` 이 마스터에 빠져 라이트다운이 접힘
  --     ②WMS 적재형태로 판정 → 의류도 품번 단위 적재 이력이 섞여 커브드팬츠가 접힘
  --     ③코드에 숫자면 사이즈로 판정 → 양말은 WMS 도 `MEASC0Z03-77` 로 사이즈째 적재해서 깨짐
  --   결론: **접지 않는다.** SKU 는 항상 품번-코드로 두고 WMS 를 두 키(품번-코드 / 품번)로 본다.
  SELECT DELVNO,
         concat(substr(ZZ_BARCODE,1,9),'-',substr(ZZ_BARCODE,10,2)) sku,
         substr(ZZ_BARCODE,1,9) style,
         substr(ZZ_BARCODE,10,2) color,
         EINDT,
         max(EBELN)   po_no,
         count(DISTINCT EBELN) po_cnt,
         max(MAKTX)   name,
         sum(EINQTY)  qty,
         sum(MENGE)   po_qty,
         sum(CMENGE)  remain,
         max(LIFTX)   supplier,
         max(LGOBE)   warehouse,
         max(DELVSTS) sts,
         min(INS_DATE) ins_at,
         max(UPD_DATE) upd_at
  FROM pbo.moms.iif_bam_asn
  WHERE substr(ZZ_BARCODE,1,9) IN ({in_styles})
    AND EINDT >= DATE_FORMAT(DATE_SUB(CURRENT_DATE(), {int(lookback)}), 'yyyyMMdd')
  GROUP BY DELVNO, 2, 3, 4, EINDT
),
color AS (
  -- 컬러코드 → 한글 컬러명. ★`mutandard_color_cd` 는 color_cd 가 중복될 수 있어(2행 5개·59행 1개)
  --   그냥 조인하면 ASN 행이 증식한다 — 코드당 하나로 접어서 붙인다.
  SELECT color_cd, max(color_kor) color_nm
  FROM gspread.musinsastandard.mutandard_color_cd
  WHERE nullif(trim(color_cd),'') IS NOT NULL GROUP BY 1
),
wms AS (
  -- STL_NO 는 상품군에 따라 `품번-컬러`·`품번-사이즈`·`품번` 이 섞여 있다. 그대로 두고 두 번 본다.
  SELECT STL_NO sku, ACT_DATE, sum(ACT_QTY) qty
  FROM pbo.moms.ui_grreport_detail
  WHERE ORD_STATUS NOT IN ('출고취소','입고취소','입고대기') AND ORD_TYPE = '일반' AND SPR_NM = 'MUSINSA'
    AND ACT_DATE >= DATE_FORMAT(DATE_SUB(CURRENT_DATE(), {int(lookback) + 7}), 'yyyyMMdd')
    AND split_part(STL_NO, '-', 1) IN ({in_styles})
  GROUP BY 1, 2
),
sk AS (  -- ① 품번-코드 정확 매칭. ★(sku, 납품일) 단위 — ASN(DELVNO)별로 잡으면 같은 실입고를
         --   여러 ASN 이 각각 전량 가져가 과대계상된다(실측: 통보 60 인데 확정 320).
  SELECT a.sku, a.EINDT, sum(w.qty) qty,
         concat_ws(',', sort_array(collect_set(w.ACT_DATE))) dts
  FROM (SELECT DISTINCT sku, EINDT FROM asn) a
  JOIN wms w
    ON w.sku = a.sku
   AND abs(datediff(to_date(w.ACT_DATE,'yyyyMMdd'), to_date(a.EINDT,'yyyyMMdd'))) <= 3
  GROUP BY 1,2
),
st AS (  -- ② 품번 단위 적재분(벨트 등 — WMS 가 STL_NO=품번 으로만 넣는 상품).
  SELECT a.style, a.EINDT, sum(w.qty) qty,
         concat_ws(',', sort_array(collect_set(w.ACT_DATE))) dts
  FROM (SELECT DISTINCT style, EINDT FROM asn) a
  JOIN wms w
    ON w.sku = a.style
   AND abs(datediff(to_date(w.ACT_DATE,'yyyyMMdd'), to_date(a.EINDT,'yyyyMMdd'))) <= 3
  GROUP BY 1,2
),
tot AS (  -- 같은 (sku, 납품일) 의 통보 총량 — 실입고를 통보량 비례로 안분해 합계를 보존한다
  SELECT sku, style, EINDT, sum(qty) tq FROM asn GROUP BY 1,2,3
)
SELECT a.DELVNO, a.po_no, a.po_cnt, a.sku, a.style, a.color, c.color_nm, a.name, a.EINDT,
       a.qty, a.po_qty, a.remain, a.supplier, a.warehouse, a.sts, a.ins_at, a.upd_at,
       -- ★통보량으로 캡한다. 조인 창(±3일)에 인접 차수의 실입고가 겹쳐 들어와 캡이 없으면
       --   합계가 110% 까지 부푼다(실측). 차수별 정확한 귀속은 입고 보드(FIFO 배분)가 하는 일이고,
       --   이 탭이 답할 질문은 "이 통보분이 WMS 에 잡혔나"라서 캡이 목적에 맞다.
       least(CAST(round(coalesce(sk.qty, st.qty, 0) * a.qty / nullif(t.tq, 0)) AS BIGINT),
             CAST(a.qty AS BIGINT)) recv_qty,
       coalesce(sk.dts, st.dts, '') recv_dates
FROM asn a
LEFT JOIN color c ON c.color_cd = a.color
LEFT JOIN tot t ON t.sku = a.sku AND t.EINDT = a.EINDT
LEFT JOIN sk ON sk.sku = a.sku AND sk.EINDT = a.EINDT
LEFT JOIN st ON st.style = a.style AND st.EINDT = a.EINDT AND sk.sku IS NULL
ORDER BY a.EINDT DESC, a.ins_at DESC, a.sku
"""
    rows = dbx_sql(sql, wait=600)
    if not rows:
        # 조용한 0 금지 — 빈 결과는 실패로 올린다([[CLAUDE 2-6]]).
        raise RuntimeError(f"ASN 0행 — 품번 {len(styles)}개 · lookback {lookback}일. "
                           f"바코드 파싱/품번 매핑을 먼저 의심할 것")
    return rows


def _clean_name(name: str | None) -> str:
    """상품명 끝의 `[컬러명]` 꼬리를 뗀다.

    ★원천 `MAKTX` 가 **40자에서 잘린다**(809건 중 8건이 한계). 잘리는 건 대개 뒤에 붙은
      컬러 표기라 `…긴소매 티셔츠 [머드 그` 처럼 남는다. 컬러는 별도 열(color_nm)로 들고 있으니
      꼬리를 떼는 게 낫다 — 닫는 괄호가 없어도(잘린 경우) 마지막 여는 괄호부터 끝까지 지운다.
    """
    t = (name or "").strip()
    import re
    t = re.sub(r"\s*\[[^\[\]]*\]\s*$", "", t)   # 온전한 [컬러]
    t = re.sub(r"\s*\[[^\[\]]*$", "", t)          # 잘린 [컬러…
    return t.strip()


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def to_grid(rows: list[list], p2h: dict[str, str]) -> list[list]:
    out = []
    for r in rows:
        (delvno, po_no, po_cnt, sku, style, color, color_nm, name, eindt,
         qty, po_qty, remain, supplier, warehouse, sts, ins_at, upd_at,
         recv_qty, recv_dates) = r
        out.append([
            delvno or "", po_no or "", int(_num(po_cnt)), sku or "", style or "", color or "",
            (color_nm or "").strip(),
            p2h.get(style or "", ""), _clean_name(name),
            eindt or "",
            int(_num(qty)), int(_num(po_qty)), int(_num(remain)),
            (supplier or "").strip(), (warehouse or "").strip(), (sts or "").strip(),
            ins_at or "", upd_at or "",
            int(_num(recv_qty)), recv_dates or "",
        ])
    return out


def ensure_tab(sheets, sheet_id: str, title: str) -> None:
    meta = sheets.spreadsheets().get(spreadsheetId=sheet_id,
                                     fields="sheets.properties(title,sheetId)").execute()
    if any(s["properties"]["title"] == title for s in meta["sheets"]):
        return
    sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={
        "requests": [{"addSheet": {"properties": {
            "title": title,
            "gridProperties": {"rowCount": 5000, "columnCount": len(HEADER), "frozenRowCount": 2},
            "hidden": True,      # 원장이라 사람이 볼 탭은 아니다
        }}}]}).execute()
    print(f"[_ASN] 탭 생성: {title}")


def write_tab(sheets, sheet_id: str, grid: list[list], as_of: str) -> None:
    """전량 덮어쓰기(멱등). ★append/INSERT_ROWS 금지 — 다른 탭 수식 참조가 밀린다([[CLAUDE 1-6]])."""
    label = (f"히어로 ASN(입하 통보) · pbo.moms.iif_bam_asn · 최근 {LOOKBACK_DAYS}일 "
             f"· 수량=EINQTY · 생성 {as_of}")
    body = [[label] + [""] * (len(HEADER) - 1), HEADER] + grid
    # 이전 실행이 더 길었을 수 있으니 뒤를 비운다(잔재 행이 남으면 화면에 유령 ASN 이 뜬다).
    sheets.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=f"'{TAB}'!A1:T", body={}).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=f"'{TAB}'!A1",
        valueInputOption="RAW", body={"values": body}).execute()


def load_asn_from_sheet(sheets, sheet_id: str = APP_SHEET_ID, cutoff: str | None = None) -> dict:
    """`_ASN` 탭 → {품번-컬러: {"qty": 통보합, "dates": [납품예정일…], "last_ins": 최근등록}}.

    입고 보드 상태 판정에 쓴다 — ASN 이 떠 있으면 '미입고(빨강)' 대신 '확정 대기'.
    탭이 없거나 읽기 실패면 None 을 돌려 게이트를 끈다(있던 화면이 깨지지 않게).
    """
    try:
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{TAB}'!A2:T",
            valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    except Exception as e:
        print(f"[_ASN] 읽기 실패 → ASN 게이트 없이 진행: {type(e).__name__}: {e}")
        return {}
    if not vals or len(vals) < 2:
        print("[_ASN] 탭이 비었다 → ASN 게이트 없이 진행")
        return {}
    hdr = [str(c).strip() for c in vals[0]]
    idx = {h: i for i, h in enumerate(hdr)}
    need = ("sku", "qty", "eindt", "ins_at")
    if any(k not in idx for k in need):
        print(f"[_ASN] 헤더가 예상과 다름 {hdr} → ASN 게이트 없이 진행")
        return {}
    out: dict[str, dict] = {}
    for row in vals[1:]:
        def gv(k):
            i = idx[k]
            return row[i] if i < len(row) else ""
        sku = str(gv("sku")).strip()
        eindt = str(gv("eindt")).strip().split(".")[0]
        if not sku or len(eindt) != 8:
            continue
        iso = f"{eindt[:4]}-{eindt[4:6]}-{eindt[6:8]}"
        if cutoff and iso < cutoff:
            continue
        e = out.setdefault(sku, {"qty": 0, "dates": [], "last_ins": ""})
        e["qty"] += int(_num(gv("qty")))
        e["dates"].append(iso)
        ins = str(gv("ins_at")).strip()
        if ins > e["last_ins"]:
            e["last_ins"] = ins
    for e in out.values():
        e["dates"] = sorted(set(e["dates"]))
    return out


C = {h: i for i, h in enumerate(HEADER)}       # 열 위치는 HEADER 에서 유도한다(열 추가에 안 밀리게)


def summarize(grid: list[list]) -> str:
    today = datetime.date.today().strftime("%Y%m%d")
    new_today = [g for g in grid if str(g[C["ins_at"]] or "")[:8] == today]
    pending = [g for g in grid if g[C["recv_qty"]] == 0]
    heroes = sorted({g[C["hero"]] for g in grid if g[C["hero"]]})
    return (f"ASN {len(grid)}행 · 히어로 {len(heroes)}종 · 오늘 등록 {len(new_today)}건 "
            f"· 입고확정 미반영 {len(pending)}건 {sum(g[C['qty']] for g in pending):,}장")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="시트에 기입(기본은 드라이런)")
    ap.add_argument("--days", type=int, default=LOOKBACK_DAYS)
    args = ap.parse_args()

    svc = build_services(get_credentials(HERO / "credentials.json", HERO / "token.json"))
    sheets = svc["sheets"]

    p2h = build_pumbon2hero(sheets)
    rows = fetch_asn(sorted(p2h), args.days)
    grid = to_grid(rows, p2h)
    # ★KST 로 못박는다 — `datetime.now()` 는 CI 러너(UTC)와 로컬(KST)에서 9시간 다르게 찍혀
    #   같은 라벨에 두 기준이 섞였다. ops_watch 가 이 라벨로 고착을 판정하므로 단위를 명시한다.
    as_of = (datetime.datetime.now(datetime.timezone.utc)
             + datetime.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST")
    print(f"[_ASN] {summarize(grid)}")

    if not args.apply:
        for g in grid[:8]:
            print("   ", g[C["eindt"]], g[C["hero"]], g[C["sku"]], g[C["color_nm"]],
                  f"{g[C['qty']]:,}장", g[C["supplier"]], g[C["warehouse"]],
                  "확정대기" if g[C["recv_qty"]] == 0 else f"입고 {g[C['recv_qty']]:,}")
        print("[_ASN] 드라이런 — 기입하려면 --apply")
        return

    ensure_tab(sheets, APP_SHEET_ID, TAB)
    write_tab(sheets, APP_SHEET_ID, grid, as_of)
    # 되읽어 검증 — 응답이 아니라 결과로 판정한다([[CLAUDE 1-16]]).
    back = sheets.spreadsheets().values().get(
        spreadsheetId=APP_SHEET_ID, range=f"'{TAB}'!A1:T",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    got = len(back) - 2
    if got != len(grid):
        raise RuntimeError(f"기입 검증 실패: 보낸 {len(grid)}행 vs 되읽은 {got}행")
    print(f"[_ASN] 기입 완료 {got}행 (검증 OK) · {as_of}")


if __name__ == "__main__":
    main()
