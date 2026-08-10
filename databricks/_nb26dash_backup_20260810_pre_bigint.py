# Databricks notebook source
# MAGIC %md
# MAGIC

# COMMAND ----------

# MAGIC   %pip install gspread
# MAGIC   dbutils.library.restartPython()

# COMMAND ----------

  import json, os, uuid
  from datetime import datetime, timedelta
  from zoneinfo import ZoneInfo
  from decimal import Decimal
  import gspread as gs
  import pandas as pd

  SCOPE = "29CM_PRODUCT"
  KEY   = "29CM_PRODUCT_GCP_API"
  FILE_URL = "https://docs.google.com/spreadsheets/d/1O78bMnJZq-U6zO2mZLHV84573uKM9DU2wpgzeGDBIk0/edit"  # 히어로 실적 (자동) 26FW

  _tmp = "/tmp/_sa_" + uuid.uuid4().hex + ".json"
  with open(_tmp, "w") as f:
      json.dump(json.loads(dbutils.secrets.get(scope=SCOPE, key=KEY)), f)
  gc = gs.service_account(filename=_tmp)
  os.remove(_tmp)
  _book = gc.open_by_url(FILE_URL)

  def _cell(v):
      if v is None: return ""
      if isinstance(v, Decimal): return float(v)
      if isinstance(v, (int, float, str, bool)): return v
      try: return float(v)
      except (TypeError, ValueError): return str(v)

  def insert_query_result(sheet_name, sdf, label=""):
      # 메모리 절감: toPandas(astype/where/values.tolist = ~4중 드라이버 복사) 대신 collect() 단일 패스 → 드라이버 OOM 방지
      header = list(sdf.columns)
      rows = [[_cell(v) for v in r] for r in sdf.collect()]
      try:
          ws = _book.worksheet(sheet_name)
      except gs.exceptions.WorksheetNotFound:
          ws = _book.add_worksheet(title=sheet_name, rows=10, cols=30)
      ncols = max(len(header), 1)
      ws.clear()
      ws.resize(rows=max(len(rows) + 2, 2), cols=ncols)
      ws.update(values=[[label] + [""] * (ncols - 1)] + [header] + rows, value_input_option="RAW")
      print(f"[OK] {sheet_name}: {len(rows)} rows x {ncols} cols")

# COMMAND ----------

# MAGIC %md
# MAGIC ### ▼ 실적 데이터 추출 (일 1회)
# MAGIC - 'YTD', '전년YTD', 'MTD', '전년MTD', 'WEEK', '전년WEEK', 'DAY', '전년DAY'

