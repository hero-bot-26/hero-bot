# MUSINSA STANDARD 히어로 랭킹봇 — 재현 프롬프트 (v1 최종)

이 문서는 **남/여 라인 분리 적재 직전의 v1 최종 상태**를 그대로 다시 만들도록 코딩 에이전트(Claude / Cursor / Aider 등)에게 시키는 단일 프롬프트다.
중간 단계 히스토리는 생략. 아래 한 덩어리를 그대로 던지면 같은 봇이 나온다.

---

## 한 줄 요약

> 매시간 무신사 [전체] 랭킹 페이지에서 **무신사 스탠다드/우먼/키즈** 브랜드 상품의 Top 100 순위를 캡처하고 Google Sheet에 raw 적재한다. 다음날 오전 9시 KST에 어제 데이터를 집계해 Slack에 일일 리포트를 발송하고, Top 10에 진입한 상품은 무신사 페이지 스크린샷도 Drive 업로드 후 image_block으로 첨부한다. 전 과정은 GitHub Actions cron으로 자동화된다.

---

## 빌드 프롬프트 (이 블록 전체를 에이전트에게 그대로 던질 것)

````
역할: 너는 "MUSINSA STANDARD 히어로 봇"이라는 사내 자동화 도구의 빌더야. 사용자는 '무탠다드' 라인을 담당하는 상품MD고, 매일 아침 9시에 어제 우리 브랜드가 무신사 랭킹에서 어떻게 움직였는지 한눈에 보고 싶어해. 처음부터 끝까지 한 번에 만들어줘.

# 스택
- Python 3.12+
- requests (무신사 비공식 API)
- playwright + chromium (랭킹 페이지 PNG 캡처)
- google-api-python-client / google-auth-oauthlib (Sheets + Drive)
- slack_sdk (Bot Token → chat.postMessage)
- PyYAML (config)
- GitHub Actions (cron + 시크릿 저장)

# 절대 원칙 (어기면 운영 사고)
1. 시간대는 항상 Asia/Seoul (KST). datetime.now()는 zoneinfo.ZoneInfo("Asia/Seoul")로.
2. 멱등성. 같은 시간 슬롯 / 같은 날짜는 두 번 적재·발송되면 안 된다 (cron 안전망이 여러 번 발사된다).
3. Sheet 적재는 valueInputOption="RAW". USER_ENTERED 쓰면 날짜/숫자가 로케일 포맷("2026. 5. 1.")으로 바뀌어 다음날 read 매칭이 깨진다.
4. 외부 의존(무신사 API, Drive, Slack)에 try/except + 로그 + 부분 실패 허용. 스크린샷 실패해도 적재는 계속.
5. 로그는 한국어 + 페르소나 톤 ("📋 ... 시작합니다.", "  ↳ ...", "✅ 완료.", "⏭️ 오늘은 일 안 합니다.", "❗ 죄송합니다, 중간에 막혔어요.").
6. 비밀값은 GitHub Actions Secret 우선, 로컬은 yaml fallback. 하드코딩 금지.

# 최종 리포지토리 레이아웃
config.yaml
requirements.txt
run_ranking_hourly.py
run_ranking_daily.py
.github/workflows/hourly.yml
.github/workflows/daily.yml
soo/
  __init__.py
  persona.py
  auth.py
  secrets.py
  hero_list.py
  scrapers/
    musinsa_ranking.py
    musinsa_screenshot.py
  storage/
    sheet_archive.py
    drive_uploader.py
    screenshots_tab.py
  tasks/
    ranking_hourly.py
    ranking_daily.py

# config.yaml
ranking.brand_keywords: ["무신사 스탠다드", "무신사 스탠다드 우먼", "무신사 스탠다드 키즈"]
ranking.section_id: 199          # 무신사 [전체] 탭
ranking.sub_pan: "product"
ranking.top_n: 100
ranking.hero_sheet_id: "<라인별 탭 A열에 히어로 UID 들어있는 Sheet ID>"
ranking.archive_sheet_id: "<Long/Wide/Screenshots 탭 들어갈 Sheet ID>"
ranking.archive_sheet_url: "<Slack 리포트 제목에 임베드할 Long 탭 직링크 (gid 포함)>"
ranking.screenshot_threshold: 10
ranking.screenshot_folder_id: "<Drive 폴더 ID — 빈 값이면 스크린샷 비활성화>"
ranking.screenshot_crop_to_rank: 12   # null/0이면 풀페이지

