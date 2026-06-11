# Databricks 노트북 — 히어로 실적(매출 8기간 + 잔여재고 + 입고) → 구글시트 (매일 자동)
# ─────────────────────────────────────────────────────────────────────────────
# 목적: 마스터앱 "실적 대시보드"의 데이터를 매일 SA 시트로 내보낸다.
#       GitHub Actions 생성기(_gen_26fw_heroes.py --sheet)가 이 시트를 읽어 app.html 갱신.
# 출력 탭(10): YTD/전년YTD/MTD/전년MTD/WEEK/전년WEEK/DAY/전년DAY · 잔여재고 · 입고현황
# 등록: Run all 1회(시트 채워지는지 확인) → Schedule(매일 새벽, Asia/Seoul). PLM 잡과 동일 패턴.
# 인증: 사내 "실적 자동화(샘플)" SA secret(29CM_PRODUCT). SA 이메일을 대상 시트 편집자로 1회 공유.
# ─────────────────────────────────────────────────────────────────────────────

# COMMAND ----------
%pip install gspread
dbutils.library.restartPython()

# COMMAND ----------
import json, os, uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
import gspread as gs

# ── 인증 (PLM 노트북과 동일) ──
SCOPE = "29CM_PRODUCT"
KEY   = "29CM_PRODUCT_GCP_API"
FILE_URL = "https://docs.google.com/spreadsheets/d/1iHH2qG8Uj5vmlC3aXkey96usktWODmguDPD_ToT2rfA/edit"  # "히어로 실적 (자동)" 전용 시트 — SA 편집자 등록됨

_tmp = "/tmp/_sa_" + uuid.uuid4().hex + ".json"
with open(_tmp, "w") as f:
    json.dump(json.loads(dbutils.secrets.get(scope=SCOPE, key=KEY)), f)
gc = gs.service_account(filename=_tmp)
os.remove(_tmp)
_book = gc.open_by_url(FILE_URL)


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (int, float, str, bool)):
        return v
    try:                       # numpy 등 → 숫자/문자
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def insert_query_result(sheet_name, sdf, label=""):
    """탭에 기록: R1=라벨, R2=헤더, R3~=데이터. (생성기 read_tab 이 헤더=2행 기준으로 읽음)"""
    import pandas as pd
    pdf = sdf.toPandas().astype(object).where(lambda x: pd.notna(x), None)
    try:
        ws = _book.worksheet(sheet_name)
    except gs.WorksheetNotFound:
        ws = _book.add_worksheet(title=sheet_name, rows=10, cols=30)
    header = pdf.columns.tolist()
    rows = [[_cell(v) for v in row] for row in pdf.values.tolist()]
    ncols = max(len(header), 1)
    ws.clear()
    ws.resize(rows=max(len(rows) + 2, 2), cols=ncols)     # 라벨1 + 헤더1 + 데이터
    ws.update(values=[[label] + [""] * (ncols - 1)] + [header] + rows, value_input_option="RAW")
    print(f"[OK] {sheet_name}: {len(rows)} rows x {ncols} cols")


# COMMAND ----------
# ── 날짜 기준 (사용자 확정: Date=어제(KST), 주별=Date-6(7일), 월별=그달1일,
#    전년: YTD·MTD=달력 동일자(year-1) / WEEK·DAY=calendar.yoy_dt(요일정렬)) ──
calendar = spark.sql("SELECT * FROM datamart.datamart.calendar").toPandas()

kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
date = (kst_now - timedelta(days=1)).strftime("%Y%m%d")
d = datetime.strptime(date, "%Y%m%d")

ytd_end    = date
mtd_start  = d.replace(day=1).strftime("%Y%m%d")
mtd_end    = date
week_start = (d - timedelta(days=6)).strftime("%Y%m%d")   # 주별 1st date (7일)
week_end   = date
day_start  = date
day_end    = date

