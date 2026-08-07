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
  FILE_URL = "https://docs.google.com/spreadsheets/d/1iHH2qG8Uj5vmlC3aXkey96usktWODmguDPD_ToT2rfA/edit"  # 히어로 실적 (자동)

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

  # 26FW 누적 = 7/1 고정 시작(시즌 시작). 달력 YTD로 26FW를 보면 캐리오버 STY의 1~6월 판매가
  # 섞여 실제 FW 판매를 못 본다(사용자 확정 2026-07-28). 전년 동기간은 2025-07-01~.
  fw_start     = f"{d.year}0701"
  fw_start_yoy = f"{d.year - 1}0701"

  # 직전주(WEEK의 한 주 전) — 성과뷰 '거래액 전주비'를 표시값과 같은 기준(실적 누판)으로 내기 위함.
  # 기존엔 PMKT 직접경로 주간으로 전주비를 냈는데, 화면 숫자는 누판이라 기준이 어긋났다.
  prevweek_start = (d - timedelta(days=13)).strftime("%Y%m%d")
  prevweek_end   = (d - timedelta(days=7)).strftime("%Y%m%d")

  # ★리스트 '맨 뒤'에만 추가할 것 — 아래 퍼널(_fp)·PMKT(_pmkt_periods)가 인덱스 0/2/4/6을 참조한다.
  params = {
      "start_dt": ["20260101","20250101", mtd_start, mtd_start_yoy, week_start, week_start_yoy, day_start, day_start_yoy,
                   fw_start, fw_start_yoy, prevweek_start],
      "end_dt":   [ytd_end, ytd_end_yoy, mtd_end, mtd_end_yoy, week_end, week_end_yoy, day_end, day_end_yoy,
                   ytd_end, ytd_end_yoy, prevweek_end],
      "file_nm":  ["YTD","전년YTD","MTD","전년MTD","WEEK","전년WEEK","DAY","전년DAY",
                   "FWTD","전년FWTD","직전WEEK"],
  }

  GOODS_FILTER = """
    (2447804),(2447806),(3051684),(3051685),(2447805),(3051687),(4642899),(3740942),(3134736),(3134737),(3134738),(4664527),
    (4664529),(4664530),(4664535),(4664536),(4664537),(5107708),(5107709),(5107710),(4682241),(4682243),(4682244),(4682245),
    (5662151),(5662152),(5662153),(5662154),(5662155),(5662157),(4682246),(4682247),(4682248),(4682249),(4682251),(4682252),
    (4682253),(4682254),(4682255),(4682250),(5884071),(5884072),(6104962),(5662191),(5662192),(5662193),(5662195),(5662196),
    (4682256),(4682257),(4682258),(4682259),(4682260),(4682261),(4682262),(4682263),(5466457),(5466459),(5466458),(5466455),
    (5466456),(5466466),(5466467),(5466468),(5466465),(5466463),(5466460),(5466462),(5466464),(5466529),(5466530),(5466531),
    (5466528),(5466527),(5466525),(6092186),(5755342),(5755343),(5755344),(5755345),(6092187),(5862852),(5862853),(5862854),
    (6078643),(5815495),(5815497),(5815498),(5815496),(5287052),(5287054),(5892071),(5166593),(5166591),(5166592),(6092188),
    (5795981),(5795982),(3793565),(3793566),(3793567),(3793568),(3793569),(4652839),(4652840),(5990760),(5990761),(5990762),
    (6092190),(5671197),(5671198),(5671199),(5671201),(5671202),(3758212),(3758213),(3758214),(3758215),(3758216),(3758217),
    (3758218),(3758219),(3758220),(3758221),(4652859),(4652860),(4652861),(4652862),(4652863),(4652864),(4652865),(4652866),
    (3790850),(3790851),(3790852),(3790853),(3790854),(4651730),(4651731),(4651732),(5671203),(5671204),(3758500),(3758501),
    (3758502),(4731740),(5812740),(5812741),(5812728),(5812729),(5812730),(5812731),(6104961),(5755302),(5755303),(5755304),
    (5755305),(6127413),(5915564),(5915566),(5915565),(5915567),(6092691),(5788233),(5788234),(5788236),(5788237),(5788238),
    (5788239),(5837368),(3966422),(3966423),(3966424),(6044591),(6044590),(3966425),(3996426),(3966428),(5788232),(5788231),
    (6058175),(6104960),(5888129),(5888131),(5888133),(5888134),(5888135),(5888136),(5888137),(5888139),(6121801),(5695800),
    (5695801),(5695802),(5695803),(5695804),(5795909),(5795910),(5795911),(5795912),(5795913),(996178),(1431741),(1117534),
    (1431733),(1117543),(1117542),(1431732),(996177),(2309127),(2309123),(1117536),(1431735),(1117539),(2309122),(1117546),
    (1457571),(1117533),(996184),(996187),(1117545),(1117544),(1431743),(996179),(1431734),(996189),(1431736),(996180),
    (1431742),(1446336),(1117541),(1117538),(1431737),(1117532),(1431744),(2309120),(2309121),(2309124),(2309125),(2309126),
    (2309128),(3651606),(2405728),(2405729),(2405769),(2405733),(2405738),(2405730),(2405732),(2405735),(2405737),(2405739),
    (2460195),(3034284),(1417692),(1424102),(1424103),(1424100),(1417691),(2374614),(2374611),(2374612),(2374619),(3153788),
    (3822236),(3822237),(1417693),(1417699),(1417697),(1417694),(1417695),(1417701),(1417696),(1841260),(1841251),(2374617),
    (2374613),(2374615),(2374618),(3153786),(3153787),(3153789),(3822239),(3753589),(3822241),(3822240),(4651365),(4651363),
    (4651364),(4651366),(1388775),(1388776),(1388777),(1388780),(1388782),(1388781),(2285960),(2976529),(2976528),(2976525),
    (1388779),(1388785),(1388778),(1388789),(1388790),(1388786),(1388792),(1388784),(1388783),(1944361),(1920863),(2285959),
    (2285961),(2285962),(2285963),(2976526),(2976527),(3651607),(2035287),(5163284),(6121803),(5795961),(5795962),(5795963),
    (5795964),(5795935),(1932037),(1932038),(1932039),(1932041),(1932046),(1932040),(1932044),(2321889),(2321890),(2341397),
    (2321891),(3059011),(3059012),(1932042),(2321888),(5915539),(5915542),(5915540),(5915541),(2341450),(2341451),(2341452),
    (2341453),(2341454),(2341456),(2341459),(2341460),(2341461),(2341462),(2976530),(2976533),(2976534),(2341455),(2341457),
    (2341458),(2976531),(2976532),(3034275),(3034276),(3034277),(3034278),(3034279),(3034280),(3034281),(3727887),(3727888),
    (4570534),(5915543),(5915547),(5915546),(5915545),(5915544),(2976549),(3052664),(5949864),(5949865),(5949866),(5949867),
    (3051693),(3051694),(3051695),(3051696),(3051697),(3740943),(4642900),(4642901),(5860707),(5860705),(5860706),(5860704),
    (5824390),(5824389),(5795869),(6450149),(4655088),(4655089),(4655090),(4655091),(4655092),(4655093),(4644755),(4644757),
    (4644756),(4644753),(4644754),(4644758),(4911480),(4655069),(4655070),(4655071),(4655072),(4655073),(4651422),(4651423),
    (4651424),(4651425),(4644815),(4644816),(4644817),(5795893),(5795894),(5795895),(5795896),(4664549),(4664552),(4664551),
    (5750428),(5750429),(5750430),(4057917),(4057918),(4057919),(4655178),(4655179),(4655180),(4655181),(4655182),(4767951),
    (4655118),(4655119),(4655120),(4655121),(4655122),(4655124),(4655125),(4655123),(4655126),(4655127),(5951630),(5951631),
    (5951632),(5951634),(5951636),(5951638),(3135345),(3135346),(3135347),(3822255),(3822254),(4651351),(4651352),(3822256),
    (3822257),(4651353),(5795965),(5795966),(5795967),(5795968),(5795969),(5812721),(5812722),(1273485),(1239618),(1970119),
    (2793592),(3667935),(1224096),(1239615),(1246244),(1669798),(1149329),(1669799),(1669800),(1224094),(1239614),(1449354),
    (2793593),(1239619),(1326565),(1224095),(1970117),(1254403),(1239617),(1669797),(1246487),(1224097),(2855593),(3667938),
    (1271471),(1669809),(1970106),(1168922),(1224098),(1970105),(1464934),(2855592),(1677686),(1970104),(1669806),(1970103),
    (1669808),(1222182),(1999996),(1168906),(1675530),(1222183),(1249103),(1452671),(1675529),(1249104),(1675531),(1303071),
    (1999993),(1999997),(1222184),(1999992),(1249102),(1999994),(2518487),(1989228),(2371969),(2957579),(2518486),(1990788),
    (1990787),(2371968),(2371970),(3054926),(1436504),(2456625),(1447705),(2371966),(1447706),(2371965),(3667931),(2371967),
    (2957578),(3054925),(1220731),(1640887),(3545586),(3545584),(3545583),(3545582),(1225000),(1243054),(3445169),(5928482),
    (5256142),(2738084),(2738085),(2738088),(2738087),(2738089),(4678016),(2738086),(1945857),(1945867),(1945839),(1945859),
    (1945849),(1945863),(1945869),(1945868),(1945844),(1945856),(1945848),(1945843),(1945841),(1945846),(5423758),(1945847),
    (5423755),(1945866),(1945862),(1945861),(1945858),(1945854),(1945860),(1945845),(1945855),(1945840),(1945851),(2485048),
    (1945853),(1945865),(1945852),(1945864),(1945850),(5423756),(5423759),(5423757),(1945838),(1945842),(4341946),(4341945),
    (5858269),(5858270),(2225907),(2225906),(3009682),(3009679),(3009680),(4246398),(4246399),(4246401),(4246402),(4246403),
    (4246404),(4246405),(2820939),(2820940),(2820941),(2820942),(2820943),(2820944),(4651338),(4651339),(4651340),(947057),
    (947058),(947061),(947060),(947059),(1801900),(1801896),(1801897),(1801898),(2795616),(2795617),(2795618),(2795620),
    (2795621),(2795624),(2795619),(2795622),(2795623),(1324127),(1324128),(1324130),(1324129),(1815000),(1805124),(1805121),
    (1805123),(1805122),(2692692),(2692693),(2692694),(2692695),(3753593),(3753594),(4651430),(4651431),(4651432),(4651433),
    (2405697),(2405698),(2405700),(2405702),(2405704),(2656894),(2656897),(2656902),(2656903),(2656901),(2656900),(4570541),
    (4570542),(4570543),(5837997),(5838000),(5838001),(5837999),(5837998),(1666443),(1720144),(1666442),(1666441),(1666444),
    (2208662),(2208663),(2208664),(3051714),(3740945),(4056413),(4572260),(4714957),(4714958),(3051698),(3051699),(3051700),
    (3051701),(3051702),(3051703),(3051704),(3051705),(3051706),(3740946),(4572262),(2028326),(2028327),(2028328),(2028329),
    (2391748),(2391744),(2391746),(2391745),(3051713),(3740944),(4056412),(4572259),(1357769),(1357770),(1357771),(1357768),
    (2208256),(2725426),(2957576),(2725425),(4642930),(4642932),(4642929),(2124425),(2124426),(2124427),(2124428),(2304246),
    (2303284),(2725427),(2725428),(2505949),(2725429),(4642928),(4642926),(2668360),(2668361),(2668362),(4467451),(5824388),
    (6277097),(6277096),(991339),(991340),(991341),(1249105),(1986029),(1986030),(1986031),(1999995),(2034137),(2149254),
    (2225908),(2306176),(2306177),(2306178),(2724650),(2781308),(2793594),(2793595),(2978590),(2978591),(2978592),(3134739),
    (3134740),(3134741),(3667936),(3966426),(3966429),(3966430),(3966431),(4651407),(4677949),(4721247),(4721248),(4724733),
    (5066416),(5066419),(5066420),(5066422),(5066423),(5066424),(5256141),
    -- 26FW 신규 히어로 goods (2026-07-09 추가; 상품MAP HERO STY×SKU uid)
    -- 웜 팬츠 (42)
    (1670229),(1670230),(1670231),(1670232),(1670233),(1670234),(2116450),(2116451),(2116452),(2116453),
    (2124221),(2124222),(2124223),(2124224),(2124225),(2124226),(2124227),(3379472),(3411543),(3411544),
    (3460268),(3460269),(3460270),(3460271),(3481541),(3481543),(4124014),(4138974),(4138975),(4138976),
    (4138977),(4210149),(4210150),(4210151),(4278161),(4278162),(4278163),(5215545),(5215546),(5215547),
    (5215548),(5215549),
    -- 슬랙스 (37)
    (1314973),(1595986),(1595987),(1596414),(1851966),(1851967),(1851968),(2093476),(2093494),(2093495),
    (2371971),(2371973),(2371974),(2551384),(2551385),(2551386),(2551387),(2551389),(2792571),(2792572),
    (2792573),(2792574),(2792575),(4352405),(4352406),(4352407),(4352408),(4352409),(4624146),(4624147),
    (4624148),(4624149),(5746361),(5746362),(5746363),(5746364),(5820531),
    -- 스웨트팬츠 (21)
    (2686423),(2686424),(2686425),(2686426),(2686427),(2686428),(2762709),(2762710),(2762711),(2762712),
    (2762714),(3431697),(3431698),(3431699),(3431700),(3939320),(3939321),(3939322),(3939323),(5161659),
    (5161660),
    -- 라이트다운 (21)
    (4356794),(4356795),(4356796),(4356798),(5148058),(5148059),(5148060),(5148061),(5148062),(5148063),
    (5148064),(5148065),(5148066),(5158603),(5158604),(5158605),(5158606),(5162177),(5162178),(5162180),
    (5162181),
    -- 빅토리아 울 (17)
    (5215594),(5215595),(5215596),(5215597),(5215598),(5215599),(5215600),(5215601),(5215602),(5215603),
    (5215614),(5215615),(5215616),(5223880),(5223881),(5223882),(5223883),
    -- 에센셜 플리스 (15)
    (2724661),(2724662),(2724663),(2724664),(2724665),(2795604),(2795605),(2795607),(2795608),(4138963),
    (4138964),(4138965),(5205753),(5205754),(5205755),
    -- 데님팬츠 (12)
    (1322219),(1322220),(1322221),(1322222),(2007396),(2208665),(2208666),(2268294),(2725421),(3884462),
    (4056415),(4572261),
    -- 양말 (3)
    (5224668),(5224748),(5870575),
    -- 그리드/메시 플리스 (2)
    (5312104),(5312105),

    -- ★26FW 히어로 goods 보강 (320, 2026-07-27): 상품MAP HERO STY x SKU/무탠 매핑(hero_goods_26fw.json)
    --   대비 필터에 없던 uid. 미보강 시 8월 26FW 발매부터 실적·PMKT·PDP가 통째로 과소집계.
    -- 빅토리아 울 [73]
    (6585170),(6585171),(6585172),(6585175),(6585177),(6585178),(6623729),(6623730),(6660437),(6660438),
    (6660439),(6660440),(6660441),(6660442),(6660443),(6660444),(6704381),(6704382),(6704383),(6704384),
    (6704385),(6704386),(6704387),(6704388),(6704389),(6704390),(6704391),(6704392),(6704393),(6704394),
    (6704396),(6704398),(6704399),(6704401),(6704402),(6704403),(6704404),(6704405),(6704406),(6704407),
    (6704408),(6704409),(6704410),(6704411),(6704412),(6704413),(6704431),(6704432),(6704433),(6704434),
    (6704435),(6704436),(6736309),(6736310),(6736311),(6736312),(6736313),(6736314),(6736315),(6736316),
    (6736317),(6736318),(6736322),(6736323),(6736324),(6736325),(6736326),(6736327),(6736328),(6736329),
    (6736330),(6736331),(6777735),
    -- 그리드/메시 플리스 [45]
    (5312106),(6364506),(6364509),(6364511),(6364512),(6622344),(6622345),(6622346),(6622347),(6658815),
    (6661179),(6661180),(6661181),(6661182),(6661183),(6661184),(6661185),(6701888),(6701889),(6701890),
    (6701891),(6702496),(6702497),(6702498),(6719677),(6719678),(6719679),(6719680),(6719682),(6719684),
    (6719686),(6719774),(6719776),(6719777),(6719778),(6719779),(6719781),(6719782),(6719783),(6719784),
    (6719785),(6748086),(6748087),(6748088),(6748089),
    -- 커브드팬츠 [42]
    (6328780),(6328781),(6328782),(6422995),(6422996),(6422997),(6422998),(6450036),(6450037),(6450038),
    (6450039),(6450040),(6501132),(6501133),(6501146),(6501147),(6501148),(6501149),(6604574),(6604575),
    (6604576),(6604577),(6604579),(6632688),(6632689),(6659258),(6659259),(6659261),(6701378),(6701379),
    (6701380),(6701382),(6754627),(6754628),(6754629),(6754630),(6820598),(6820599),(6820601),(6820603),
    (6820604),(6917750),
    -- 힛탠다드 [37]
    (6761483),(6761484),(6761485),(6761486),(6761487),(6761488),(6761489),(6761490),(6761491),(6761492),
    (6761493),(6761495),(6761496),(6761497),(6761498),(6761500),(6761502),(6761504),(6761505),(6761506),
    (6761507),(6761508),(6761509),(6761512),(6761513),(6761514),(6761515),(6761516),(6761517),(6761518),
    (6761519),(6761548),(6761549),(6761550),(6761551),(6761552),(6761553),
    -- 에센셜 플리스 [33]
    (2795606),(2795609),(6595801),(6595802),(6595803),(6595806),(6725058),(6725059),(6725060),(6725061),
    (6725062),(6725063),(6725064),(6725065),(6725066),(6725067),(6725069),(6727204),(6727205),(6727207),
    (6727209),(6727212),(6727215),(6748079),(6748080),(6748081),(6748082),(6761703),(6761704),(6761705),
    (6761706),(6917753),(6917754),
    -- 라이트다운 [22]
    (6039363),(6039364),(6039366),(6039367),(6039369),(6039370),(6039385),(6039386),(6398934),(6434285),
    (6434286),(6446902),(6796665),(6796667),(6796669),(6796671),(6796673),(6796674),(6796675),(6796676),
    (6796677),(6917752),
    -- 데님팬츠 [22]
    (3754251),(6450015),(6450016),(6450017),(6450018),(6450019),(6450020),(6450021),(6450022),(6450023),
    (6450024),(6450025),(6450026),(6450027),(6450028),(6450029),(6450030),(6450031),(6450032),(6450033),
    (6450035),(6460909),
    -- 웜 팬츠 [14]
    (6330409),(6610397),(6610399),(6610400),(6610401),(6610402),(6719693),(6719694),(6719695),(6719697),
    (6719771),(6719772),(6719773),(6719775),
    -- 스웨트팬츠 [14]
    (2762713),(2820945),(3753612),(6317606),(6317607),(6330407),(6330408),(6616037),(6616038),(6710481),
    (6710482),(6710483),(6710484),(6710485),
    -- 헤비다운 [8]
    (6787459),(6787460),(6787461),(6787462),(6790435),(6790438),(6790440),(6791720),
    -- 양말 [7]
    (5858268),(6426035),(6676495),(6676496),(6676497),(6676498),(6676499),
    -- 슬랙스 [2]
    (2371972),(2551388),
    -- 벨트 [1]
    (3765292)
"""

  # ★GOODS_FILTER 자동화(2026-07-31) — 위 목록은 이제 **폴백**이다.
  #   수기 목록이라 MSTRD에 새 uid(신규 컬러/스타일)가 생기면 그 상품 매출이 통째로 빠졌다
  #   (라이트다운 uid 2개 = 1,678,800원 과소집계, 26FW 60 uid 누락 — 2026-07-31 확인).
  #   → 앱 CI가 매일 아침 `_히어로UID` 탭(uid/hero/season)을 갱신하고, 여기서 그걸 읽어 필터를 만든다.
  #   탭이 없거나 비정상적으로 적으면(500 미만) 폴백 목록을 그대로 쓴다.
  try:
      _uid_ws = _book.worksheet("_히어로UID")
      _uid_rows = _uid_ws.get_all_values()[2:]          # 1행 라벨 · 2행 헤더
      _uids = sorted({r[0].strip() for r in _uid_rows if r and r[0].strip().isdigit()}, key=int)
      # ★히어로 맵(uid→hero/season) — 유입경로를 goods가 아닌 '히어로' 단위로 집계하려고 함께 읽는다
      #   (goods x 경로 x 83주 = 40만행 초과라 시트에 못 싣는다. 히어로 단위면 2~3만행).
      #   같은 uid가 두 시즌에 걸리면(캐리오버) 두 행 다 유지 = 시즌 레인 각각에 계상.
      _HERO_MAP = sorted({(r[0].strip(), r[1].strip(), r[2].strip()) for r in _uid_rows
                          if len(r) >= 3 and r[0].strip().isdigit() and r[1].strip() and r[2].strip()})
  except Exception as _euid:
      _uids, _HERO_MAP = [], []
      print("[주의] '_히어로UID' 탭 읽기 실패 — 폴백 목록 사용:", type(_euid).__name__, _euid)
  if len(_uids) >= 500:
      GOODS_FILTER = ",".join("(%s)" % u for u in _uids)
      print("GOODS_FILTER = _히어로UID 탭 %d개(자동)" % len(_uids))
  else:
      print("GOODS_FILTER = 폴백 목록 사용(탭 uid %d개)" % len(_uids))

  print("준비 완료. date =", date)