# soo/persona.py
- Persona 데이터클래스 (name, tagline, slack_username, slack_icon_emoji)
- RANKING_BOT = Persona(name="MUSINSA STANDARD 히어로 봇", icon_emoji=":superhero:", ...)
- setup_logger(log_dir, dry_run) — stdout(UTF-8) + 날짜별 파일 handler.
- greet/starting_task/step/task_done_ok/task_done_skip/task_failed — 한국어 페르소나 문구.
- send_slack(message, *, bot_token, target, persona, log, blocks=None)
  · slack_sdk.WebClient.chat_postMessage. blocks 있으면 message는 fallback text.
  · 실패 시 log.error로 사유 남기고 False 리턴 (예외 던지지 말 것).

# soo/auth.py — Google OAuth refresh_token 플로우
SCOPES = drive, spreadsheets, presentations, gmail.compose (4개 박아둠).
get_credentials(credentials_path, token_path):
  1) 환경변수 GOOGLE_OAUTH_TOKEN(JSON 문자열) 있으면 거기서 Credentials.
  2) 없으면 token.json 파일에서.
  3) expired면 refresh, 가능하면 token.json에 저장.
  4) 둘 다 실패면:
     - GOOGLE_OAUTH_TOKEN 있는데 invalid → RuntimeError ("로컬에서 새 token.json 발급해 Secret 갱신")
     - 로컬이면 InstalledAppFlow.run_local_server 브라우저 플로우.
build_services(creds) → {"drive":..., "sheets":..., "slides":..., "gmail":...}

# soo/secrets.py
ENV_KEYS = {"slack_bot_token":"SLACK_BOT_TOKEN", "slack_target":"SLACK_TARGET", ...}
load_secrets(path): yaml 파일 + env 병합, env 우선.

# soo/hero_list.py
DEFAULT_LINE_TABS = ["워셔블수피마","커브드팬츠","윈드브레이커","심리스브라","NEW 티셔츠","쿨탠다드티셔츠","쿨탠다드팬츠"]
load_hero_list(sheets_service, sheet_id, line_tabs=DEFAULT_LINE_TABS, a_range="A1:A200") → {uid: HeroEntry(uid, line)}
- 각 탭 A열에서 정규식 ^\d{6,10}$ 매칭되는 값만 추출. 첫 등장 라인 유지.

# soo/scrapers/musinsa_ranking.py
엔드포인트:
  page 1: https://client.musinsa.com/api/home/web/v5/pans/ranking
          params: storeCode=musinsa, sectionId=199, contentsId="", categoryCode=000,
                  ageBand=AGE_BAND_ALL, gf=A, subPan=product
  page 2+: https://client.musinsa.com/api/home/web/v5/pans/ranking/sections/199
           params: 위 + period=REALTIME, eventPeriod=BASIC_REALTIME, page=N, offset, startRank, variantValue=""
응답: data.modules → type=="MULTICOLUMN" → items → type=="PRODUCT_COLUMN".
  image.rank가 None이면 광고 슬롯 → skip. info.brandName, info.productName, onClick.url, id 추출.
fetch_top(n=100, section_id=199, sub_pan="product", sleep_between=0.5) → RankItem 리스트.
  page 2 이상에서 page > 10이면 안전장치로 break.
filter_by_brand(items, keywords, mode="exact") — 기본 exact (다른 라인 브랜드 안 섞이게).
User-Agent: Chrome 데스크톱.

