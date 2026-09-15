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

★취소 ASN — ERP 원본(`pbo.erp.supplier_order_asn`) delete_flag 로 `cancelled` 딱지(2026-09-10).

★★입고확정 매칭 = ASN 번호 정확 매칭 (2026-09-15, ±3일 창 폐기)
  WMS `ui_grreport_detail` 은 입고 오더마다 **`ORD_OPT_NO` = ASN 번호**(`TX-INB…`·`260902-MUTAN-…`),
  `GDS_CD` = ZZ_BARCODE 와 같은 `품번+컬러+사이즈` 문자열을 들고 있다(`REF_NO1/2` = PO/라인).
  그래서 "이 통보분이 WMS 에 잡혔나"를 날짜 창 없이 **ASN × SKU 로 바로** 센다.
  옛 방식(같은 SKU 의 실입고를 납품일 ±3일로 붙이고 통보량 비례 안분 + 캡)이 틀리던 두 경우 —
  ①창 밖 입고: 빅토리아 울 `MWFWL9A25` 8/29 통보 7,888 이 **8/25 에 정확히 7,888 입고**됐는데 0% ·
    데님 8/7 통보분이 8/13 입고라 0% ②중복 등록이 실입고를 나눠 가짐: 슬랙스 `MMDPL3Z07` 8/7 통보 2건이
    WMS 확정 4,933 을 안분받아 둘 다 54%(실제로는 13:54 통보만 100% 입고, 10:06 통보는 입고대기 0).
  ★실측 연결률 = 최근 45일 ASN×SKU 의 97%+ 가 WMS 오더를 가진다(없는 건 WMS 오더 생성 전인 최근분).

★★중복 등록 = `dup_qty` 딱지 (2026-09-15)
  업체가 같은 PO·SKU·납품일로 ASN 을 **다시 등록**하면 앞 통보가 취소 없이 남는다(ERP delete_flag 도 비어 있음).
  WMS 는 새 통보로만 입고하고 옛 통보 오더는 '입고대기' 로 영원히 남아 화면에 '확정대기' 로 굳는다.
  → 유예(`DUP_GRACE_DAYS`)가 지났는데 **WMS 가 사실상 안 받은 통보**(입고 10% 미만)의 미입고분을,
  같은 SKU·PO 로 납품일 ±3일 안에 등록된 다른 ASN 이 실제 입고한 수량 한도 안에서 `dup_qty` 로 표시한다
  (`dup_of` = 그 ASN). 행은 버리지 않는다([[CLAUDE 1-12]]). 대부분 입고된 통보의 잔량은 판정하지 않는다
  (검수 부족분과 못 가른다). ★날짜가 달라도 본다 — `MMFDJ9A82` 는 8/27 통보를 8/28 로 옮겨 재등록했다.
  ★규칙을 PO 잔량·등록시각으로 짜려던 시도는 실측으로 폐기 — 같은 날 분할 출고(트럭 2대)가 흔하고,
  WMS 가 받아 준 쪽이 먼저 등록된 통보인 경우도 절반이라 '나중 것이 정본'이 성립하지 않았다.
  실입고라는 결과로만 판정한다.

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
# 중복 등록 판정 유예 — 같은 날 분할 출고의 두 번째 트럭이 하루이틀 늦게 확정되는 건 정상이다.
#   입고 보드의 확정 유예(`inbound_board.CONFIRM_GRACE_DAYS` = 3)와 같은 값을 쓴다.
DUP_GRACE_DAYS = 3
DUP_DATE_WINDOW = 3          # 재등록은 납품일을 옮기기도 한다 — 같은 SKU·PO 의 ±N일 통보를 형제로 본다
DUP_MAX_RECV_RATIO = 0.1     # 이만큼도 안 들어온 통보만 중복 후보(대부분 들어온 통보의 잔량은 검수 부족분)
# 유예 지난 통보 중 WMS 오더가 연결된 비율의 하한 — 밑돌면 키(ORD_OPT_NO·GDS_CD) 형식이 바뀐 것이다.
#   조용히 전건 '확정대기' 가 되느니 실패로 올린다([[CLAUDE 1-1]] 값 입도가 바뀌면 교집합이 0 이 된다).
MIN_LINK_RATIO = 0.8