# COMMAND ----------

  # ── 공통 빌딩블록을 1회만 계산+캐시 (8기간 루프가 매번 재계산하던 것 → 8x를 1x로) ──
  spark.sql(f"CREATE OR REPLACE TEMP VIEW v_goods_filter AS SELECT DISTINCT goods_no FROM (VALUES {GOODS_FILTER}) AS t(goods_no)")
  # ★히어로 맵 뷰(2026-08-01) — 경로 주차/상세를 히어로 단위로 내리는 조인 키.
  _HM_VALUES = ",".join("('%s','%s','%s')" % (u, h.replace("'", "''"), s) for u, h, s in (_HERO_MAP or []))
  if _HM_VALUES:
      spark.sql(f"CREATE OR REPLACE TEMP VIEW v_hero_map AS SELECT * FROM (VALUES {_HM_VALUES}) AS t(goods_no, hero, season)")
      spark.sql("CACHE TABLE v_hero_map")
      print("v_hero_map = %d행(uid x 시즌)" % len(_HERO_MAP))
  else:
      print("[주의] 히어로 맵이 비었다 — PMKT경로주차/경로상세 셀은 시트를 건드리지 않고 스킵된다")
  spark.sql("CREATE OR REPLACE TEMP VIEW v_goods_base AS SELECT g.goods_no, g.wonga, g.normal_price FROM datamart.datamart.goods g JOIN v_goods_filter gf ON g.goods_no = gf.goods_no")
  spark.sql("""CREATE OR REPLACE TEMP VIEW v_meta AS
    SELECT goods_no, team, goods_gender_cd AS gender_line, category_nm_1depth AS category1, category_nm_2depth AS category2,
           md_nm AS md_name, release_season_type AS release_season, season AS sell_season, style_no
    FROM (SELECT goods_no, team, goods_gender_cd, category_nm_1depth, category_nm_2depth, md_nm, release_season_type, season, style_no,
                 ROW_NUMBER() OVER (PARTITION BY goods_no ORDER BY md_nm, team) rn
          FROM gspread.musinsastandard.mutandard_goods_meta_v2 WHERE goods_no IS NOT NULL) x WHERE rn = 1""")
  spark.sql("CREATE OR REPLACE TEMP VIEW v_shop_list AS SELECT DISTINCT shop_no FROM musinsa.order_group.shop WHERE LOWER(shop_type) IN ('offline','selectshop') OR shop_no=68")
  spark.sql("CREATE OR REPLACE TEMP VIEW v_pos_fee AS SELECT sales_key, MAX(fee_amount) fee_amount FROM musinsa.order_group.pos_settlement_item GROUP BY sales_key")
  for _v in ["v_goods_filter", "v_goods_base", "v_meta", "v_shop_list", "v_pos_fee"]:
      spark.sql(f"CACHE TABLE {_v}")
  print("공통 뷰 캐시 완료 (goods_filter/goods_base/meta/shop_list/pos_fee)")

  for start_date, end_date, f_name in zip(params["start_dt"], params["end_dt"], params["file_nm"]):
      query = f"""
  WITH date_range AS (
    SELECT DISTINCT cal.dt, TO_DATE(cal.dt,'yyyyMMdd') AS date
    FROM datamart.datamart.calendar cal
    WHERE cal.dt BETWEEN '{start_date}' AND '{end_date}'
  ),
  online_base AS (
    SELECT om.goods_no, om.goods_opt, LOWER(om.brand) brand, om.normal_price,
           om.sell_sub_clm_qty, om.sell_sub_clm_amt, om.head_wonga, om.partner_sale_fee,
           om.recv_amt, om.gmv_state, om.ord_com_type
    FROM datamart.datamart.orders_merged om
    JOIN date_range dr ON om.ord_state_date = dr.dt
    JOIN v_goods_filter gf ON om.goods_no = gf.goods_no
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
    FROM online_base ob LEFT JOIN v_meta m ON ob.goods_no = m.goods_no
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
  offline_base AS (
    SELECT pos.goods_no, pos.goods_opt, LOWER(pos.brand_id) brand, pos.sales_type, pos.normal_price,
           pos.raw_price, pos.sales_price, pos.pay_amount, IFNULL(pf.fee_amount, 0) fee_amount, pos.qty,
           pos.coupon_partner_amount, pos.cart_discount_partner_amount, pos.order_sheet_promotion_brand,
           IF(pos.sales_type='SALE',1,-1) np, IF(pos.product_type='100','3P','1P') com_type
    FROM musinsa.order_group.pos_order_sales pos
    JOIN date_range dr ON DATE(pos.sales_date)=dr.date
    JOIN v_goods_filter gf ON pos.goods_no = gf.goods_no
    JOIN v_shop_list sl ON pos.shop_no = sl.shop_no
    LEFT JOIN v_pos_fee pf ON pos.sales_key = pf.sales_key
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
    FROM offline_base ob LEFT JOIN v_goods_base gb ON ob.goods_no = gb.goods_no LEFT JOIN v_meta m ON ob.goods_no = m.goods_no
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

# MAGIC %md
# MAGIC ### ▼ 입고일자별 (WMS 실입고, 일자×품번-컬러)
# MAGIC - '입고일자별' — 입고 보드 실적 소스 (BARCODE 파싱 품번-컬러 단위)

# COMMAND ----------

  inbound_daily_query = """
  WITH gm AS (
    SELECT style_no, brand_nm FROM (
      SELECT style_no, brand_nm,
             ROW_NUMBER() OVER (PARTITION BY style_no ORDER BY style_no DESC) rn
      FROM datamart.datamart.goods WHERE com_id NOT IN ('musinsa_used','musinsa_event','musinsa')
    ) WHERE rn = 1
  )
  SELECT a.ACT_DATE AS dt,
         a.STL_NO AS sku_code,              -- STL_NO = 품번-컬러 (보드 SKU 키와 동일)
         MAX(gm.brand_nm) AS brd_nm,
         SUM(CAST(a.ACT_QTY AS BIGINT)) AS inbound_qty
  FROM pbo.moms.ui_grreport_detail a
  JOIN gm ON a.STL_NO = gm.style_no          -- goods.style_no 도 품번-컬러 (기존 입고현황과 동일 조인)
  WHERE a.ORD_STATUS NOT IN ('출고취소','입고취소','입고대기')
    AND a.ORD_TYPE = '일반' AND a.SPR_NM = 'MUSINSA'
    AND gm.brand_nm IN ('무신사 스탠다드 우먼','무신사 스탠다드','무신사 스탠다드 홈',
                        '무신사 스탠다드 키즈','무신사 스탠다드 뷰티','무신사 스탠다드 스포츠','무신사스탠다드 지비지에이치')
    AND a.ACT_DATE >= '20251101'
    AND a.ACT_DATE <= DATE_FORMAT(DATE_SUB(CURRENT_DATE(), 1), 'yyyyMMdd')
  GROUP BY a.ACT_DATE, a.STL_NO
  ORDER BY dt, sku_code
  """
  insert_query_result("입고일자별", spark.sql(inbound_daily_query), label="25.11.01~전일 일자별 실입고 (BARCODE 파싱, 품번-컬러 단위)")

# COMMAND ----------

  # -- PDP 유입 -> 구매전환 퍼널 (team.sales.pdp_path_daily_summary_v) --
  # path_type='direct' 하나만 (direct/indirect는 같은 UV를 2가지로 분해한 복제 -> 둘 다 더하면 2배). 전환율=purchase_uv/pdp_uv.
  # ★FWTD(26FW 시즌 누계 7/1~) 추가 — 26FW 대시보드 누계가 FWTD인데 퍼널에 FWTD가 없어 유입·전환을 껐었다.
  #   인덱스 8 = params의 FWTD 슬롯(리스트 맨 뒤 추가 규칙 준수).
  _fp = [("YTD", params["start_dt"][0], params["end_dt"][0]), ("MTD", params["start_dt"][2], params["end_dt"][2]),
         ("WEEK", params["start_dt"][4], params["end_dt"][4]), ("DAY", params["start_dt"][6], params["end_dt"][6]),
         ("FWTD", params["start_dt"][8], params["end_dt"][8])]
  _fu = " UNION ALL ".join(f"""
    SELECT '{nm}' AS period, CAST(p.goods_no AS STRING) AS goods_no, m.style_no AS style_no,
           SUM(p.pdp_uv_cnt) AS pdp_uv, SUM(p.purchase_uv_cnt) AS purchase_uv, SUM(p.qty) AS qty, SUM(p.gmv) AS gmv
    FROM team.sales.pdp_path_daily_summary_v p
    JOIN v_goods_filter gf ON CAST(p.goods_no AS STRING) = CAST(gf.goods_no AS STRING)
    LEFT JOIN v_meta m ON CAST(p.goods_no AS STRING) = CAST(m.goods_no AS STRING)
    WHERE p.dt BETWEEN '{s}' AND '{e}' AND p.path_type = 'direct'
      AND LOWER(p.brand) IN ('musinsastandard','musinsastandardwoman','musinsastandardkids','musinsastandardhome')
      AND LOWER(p.com_id) NOT IN ('musinsa','musinsa_event')
    GROUP BY CAST(p.goods_no AS STRING), m.style_no""" for nm, s, e in _fp)
  insert_query_result("PDP퍼널", spark.sql(_fu), label="PDP funnel(direct) ~" + date)

# COMMAND ----------

# -- IMC 성과(PMKT): goods x 기간(YTD/MTD/WEEK) 스냅샷 + 주차 추세 (team.sales.pdp_path_daily_summary_v) --
# 생성기(_gen_26fw_heroes.py)가 'PMKT기간'(기간별 거래액/PDP/전환/마케팅기여)·'PMKT주차'(주차 추세)를 읽어 IMC 성과뷰를 만든다.
# path_type='direct'만 (direct/indirect는 같은 UV 복제분해). 전환율=buy_uv/pdp_uv(UV). 마케팅기여=prev_path1 in (캠페인/기획전,외부 유입).
_PMKT_FROM = f"""
  FROM team.sales.pdp_path_daily_summary_v p
  JOIN v_goods_filter gf ON CAST(p.goods_no AS STRING) = CAST(gf.goods_no AS STRING)
  LEFT JOIN v_meta m ON CAST(p.goods_no AS STRING) = CAST(m.goods_no AS STRING)
  WHERE p.path_type = 'direct'
    AND LOWER(p.brand) IN ('musinsastandard','musinsastandardwoman','musinsastandardkids','musinsastandardhome')
    AND LOWER(p.com_id) NOT IN ('musinsa','musinsa_event')