# soo/scrapers/musinsa_screenshot.py
URL: https://www.musinsa.com/main/musinsa/ranking?storeCode=musinsa&sectionId={section_id}&categoryCode=000&gf=A&ageBand=AGE_BAND_ALL
screenshot_ranking_full_page(section_id=199, timeout_ms=30000, viewport_width=1280, crop_to_rank=12) -> bytes:
- chromium headless, locale="ko-KR", User-Agent 데스크톱.
- page.goto(wait_until="domcontentloaded") + 2.5s 대기.
- crop_to_rank가 None/0이면 full_page screenshot return.
- 아니면:
  1) 다음 JS 평가로 lazy-load 안정화:
     async () => {
       for (let y=0; y<=4000; y+=400) { window.scrollTo(0,y); await sleep(150); }
       window.scrollTo(0,0); await sleep(400);
       await Promise.all(Array.from(document.images).filter(i=>!i.complete)
         .map(i => new Promise(r => { i.onload=i.onerror=()=>r(); setTimeout(r,2500); })));
     }
  2) 다음 JS로 rank N번째 카드 bottom Y 측정:
     (rankLimit) => {
       const seen=new Set(), cards=[];
       for (const a of document.querySelectorAll('a[href*="/products/"]')) {
         if (seen.has(a.href)) continue;
         const r=a.getBoundingClientRect();
         if (r.width<80||r.height<80) continue;     // ad/icon 제외
         seen.add(a.href);
         cards.push({y:r.top+window.scrollY, height:r.height});
       }
       cards.sort((a,b)=>a.y-b.y);
       if (cards.length<rankLimit) return null;
       const last=cards[rankLimit-1];
       return Math.ceil(last.y+last.height+8);
     }
  3) clip={x:0,y:0,width:viewport_width,height:bottom_y}로 screenshot.
  4) bottom_y None이면 full_page fallback.

# soo/storage/sheet_archive.py
LONG_TAB="Long", header=["날짜","시간","goods_no","랭킹 순위","브랜드","상품명","히어로여부"]
TIME_SLOTS=["00:00","01:00",...,"23:00"]  (24개 정각 슬롯)
WIDE_TAB="Wide", header=["날짜","goods_no","브랜드","상품명","히어로여부"]+TIME_SLOTS

_ensure_tab(svc, sheet_id, tab, header):
  - 메타 조회로 존재 확인. 없으면 addSheet → header 작성 (valueInputOption="RAW").
  - 있는데 1행이 비었으면 header 채움.

has_hour_data(svc, sheet_id, ts) -> bool:
  - Long 탭의 gridProperties.rowCount 가져와서 마지막 ~200행 (max(2,rowCount-200) ~ rowCount)만 read.
  - valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="FORMATTED_STRING".
  - (row[0]==ts.date.iso, row[1]==ts.strftime("%H:%M")) 매칭되면 True.

append_realtime(svc, sheet_id, ts, items, log):
  - items: [(goods_no, rank, brand, product_name, is_hero), ...]
  - 행: [ts.date.iso, ts.strftime("%H:%M"), str(goods_no), int(rank), brand, product_name, "히어로" if is_hero else ""]
  - values().append range="'Long'!A1", valueInputOption="RAW", insertDataOption="INSERT_ROWS".

read_day_long(svc, sheet_id, target_day) -> [{ts, goods_no, rank, brand, product_name, is_hero}, ...]:
  - 'Long'!A2:G read (UNFORMATTED_VALUE + FORMATTED_STRING).
  - 날짜 매칭: ISO("2026-05-11"), 한국 로케일 strict("2026. 5. 11."), compact("2026.5.11.") 세 가지.
  - 시간 정규화: "8:00:00", "오전 8:00:00", "08:00" → "HH:00" (오후면 +12).
  - ts_iso = f"{day_str}T{HH:00}:00", is_hero = (hero_s == "히어로").

count_snapshots(rows) -> int : unique ts 개수.

_build_wide_rows(rows, target_day) -> [[...]]:
  - by_goods로 묶고 ranks[slot]=rank.
  - 정렬: (is_hero 우선, min(ranks.values()) ASC).
  - 행: [day_iso, goods_no, brand, product_name, "히어로"or"", *TIME_SLOTS 값 (없으면 "")]

append_day_wide(svc, sheet_id, target_day, rows, log) : Wide에 append (RAW).

has_day_wide(svc, sheet_id, target_day) -> bool:
  - 'Wide'!A2:A read해 ISO/로케일 3종 매칭.

# soo/storage/drive_uploader.py
upload_png(drive_service, folder_id, filename, image_bytes) -> (image_url, file_id):
  - files().create(body={name,parents:[folder_id]}, media=MediaIoBaseUpload(BytesIO,"image/png"),
                   fields="id", supportsAllDrives=True).
  - permissions().create(fileId, body={role:"reader",type:"anyone"}, fields="id", supportsAllDrives=True).
  - image_url = f"https://lh3.googleusercontent.com/d/{file_id}"  ← Slack image_block에서 그대로 임베드.