# COMMAND ----------

  calendar = spark.sql("SELECT * FROM datamart.datamart.calendar").toPandas()

  kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
  date = (kst_now - timedelta(days=1)).strftime("%Y%m%d")
  d = datetime.strptime(date, "%Y%m%d")

  ytd_end    = date
  mtd_start  = d.replace(day=1).strftime("%Y%m%d")
  mtd_end    = date
  week_start = (d - timedelta(days=6)).strftime("%Y%m%d")   # 주별 1st = Date-6 (7일)
  week_end   = date
  day_start  = date
  day_end    = date

  ytd_end_yoy    = d.replace(year=d.year - 1).strftime("%Y%m%d")
  mtd_start_yoy  = d.replace(year=d.year - 1, day=1).strftime("%Y%m%d")
  mtd_end_yoy    = d.replace(year=d.year - 1).strftime("%Y%m%d")
  week_start_yoy = calendar.loc[calendar["dt"] == week_start, "yoy_dt"].values[0]
  week_end_yoy   = calendar.loc[calendar["dt"] == date, "yoy_dt"].values[0]
  day_start_yoy  = calendar.loc[calendar["dt"] == date, "yoy_dt"].values[0]
  day_end_yoy    = calendar.loc[calendar["dt"] == date, "yoy_dt"].values[0]

  params = {
      # ★2026-07-21: YTD 누적을 26FW(7/1~)로 재정의 — 대시보드 누적블록 라벨 "26FW 누적 판매현황 (26.7.1~)"에 맞춤.
      #   YTD start 20260101→20260701, 전년YTD 20250101→20250701. 별도 FW/전년FW는 이제 YTD와 동일이라 제거.
      "start_dt": ["20260701","20250701", mtd_start, mtd_start_yoy, week_start, week_start_yoy, day_start, day_start_yoy],
      "end_dt":   [ytd_end, ytd_end_yoy, mtd_end, mtd_end_yoy, week_end, week_end_yoy, day_end, day_end_yoy],
      "file_nm":  ["YTD","전년YTD","MTD","전년MTD","WEEK","전년WEEK","DAY","전년DAY"],
  }

  GOODS_FILTER = """
    (991339),(991340),(991341),(1149329),(1168906),(1168922),(1222182),(1222183),(1222184),(1224094),(1224095),(1224096),
    (1224097),(1224098),(1225000),(1239614),(1239615),(1239617),(1239618),(1239619),(1243054),(1246244),(1246487),(1249102),
    (1249103),(1249104),(1249105),(1254403),(1271471),(1273485),(1303071),(1314973),(1322219),(1322220),(1322221),(1322222),
    (1326565),(1449354),(1452671),(1464934),(1595986),(1595987),(1596414),(1640887),(1666441),(1666442),(1666443),(1666444),
    (1669797),(1669798),(1669799),(1669800),(1669806),(1669808),(1669809),(1670229),(1670230),(1670231),(1670232),(1670233),
    (1670234),(1675529),(1675530),(1675531),(1677686),(1720144),(1851966),(1851967),(1851968),(1945838),(1945839),(1945840),
    (1945841),(1945842),(1945843),(1945844),(1945845),(1945846),(1945847),(1945848),(1945849),(1945850),(1945851),(1945852),
    (1945853),(1945854),(1945855),(1945856),(1945857),(1945858),(1945859),(1945860),(1945861),(1945862),(1945863),(1945864),
    (1945865),(1945866),(1945867),(1945868),(1945869),(1970103),(1970104),(1970105),(1970106),(1970117),(1970119),(1986029),
    (1986030),(1986031),(1999992),(1999993),(1999994),(1999995),(1999996),(1999997),(2007396),(2093476),(2093494),(2093495),
    (2116450),(2116451),(2116452),(2116453),(2124221),(2124222),(2124223),(2124224),(2124225),(2124226),(2124227),(2208662),
    (2208663),(2208664),(2208665),(2208666),(2225906),(2225907),(2225908),(2268294),(2371971),(2371973),(2371974),(2485048),
    (2551384),(2551385),(2551386),(2551387),(2551389),(2686423),(2686424),(2686425),(2686426),(2686428),(2724661),(2724662),
    (2724663),(2724664),(2724665),(2725421),(2738084),(2738085),(2738086),(2738087),(2738088),(2738089),(2762709),(2762710),
    (2762711),(2762712),(2762714),(2792571),(2792572),(2792573),(2792574),(2792575),(2793592),(2793593),(2793594),(2793595),
    (2795604),(2795605),(2795607),(2795608),(2795616),(2795617),(2795618),(2795619),(2795620),(2795621),(2795622),(2795624),
    (2820939),(2820940),(2820941),(2820942),(2820943),(2820944),(2855592),(2855593),(3009679),(3009680),(3009682),(3051698),
    (3051699),(3051700),(3051701),(3051702),(3051703),(3051704),(3051705),(3051706),(3051714),(3379472),(3411543),(3411544),
    (3431697),(3431698),(3431699),(3431700),(3445169),(3460268),(3460269),(3460270),(3460271),(3481541),(3481543),(3545582),
    (3545583),(3545584),(3545586),(3667935),(3667936),(3667938),(3740945),(3740946),(3754251),(3884462),(3939320),(3939321),
    (3939322),(3939323),(3966422),(3966423),(3966424),(4056413),(4056415),(4124014),(4138963),(4138964),(4138965),(4138974),
    (4138975),(4138976),(4138977),(4210149),(4210150),(4210151),(4246398),(4246399),(4246401),(4246402),(4246403),(4246404),
    (4246405),(4278161),(4278162),(4278163),(4341945),(4341946),(4352405),(4352406),(4352407),(4352408),(4352409),(4356794),
    (4356795),(4356796),(4356798),(4572260),(4572261),(4572262),(4624146),(4624147),(4624148),(4624149),(4651338),(4651339),
    (4651340),(4677949),(4678016),(4714957),(4714958),(5066416),(5066419),(5066420),(5148058),(5148059),(5148060),(5148061),
    (5148062),(5148063),(5148064),(5148065),(5148066),(5158603),(5158604),(5158605),(5158606),(5161659),(5161660),(5162177),
    (5162178),(5162180),(5162181),(5205753),(5205754),(5205755),(5215545),(5215546),(5215547),(5215548),(5215549),(5215594),
    (5215595),(5215596),(5215597),(5215598),(5215599),(5215600),(5215601),(5215602),(5215603),(5215614),(5215615),(5215616),
    (5223880),(5223881),(5223882),(5223883),(5256141),(5256142),(5312104),(5312105),(5423755),(5423756),(5423757),(5423758),
    (5423759),(5746361),(5746362),(5746363),(5746364),(5755342),(5755343),(5755344),(5755345),(5788231),(5788232),(5788233),
    (5788234),(5788236),(5788237),(5788238),(5788239),(5815495),(5815496),(5815497),(5815498),(5820531),(5837368),(5858268),
    (5858269),(5858270),(5862852),(5862853),(5862854),(5870575),(5928482),(6039363),(6039364),(6039366),(6039367),(6039369),
    (6039370),(6039385),(6039386),(6044590),(6044591),(6058175),(6078643),(6092186),(6092187),(6092691),(6317606),(6317607),
    (6328780),(6328781),(6328782),(6330407),(6330408),(6330409),(6364506),(6364509),(6364511),(6364512),(6398934),(6422995),
    (6422996),(6422997),(6422998),(6426035),(6434285),(6434286),(6446902),(6450015),(6450016),(6450017),(6450018),(6450019),
    (6450020),(6450021),(6450022),(6450023),(6450024),(6450025),(6450026),(6450027),(6450028),(6450029),(6450030),(6450031),
    (6450032),(6450033),(6450035),(6450036),(6450037),(6450038),(6450039),(6450040),(6450041),(6450042),(6450043),(6460909),
    (6501132),(6501133),(6501146),(6501147),(6501148),(6501149),(6585170),(6585171),(6585172),(6585175),(6585177),(6585178),
    (6595801),(6595802),(6595803),(6595806),(6604574),(6604575),(6604576),(6604577),(6604579),(6610397),(6610399),(6610400),
    (6610401),(6610402),(6616037),(6616038),(6622344),(6622345),(6622346),(6622347),(6623729),(6623730),(6632688),(6632689),
    (6658815),(6659258),(6659259),(6659261),(6660437),(6660438),(6660439),(6660440),(6660441),(6660442),(6660443),(6660444),
    (6661179),(6661180),(6661181),(6661182),(6661183),(6661184),(6661185),(6676495),(6676496),(6676497),(6676498),(6676499),
    (6701378),(6701379),(6701380),(6701382),(6701888),(6701889),(6701890),(6701891),(6702496),(6702497),(6702498),(6704381),
    (6704382),(6704383),(6704384),(6704385),(6704386),(6704387),(6704388),(6704389),(6704390),(6704391),(6704392),(6704393),
    (6704394),(6704396),(6704398),(6704399),(6704401),(6704402),(6704403),(6704404),(6704405),(6704406),(6704407),(6704408),
    (6704409),(6704410),(6704411),(6704412),(6704413),(6704431),(6704432),(6704433),(6704434),(6704435),(6704436),(6710481),
    (6710482),(6710483),(6710484),(6710485),(6719677),(6719678),(6719679),(6719680),(6719682),(6719684),(6719686),(6719693),
    (6719694),(6719695),(6719697),(6719771),(6719772),(6719773),(6719774),(6719775),(6719776),(6719777),(6719778),(6719779),
    (6719781),(6719782),(6719783),(6719784),(6719785),(6725058),(6725059),(6725060),(6725061),(6725062),(6725063),(6725064),
    (6725065),(6725066),(6725067),(6725069),(6727204),(6727205),(6727207),(6727209),(6727212),(6727215),(6736309),(6736310),
    (6736311),(6736312),(6736313),(6736314),(6736315),(6736316),(6736317),(6736318),(6736322),(6736323),(6736324),(6736325),
    (6736326),(6736327),(6736328),(6736329),(6736330),(6736331),(6748079),(6748080),(6748081),(6748082),(6748086),(6748087),
    (6748088),(6748089),(6754627),(6754628),(6754629),(6754630),(6761483),(6761484),(6761485),(6761486),(6761487),(6761488),
    (6761489),(6761490),(6761491),(6761492),(6761493),(6761495),(6761496),(6761497),(6761498),(6761500),(6761502),(6761504),
    (6761505),(6761506),(6761507),(6761508),(6761509),(6761512),(6761513),(6761514),(6761515),(6761516),(6761517),(6761518),
    (6761519),(6761548),(6761549),(6761550),(6761551),(6761552),(6761553),(6761703),(6761704),(6761705),(6761706),(6777735),
    (6787459),(6787460),(6787461),(6787462),(6790435),(6790438),(6790440),(6791720),(6796665),(6796667),(6796669),(6796671),
    (6796673),(6796674),(6796675),(6796676),(6796677),(6820598),(6820599),(6820601),(6820603),(6820604),(6842536),(6842537),
    (6842538),(6842539),(6842540),(6842541),(6842542),(6842543),(6842544),(6842545),(6842547),(6842549),(6842550),(6842551),
    (6842552),(6853550),(6853551),(6853552),(6853553),(6853554),(6853555),(6853556),(6853557),(6853558),(6853559),(6853560),
    (6853561),(6853562),(6887562),(6887563),(6887564),(6887565),(6887566),(6887567),(6887568),(6887569),(6908797),(6908799),
    (6908802),(6908803),(6908812),(6908813),(6908814),(6908815),(6917752),(6917753),(6917754),(6926245),(6926249),(6949330),
    (6949333),(6949335),(6955900),(6955901),(6955904),(6955905),(6955907)
    """

  # ★UID 자동 동기화(2026-07-31): 위 목록은 손으로 관리하던 것이라 신규 발매 uid 가 빠지면
  #   대시보드 A열을 채워도 데이터가 안 들어왔다(실적 조용히 누락). hero_bot daily 가
  #   대시보드 A열 uid 전량을 이 파일의 '_UID_FILTER' 탭에 매일 기록한다.
  #   ★합집합으로만 더한다 — 탭이 비거나 깨져도 기존 목록이 줄지 않는다.
  try:
      _extra = {int(str(_r[0]).strip()) for _r in _book.worksheet("_UID_FILTER").get_all_values()[1:]
                if _r and str(_r[0]).strip().isdigit()}
      _base  = {int(_x) for _x in __import__("re").findall(r"\((\d+)\)", GOODS_FILTER)}
      _all   = sorted(_base | _extra)
      GOODS_FILTER = ",".join(f"({_u})" for _u in _all)
      print(f"GOODS_FILTER: 기존 {len(_base)} + _UID_FILTER {len(_extra)} → {len(_all)}건")
  except Exception as _e:
      print("_UID_FILTER 읽기 실패 — 하드코딩 목록 그대로 사용:", _e)

  print("준비 완료. date =", date)