"""

# (1) 기간 스냅샷 — YTD/MTD/WEEK x goods + 마케팅기여(조건합산) + 전년 동기간(YoY: *_ly)
# ★FWTD(26FW 시즌 누계 7/1~) 추가(2026-07-31) — IMC 성과의 26FW 히어로 '누계'가 실적(FWTD)과 같은
#   기간이 되도록. 없을 땐 26FW 누계 유입이 달력 YTD(1/1~)라 캐리오버의 봄 유입이 섞였다.
#   인덱스 8/9 = params의 FWTD·전년FWTD 슬롯. PMKT기간·PMKT경로기간이 함께 쓴다.
_pmkt_periods = [("YTD", params["start_dt"][0], params["end_dt"][0], params["start_dt"][1], params["end_dt"][1]),
                 ("MTD", params["start_dt"][2], params["end_dt"][2], params["start_dt"][3], params["end_dt"][3]),
                 ("WEEK", params["start_dt"][4], params["end_dt"][4], params["start_dt"][5], params["end_dt"][5]),
                 ("FWTD", params["start_dt"][8], params["end_dt"][8], params["start_dt"][9], params["end_dt"][9])]
_pmkt_union = " UNION ALL ".join(f"""
  SELECT '{nm}' AS period, CAST(p.goods_no AS STRING) AS goods_no, ANY_VALUE(m.style_no) AS style_no,
         SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' THEN p.pdp_uv_cnt ELSE 0 END) AS pdp_uv,
         SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' THEN p.purchase_uv_cnt ELSE 0 END) AS buy_uv,
         SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' THEN p.qty ELSE 0 END) AS qty,
         SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' THEN p.gmv ELSE 0 END) AS gmv,
         SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' AND p.prev_path1 IN ('캠페인/기획전','외부 유입') THEN p.pdp_uv_cnt ELSE 0 END) AS mkt_pdp_uv,
         SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' AND p.prev_path1 IN ('캠페인/기획전','외부 유입') THEN p.gmv ELSE 0 END) AS mkt_gmv,
         SUM(CASE WHEN p.dt BETWEEN '{sly}' AND '{ely}' THEN p.pdp_uv_cnt ELSE 0 END) AS pdp_uv_ly,
         SUM(CASE WHEN p.dt BETWEEN '{sly}' AND '{ely}' THEN p.purchase_uv_cnt ELSE 0 END) AS buy_uv_ly,
         SUM(CASE WHEN p.dt BETWEEN '{sly}' AND '{ely}' THEN p.gmv ELSE 0 END) AS gmv_ly,
         SUM(CASE WHEN p.dt BETWEEN '{sly}' AND '{ely}' AND p.prev_path1 IN ('캠페인/기획전','외부 유입') THEN p.pdp_uv_cnt ELSE 0 END) AS mkt_pdp_uv_ly,
         SUM(CASE WHEN p.dt BETWEEN '{sly}' AND '{ely}' AND p.prev_path1 IN ('캠페인/기획전','외부 유입') THEN p.gmv ELSE 0 END) AS mkt_gmv_ly
  {_PMKT_FROM.rstrip()}
    AND (p.dt BETWEEN '{s}' AND '{e}' OR p.dt BETWEEN '{sly}' AND '{ely}')
  GROUP BY CAST(p.goods_no AS STRING)""" for nm, s, e, sly, ely in _pmkt_periods)
insert_query_result("PMKT기간", spark.sql(_pmkt_union), label=f"히어로 goods x 기간(YTD/MTD/WEEK/FWTD) PMKT(direct) ~{date}")

# (2) 주차 추세 — goods x ISO주차 (거래액/PDP 스파크라인용)
pmkt_week_query = f"""
WITH base AS (
  SELECT CAST(p.goods_no AS STRING) AS goods_no, m.style_no AS style_no,
         TO_DATE(p.dt,'yyyyMMdd') AS d, DATE_SUB(TO_DATE(p.dt,'yyyyMMdd'), MOD(DAYOFWEEK(TO_DATE(p.dt,'yyyyMMdd'))+5, 7)) AS wm, p.pdp_uv_cnt, p.purchase_uv_cnt, p.qty, p.gmv, p.prev_path1
  {_PMKT_FROM.rstrip()}
    AND p.dt BETWEEN '20250101' AND '{date}'
)
SELECT goods_no, ANY_VALUE(style_no) AS style_no,
       YEAR(DATE_ADD(wm,3)) AS yyyy, WEEKOFYEAR(wm) AS week_no, MIN(d) AS week_start, MAX(d) AS week_end,
       SUM(pdp_uv_cnt) AS pdp_uv, SUM(purchase_uv_cnt) AS buy_uv, SUM(qty) AS qty, SUM(gmv) AS gmv,
       SUM(CASE WHEN prev_path1 IN ('캠페인/기획전','외부 유입') THEN gmv ELSE 0 END) AS mkt_gmv,
       SUM(CASE WHEN prev_path1 IN ('캠페인/기획전','외부 유입') THEN pdp_uv_cnt ELSE 0 END) AS mkt_pdp_uv