ensure_subfolder(drive_service, parent_id, name) -> folder_id:
  - files().list(q=`name='{name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false`,
                 fields="files(id,name)", pageSize=1,
                 supportsAllDrives=True, includeItemsFromAllDrives=True)
  - 없으면 files().create(body={name, mimeType:'application/vnd.google-apps.folder', parents:[parent_id]},
                          fields="id", supportsAllDrives=True).
※ 모든 Drive API 호출에 supportsAllDrives=True (+ list에 includeItemsFromAllDrives=True). 빠지면 Shared Drive 404.

# soo/storage/screenshots_tab.py
SCREENSHOTS_TAB="Screenshots", header=["날짜","goods_no","peak_rank","screenshot_url","file_id","captured_at"]
_ensure_tab — 없으면 생성.
read_day_records(svc, sheet_id, target_day) -> {goods_no: {peak_rank, screenshot_url, file_id, captured_at, row_idx}}:
  - 헤더 다음(row 2)부터 row_idx 1-based로 보관 — upsert 시 정확한 row 위치 update.
  - 날짜 매칭은 ISO + 한국 로케일 strict 둘 다.
upsert_record(svc, sheet_id, target_day, goods_no, peak_rank, screenshot_url, file_id, captured_at, log):
  - 같은 (날짜, goods_no) 있으면 'Screenshots'!A{row}:F{row} update(RAW), 없으면 append.
  - 호출자가 "기존보다 좋은 peak"임을 사전 검증한 뒤 호출.

# soo/tasks/ranking_hourly.py
KST = ZoneInfo("Asia/Seoul")

run(sheets_service, sheet_id, brand_keywords, hero_uids, log, top_n=100, section_id=199, sub_pan="product",
    drive_service=None, screenshot_threshold=10, screenshot_folder_id="", screenshot_crop_to_rank=12):
  captured_at = datetime.now(KST)
  ts = captured_at.replace(minute=0, second=0, microsecond=0)   # 어떤 trigger든 :00 슬롯으로 정규화

  log "📋 랭킹 캡처 YYYY-MM-DD HH:00 KST 시작합니다."

  # 멱등성
  if sheet_archive.has_hour_data(svc, sheet_id, ts):
    log "  ↳ HH:00 슬롯 이미 적재됨 — skip"
    return {..., "skipped": True}

  all_items = fetch_top(n=top_n, section_id=section_id, sub_pan=sub_pan)
  matched   = filter_by_brand(all_items, brand_keywords)
  items_for_sheet = []
  hero_hits = []
  for it in matched:
    is_hero = it.goods_no in hero_uids
    if is_hero: hero_hits.append(it)
    items_for_sheet.append((it.goods_no, it.rank, it.brand, it.product_name, is_hero))
  appended = sheet_archive.append_realtime(svc, sheet_id, ts, items_for_sheet, log)

  hero_hits 로그(최대 5개) — "  · #R 상품명[:40]"

  # 스크린샷
  screenshot_updated = 0
  if drive_service and screenshot_folder_id:
    screenshot_updated = _maybe_capture_screenshot(...)

  log "✅ 완료. HH:00 캡처 — Sheet에 N행 (무탠 X / 히어로 Y / 스크린샷 갱신 Z)"

_maybe_capture_screenshot(*, sheets_service, drive_service, sheet_id, matched, captured_at,
                          threshold, folder_id, section_id, crop_to_rank, log) -> int:
  if not folder_id: return 0
  target_day = captured_at.date()
  candidates = [it for it in matched if it.rank <= threshold]
  if not candidates: return 0
  existing = screenshots_tab.read_day_records(svc, sheet_id, target_day)
  needs_update = [it for it in candidates if (existing.get(it.goods_no) is None or it.rank < existing[it.goods_no]["peak_rank"])]
  if not needs_update:
    log "  ↳ 스크린샷 — Top {threshold} 후보 {n}개 모두 best 갱신 X (skip)"
    return 0

  try:    png = musinsa_screenshot.screenshot_ranking_full_page(section_id, crop_to_rank=crop_to_rank)
  except: 로그 후 return 0
  try:
    day_folder_id = drive_uploader.ensure_subfolder(drive_service, folder_id, target_day.iso)
    filename = f"ranking_{captured_at.strftime('%Y%m%d_%H%M%S')}.png"
    url, file_id = drive_uploader.upload_png(drive_service, day_folder_id, filename, png)
  except: 로그 후 return 0

  for it in needs_update:
    try: screenshots_tab.upsert_record(svc, sheet_id, target_day, it.goods_no, it.rank, url, file_id, captured_at, log)
    except: 로그 (한 상품 실패해도 다음 진행)

  return len(needs_update)