ytd_end_yoy   = d.replace(year=d.year - 1).strftime("%Y%m%d")
mtd_start_yoy = d.replace(year=d.year - 1, day=1).strftime("%Y%m%d")
mtd_end_yoy   = d.replace(year=d.year - 1).strftime("%Y%m%d")
week_start_yoy = calendar.loc[calendar["dt"] == week_start, "yoy_dt"].values[0]
week_end_yoy   = calendar.loc[calendar["dt"] == date, "yoy_dt"].values[0]
day_start_yoy  = calendar.loc[calendar["dt"] == date, "yoy_dt"].values[0]
day_end_yoy    = calendar.loc[calendar["dt"] == date, "yoy_dt"].values[0]

params = {
    "start_dt": ["20260101", "20250101", mtd_start, mtd_start_yoy, week_start, week_start_yoy, day_start, day_start_yoy],
    "end_dt":   [ytd_end,    ytd_end_yoy, mtd_end,   mtd_end_yoy,   week_end,   week_end_yoy,   day_end,   day_end_yoy],
    "file_nm":  ["YTD", "전년YTD", "MTD", "전년MTD", "WEEK", "전년WEEK", "DAY", "전년DAY"],
}

# ── 히어로 goods_filter (매출·재고 공통) ──
GOODS_FILTER = """
    (2447804),(2447806),(3051684),(3051685),(2447805),(3051687),(4642899),(3740942),(3134736),(3134737),(3134738),(4664527),(4664529),(4664530),(4664535),(4664536),(4664537),
    (5107708),(5107709),(5107710),(4682241),(4682243),(4682244),(4682245),(5662151),(5662152),(5662153),(5662154),(5662155),(5662157),
    (4682246),(4682247),(4682248),(4682249),(4682251),(4682252),(4682253),(4682254),(4682255),(4682250),
    (5884071),(5884072),(6104962),(5662191),(5662192),(5662193),(5662195),(5662196),
    (4682256),(4682257),(4682258),(4682259),(4682260),(4682261),(4682262),(4682263),
    (5466457),(5466459),(5466458),(5466455),(5466456),(5466466),(5466467),(5466468),(5466465),
    (5466463),(5466460),(5466462),(5466464),(5466529),
    (5466530),(5466531),(5466528),(5466527),(5466525),
    (6092186),(5755342),(5755343),(5755344),(5755345),
    (6092187),(5862852),(5862853),(5862854),
    (6078643),(5815495),(5815497),(5815498),(5815496),
    (5287052),(5287054),(5892071),(5166593),(5166591),(5166592),
    (6092188),(5795981),(5795982),
    (3793565),(3793566),(3793567),(3793568),(3793569),
    (4652839),(4652840),(5990760),(5990761),(5990762),
    (6092190),(5671197),(5671198),(5671199),(5671201),(5671202),
    (3758212),(3758213),(3758214),(3758215),(3758216),(3758217),(3758218),(3758219),(3758220),(3758221),
    (4652859),(4652860),(4652861),(4652862),(4652863),(4652864),(4652865),(4652866),
    (3790850),(3790851),(3790852),(3790853),(3790854),
    (4651730),(4651731),(4651732),(5671203),(5671204),
    (3758500),(3758501),(3758502),(4731740),
    (5812740),(5812741),(5812728),(5812729),(5812730),(5812731),
    (6104961),(5755302),(5755303),(5755304),(5755305),
    (6127413),(5915564),(5915566),(5915565),(5915567),
    (6092691),(5788233),(5788234),(5788236),(5788237),(5788238),(5788239),(5837368),
    (3966422),(3966423),(3966424),(6044591),(6044590),(3966425),(3996426),(3966428),
    (5788232),(5788231),(6058175),
    (6104960),(5888129),(5888131),(5888133),(5888134),(5888135),(5888136),(5888137),(5888139),
    (6121801),(5695800),(5695801),(5695802),(5695803),(5695804),
    (5795909),(5795910),(5795911),(5795912),(5795913),
    (996178),(1431741),(1117534),(1431733),(1117543),(1117542),(1431732),(996177),(2309127),
    (2309123),(1117536),(1431735),(1117539),(2309122),(1117546),(1457571),(1117533),(996184),
    (996187),(1117545),(1117544),(1431743),(996179),(1431734),(996189),(1431736),(996180),
    (1431742),(1446336),(1117541),(1117538),(1431737),
    (1117532),(1431744),(2309120),(2309121),(2309124),(2309125),(2309126),(2309128),(3651606),
    (2405728),(2405729),(2405769),(2405733),(2405738),(2405730),(2405732),(2405735),(2405737),
    (2405739),(2460195),(3034284),(1417692),(1424102),(1424103),(1424100),(1417691),
    (2374614),(2374611),(2374612),(2374619),(3153788),(3822236),(3822237),
    (1417693),(1417699),(1417697),(1417694),(1417695),(1417701),(1417696),
    (1841260),(1841251),(2374617),(2374613),(2374615),(2374618),
    (3153786),(3153787),(3153789),(3822239),(3753589),(3822241),(3822240),
    (4651365),(4651363),(4651364),(4651366),
    (1388775),(1388776),(1388777),(1388780),(1388782),(1388781),(2285960),(2976529),(2976528),
    (2976525),(1388779),(1388785),(1388778),(1388789),(1388790),(1388786),(1388792),(1388784),
    (1388783),(1944361),(1920863),(2285959),(2285961),(2285962),(2285963),(2976526),(2976527),
    (3651607),(2035287),(5163284),(6121803),
    (5795961),(5795962),(5795963),(5795964),(5795935),
    (1932037),(1932038),(1932039),(1932041),(1932046),(1932040),(1932044),
    (2321889),(2321890),(2341397),(2321891),(3059011),(3059012),(1932042),(2321888),
    (5915539),(5915542),(5915540),(5915541),
    (2341450),(2341451),(2341452),(2341453),(2341454),(2341456),(2341459),(2341460),(2341461),(2341462),
    (2976530),(2976533),(2976534),(2341455),(2341457),(2341458),(2976531),(2976532),
    (3034275),(3034276),(3034277),(3034278),(3034279),(3034280),(3034281),
    (3727887),(3727888),(4570534),
    (5915543),(5915547),(5915546),(5915545),(5915544),
    (2976549),(3052664),(5949864),(5949865),(5949866),(5949867),
    (3051693),(3051694),(3051695),(3051696),(3051697),(3740943),(4642900),(4642901),
    (5860707),(5860705),(5860706),(5860704),(5824390),(5824389),(5795869),
    (6450149),
    (4655088),(4655089),(4655090),(4655091),(4655092),(4655093),
    (4644755),(4644757),(4644756),(4644753),(4644754),(4644758),
    (4911480),(4655069),(4655070),(4655071),(4655072),(4655073),
    (4651422),(4651423),(4651424),(4651425),
    (4644815),(4644816),(4644817),
    (5795893),(5795894),(5795895),(5795896),
    (4664549),(4664552),(4664551),
    (5750428),(5750429),(5750430),
    (4057917),(4057918),(4057919),
    (4655178),(4655179),(4655180),(4655181),(4655182),
    (4767951),
    (4655118),(4655119),(4655120),(4655121),(4655122),(4655124),(4655125),(4655123),(4655126),(4655127),
    (5951630),(5951631),(5951632),(5951634),(5951636),(5951638),
    (3135345),(3135346),(3135347),
    (3822255),(3822254),(4651351),(4651352),(3822256),(3822257),(4651353),
    (5795965),(5795966),(5795967),(5795968),(5795969),
    (5812721),(5812722),(5812721),(5812722),
    (1273485),(1239618),(1970119),(2793592),(3667935),(1224096),(1239615),(1246244),(1669798),(1149329),
    (1669799),(1669800),(1224094),(1239614),(1449354),(2793593),(1239619),(1326565),(1224095),(1970117),
    (1254403),(1239617),(1669797),(1246487),(1224097),(2855593),(3667938),(1271471),(1669809),(1970106),
    (1168922),(1224098),(1970105),(1464934),(2855592),(1677686),(1970104),(1669806),(1970103),(1669808),
    (1222182),(1999996),(1168906),(1675530),(1222183),(1249103),(1452671),(1675529),(1249104),(1675531),
    (1303071),(1999993),(1999997),(1222184),(1999992),(1249102),(1999994),(2518487),(1989228),(2371969),
    (2957579),(2518486),(1990788),(1990787),(2371968),(2371970),(3054926),(1436504),(2456625),(1447705),
    (2371966),(1447706),(2371965),(3667931),(2371967),(2957578),(3054925),(1220731),(1640887),(3545586),
    (3545584),(3545583),(3545582),(1225000),(1243054),(3445169),(5928482),(5256142),(2738084),(2738085),
    (2738088),(2738087),(2738089),(4678016),(2738086),(1945857),(1945867),(1945839),(1945859),(1945849),
    (1945863),(1945869),(1945868),(1945844),(1945856),(1945848),(1945843),(1945841),(1945846),(5423758),
    (1945847),(5423755),(1945866),(1945862),(1945861),(1945858),(1945854),(1945860),(1945845),(1945855),
    (1945840),(1945851),(2485048),(1945853),(1945865),(1945852),(1945864),(1945850),(5423756),(5423759),
    (5423757),(1945838),(1945842),(4341946),(4341945),(5858269),(5858270),(2225907),(2225906),(3009682),
    (3009679),(3009680),(4246398),(4246399),(4246401),(4246402),(4246403),(4246404),(4246405),(2820939),
    (2820940),(2820941),(2820942),(2820943),(2820944),(4651338),(4651339),(4651340),(947057),(947058),
    (947061),(947060),(947059),(1801900),(1801896),(1801897),(1801898),(2795616),(2795617),(2795618),
    (2795620),(2795621),(2795624),(2795619),(2795622),(2795623),(1324127),(1324128),(1324130),(1324129),
    (1815000),(1805124),(1805121),(1805123),(1805122),(2692692),(2692693),(2692694),(2692695),(3753593),
    (3753594),(4651430),(4651431),(4651432),(4651433),(2405697),(2405698),(2405700),(2405702),(2405704),
    (2656894),(2656897),(2656902),(2656903),(2656901),(2656900),(4570541),(4570542),(4570543),(5837997),
    (5838000),(5838001),(5837999),(5837998),(1666443),(1720144),(1666442),(1666441),(1666444),(2208662),
    (2208663),(2208664),(3051714),(3740945),(4056413),(4572260),(4714957),(4714958),(3051698),(3051699),
    (3051700),(3051701),(3051702),(3051703),(3051704),(3051705),(3051706),(3740946),(4572262),(2028326),
    (2028327),(2028328),(2028329),(2391748),(2391744),(2391746),(2391745),(3051713),(3740944),(4056412),
    (4572259),(1357769),(1357770),(1357771),(1357768),(2208256),(2725426),(2957576),(2725425),(4642930),
    (4642932),(4642929),(2124425),(2124426),(2124427),(2124428),(2304246),(2303284),(2725427),(2725428),
    (2505949),(2725429),(4642928),(4642926),(2668360),(2668361),(2668362),(4467451),(5824388),(6277097),(6277096)
"""