FROM base
GROUP BY goods_no, wm
ORDER BY goods_no, yyyy, week_no
"""
insert_query_result("PMKT주차", spark.sql(pmkt_week_query), label=f"히어로 goods x 주차 PMKT 퍼널(direct) 2025~{date}")
print("[OK] PMKT기간 / PMKT주차 기록 완료")

# (3) 유입경로(prev_path1) x 기간 — 온사이트 경로별 유입·전환·전년(YoY). 성과뷰 '유입 경로 구성'용.
#     PMKT기간과 동일 기간(_pmkt_periods)·필터(_PMKT_FROM)에 prev_path1을 축으로 추가. GMV 미사용(유입·전환만).
#     생성기(_gen_26fw_heroes.py)가 goods→히어로로 롤업해 경로별 비중·전환율(buy_uv/pdp_uv)·유입 전년비를 만든다.
_path_union = " UNION ALL ".join(f"""
  SELECT '{nm}' AS period, CAST(p.goods_no AS STRING) AS goods_no,
         COALESCE(NULLIF(TRIM(p.prev_path1),''),'기타') AS path,
         SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' THEN p.pdp_uv_cnt ELSE 0 END) AS pdp_uv,
         SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' THEN p.purchase_uv_cnt ELSE 0 END) AS buy_uv,
         SUM(CASE WHEN p.dt BETWEEN '{sly}' AND '{ely}' THEN p.pdp_uv_cnt ELSE 0 END) AS pdp_uv_ly,
         SUM(CASE WHEN p.dt BETWEEN '{sly}' AND '{ely}' THEN p.purchase_uv_cnt ELSE 0 END) AS buy_uv_ly
  {_PMKT_FROM.rstrip()}
    AND (p.dt BETWEEN '{s}' AND '{e}' OR p.dt BETWEEN '{sly}' AND '{ely}')
  GROUP BY CAST(p.goods_no AS STRING), COALESCE(NULLIF(TRIM(p.prev_path1),''),'기타')""" for nm, s, e, sly, ely in _pmkt_periods)
insert_query_result("PMKT경로기간", spark.sql(_path_union), label=f"히어로 goods x 경로(prev_path1) x 기간 유입·전환·YoY(direct) ~{date}")
print("[OK] PMKT경로기간 기록 완료")

# COMMAND ----------

# (4) 유입경로 x 주차 — ★히어로 단위 x 2025-01-01~ (2026-08-01 개편).
#     예전엔 goods 단위 최근 12주라 '전주비' 말고는 쓸 데가 없었다. 성과 탭 '주차 스냅샷'(과거 주차 회고)을
#     26년 1월부터 보려면 주차 이력이 통째로 필요한데, goods x 경로 x 83주는 40만행이 넘어 시트에 못 싣는다.
#     → 조인을 v_hero_map으로 바꿔 **히어로 x 시즌 x 경로 x 주차**(2~3만행)로 내린다. 전주비도 그대로 나온다.
#     주차 정의는 PMKT주차와 동일(YEAR/WEEKOFYEAR + week_start/week_end) → 생성기가 같은 일평균 정규화를 재사용.
_HERO_MAP = globals().get("_HERO_MAP") or []      # 이 셀만 따로 돌릴 때 NameError 방지
_PMKT_FROM_HERO = """
  FROM team.sales.pdp_path_daily_summary_v p
  JOIN v_hero_map hm ON CAST(p.goods_no AS STRING) = hm.goods_no
  WHERE p.path_type = 'direct'
    AND LOWER(p.brand) IN ('musinsastandard','musinsastandardwoman','musinsastandardkids','musinsastandardhome')
    AND LOWER(p.com_id) NOT IN ('musinsa','musinsa_event')