# soo/tasks/ranking_daily.py
JUMP_THRESHOLD = 50

_aggregate(rows) -> {goods_no: {brand, product_name, is_hero, hours_in_chart, peak_rank, peak_ts, ranks}}

_format_time(iso_ts): 분=0이면 "{H}시", 아니면 "{H}:{MM}"

_hero_summary_line(agg) -> "최고 랭킹 #{peak_rank:>3}  {product_name[:42]:<42}  ({time} 피크)"

_new_and_jumped(aggregated, prev_aggregated) -> (new_entries, jumped):
  - new_entries: 어제 등장 + 그제 미등장 → peak_rank ASC
  - jumped: 양쪽 다 있고 (prev_peak - cur_peak) >= JUMP_THRESHOLD → 큰 폭부터

_title(target_day, sheet_url, top_n) -> str:
  sheet_url 있으면: "📊 *<{sheet_url}|{date} 무탠다드 랭킹 리포트>* (Top {top_n} 기준)"
  없으면:           "📊 *{date} 무탠다드 랭킹 리포트* (Top {top_n} 기준)"

build_report(rows, prev_rows, target_day, hero_uids, sheet_url=None, top_n=100) -> str:
  # 과도기 호환: top_n 줄어든 직후엔 Sheet에 옛 rank>top_n 행이 섞여있을 수 있음. 리포트는 현재 top_n 기준만.
  rows      = [r for r in rows      if r.rank <= top_n]
  prev_rows = [r for r in prev_rows if r.rank <= top_n]
  n_snapshots = count_snapshots(rows)
  if n_snapshots == 0: return title + " — 캡처된 스냅샷이 없어요."

  aggregated      = _aggregate(rows)
  prev_aggregated = _aggregate(prev_rows) if prev_rows else {}
  new_entries, jumped = _new_and_jumped(aggregated, prev_aggregated)

  hero_aggs  = sorted([a for a in aggregated.values() if a.is_hero],   key=(peak_rank, -hours_in_chart))
  other_aggs = sorted([a for a in aggregated.values() if not a.is_hero], key=(peak_rank, -hours_in_chart))
  missing_hero_uids = hero_uids - {gn for gn,a in aggregated.items() if a.is_hero}

  섹션 구성:
    title
    "_캡처 {n}/24 회 · 무탠 계열 누적 등장 {len(aggregated)}개 · 히어로 {hero_count}/{total} 진입_"
    ""
    "🎯 *히어로 (사전 지정 상품)*"
       hero_aggs[:20] → "  • {_hero_summary_line}"
       (>20이면) "  _… 외 N개_"
       missing 있으면 "  ⚠️ 미진입 히어로 {n}개 (전체 {total}개 중)"
    ""
    "📈 *기타 무탠 계열 진입* (히어로 외 — 상위 10)"
       other_aggs[:10] → "  • {_hero_summary_line}"
       (>10) "  _… 외 N개_"
    ""
    "🚀 *전일 대비 급상승 / 신규 진입* (peak rank 50위 이상 향상)"
       prev_rows 비었으면: "  _전일 데이터 부족 — 비교 불가_"
       new/jumped 모두 0이면: "  _급상승/신규 진입 없음_"
       new_entries[:10] → "  • 🆕 최고 랭킹 #R  {name}  (신규 · {time} 피크)"
       (>10) "  _… 신규 외 N개_"
       jumped[:10] → "  • 📈 최고 랭킹 #R  {name}  (전일 #{prev} → +{jump}↑)"
       (>10) "  _… 급상승 외 N개_"