# COMMAND ----------
# ── 매출 8기간 (Online=orders_merged + Offline=pos_order_sales UNION) ──
for start_date, end_date, f_name in zip(params["start_dt"], params["end_dt"], params["file_nm"]):
    query = f"""
WITH goods_filter AS (
  SELECT goods_no FROM (VALUES {GOODS_FILTER}) AS t(goods_no)
),
date_range AS (
  SELECT DISTINCT cal.dt, TO_DATE(cal.dt,'yyyyMMdd') AS date
  FROM datamart.datamart.calendar cal
  WHERE cal.dt BETWEEN '{start_date}' AND '{end_date}'
),
goods_base AS (
  SELECT g.goods_no, g.wonga, g.normal_price
  FROM datamart.datamart.goods g JOIN goods_filter gf ON g.goods_no = gf.goods_no
),
meta AS (
  SELECT goods_no, team, goods_gender_cd AS gender_line,
         category_nm_1depth AS category1, category_nm_2depth AS category2,
         md_nm AS md_name, release_season_type AS release_season, season AS sell_season, style_no
  FROM (SELECT goods_no, team, goods_gender_cd, category_nm_1depth, category_nm_2depth, md_nm,
               release_season_type, season, style_no,
               ROW_NUMBER() OVER (PARTITION BY goods_no ORDER BY md_nm, team) rn
        FROM gspread.musinsastandard.mutandard_goods_meta_v2 WHERE goods_no IS NOT NULL) x
  WHERE rn = 1
),
online_base AS (
  SELECT om.goods_no, om.goods_opt, LOWER(om.brand) brand, om.normal_price,
         om.sell_sub_clm_qty, om.sell_sub_clm_amt, om.head_wonga, om.partner_sale_fee,
         om.recv_amt, om.gmv_state, om.ord_com_type
  FROM datamart.datamart.orders_merged om
  JOIN date_range dr ON om.ord_state_date = dr.dt
  JOIN goods_filter gf ON om.goods_no = gf.goods_no
  WHERE om.state_order = TRUE
    AND LOWER(om.brand) IN ('musinsastandard','musinsastandardhome','musinsastandardwoman','musinsastandardkids')
    AND om.com_id NOT IN ('musinsa','musinsa_event')
),
online_processed AS (
  SELECT 'Online' channel, ob.goods_no, ob.goods_opt, ob.brand,
         m.team, m.gender_line, m.category1, m.category2, m.md_name, m.release_season, m.sell_season, m.style_no,
         ob.normal_price * ob.sell_sub_clm_qty tag_gmv, ob.sell_sub_clm_amt gmv, ob.sell_sub_clm_qty qty,
         ob.sell_sub_clm_amt - IF(ob.gmv_state IN ('1000','5000'), ob.recv_amt, -1*ob.recv_amt) total_discount,
         ob.partner_sale_fee + IF(ob.ord_com_type =1, ob.sell_sub_clm_amt - ob.head_wonga, 0) gross_take
  FROM online_base ob LEFT JOIN meta m ON ob.goods_no = m.goods_no
),
online_aggregated AS (
  SELECT channel, goods_no, goods_opt, brand,
         ANY_VALUE(team) team, ANY_VALUE(gender_line) gender_line, ANY_VALUE(category1) category1,
         ANY_VALUE(category2) category2, ANY_VALUE(md_name) md_name, ANY_VALUE(release_season) release_season,
         ANY_VALUE(sell_season) sell_season, ANY_VALUE(style_no) style_no,
         SUM(tag_gmv) tag_gmv, SUM(gmv) gmv, SUM(qty) qty, SUM(total_discount) total_discount,
         SUM(gross_take) gross_take, (SUM(gross_take)-SUM(total_discount))/1.1 net_take
  FROM online_processed GROUP BY channel, goods_no, goods_opt, brand
),
shop_list AS (
  SELECT DISTINCT shop_no FROM musinsa.order_group.shop WHERE LOWER(shop_type)='offline' OR shop_no=68
),
pos_fee AS (
  SELECT sales_key, MAX(fee_amount) fee_amount FROM musinsa.order_group.pos_settlement_item GROUP BY sales_key
),
offline_base AS (
  SELECT pos.goods_no, pos.goods_opt, LOWER(pos.brand_id) brand, pos.sales_type, pos.normal_price,
         pos.raw_price, pos.sales_price, pos.pay_amount, pf.fee_amount, pos.qty,
         pos.coupon_partner_amount, pos.cart_discount_partner_amount, pos.order_sheet_promotion_brand,
         IF(pos.sales_type='SALE',1,-1) np, IF(pos.product_type='100','3P','1P') com_type
  FROM musinsa.order_group.pos_order_sales pos
  JOIN date_range dr ON DATE(pos.sales_date)=dr.date
  JOIN goods_filter gf ON pos.goods_no = gf.goods_no
  JOIN shop_list sl ON pos.shop_no = sl.shop_no
  JOIN pos_fee pf ON pos.sales_key = pf.sales_key
  WHERE LOWER(pos.brand_id) IN ('musinsastandard','musinsastandardhome','musinsastandardwoman','musinsastandardkids')
),
offline_processed AS (
  SELECT 'Offline' channel, ob.goods_no, ob.goods_opt, ob.brand,
         m.team, m.gender_line, m.category1, m.category2, m.md_name, m.release_season, m.sell_season, m.style_no,
         ob.np*ob.qty qty,
         ob.np*ob.qty*IF(ob.normal_price=ob.raw_price OR ob.normal_price=0 OR ob.normal_price IS NULL,
                         IFNULL(gb.normal_price,0), ob.normal_price) tag_gmv,
         ob.np*ob.sales_price gmv, ob.np*ob.pay_amount pay_amount,
         CASE WHEN ob.com_type='1P' THEN IF(ob.normal_price=ob.raw_price OR ob.raw_price=0 OR ob.raw_price IS NULL,
                                            IFNULL(gb.wonga,0), ob.raw_price)*ob.qty
              ELSE (ob.sales_price-ob.fee_amount) END*ob.np cogs,
         ob.np*(ob.coupon_partner_amount+ob.cart_discount_partner_amount+ob.order_sheet_promotion_brand) brand_dc_amt
  FROM offline_base ob LEFT JOIN goods_base gb ON ob.goods_no = gb.goods_no LEFT JOIN meta m ON ob.goods_no = m.goods_no
),
offline_aggregated AS (
  SELECT channel, goods_no, goods_opt, brand,
         ANY_VALUE(team) team, ANY_VALUE(gender_line) gender_line, ANY_VALUE(category1) category1,
         ANY_VALUE(category2) category2, ANY_VALUE(md_name) md_name, ANY_VALUE(release_season) release_season,
         ANY_VALUE(sell_season) sell_season, ANY_VALUE(style_no) style_no,
         SUM(tag_gmv) tag_gmv, SUM(gmv) gmv, SUM(qty) qty, SUM(gmv-pay_amount) total_discount,
         SUM(gmv-cogs) gross_take, SUM(pay_amount-cogs+brand_dc_amt)/1.1 net_take
  FROM offline_processed GROUP BY channel, goods_no, goods_opt, brand
),
final_union AS (
  SELECT channel, goods_no, brand, team, gender_line, category1, category2, md_name, release_season, sell_season,
         style_no, tag_gmv, gmv, qty, total_discount, (gmv-total_discount)/1.1 revenue, gross_take, net_take, goods_opt
  FROM online_aggregated
  UNION ALL
  SELECT channel, goods_no, brand, team, gender_line, category1, category2, md_name, release_season, sell_season,
         style_no, tag_gmv, gmv, qty, total_discount, (gmv-total_discount)/1.1 revenue, gross_take, net_take, goods_opt
  FROM offline_aggregated
)
SELECT * FROM final_union ORDER BY channel, brand, goods_no, goods_opt LIMIT 200000
"""
    insert_query_result(f_name, spark.sql(query), label=f"매출 {f_name} {start_date}~{end_date}")