"""
if _HERO_MAP:
    pmkt_path_week_query = f"""
    WITH base AS (
      SELECT hm.hero AS hero, hm.season AS season,
             COALESCE(NULLIF(TRIM(p.prev_path1),''),'기타') AS path,
             TO_DATE(p.dt,'yyyyMMdd') AS d, DATE_SUB(TO_DATE(p.dt,'yyyyMMdd'), MOD(DAYOFWEEK(TO_DATE(p.dt,'yyyyMMdd'))+5, 7)) AS wm, p.pdp_uv_cnt, p.purchase_uv_cnt, p.gmv
      {_PMKT_FROM_HERO.rstrip()}
        AND p.dt BETWEEN '20250101' AND '{date}'
    )
    SELECT hero, season, path,
           YEAR(DATE_ADD(wm,3)) AS yyyy, WEEKOFYEAR(wm) AS week_no, MIN(d) AS week_start, MAX(d) AS week_end,
           SUM(pdp_uv_cnt) AS pdp_uv, SUM(purchase_uv_cnt) AS buy_uv, SUM(gmv) AS gmv
    FROM base
    GROUP BY hero, season, path, wm
    ORDER BY hero, season, path, yyyy, week_no
    """
    insert_query_result("PMKT경로주차", spark.sql(pmkt_path_week_query),
                        label=f"히어로 x 시즌 x 경로(prev_path1) x 주차 유입·전환·거래액(direct) 2025~{date}")
    print("[OK] PMKT경로주차 기록 완료")
else:
    print("[스킵] PMKT경로주차 — 히어로 맵 없음(직전 시트값 유지)")

# COMMAND ----------

# (5) 유입경로 상세 — ★중분류(prev_path2)까지. 히어로 x 시즌 x 기간 x 대분류 x 중분류 + 전년(YoY).
#     배경: 대분류만 보면 온라인팀 기획전 유입이 '메인-세일/발매'·'기타'로 흩어져 보이지 않는다
#     (캠페인/기획전 라벨은 월 100UV 수준). 중분류를 펼쳐야 '세일/발매/추천/랭킹'이 구분된다.
#     기간·필터는 PMKT기간과 동일(_pmkt_periods) → 누계(YTD)는 1/1~, 전년은 25년 1/1~ 동일 창.
if _HERO_MAP:
    _path_dtl_union = " UNION ALL ".join(f"""
      SELECT '{nm}' AS period, hm.hero AS hero, hm.season AS season,
             COALESCE(NULLIF(TRIM(p.prev_path1),''),'기타') AS path,
             COALESCE(NULLIF(TRIM(p.prev_path2),''),'기타') AS path2,
             SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' THEN p.pdp_uv_cnt ELSE 0 END) AS pdp_uv,
             SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' THEN p.purchase_uv_cnt ELSE 0 END) AS buy_uv,
             SUM(CASE WHEN p.dt BETWEEN '{s}' AND '{e}' THEN p.gmv ELSE 0 END) AS gmv,
             SUM(CASE WHEN p.dt BETWEEN '{sly}' AND '{ely}' THEN p.pdp_uv_cnt ELSE 0 END) AS pdp_uv_ly,
             SUM(CASE WHEN p.dt BETWEEN '{sly}' AND '{ely}' THEN p.purchase_uv_cnt ELSE 0 END) AS buy_uv_ly
      {_PMKT_FROM_HERO.rstrip()}
        AND (p.dt BETWEEN '{s}' AND '{e}' OR p.dt BETWEEN '{sly}' AND '{ely}')
      GROUP BY hm.hero, hm.season, COALESCE(NULLIF(TRIM(p.prev_path1),''),'기타'),
               COALESCE(NULLIF(TRIM(p.prev_path2),''),'기타')""" for nm, s, e, sly, ely in _pmkt_periods)
    insert_query_result("PMKT경로상세", spark.sql(_path_dtl_union),
                        label=f"히어로 x 기간 x 경로 대분류/중분류(prev_path2) 유입·전환·YoY(direct) ~{date}")
    print("[OK] PMKT경로상세 기록 완료")

# COMMAND ----------

# (6) 상품 퍼널(노출·클릭·CTR) + 조회자 성·연령 — 2026-08-01 신설.
#     공식 온사이트 퍼포먼스 대시보드(QuickSight)와 같은 재료로 맞추기 위한 원천.
#     `team.sales.goods_funnel_daily` = goods x 일자 x platform x 성별 x 연령대,
#     impression_cnt(노출) · click_cnt(클릭) · gv_cnt(조회 건수) · gv_user_cnt(조회 UV).
#     ★platform='무신사'만 (29CM 제외). 브랜드 컬럼이 없어 히어로 uid 필터(v_goods_filter)로 좁힌다.
#     ★CTR = 클릭/노출 (공식 정의와 동일). 조회UV는 기존 pdp_uv와 1.02배로 일치 확인(커브드 7/20~26).
_GF_FROM = """
  FROM team.sales.goods_funnel_daily g
  JOIN v_goods_filter gf ON CAST(g.goods_no AS STRING) = CAST(gf.goods_no AS STRING)
  WHERE g.platform = '무신사'