# COMMAND ----------

  # ── 실적: 26SS와 동일하게 사전집계 테이블(f_mutandard_hero_goods_daily) 기반으로 통일 (2026-07-22) ──
  #   기존 원천 직접계산 방식을 데이터팀 히어로 테이블 읽기로 교체. 오프라인 매장로직도 테이블 정의를 따름.
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
  ouput AS (
      SELECT
          channel, om.goods_no, brand,
          ANY_VALUE(team) team, ANY_VALUE(gender_line) gender_line,
          ANY_VALUE(category1) category1, ANY_VALUE(category2) category2,
          ANY_VALUE(md_name) md_name, ANY_VALUE(release_season) release_season,
          ANY_VALUE(sell_season) sell_season, ANY_VALUE(style_no) style_no,
          SUM(tag_gmv) tag_gmv, SUM(gmv) gmv, SUM(qty) qty,
          SUM(total_discount) total_discount, SUM(revenue) revenue,
          SUM(gross_take) gross_take, SUM(net_take) net_take,
          goods_opt
      FROM team.`brand-strategy`.f_mutandard_hero_goods_daily om
      JOIN date_range dr ON date_format(om.date,'yyyyMMdd') = dr.dt
      JOIN goods_filter gf ON om.goods_no = gf.goods_no
      GROUP BY channel, om.goods_no, goods_opt, brand, team, gender_line,
               category1, category2, md_name, release_season, sell_season, style_no
  )
  SELECT * FROM ouput ORDER BY channel, brand, goods_no, goods_opt LIMIT 200000
  """
      insert_query_result(f_name, spark.sql(query), label=f"매출 {f_name} {start_date}~{end_date}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### ▼ 재고 데이터 추출 (일 1회)
# MAGIC - '잔여재고'

# COMMAND ----------

  stock_query = f"""
  WITH goods_filter AS (SELECT goods_no FROM (VALUES {GOODS_FILTER}) AS t(goods_no)),
  meta AS (
    SELECT DISTINCT goods_no, style_no, team FROM gspread.musinsastandard.mutandard_goods_meta_v2
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

# MAGIC %md
# MAGIC ### ▼ 입고현황 데이터 추출 (일 1회)
# MAGIC - '입고현황'

# COMMAND ----------

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
      AND a.ACT_DATE >= '20260601' AND a.ACT_DATE <= DATE_FORMAT(DATE_SUB(CURRENT_DATE(), 1), 'yyyyMMdd')
  )
  SELECT plant_nm, brand_nm, team, goods_no, style_no,
         SUM(inbound_qty) AS inbound_qty, SUM(inbound_qty * CAST(normal_price AS BIGINT)) AS normal_price_amt,
         SUM(CAST(inbound_qty AS BIGINT) * CAST(wonga AS BIGINT)) AS wonga_amt, barcode
  FROM inbound GROUP BY plant_nm, brand_nm, team, goods_no, style_no, barcode
  ORDER BY plant_nm, brand_nm, team, goods_no, style_no, barcode
  """
  insert_query_result("입고현황", spark.sql(inbound_query), label="26년 6월 1일부터 전일자 누적 입고")