# COMMAND ----------
# ── 잔여재고 (어제 스냅샷) ──
stock_query = f"""
WITH goods_filter AS (SELECT goods_no FROM (VALUES {GOODS_FILTER}) AS t(goods_no)),
meta AS (
  SELECT DISTINCT goods_no, style_no, team
  FROM gspread.musinsastandard.mutandard_goods_meta_v2
),
stocks AS (
  SELECT a.dt,
    CASE WHEN a.lgort='2000' THEN '온라인창고' WHEN a.lgort='2010' THEN '오프라인허브' ELSE '매장' END AS stock_type,
    a.lgort, a.goods_no, c.style_no, b.brand_nm, c.team, a.barcode,
    a.`재고수량` AS qty, b.normal_price * a.`재고수량` AS normal_price_amt, b.wonga * a.`재고수량` AS wonga_amt
  FROM datamart.datamart.stock_snapshot a
  INNER JOIN goods_filter gf ON a.goods_no = gf.goods_no
  LEFT JOIN datamart.datamart.goods b ON a.goods_no = b.goods_no
  LEFT JOIN meta c ON a.goods_no = c.goods_no
  WHERE a.dt = DATE_FORMAT(DATE_SUB(CURRENT_DATE(), 1), 'yyyyMMdd')
    AND b.brand_nm IN ('무신사 스탠다드','무신사 스탠다드 우먼','무신사 스탠다드 홈','무신사 스탠다드 키즈',
                       '무신사 스탠다드 뷰티','무신사 스탠다드 스포츠','무신사스탠다드 지비지에이치')
    AND a.`재고수량` <> 0
)
SELECT dt, stock_type, lgort, brand_nm, team, goods_no, style_no,
       SUM(qty) AS qty, SUM(normal_price_amt) AS normal_price_amt, SUM(wonga_amt) AS wonga_amt, barcode
FROM stocks GROUP BY dt, stock_type, lgort, brand_nm, team, goods_no, style_no, barcode
ORDER BY stock_type, brand_nm, goods_no, barcode
"""
insert_query_result("잔여재고", spark.sql(stock_query), label="전일자 기준 남은 재고")