_send_screenshots(*, sheets_service, sheet_id, target_day, rows, slack_bot_token, slack_target, log) -> int:
  records = screenshots_tab.read_day_records(svc, sheet_id, target_day)
  if not records: return 0
  name_lookup: rows의 첫 등장 product_name 사용 (Screenshots 탭엔 이름 없음).
  items = sorted(records.items(), key=lambda kv: kv[1].peak_rank)
  blocks = [{"type":"header","text":{"type":"plain_text","text":"📸 Top 10 진입 스크린샷"}}]
  for gn, rec in items:
    if not rec.screenshot_url: continue
    name = name_lookup.get(gn, gn)
    caption = f"*#{rec.peak_rank:>2}*  {name[:60]}"
    blocks.append({"type":"section","text":{"type":"mrkdwn","text":caption}})
    blocks.append({"type":"image","image_url":rec.screenshot_url,"alt_text":name[:100] or gn})
  if len(blocks) <= 1: return 0
  # Slack 50 block 제한 → 48개씩 청크 분할 발송
  for chunk in chunks(blocks, 48):
    persona.send_slack("📸 스크린샷", bot_token=..., target=..., persona=RANKING_BOT, log=log, blocks=chunk)

run(sheets_service, sheet_id, hero_uids, slack_bot_token, slack_target, log, target_day=None, sheet_url=None, top_n=100):
  if target_day is None: target_day = date.today() - 1day
  rows      = sheet_archive.read_day_long(svc, sheet_id, target_day)
  prev_rows = sheet_archive.read_day_long(svc, sheet_id, target_day - 1day)
  report = build_report(rows, prev_rows, target_day, hero_uids, sheet_url=sheet_url, top_n=top_n)
  log 한 줄씩 출력.
  if slack_bot_token and slack_target:
    sent = persona.send_slack(report, bot_token, target, persona=RANKING_BOT, log)
    if sent:
      _send_screenshots(...)
  sheet_archive.append_day_wide(svc, sheet_id, target_day, rows, log)
  log "✅ 완료. {date} 리포트 + Wide 정리 완료"

# run_ranking_hourly.py
- argparse(--dry-run)
- config 로드, GOOGLE_OAUTH_TOKEN/credentials.json 으로 build_services.
- load_hero_list(sheets, hero_sheet_id) → hero_uids 집합.
- ranking_hourly.run(...) 호출. screenshot_folder_id 빈 문자열이면 자동 비활성화.
- 예외 시 persona.task_failed + traceback debug 로그.

# run_ranking_daily.py
- argparse(--dry-run, --as-of YYYY-MM-DD, --force)
- target_day: --as-of 없으면 (now KST - 1day).date().
- 멱등성: --force 아니고 --dry-run 아니고 has_day_wide(target_day) True면 task_done_skip 후 exit 0.
- ranking_daily.run(...) 호출. dry_run이면 slack_token/target 전달 안 함.

# .github/workflows/hourly.yml
on:
  schedule:
    - cron: "3,13,23,33,43,53 * * * *"   # 시간당 6회 안전망 — ranking_hourly가 has_hour_data로 첫 성공 외 skip
  workflow_dispatch:
concurrency: { group: hourly-capture, cancel-in-progress: false }
jobs.capture:
  runs-on: ubuntu-latest
  timeout-minutes: 8
  steps:
    - actions/checkout@v4
    - actions/setup-python@v5 (python 3.12, cache:pip)
    - pip install -r requirements.txt
    - actions/cache@v4   ~/.cache/ms-playwright  (key: playwright-chromium-${{ runner.os }}-v1)
    - python -m playwright install --with-deps chromium
    - env: GOOGLE_OAUTH_CREDENTIALS, GOOGLE_OAUTH_TOKEN  (Slack은 hourly에서 발송 X)
      run: python run_ranking_hourly.py

# .github/workflows/daily.yml
on:
  schedule:
    - cron: "0 0 * * *"    # KST 09:00
    - cron: "15 0 * * *"   # 09:15 안전망
    - cron: "30 0 * * *"
    - cron: "0 1 * * *"    # 10:00 마지막 안전망
  workflow_dispatch:
    inputs: { as_of, dry_run, force }
concurrency: { group: daily-report, cancel-in-progress: false }
jobs.report:
  timeout-minutes: 10
  steps: checkout / setup-python / pip install / run
  env: GOOGLE_OAUTH_CREDENTIALS, GOOGLE_OAUTH_TOKEN, SLACK_BOT_TOKEN, SLACK_TARGET
  inputs → CLI flag 빌더(--as-of/--dry-run/--force).

# requirements.txt
slack_sdk>=3.27.0
google-api-python-client>=2.120.0
google-auth-oauthlib>=1.2.0
google-auth-httplib2>=0.2.0
PyYAML>=6.0.1
requests>=2.31.0
playwright>=1.40