# ★열은 끝에만 붙인다 — 앱·알림은 헤더 이름으로 읽지만 옛 범위(A1:T 등)가 남아 있을 수 있다.
HEADER = ["asn_no", "po_no", "po_cnt", "sku", "style", "color", "color_nm", "hero", "name",
          "eindt", "qty", "po_qty", "remain", "supplier", "warehouse",
          "sts", "ins_at", "upd_at", "recv_qty", "recv_dates", "cancelled",
          "wms_linked", "dup_qty", "dup_of"]


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


LAST_COL = _col(len(HEADER))     # 범위는 HEADER 에서 유도한다(열이 늘 때 뒤 열이 잘리던 사고 2회)


def fetch_asn(styles: list[str], lookback: int = LOOKBACK_DAYS) -> list[list]:
    """히어로 품번의 ASN + 그 ASN 번호로 잡힌 WMS 실입고를 한 번에 읽는다(상단 주석 참조)."""
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
  --   결론: **접지 않는다.** SKU 는 항상 품번-코드로 둔다. WMS 는 STL_NO 가 아니라 ZZ_BARCODE 와
  --   같은 형식인 `GDS_CD` 로 붙이므로 상품군별 STL_NO 적재형태 차이를 탈 일이 없다.
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
canc AS (
  -- ★취소 ASN. 우리 원천(`pbo.moms.iif_bam_asn`)엔 delete_flag 가 없어 여태 못 걸렀다 →
  --   권한이 열린 ERP 원본에서 읽는다(JIRA DAC-4570, 2026-09-10 승인).
  -- ★★그레인 주의 = 이 테이블은 **일자별 전체 스냅샷**이다. date 를 최신 하나로 고정하지
  --   않으면 같은 건이 날마다 반복돼 집계가 배로 뛴다(실측: 한 건이 42개 date 에 있어 수량 42배).
  -- ★style_no 표기가 `품번-컬러`·`품번` 으로 섞여 있어(WMS STL_NO 과 같은 함정) **품번 9자로
  --   접어서** 맞춘다. 실측으로 이렇게 접었을 때 우리 843행이 ERP 와 214/214 전건 일치했다.
  SELECT DISTINCT asn_no, substr(style_no, 1, 9) style
  FROM pbo.erp.supplier_order_asn
  WHERE date = (SELECT max(date) FROM pbo.erp.supplier_order_asn)
    AND upper(coalesce(delete_flag, '')) = 'X'
    AND substr(style_no, 1, 9) IN ({in_styles})
),
color AS (
  -- 컬러코드 → 한글 컬러명. ★`mutandard_color_cd` 는 color_cd 가 중복될 수 있어(2행 5개·59행 1개)
  --   그냥 조인하면 ASN 행이 증식한다 — 코드당 하나로 접어서 붙인다.
  SELECT color_cd, max(color_kor) color_nm
  FROM gspread.musinsastandard.mutandard_color_cd
  WHERE nullif(trim(color_cd),'') IS NOT NULL GROUP BY 1
),
wms AS (
  -- ★ASN 번호 정확 매칭. WMS 입고 오더의 `ORD_OPT_NO` 가 ASN 번호다(`TX-INB…` 외 `260902-MUTAN-…`
  --   류도 있으니 접두로 거르지 말 것 — 실측으로 95건이 그 형식이었다).
  --   ★ORD_TYPE·SPR_NM 필터는 걸지 않는다 — ASN 번호로 이미 좁혀졌고, 걸면 조용히 빠질 뿐이다.
  --   linked = 상태 무관 오더 존재 여부(입고대기 포함) — 연결률 가드와 중복 판정에 쓴다.
  SELECT ORD_OPT_NO asn_no,
         concat(substr(GDS_CD,1,9),'-',substr(GDS_CD,10,2)) sku,
         sum(CASE WHEN ORD_STATUS NOT IN ('출고취소','입고취소','입고대기') THEN ACT_QTY ELSE 0 END) qty,
         concat_ws(',', sort_array(collect_set(
           CASE WHEN ORD_STATUS NOT IN ('출고취소','입고취소','입고대기') AND ACT_QTY > 0 THEN ACT_DATE END))) dts
  FROM pbo.moms.ui_grreport_detail
  WHERE PLN_DATE >= DATE_FORMAT(DATE_SUB(CURRENT_DATE(), {int(lookback) + 30}), 'yyyyMMdd')
    AND ORD_OPT_NO IN (SELECT DISTINCT DELVNO FROM asn)
  GROUP BY 1, 2
)
SELECT a.DELVNO, a.po_no, a.po_cnt, a.sku, a.style, a.color, c.color_nm, a.name, a.EINDT,
       a.qty, a.po_qty, a.remain, a.supplier, a.warehouse, a.sts, a.ins_at, a.upd_at,
       -- ★캡·안분 없음 — 이 ASN 번호로 잡힌 실입고 그대로다(과입고면 통보량을 넘을 수 있다).
       CAST(coalesce(w.qty, 0) AS BIGINT) recv_qty,
       coalesce(w.dts, '') recv_dates,
       -- 취소면 'Y'. ★행을 버리지 않고 딱지만 붙인다 — 알림은 제외하되 화면에서는 보여야
       --   "왜 사라졌지"가 되지 않는다([[CLAUDE 1-12]] 필터로 영구 드롭하지 말고 토글로 가려라).
       CASE WHEN cx.asn_no IS NOT NULL THEN 'Y' ELSE '' END cancelled,
       CASE WHEN w.asn_no IS NOT NULL THEN 'Y' ELSE '' END wms_linked