# COMMAND ----------
# ── 입고현황 (2025-11-01 ~ 어제 누적; goods_filter 없이 무탠 전체) ──
inbound_query = """
WITH meta AS (
  SELECT style_no, team FROM (
    SELECT style_no, team, ROW_NUMBER() OVER (PARTITION BY style_no ORDER BY style_no DESC) rn
    FROM gspread.musinsastandard.mutandard_goods_meta_v2) t WHERE rn = 1
),
goods_map AS (
  SELECT style_no, goods_no, brand_nm, normal_price, wonga FROM (
    SELECT style_no, goods_no, brand_nm, normal_price, wonga,
           ROW_NUMBER() OVER (PARTITION BY style_no ORDER BY style_no DESC) rn
    FROM datamart.datamart.goods WHERE com_id NOT IN ('musinsa_used','musinsa_event','musinsa')) g WHERE rn = 1
),
inbound AS (
  SELECT a.SPR_NM AS plant_nm, gm.brand_nm, m.team, gm.goods_no, a.STL_NO AS style_no,
         a.ACT_QTY AS inbound_qty, gm.normal_price, gm.wonga, a.BARCODE AS barcode
  FROM pbo.moms.ui_grreport_detail a
  LEFT JOIN goods_map gm ON a.STL_NO = gm.style_no
  LEFT JOIN meta m ON a.STL_NO = m.style_no
  WHERE a.ORD_STATUS NOT IN ('출고취소','입고취소','입고대기') AND a.ORD_TYPE = '일반' AND a.SPR_NM = 'MUSINSA'
    AND gm.brand_nm IN ('무신사 스탠다드 우먼','무신사 스탠다드','무신사 스탠다드 홈','무신사 스탠다드 키즈',
                        '무신사 스탠다드 뷰티','무신사 스탠다드 스포츠','무신사스탠다드 지비지에이치')
    AND a.ACT_DATE >= '20251101' AND a.ACT_DATE <= DATE_FORMAT(DATE_SUB(CURRENT_DATE(), 1), 'yyyyMMdd')
)
SELECT plant_nm, brand_nm, team, goods_no, style_no,
       SUM(inbound_qty) AS inbound_qty, SUM(inbound_qty * CAST(normal_price AS BIGINT)) AS normal_price_amt,
       SUM(inbound_qty * wonga) AS wonga_amt, barcode
FROM inbound GROUP BY plant_nm, brand_nm, team, goods_no, style_no, barcode
ORDER BY plant_nm, brand_nm, team, goods_no, style_no, barcode
"""
insert_query_result("입고현황", spark.sql(inbound_query), label="25년 11월 1일부터 전일자 누적 입고")

# COMMAND ----------
print("완료 — 10개 탭 기록. 생성기(_gen_26fw_heroes.py --sheet)가 이 시트를 읽어 app.html 갱신.")