"""
_gf_union = " UNION ALL ".join(f"""
  SELECT '{nm}' AS period, CAST(g.goods_no AS STRING) AS goods_no,
         SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.impression_cnt ELSE 0 END) AS imp,
         SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.click_cnt ELSE 0 END) AS clk,
         SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.gv_cnt ELSE 0 END) AS gv,
         SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.gv_user_cnt ELSE 0 END) AS gv_uv,
         SUM(CASE WHEN g.dt BETWEEN '{sly}' AND '{ely}' THEN g.impression_cnt ELSE 0 END) AS imp_ly,
         SUM(CASE WHEN g.dt BETWEEN '{sly}' AND '{ely}' THEN g.click_cnt ELSE 0 END) AS clk_ly,
         SUM(CASE WHEN g.dt BETWEEN '{sly}' AND '{ely}' THEN g.gv_cnt ELSE 0 END) AS gv_ly,
         SUM(CASE WHEN g.dt BETWEEN '{sly}' AND '{ely}' THEN g.gv_user_cnt ELSE 0 END) AS gv_uv_ly
  {_GF_FROM.rstrip()}
    AND (g.dt BETWEEN '{s}' AND '{e}' OR g.dt BETWEEN '{sly}' AND '{ely}')
  GROUP BY CAST(g.goods_no AS STRING)""" for nm, s, e, sly, ely in _pmkt_periods)
insert_query_result("상품퍼널기간", spark.sql(_gf_union),
                    label=f"히어로 goods x 기간 노출·클릭·조회(+전년) · goods_funnel_daily(무신사) ~{date}")
print("[OK] 상품퍼널기간 기록 완료")

# COMMAND ----------

# (7) 히어로 x 매체(utm) 유입 — 2026-08-02 신설. "퍼포먼스 마케팅을 히어로 단위로" 요청.
#     원천 team.marketing.musinsa_session_goods_view_pdp_daily = goods_no x utm x 세션.
#     ★utm_campaign(광고코드)은 카디널리티가 커서 제외 — 매체(source/medium)까지만 싣는다.
#     medium 코드 관례: da=디스플레이 · sa=검색광고 · sh=쇼핑 · cr=CRM · if=인플루언서 · organic=오가닉.
#     utm이 비면 온사이트/직접 유입이다(그쪽은 기존 PMKT경로 탭이 담당).
_HERO_MAP = globals().get("_HERO_MAP") or []
if _HERO_MAP:
    _media_union = " UNION ALL ".join(f"""
      SELECT '{nm}' AS period, hm.hero AS hero, hm.season AS season,
             COALESCE(NULLIF(TRIM(s.utm_source),''),'(없음)') AS src,
             COALESCE(NULLIF(TRIM(s.utm_medium),''),'(없음)') AS med,
             COUNT(DISTINCT CASE WHEN s.partition_date BETWEEN '{s[:4]}-{s[4:6]}-{s[6:]}' AND '{e[:4]}-{e[4:6]}-{e[6:]}' THEN s.session_id END) AS sessions,
             SUM(CASE WHEN s.partition_date BETWEEN '{s[:4]}-{s[4:6]}-{s[6:]}' AND '{e[:4]}-{e[4:6]}-{e[6:]}' THEN s.count_pdp_logs ELSE 0 END) AS pdp,
             COUNT(DISTINCT CASE WHEN s.partition_date BETWEEN '{s[:4]}-{s[4:6]}-{s[6:]}' AND '{e[:4]}-{e[4:6]}-{e[6:]}' THEN s.hash_id_mapped END) AS users,
             SUM(CASE WHEN s.partition_date BETWEEN '{sly[:4]}-{sly[4:6]}-{sly[6:]}' AND '{ely[:4]}-{ely[4:6]}-{ely[6:]}' THEN s.count_pdp_logs ELSE 0 END) AS pdp_ly
      FROM team.marketing.musinsa_session_goods_view_pdp_daily s
      JOIN v_hero_map hm ON CAST(s.goods_no AS STRING) = hm.goods_no
      WHERE (s.partition_date BETWEEN '{s[:4]}-{s[4:6]}-{s[6:]}' AND '{e[:4]}-{e[4:6]}-{e[6:]}'
          OR s.partition_date BETWEEN '{sly[:4]}-{sly[4:6]}-{sly[6:]}' AND '{ely[:4]}-{ely[4:6]}-{ely[6:]}')
      GROUP BY hm.hero, hm.season,
               COALESCE(NULLIF(TRIM(s.utm_source),''),'(없음)'),
               COALESCE(NULLIF(TRIM(s.utm_medium),''),'(없음)')
      HAVING SUM(CASE WHEN s.partition_date BETWEEN '{s[:4]}-{s[4:6]}-{s[6:]}' AND '{e[:4]}-{e[4:6]}-{e[6:]}' THEN s.count_pdp_logs ELSE 0 END) > 0
          OR SUM(CASE WHEN s.partition_date BETWEEN '{sly[:4]}-{sly[4:6]}-{sly[6:]}' AND '{ely[:4]}-{ely[4:6]}-{ely[6:]}' THEN s.count_pdp_logs ELSE 0 END) > 0""" for nm, s, e, sly, ely in _pmkt_periods)
    insert_query_result("히어로매체기간", spark.sql(_media_union),
                        label=f"히어로 x 기간 x 매체(utm source/medium) 유입 세션·PDP(+전년) ~{date}")
    print("[OK] 히어로매체기간 기록 완료")
else:
    print("[스킵] 히어로매체기간 — 히어로 맵 없음(직전 시트값 유지)")

_HERO_MAP = globals().get("_HERO_MAP") or []
if _HERO_MAP:
    _demo_union = " UNION ALL ".join(f"""
      SELECT '{nm}' AS period, hm.hero AS hero, hm.season AS season,
             CASE WHEN g.gender IN ('남성','여성') THEN g.gender ELSE '미상' END AS gender,
             CASE WHEN g.age_group RLIKE '^[0-9]' THEN g.age_group ELSE '미상' END AS age_group,
             SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.impression_cnt ELSE 0 END) AS imp,
             SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.click_cnt ELSE 0 END) AS clk,
             SUM(CASE WHEN g.dt BETWEEN '{s}' AND '{e}' THEN g.gv_user_cnt ELSE 0 END) AS gv_uv,
             SUM(CASE WHEN g.dt BETWEEN '{sly}' AND '{ely}' THEN g.gv_user_cnt ELSE 0 END) AS gv_uv_ly
      FROM team.sales.goods_funnel_daily g
      JOIN v_hero_map hm ON CAST(g.goods_no AS STRING) = hm.goods_no
      WHERE g.platform = '무신사'
        AND (g.dt BETWEEN '{s}' AND '{e}' OR g.dt BETWEEN '{sly}' AND '{ely}')
      GROUP BY hm.hero, hm.season,
               CASE WHEN g.gender IN ('남성','여성') THEN g.gender ELSE '미상' END,
               CASE WHEN g.age_group RLIKE '^[0-9]' THEN g.age_group ELSE '미상' END""" for nm, s, e, sly, ely in _pmkt_periods)
    insert_query_result("상품성연령", spark.sql(_demo_union),
                        label=f"히어로 x 기간 x 성별 x 연령대 조회 구성(무신사) ~{date}")
    print("[OK] 상품성연령 기록 완료")
else:
    print("[스킵] 상품성연령 — 히어로 맵 없음(직전 시트값 유지)")
# COMMAND ----------

# (4) 일별 유입 — goods x 날짜(최근 90일) PDP 유입 UV. 성과탭 상단 'PDP 일별 유입 트렌드'(app window.__PDP_DAILY) 소스.
#     생성기(_gen_26fw_heroes.py)가 goods→26FW 히어로로 롤업해 히어로별 일별 곡선 + IMC 액션 핀을 그린다.
#     기간·필터는 PMKT과 동일(direct·무탠 브랜드·com_id 제외). 유입 0인 goods-day는 제외(행수 절감).
# ★기간: 최근 90일 → **시즌 전체**(26SS 2/1~ · 26FW 8/1~익년 1/31)로 확장(2026-07-31 사용자 요청).
#   앱 트렌드가 시즌 창을 가로로 길게 보여주려면 원천도 그만큼 있어야 한다. 시즌 시작 = 그 해 2/1(SS)·8/1(FW).
#   지금 시점(26FW 진행)에선 26SS 회고까지 함께 보므로 **2/1부터** 통째로 뽑는다(행수 ~3.5배, 시트 여유 충분).
_pdpd_start = f"{d.year}0201" if d.month >= 2 else f"{d.year - 1}0201"
pdp_daily_query = f"""
SELECT TO_DATE(p.dt,'yyyyMMdd') AS date, CAST(p.goods_no AS STRING) AS goods_no,
       ANY_VALUE(m.style_no) AS style_no, SUM(p.pdp_uv_cnt) AS pdp_uv
{_PMKT_FROM.rstrip()}
    AND p.dt BETWEEN '{_pdpd_start}' AND '{date}'
GROUP BY p.dt, CAST(p.goods_no AS STRING)
HAVING SUM(p.pdp_uv_cnt) > 0
ORDER BY date, goods_no
"""
insert_query_result("PDP일별", spark.sql(pdp_daily_query), label=f"히어로 goods x 일별 PDP 유입 UV(direct) {_pdpd_start}~{date}")
print("[OK] PDP일별 기록 완료")