FROM asn a
LEFT JOIN color c ON c.color_cd = a.color
LEFT JOIN canc cx ON cx.asn_no = a.DELVNO AND cx.style = a.style
LEFT JOIN wms w ON w.asn_no = a.DELVNO AND w.sku = a.sku
ORDER BY a.EINDT DESC, a.ins_at DESC, a.sku
"""
    # ★대기 예산 = 900초 x 3회(+백오프 30·60초) ≈ 47분 < 잡 timeout 60분.
    #   (2026-09-14 확대 — 전엔 240초 x 3회 ≈ 13.5분이었다.)
    #   9/14(월) 혼잡이 00:26Z~01:16Z 최소 50분 이어져 **두 런 연속 3회를 다 소진**했다.
    #   9/11 과 합쳐 실패 4건이 전부 00~01Z(KST 09~10시) 창이다. 13.5분으로는 그 창을
    #   못 건너고, 짧게 끊고 다시 내면 **대기열 자리를 매번 버리고 맨 뒤로 선다**.
    #   → 한 번을 길게 기다려 자리를 지킨다. 대기 중인 statement 는 실행 슬롯을 물지 않고
    #   줄만 선다(포기할 땐 여전히 취소한다 — launch_report.dbx_sql).
    #   47분 + 다음 hourly 런(concurrency 로 이어서 대기)이면 1시간 창이 사실상 끊김 없이 덮인다.
    #   ★잡 timeout 을 같이 안 늘리면 GH 하드 타임아웃에 걸려 **잘린 로그**만 남는다(yml 과 짝).
    rows = dbx_sql(sql, wait=900, attempts=3)
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
         recv_qty, recv_dates, cancelled, wms_linked) = r
        out.append([
            delvno or "", po_no or "", int(_num(po_cnt)), sku or "", style or "", color or "",
            (color_nm or "").strip(),
            p2h.get(style or "", ""), _clean_name(name),
            eindt or "",
            int(_num(qty)), int(_num(po_qty)), int(_num(remain)),
            (supplier or "").strip(), (warehouse or "").strip(), (sts or "").strip(),
            ins_at or "", upd_at or "",
            int(_num(recv_qty)), recv_dates or "",
            (cancelled or "").strip(),
            (wms_linked or "").strip(),
            0, "",                                   # dup_qty · dup_of — mark_duplicates 가 채운다
        ])
    return out


def mark_duplicates(grid: list[list], today: datetime.date,
                    grace: int = DUP_GRACE_DAYS) -> list[list]:
    """중복 등록 통보의 미입고분에 `dup_qty`·`dup_of` 를 채운다(상단 주석 '중복 등록' 참조).

    같은 (SKU, PO, 납품일) 묶음에서, 유예가 지났는데 아직 안 들어온 통보분을 **다른 ASN 이 실제로
    입고한 수량** 한도 안에서만 중복으로 본다. 한 ASN 의 실입고를 두 통보가 나눠 쓰지 않게
    먼저 등록된 통보부터 차감한다. 취소 통보는 판정에서 뺀다(이미 딱지가 있다).
    """
    def _d(s):
        s = str(s)
        return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))

    cut = (today - datetime.timedelta(days=grace)).strftime("%Y%m%d")
    groups: dict[tuple, list[list]] = {}
    for g in grid:
        if str(g[C["cancelled"]]).upper() == "Y" or len(str(g[C["eindt"]])) != 8:
            continue
        groups.setdefault((g[C["sku"]], g[C["po_no"]]), []).append(g)
    for rows in groups.values():
        if len({g[C["asn_no"]] for g in rows}) < 2:
            continue
        used: dict[str, int] = {}                    # ASN → 다른 통보의 중복 판정에 이미 쓴 실입고
        for g in sorted(rows, key=lambda x: str(x[C["ins_at"]])):
            if str(g[C["eindt"]]) > cut:
                continue
            # ★WMS 가 사실상 안 받은 통보(입고 < DUP_MAX_RECV_RATIO)만 본다. 대부분 들어온 통보의 잔량은
            #   검수 부족분(97~99%)이라 중복이 아니다 — 라이트다운 `MMFDJ9A82-BR` 780 중 765 입고분의
            #   부족 15장이 옆 ASN 입고분에 걸려 중복으로 오판된 걸 드라이런에서 잡았다.
            #   0 이 아니라 비율로 거는 이유 = 재등록 직전에 옛 통보로 몇 장이 먼저 찍힌다
            #   (`MMFDJ9A82-BK` 옛 통보 3,270 중 15장 입고 → 나머지는 새 통보로 3,195장).
            if g[C["recv_qty"]] >= g[C["qty"]] * DUP_MAX_RECV_RATIO:
                continue
            pending = g[C["qty"]] - g[C["recv_qty"]]
            if pending <= 0:
                continue
            dup, of = 0, []
            # 가까운 납품일 → 나중에 등록된 통보 순으로 본다(재등록은 대개 같은 날·더 늦은 등록이다).
            near = sorted(rows, key=lambda o: (abs((_d(o[C["eindt"]]) - _d(g[C["eindt"]])).days),
                                               0 if str(o[C["ins_at"]]) > str(g[C["ins_at"]]) else 1))
            for o in near:
                if o[C["asn_no"]] == g[C["asn_no"]] or dup >= pending:
                    continue
                # ★납품일이 같을 필요는 없다 — 재등록하면서 날짜를 하루 옮기는 경우가 있다
                #   (`MMFDJ9A82` 8/27 통보 → 8/28 로 재등록, WMS 는 새 통보로 입고).
                if abs((_d(o[C["eindt"]]) - _d(g[C["eindt"]])).days) > DUP_DATE_WINDOW:
                    continue
                # 다른 ASN 이 자기 통보량을 채우고 남긴 실입고가 아니라 '실입고 전체'를 근거로 쓴다 —
                #   재등록은 보통 같은 수량을 다시 올린 것이라 상대는 자기 통보분만큼 들어온다.
                avail = o[C["recv_qty"]] - used.get(o[C["asn_no"]], 0)
                take = min(pending - dup, avail)
                if take > 0:
                    dup += take
                    used[o[C["asn_no"]]] = used.get(o[C["asn_no"]], 0) + take
                    of.append(o[C["asn_no"]])
            if dup:
                g[C["dup_qty"]] = dup
                g[C["dup_of"]] = ",".join(of)
    return grid


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
    #   ★범위는 HEADER 에서 유도 — `A1:T` 로 박혀 있어 U열(cancelled) 이후는 안 지워지고 있었다.
    sheets.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=f"'{TAB}'!A1:{LAST_COL}", body={}).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=f"'{TAB}'!A1",
        valueInputOption="RAW", body={"values": body}).execute()


def load_asn_from_sheet(sheets, sheet_id: str = APP_SHEET_ID, cutoff: str | None = None) -> dict:
    """`_ASN` 탭 → {품번-컬러: {"qty": 통보합, "dates": [납품예정일…], "last_ins": 최근등록}}.

    입고 보드 상태 판정에 쓴다 — ASN 이 떠 있으면 '미입고(빨강)' 대신 '확정 대기'.
    탭이 없거나 읽기 실패면 None 을 돌려 게이트를 끈다(있던 화면이 깨지지 않게).
    ★취소 통보는 빼고, 중복 등록분(`dup_qty`)은 통보량에서 뺀다 — 안 빼면 중복 통보만 남은 SKU 가
      '확정 대기'로 게이트를 열어 진짜 미입고(빨강)를 가린다. (예전엔 `A2:T` 로 읽어 cancelled 열이
      아예 안 읽혔다 — 알림만 고치고 이 소비자는 안 고쳐져 있었다 [[CLAUDE 1-1]].)
    """
    try:
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{TAB}'!A2:{LAST_COL}",
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
        if "cancelled" in idx and str(gv("cancelled")).strip().upper() == "Y":
            continue
        live = int(_num(gv("qty"))) - (int(_num(gv("dup_qty"))) if "dup_qty" in idx else 0)
        if live <= 0:
            continue
        e = out.setdefault(sku, {"qty": 0, "dates": [], "last_ins": ""})
        e["qty"] += live
        e["dates"].append(iso)
        ins = str(gv("ins_at")).strip()
        if ins > e["last_ins"]:
            e["last_ins"] = ins
    for e in out.values():
        e["dates"] = sorted(set(e["dates"]))
    return out


C = {h: i for i, h in enumerate(HEADER)}       # 열 위치는 HEADER 에서 유도한다(열 추가에 안 밀리게)


def _kst_today() -> datetime.date:
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)).date()


def link_stats(grid: list[list], today: datetime.date, grace: int = DUP_GRACE_DAYS) -> tuple[int, int]:
    """유예 지난(취소 아닌) 통보 중 WMS 오더가 연결된 행 수 / 전체 행 수."""
    cut = (today - datetime.timedelta(days=grace)).strftime("%Y%m%d")
    aged = [g for g in grid
            if str(g[C["eindt"]]) <= cut and str(g[C["cancelled"]]).upper() != "Y"]
    return sum(1 for g in aged if g[C["wms_linked"]] == "Y"), len(aged)


def summarize(grid: list[list], today: datetime.date) -> str:
    t = today.strftime("%Y%m%d")
    new_today = [g for g in grid if str(g[C["ins_at"]] or "")[:8] == t]
    pending = [g for g in grid if g[C["recv_qty"]] == 0 and not g[C["dup_qty"]]]
    heroes = sorted({g[C["hero"]] for g in grid if g[C["hero"]]})
    canc = [g for g in grid if str(g[C["cancelled"]] or "").upper() == "Y"]
    dups = [g for g in grid if g[C["dup_qty"]]]
    linked, aged = link_stats(grid, today)
    return (f"ASN {len(grid)}행 · 히어로 {len(heroes)}종 · 오늘 등록 {len(new_today)}건 "
            f"· 입고확정 미반영 {len(pending)}건 {sum(g[C['qty']] for g in pending):,}장"
            + (f" · ★취소 {len(canc)}건 {sum(g[C['qty']] for g in canc):,}장(알림 제외)"
               if canc else " · 취소 0건")
            + f" · 중복등록 {len(dups)}건 {sum(g[C['dup_qty']] for g in dups):,}장"
            + f" · WMS 연결 {linked}/{aged}행(유예 {DUP_GRACE_DAYS}일 지난 통보)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="시트에 기입(기본은 드라이런)")
    ap.add_argument("--days", type=int, default=LOOKBACK_DAYS)
    args = ap.parse_args()

    svc = build_services(get_credentials(HERO / "credentials.json", HERO / "token.json"))
    sheets = svc["sheets"]

    p2h = build_pumbon2hero(sheets)
    rows = fetch_asn(sorted(p2h), args.days)
    today = _kst_today()
    grid = mark_duplicates(to_grid(rows, p2h), today)
    # ★KST 로 못박는다 — `datetime.now()` 는 CI 러너(UTC)와 로컬(KST)에서 9시간 다르게 찍혀
    #   같은 라벨에 두 기준이 섞였다. ops_watch 가 이 라벨로 고착을 판정하므로 단위를 명시한다.
    as_of = (datetime.datetime.now(datetime.timezone.utc)
             + datetime.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M KST")
    print(f"[_ASN] {summarize(grid, today)}")

    # ★연결률 가드 — ASN 번호 매칭이 깨지면(키 형식 변경) 전건 '확정대기' 가 조용히 쌓인다.
    #   매칭 0 은 에러가 아니라 숫자로만 나타나므로 여기서 실패로 올리고 양쪽 키 예시를 싣는다.
    linked, aged = link_stats(grid, today)
    if aged >= 20 and linked < aged * MIN_LINK_RATIO:
        miss = [g for g in grid if g[C["wms_linked"]] != "Y"][:5]
        raise RuntimeError(
            f"WMS 연결률 {linked}/{aged} < {MIN_LINK_RATIO:.0%} — ORD_OPT_NO(ASN번호)·GDS_CD 형식이 "
            f"바뀌었는지 볼 것. 미연결 예: " + ", ".join(f"{g[C['asn_no']]}:{g[C['sku']]}" for g in miss))

    if not args.apply:
        for g in grid[:8]:
            print("   ", g[C["eindt"]], g[C["hero"]], g[C["sku"]], g[C["color_nm"]],
                  f"{g[C['qty']]:,}장", g[C["supplier"]], g[C["warehouse"]],
                  "확정대기" if g[C["recv_qty"]] == 0 else f"입고 {g[C['recv_qty']]:,}")
        for g in [g for g in grid if g[C["dup_qty"]]]:
            print("    중복", g[C["eindt"]], g[C["hero"]], g[C["sku"]], g[C["asn_no"]],
                  f"통보 {g[C['qty']]:,} · 입고 {g[C['recv_qty']]:,} · 중복 {g[C['dup_qty']]:,}",
                  "← " + g[C["dup_of"]])
        print("[_ASN] 드라이런 — 기입하려면 --apply")
        return

    ensure_tab(sheets, APP_SHEET_ID, TAB)
    write_tab(sheets, APP_SHEET_ID, grid, as_of)
    # 되읽어 검증 — 응답이 아니라 결과로 판정한다([[CLAUDE 1-16]]).
    back = sheets.spreadsheets().values().get(
        spreadsheetId=APP_SHEET_ID, range=f"'{TAB}'!A1:{LAST_COL}",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    got = len(back) - 2
    if got != len(grid):
        raise RuntimeError(f"기입 검증 실패: 보낸 {len(grid)}행 vs 되읽은 {got}행")
    print(f"[_ASN] 기입 완료 {got}행 (검증 OK) · {as_of}")


if __name__ == "__main__":
    main()