# .gitignore
secrets.yaml
credentials.json
token.json
data/
logs/
__pycache__/
*.pyc
.venv/
venv/
.idea/
.vscode/

이 명세대로 다 만들어줘. 로컬에서 `python run_ranking_hourly.py --dry-run`이 무신사 fetch + brand 매칭까지만 돌고, `python run_ranking_daily.py --dry-run`이 Slack 없이 리포트 텍스트만 콘솔에 찍히면 성공.
````

---

## 부록 A — 최초 setup 체크리스트 (코드 외 작업)

1. **Google Cloud Console**
   - OAuth Client ID (Desktop type) 발급 → `credentials.json`.
   - 로컬에서 `python run_ranking_hourly.py --dry-run` 한 번 돌려 브라우저 플로우 통과 → `token.json` 생성.
   - 두 파일 내용을 GitHub Secret `GOOGLE_OAUTH_CREDENTIALS` / `GOOGLE_OAUTH_TOKEN` 에 JSON 그대로 저장.

2. **Slack App**
   - Bot Token Scopes: `chat:write`, `chat:write.customize`.
   - 봇을 대상 채널에 invite.
   - Bot Token → `SLACK_BOT_TOKEN`, 채널 ID(`C0XXX...`) → `SLACK_TARGET`.

3. **Google Sheet**
   - `hero_sheet_id`: 라인별 탭 A열에 히어로 상품 UID(숫자) 채워둔다.
   - `archive_sheet_id`: 빈 시트. Long/Wide/Screenshots 탭은 봇이 자동 생성.

4. **Google Drive**
   - 스크린샷 업로드용 폴더 생성 → ID를 `screenshot_folder_id`에. Shared Drive 안이면 OAuth 계정이 멤버여야 함.

5. **GitHub**
   - Secrets 5개 등록 후 Actions 탭에서 workflow_dispatch로 hourly/daily 한 번씩 손으로 돌려 결과 확인.

---

## 부록 B — 자주 부딪힌 함정

| 함정 | 원인 | 해결 |
|---|---|---|
| `read_day_long`가 0행 반환 | Sheet가 날짜를 로케일 포맷("2026. 5. 1.")으로 저장 | `valueInputOption="RAW"`로 적재 + read 시 ISO/로케일 모두 매칭 |
| 같은 날 리포트가 2~3번 발송됨 | daily cron 4개 안전망 모두 동작 | `has_day_wide` 멱등성 체크 + `concurrency` 그룹 |
| 같은 시간 Long 탭에 행 다중 중복 | hourly cron 6개 안전망 모두 동작 | `has_hour_data` (마지막 200행 스캔) + ts를 :00 슬롯으로 정규화 |
| Drive 업로드 404 NotFound | 폴더가 Shared Drive 안 | 모든 Drive API에 `supportsAllDrives=True` (list엔 `includeItemsFromAllDrives=True`) |
| Slack 이미지가 안 보임 | Drive 공유 권한 누락 | upload 직후 `permissions.create({role:"reader", type:"anyone"})` |
| 스크린샷이 빈 페이지 | 카드 lazy-load 미완료 | goto 후 2.5s 대기 + 0~4000px 스크롤 JS + 모든 `<img>.onload` 대기 |
| 스크린샷이 너무 길어 Slack 가독성 ↓ | full_page | `crop_to_rank=12`로 rank N번째 카드 bottom까지 clip |
| Actions에서 token refresh 실패 | refresh_token 만료/회수 | 로컬에서 새 `token.json` 발급해 `GOOGLE_OAUTH_TOKEN` Secret 재등록 |
| 다른 라인 상품 섞임 ("무신사 스탠다드 스포츠" 등) | brand 매칭이 substring | `filter_by_brand(mode="exact")` 유지 |

---

## 부록 C — 운영 명령어 치트시트

```bash
# 로컬 dry-run (Slack 발송 X)
python run_ranking_hourly.py --dry-run
python run_ranking_daily.py --dry-run

# 특정 날짜 리포트 재발송 (이미 발송됐어도 강제)
python run_ranking_daily.py --as-of 2026-05-10 --force

# GitHub Actions에서 수동 실행
#   Actions 탭 → "Daily ranking report" → Run workflow
#   inputs: as_of=2026-05-10, force=true
```

---

이 문서를 그대로 에이전트에게 한 번에 던지면, 남/여 분리 작업 시작 직전의 v1 봇이 그대로 재현된다.
