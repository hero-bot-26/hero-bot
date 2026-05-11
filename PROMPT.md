# MUSINSA STANDARD 히어로 랭킹봇 — 재현 프롬프트

이 문서는 "무탠다드 히어로 랭킹봇"을 **처음부터 똑같이 만들도록 Claude(또는 다른 코딩 에이전트)에게 시키는 프롬프트 모음**이다.
각 Phase는 실제 개발 순서(= git history)를 그대로 따라간다. Phase 단위로 복붙해서 에이전트에게 시키면 된다.

---

## 0. 봇 한 줄 요약

> 매시간 무신사 [전체] 랭킹 페이지에서 **무신사 스탠다드/우먼/키즈** 브랜드 상품의 Top 100 순위를 캡처하고, Google Sheet에 raw 적재한다. 다음날 오전 9시 KST에 어제 데이터를 집계해서 Slack에 일일 리포트를 발송하고, Top 10에 진입한 상품은 무신사 페이지 스크린샷도 Drive 업로드 후 image_block으로 첨부한다. 전 과정은 GitHub Actions cron으로 자동화된다.

---

## 1. 전역 컨텍스트 (모든 Phase 공통 — 한 번만 읽고 시작)

```
역할: 너는 "MUSINSA STANDARD 히어로 봇" 이라는 사내 자동화 도구의 빌더야. 사용자는 '무탠다드' 라인을 담당하는 상품MD고, 매일 아침 9시에 어제 우리 브랜드가 무신사 랭킹에서 어떻게 움직였는지 한눈에 보고 싶어해.

스택 / 외부 의존성:
- Python 3.12+
- requests (무신사 비공식 API 호출)
- playwright + chromium (랭킹 페이지 PNG 캡처)
- google-api-python-client / google-auth-oauthlib (Sheets + Drive)
- slack_sdk (Bot Token → chat.postMessage)
- PyYAML (config)
- GitHub Actions (cron 스케줄러 + 시크릿 저장소)

핵심 원칙 (반드시 지킬 것):
1. **시간대는 항상 Asia/Seoul (KST)**. UTC로 비교하지 말고 zoneinfo.ZoneInfo("Asia/Seoul") 사용.
2. **멱등성**. 같은 시간 슬롯 / 같은 날짜는 두 번 적재/발송되면 안 된다 (cron 안전망이 여러 번 발사되므로).
3. **Sheet 적재는 valueInputOption="RAW"**. USER_ENTERED 쓰면 날짜/숫자가 로케일 포맷("2026. 5. 1.")으로 바뀌어서 다음날 read 할 때 매칭이 깨진다.
4. **외부 의존(무신사 API, Drive, Slack)에 try/except + 로그 + 부분 실패 허용**. 스크린샷 실패해도 적재는 계속 진행.
5. **로그는 한국어 + 페르소나 톤** ("📋 ... 시작합니다.", "  ↳ ...", "✅ 완료.", "⏭️ 오늘은 일 안 합니다.", "❗ 죄송합니다, 중간에 막혔어요.").
6. **비밀값은 GitHub Actions Secret 우선, 로컬은 yaml fallback**. 코드에 하드코딩 금지.

리포지토리 레이아웃 (최종 상태):
  config.yaml                      ← 봇 설정 (브랜드 키워드, sheet ID, threshold 등)
  requirements.txt
  run_ranking_hourly.py            ← 시간당 진입점
  run_ranking_daily.py             ← 일일 진입점
  .github/workflows/hourly.yml
  .github/workflows/daily.yml
  soo/
    __init__.py
    persona.py                     ← 로거 + Slack 발송 + 페르소나 문구
    auth.py                        ← Google OAuth (refresh token)
    secrets.py                     ← yaml + env 병합 로더
    hero_list.py                   ← Sheet의 라인별 탭 A열에서 히어로 UID 추출
    scrapers/
      musinsa_ranking.py           ← API 페이징 + 브랜드 필터
      musinsa_screenshot.py        ← Playwright PNG + rank N으로 크롭
    storage/
      sheet_archive.py             ← Long / Wide 탭 read·append·멱등성 체크
      drive_uploader.py            ← PNG 업로드 + 공유링크 (Shared Drive 호환)
      screenshots_tab.py           ← (날짜, goods_no) → peak rank + URL 추적
    tasks/
      ranking_hourly.py            ← 시간당 캡처 오케스트레이션
      ranking_daily.py             ← 일일 집계 + Slack 발송 오케스트레이션
```

각 Phase를 진행하기 전에 위 컨텍스트를 항상 염두에 둘 것. Phase 사이에 작성한 파일들은 누적되며, 다음 Phase는 기존 파일을 수정/확장해야 한다.

---

## Phase 1 — 초기 스캐폴딩 + 시간당 캡처 + 일일 리포트

**Commit ref**: `844c3d6 Initial: MUSINSA STANDARD 히어로 봇 (랭킹 추적 자동화)`

```
[Phase 1] 봇의 골격을 만들어줘.

1. requirements.txt 작성:
   slack_sdk, google-api-python-client, google-auth-oauthlib, google-auth-httplib2, PyYAML, requests, playwright

2. config.yaml 작성. 핵심 키:
   - ranking.brand_keywords: ["무신사 스탠다드", "무신사 스탠다드 우먼", "무신사 스탠다드 키즈"]
   - ranking.section_id: 199          (무신사 [전체] 탭)
   - ranking.sub_pan: "product"
   - ranking.top_n: 300               (Phase 6에서 100으로 줄어들 예정)
   - ranking.hero_sheet_id: "..."     (라인별 탭에 히어로 UID 들어있는 Sheet)
   - ranking.archive_sheet_id: "..."  (Long/Wide 탭 들어갈 Sheet)

3. soo/persona.py — 페르소나 + 로거 + Slack 발송.
   - Persona 데이터클래스 (name, tagline, slack_username, slack_icon_emoji)
   - RANKING_BOT = Persona(name="MUSINSA STANDARD 히어로 봇", icon=":superhero:", ...)
   - setup_logger(log_dir, dry_run) — stdout(UTF-8) + file handler, 날짜별 로그.
   - greet/starting_task/step/task_done_ok/task_done_skip/task_failed — 한국어 페르소나 문구.
   - send_slack(message, *, bot_token, target, persona, log, blocks=None)
     - slack_sdk.WebClient.chat_postMessage 사용. blocks 있으면 message는 fallback text.
     - 실패 시 log.error로 사유 남기고 False 리턴 (예외 던지지 말 것).

4. soo/auth.py — Google OAuth refresh_token 플로우.
   SCOPES = drive, spreadsheets, presentations, gmail.compose (4개 다 미리 박아두기).
   get_credentials(credentials_path, token_path):
     - 환경변수 GOOGLE_OAUTH_TOKEN 있으면 거기서 Credentials 만들기.
     - 없으면 token.json 파일에서.
     - expired이면 refresh, 가능하면 token.json에 저장.
     - 둘 다 안 되면 (로컬일 때만) InstalledAppFlow.run_local_server 브라우저 플로우.
   build_services(creds) → {"drive": ..., "sheets": ..., "slides": ..., "gmail": ...}

5. soo/secrets.py — yaml 파일 + 환경변수 병합, env 우선.
   ENV_KEYS = {"slack_bot_token": "SLACK_BOT_TOKEN", "slack_target": "SLACK_TARGET", ...}

6. soo/hero_list.py — 라인별 탭("워셔블수피마","커브드팬츠",...) A열에서 6~10자리 숫자만 정규식으로 추출.
   load_hero_list(sheets_service, sheet_id) → {uid: HeroEntry(uid, line)}.
   각 탭에서 A1:A200 read, 매칭 안 되는 행/빈 행 무시, 충돌 시 첫 등장 유지.

7. soo/scrapers/musinsa_ranking.py — 무신사 비공식 API.
   엔드포인트:
     page 1: https://client.musinsa.com/api/home/web/v5/pans/ranking
             params: storeCode=musinsa, sectionId=199, contentsId="", categoryCode=000,
                     ageBand=AGE_BAND_ALL, gf=A, subPan=product
     page 2+: https://client.musinsa.com/api/home/web/v5/pans/ranking/sections/199
             params: 위에 + period=REALTIME, eventPeriod=BASIC_REALTIME, page=N, offset, startRank
   응답 파싱: data.modules → type=="MULTICOLUMN" → items → type=="PRODUCT_COLUMN".
     image.rank 가 None인 광고 슬롯은 skip. info.brandName, info.productName, onClick.url, id 추출.
   fetch_top(n=300, section_id=199, sub_pan="product") → RankItem 리스트.
   filter_by_brand(items, keywords, mode="exact") — mode="exact"가 기본 (다른 라인 브랜드 안 섞이게).
   User-Agent는 Chrome 데스크톱으로 위장.

8. soo/storage/sheet_archive.py — 두 탭 관리:
   LONG_TAB header: [날짜, 시간, goods_no, 랭킹 순위, 브랜드, 상품명, 히어로여부]
   WIDE_TAB header: [날짜, goods_no, 브랜드, 상품명, 히어로여부, 00:00, 00:30, ..., 23:30]  ← Phase 4에서 24슬롯으로 줄어듬
   _ensure_tab — 없으면 addSheet + header. 헤더 valueInputOption="RAW".
   append_realtime(ts, items, log) — Long에 append.
   read_day_long(target_day) — Long에서 ISO 또는 한국 로케일 ("2026. 5. 1.") 둘 다 매칭.
   append_day_wide(target_day, rows) — by_goods로 묶고 슬롯별 rank 채워서 append.

9. soo/tasks/ranking_hourly.py / ranking_daily.py — 위 모듈을 조립하는 오케스트레이터.
   Hourly: fetch_top → filter_by_brand → hero 매칭 → Long 탭 append.
   Daily: read_day_long → _aggregate (per goods_no peak rank, hours_in_chart) → build_report 텍스트 →
          send_slack → append_day_wide.

10. run_ranking_hourly.py / run_ranking_daily.py — argparse(--dry-run, --as-of) + config 로드.

이 단계에서 봇은 로컬에서 `python run_ranking_hourly.py` 로 돌아가야 한다.
```

---

## Phase 2 — GitHub Actions 마이그레이션

**Commit ref**: `3284e2a Migrate to GitHub Actions: SQLite 제거 + Sheet 직접 적재 + 환경변수 인증`

```
[Phase 2] 자동화: GitHub Actions에서 동작하도록 만들어줘.

1. 원래 SQLite (soo/storage/ranking_db.py) 에 캐싱하던 것을 전면 폐기. Sheet의 Long 탭이 single source of truth가 된다.
   (ranking_db.py 파일 자체는 지워도 되지만, 기존 코드 호환을 위해 read 함수만 남겨도 됨.)

2. soo/auth.py 수정:
   - GOOGLE_OAUTH_CREDENTIALS / GOOGLE_OAUTH_TOKEN 환경변수에서 JSON 문자열 읽기 우선.
   - CI 환경에서 토큰이 invalid이면 명확한 RuntimeError ("로컬에서 새 token.json 발급해서 Secret 갱신해주세요").

3. soo/secrets.py — env가 yaml보다 우선되게.

4. .github/workflows/hourly.yml:
   on:
     schedule:
       - cron: "0 * * * *"           ← Phase 5에서 분산됨
     workflow_dispatch:
   concurrency: hourly-capture (cancel-in-progress=false)
   steps:
     - actions/checkout@v4
     - actions/setup-python@v5 (3.12, cache: pip)
     - pip install -r requirements.txt
     - actions/cache@v4 ~/.cache/ms-playwright   (Phase 8부터 실제로 chromium 설치)
     - env: GOOGLE_OAUTH_CREDENTIALS, GOOGLE_OAUTH_TOKEN
     - run: python run_ranking_hourly.py

5. .github/workflows/daily.yml:
   on:
     schedule:
       - cron: "0 0 * * *"  ← KST 09:00
     workflow_dispatch:
       inputs: as_of, dry_run, force
   env에 SLACK_BOT_TOKEN, SLACK_TARGET 추가.
   args 빌더로 inputs → CLI flags 변환.

이 단계 후 push만 해두면 자동으로 봇이 돈다.
```

---

## Phase 3 — 30분 캡처로 변경 + 리포트 포맷 단순화

**Commit ref**: `2aebb40`

```
[Phase 3] 30분 단위 캡처로 해상도를 올리고, 리포트 포맷을 단순하게 바꿔줘.

1. hourly cron: 0 * * * *  →  0,30 * * * *
2. sheet_archive.WIDE_HEADER 의 슬롯을 48개로 (00:00, 00:30, ..., 23:30).
   ts 정규화도 분=0 or 30 으로 반올림 (replace(minute=0 if m<30 else 30, second=0, microsecond=0)).
3. ranking_daily.build_report의 히어로 라인 포맷:
   "최고 랭킹 #{peak_rank:>3}  {product_name[:42]:<42}  ({HH:MM} 피크)"
   _format_time: 분이 0이면 "{H}시", 아니면 "{H}:{MM}".
```

---

## Phase 4 — 신규 진입 / 급상승 섹션 + 매시간 1회로 회귀

**Commit refs**: `76e8f5d`, `6250f4c`

```
[Phase 4] 리포트에 "전일 대비 급상승 / 신규 진입" 섹션을 추가하고, 캡처 빈도를 매시간 1회로 되돌려.

1. ranking_daily에서 어제 + 그제 rows를 둘 다 read.
   prev_day = target_day - 1day. prev_rows = read_day_long(prev_day).

2. _new_and_jumped(aggregated, prev_aggregated):
   - new_entries: 어제 등장 + 그제 미등장 → peak_rank ASC 정렬.
   - jumped: 양쪽 다 있고 (prev_peak - peak_rank) >= JUMP_THRESHOLD(=50) → 큰 폭부터.

3. 리포트 마지막 섹션 "🚀 *전일 대비 급상승 / 신규 진입* (peak rank 50위 이상 향상)" 추가.
   - 신규: 🆕 #{peak_rank}  {name}  (신규 · {time} 피크)
   - 급상승: 📈 #{peak_rank}  {name}  (전일 #{prev_peak} → +{jump}↑)
   - prev_rows 비었으면 "_전일 데이터 부족 — 비교 불가_".
   - 각 섹션 10개까지만, 초과 시 "_… 외 N개_".

4. 캡처 빈도 매시간 1회로 회귀:
   - WIDE_HEADER 슬롯 24개로 축소 (00:00 ~ 23:00 정각).
   - ts 정규화는 replace(minute=0, second=0, microsecond=0).
   - 리포트 분모 "캡처 N/24회" 로 동기화.
```

---

## Phase 5 — Sheet 링크 임베드 + cron 분산 + 멱등성 + RAW 적재

**Commit refs**: `a2cc844`, `ba9d546`, `af64d7e`, `65d15d1`

> 이 4개 커밋이 안정성 핵심. **하나의 Phase로 묶어서 한 번에 시킨다.**

```
[Phase 5] 봇의 신뢰성을 본격적으로 챙겨줘. (이전까지 cron 누락/중복 발송 사고가 있었음.)

1. config.yaml에 archive_sheet_url 추가 (Long 탭 직링크, gid 포함).
   ranking_daily._title()에서 sheet_url 있으면 Slack mrkdwn 링크로 제목 감싸기:
   "📊 *<{sheet_url}|{date} 무탠다드 랭킹 리포트>* (Top {top_n} 기준)"

2. cron 시간 시프트 — 정각 폭주 회피:
   hourly.yml schedule을 단일 라인에서 다중 라인으로 확장:
     - cron: "3,13,23,33,43,53 * * * *"   ← 분당 여러 trigger로 안전망
   daily.yml schedule:
     - cron: "0 0 * * *"     ← KST 09:00
     - cron: "15 0 * * *"    ← 09:15 안전망
     - cron: "30 0 * * *"
     - cron: "0 1 * * *"     ← 10:00 마지막 안전망

3. 멱등성 (CRITICAL — cron이 여러 번 발사되므로):
   sheet_archive.has_hour_data(ts):
     - Long 탭의 마지막 ~200행만 읽어서 (date, time) == (ts.date, ts.strftime("%H:%M")) 있으면 True.
     - 메타로 rowCount 얻고 max(2, rowCount-200) ~ rowCount 만 읽기.
   ranking_hourly.run() 시작부에서 has_hour_data True면 즉시 skip return.

   sheet_archive.has_day_wide(target_day):
     - Wide 탭 A열에 target_day ISO/로케일 매칭되는 행 있으면 True.
   run_ranking_daily.py에서 has_day_wide True && not --force && not --dry-run 이면
     "이미 발송됨, --force 사용" 로깅 후 exit 0.

4. valueInputOption "USER_ENTERED" → "RAW" 전환 (전 영역):
   _ensure_tab 헤더 작성, append_realtime, _build_wide_rows append, screenshots_tab upsert 등 전부 RAW.
   동시에 read_day_long / has_day_wide의 valueRenderOption은
   "UNFORMATTED_VALUE" + dateTimeRenderOption="FORMATTED_STRING" 로 명시.
   이렇게 해야 ISO 문자열 그대로 라운드트립 + 기존 USER_ENTERED 로 들어간 데이터도
   "YYYY-MM-DD" / "YYYY. M. D." / "YYYY.M.D." 세 가지로 매칭해서 호환.

5. KST 명시: 모든 datetime.now()를 datetime.now(ZoneInfo("Asia/Seoul"))로.
   run_ranking_daily 의 target_day 기본값은 (now KST - 1day).date().

6. ranking_hourly 시간 정규화:
   captured_at = datetime.now(KST)
   ts = captured_at.replace(minute=0, second=0, microsecond=0)
   → 03/13/23/.../53분 어느 trigger든 같은 :00 슬롯으로 정규화되고 멱등성 체크 가능.

7. concurrency: hourly-capture / daily-report (cancel-in-progress=false) 그대로 유지.
```

---

## Phase 6 — Top 300 → 100 + 동적 top_n

**Commit refs**: `358c965`, `bce94fb`

```
[Phase 6] Top 300은 노이즈가 많아. Top 100으로 줄이고, 리포트가 동적 top_n을 따라가게 해줘.

1. config.yaml: ranking.top_n: 300 → 100.
2. ranking_daily.build_report(rows, prev_rows, ..., top_n):
   - 함수 시그니처에 top_n 추가.
   - rows = [r for r in rows if r["rank"] <= top_n]   ← 과도기 호환 (Sheet에 옛날 rank 200+ 데이터가 남아있을 수 있음)
   - prev_rows 도 동일하게 필터.
   - 제목 텍스트의 "(Top 100 기준)" 부분이 top_n 값을 그대로 반영하도록.
3. run_ranking_daily.py에서 cfg["top_n"] 읽어 ranking_daily.run에 전달.
4. run_ranking_hourly.py도 top_n을 fetch_top()에 전달 (이미 그렇게 되어있을 거면 OK).
```

---

## Phase 7 — Top 10 진입 시 스크린샷 → Drive → Slack image_block

**Commit ref**: `b249778`

> 이 Phase가 가장 큰 추가. Playwright + Drive + Screenshots 시트 + Slack blocks 까지 새로 들어옴.

```
[Phase 7] 무탠 상품이 Top 10에 들어간 날에는, 무신사 랭킹 페이지 캡처본을 Slack에 image로 같이 보내줘.

스코프 결정:
- "Top 10 진입" 판단: 그날 매 시간 캡처에서 한 번이라도 rank <= 10 진입한 무탠 상품.
- 같은 (날짜, goods_no) 안에서는 "그날 best peak rank" 일 때만 PNG 교체 (불필요한 캡처 줄임).
- 캡처는 1시간에 1번만 (필요한 상품 묶어서 한 번에 처리).

1. config.yaml 추가:
   ranking.screenshot_threshold: 10
   ranking.screenshot_folder_id: "<Drive 폴더 ID>"   ← 빈 값이면 기능 비활성화
   ranking.screenshot_crop_to_rank: 12               ← Phase 9에서 추가됨

2. requirements.txt에 playwright>=1.40 (이미 들어있으면 OK).

3. soo/scrapers/musinsa_screenshot.py — Playwright PNG.
   _RANKING_URL_TEMPLATE = "https://www.musinsa.com/main/musinsa/ranking?storeCode=musinsa&sectionId={section_id}&categoryCode=000&gf=A&ageBand=AGE_BAND_ALL"
   screenshot_ranking_full_page(section_id=199, timeout_ms=30000, viewport_width=1280, crop_to_rank=12) -> bytes:
     - chromium headless, locale="ko-KR", User-Agent 데스크톱.
     - page.goto(url, wait_until="domcontentloaded") + 2.5s 대기.
     - crop_to_rank 없으면 full_page screenshot return.
     - 있으면:
       1) _TRIGGER_LAZY_LOAD_JS 실행: 0~4000px 400씩 스크롤 → 위로 → 모든 <img> 의 onload 대기.
       2) _FIND_RANK_BOTTOM_JS(rankLimit): a[href*="/products/"] 카드들의 boundingBox top+height를 모아서
          y 정렬, rankLimit번째 카드의 bottom+8px 반환 (못 찾으면 null).
       3) clip={x:0, y:0, width:viewport_width, height:bottom_y} 으로 screenshot.
       4) bottom_y가 None이면 full_page로 fallback.

4. soo/storage/drive_uploader.py:
   upload_png(drive_service, folder_id, filename, image_bytes) -> (image_url, file_id):
     - files().create(body={name, parents:[folder_id]}, media_body=MediaIoBaseUpload(BytesIO,"image/png"), fields="id")
     - permissions().create({role:"reader", type:"anyone"})
     - image_url = f"https://lh3.googleusercontent.com/d/{file_id}"  ← Slack image_block에서 직접 임베드됨
   ensure_subfolder(drive_service, parent_id, name) -> folder_id:
     - 같은 이름 폴더 검색, 없으면 생성. (Daily마다 YYYY-MM-DD 하위 폴더로 정리하기 위함.)

5. soo/storage/screenshots_tab.py — (날짜, goods_no) 단위 peak rank 추적.
   탭 header: [날짜, goods_no, peak_rank, screenshot_url, file_id, captured_at]
   read_day_records(target_day) -> {goods_no: {peak_rank, screenshot_url, file_id, captured_at, row_idx}}
   upsert_record(target_day, goods_no, peak_rank, screenshot_url, file_id, captured_at, log):
     - 같은 (날짜, goods_no) 있으면 row_idx 위치에 update, 없으면 append.
     - 호출자가 "기존 peak_rank 보다 좋다"를 사전 검증해서 호출하는 책임 분리.

6. soo/tasks/ranking_hourly.py 에 _maybe_capture_screenshot 추가:
   - candidates = [it for it in matched if it.rank <= threshold]
   - existing = screenshots_tab.read_day_records(today)
   - needs_update = candidates 중 (기존 없거나 it.rank < existing[gn].peak_rank) 인 것.
   - needs_update 비어있으면 "skip" 로그 후 0 return.
   - 비어있지 않으면:
     1) musinsa_screenshot.screenshot_ranking_full_page() 1회 호출 → PNG bytes.
     2) drive_uploader.ensure_subfolder(folder_id, "YYYY-MM-DD").
     3) drive_uploader.upload_png(day_folder, "ranking_YYYYMMDD_HHMMSS.png", png).
     4) needs_update의 각 항목에 대해 upsert_record (모두 같은 URL/file_id 공유).
   - 캡처/업로드/upsert 각 단계에 try/except — 한 단계 실패해도 다른 상품 처리 계속.

7. soo/tasks/ranking_daily.py 에 _send_screenshots 추가 (본문 Slack 보낸 직후 실행):
   - records = screenshots_tab.read_day_records(target_day). 비어있으면 0 return.
   - peak_rank ASC 정렬.
   - Slack Block Kit blocks 구성:
     [{type:"header", text:{type:"plain_text", text:"📸 Top 10 진입 스크린샷"}}]
     각 record마다:
       {type:"section", text:{type:"mrkdwn", text:f"*#{peak_rank}*  {name[:60]}"}}
       {type:"image", image_url: record.screenshot_url, alt_text: name[:100] or gn}
   - Slack 50 block 제한 → 48개씩 청크로 분할 발송.
   - persona.send_slack(fallback_text="📸 스크린샷", ..., blocks=chunk).

8. workflow hourly.yml:
   - actions/cache@v4 ~/.cache/ms-playwright (key: playwright-chromium-${{ runner.os }}-v1)
   - "python -m playwright install --with-deps chromium" step 추가.
   - timeout-minutes: 8.

9. run_ranking_hourly.py:
   - cfg에서 screenshot_threshold / screenshot_folder_id / screenshot_crop_to_rank 읽기.
   - drive_service, screenshot_* 인자로 ranking_hourly.run() 호출.
   - screenshot_folder_id가 빈 문자열이면 ranking_hourly 내부에서 비활성화 처리.
```

---

## Phase 8 — Shared Drive 호환

**Commit ref**: `58ab965 Drive API 호출에 supportsAllDrives=True 추가 (Shared Drive 호환)`

```
[Phase 8] 운영팀이 Drive 폴더를 Shared Drive (공유 드라이브) 안으로 옮겼는데, 404 NotFound가 떠. 고쳐줘.

원인: Workspace의 Shared Drive에 있는 파일/폴더는 기본 API 호출에 안 보임.

soo/storage/drive_uploader.py 의 모든 Drive API 호출에 추가:
  - files().create(...): supportsAllDrives=True
  - permissions().create(...): supportsAllDrives=True
  - files().list(...) (ensure_subfolder 안에서): supportsAllDrives=True + includeItemsFromAllDrives=True

이 한 줄들이 누락되면 Shared Drive 폴더에 업로드하는 순간 404로 죽음.
```

---

## Phase 9 — 스크린샷 하단 크롭 (rank 12까지만)

**Commit ref**: `76faff9 스크린샷 하단 크롭 — rank 12까지만 보이도록 자름`

```
[Phase 9] 스크린샷 full_page는 페이지가 너무 길어서 Slack에서 가독성이 떨어져. Top 12 (= 데스크톱 2줄)까지만 보이게 잘라줘.

1. config.yaml: ranking.screenshot_crop_to_rank: 12 (null/0이면 풀페이지 fallback).

2. musinsa_screenshot.screenshot_ranking_full_page() 에 crop_to_rank 파라미터.
   crop_to_rank가 None/0이면 page.screenshot(full_page=True) 그대로 반환.
   아니면:
   - page.evaluate(_TRIGGER_LAZY_LOAD_JS) — 0~4000px 스크롤로 lazy-load 카드 모두 그려지게.
   - clip_height = page.evaluate(_FIND_RANK_BOTTOM_JS, crop_to_rank) — rank N번째 카드 bottom Y px.
   - clip_height 없으면 full_page fallback.
   - 있으면 page.screenshot(full_page=True, clip={x:0,y:0,width:viewport_width,height:clip_height}).

3. run_ranking_hourly.py 에서 cfg["screenshot_crop_to_rank"]를 ranking_hourly.run()으로 패스.
   ranking_hourly._maybe_capture_screenshot 가 그대로 screenshot_ranking_full_page에 전달.
```

---

## Phase 10 — 운영 안정화 (선택)

```
[Phase 10] 운영 중에 발견한 잔잔한 개선들 — 같이 묶어서 한 번에 처리해줘.

A. cron-job.org 같은 외부 스케줄러 트리거를 추가 안전망으로 두려면 workflow에 workflow_dispatch가 이미 있으니
   GitHub REST API (POST /repos/{owner}/{repo}/actions/workflows/hourly.yml/dispatches) 로 호출하면 됨.
   PAT만 있으면 별도 코드 변경 불요.

B. screenshot_threshold를 일시 50으로 올리고 운영 확인 → 다시 10으로 복귀 같은 운영 토글이 잦으니,
   config.yaml 한 줄만 PR로 바꿔도 다음 hourly 실행부터 즉시 적용되도록 (이미 그렇게 동작함).

C. logs 디렉토리는 .gitignore에 포함. 로컬 디버깅용.
```

---

## 부록 A — 최초 setup 체크리스트 (코드 외 작업)

1. **Google Cloud Console**
   - OAuth Client ID (Desktop type) 발급 → credentials.json.
   - 로컬에서 `python run_ranking_hourly.py --dry-run` 한 번 돌려 브라우저 플로우 통과 → token.json 생성.
   - credentials.json / token.json 의 내용을 각각 GitHub Secret `GOOGLE_OAUTH_CREDENTIALS` / `GOOGLE_OAUTH_TOKEN` 에 JSON 그대로 저장.

2. **Slack App**
   - Bot Token Scopes: `chat:write`, `chat:write.customize`, (필요 시 `chat:write.public`).
   - 봇을 대상 채널에 invite.
   - Bot Token → GitHub Secret `SLACK_BOT_TOKEN`.
   - 대상 채널 ID(`C0XXX...`) → GitHub Secret `SLACK_TARGET`.

3. **Google Sheet**
   - hero_sheet_id: 라인별 탭("워셔블수피마", ... ) A열에 히어로 상품 UID(숫자) 채워둔다.
   - archive_sheet_id: 빈 시트. Long/Wide/Screenshots 탭은 봇이 자동 생성.

4. **Google Drive**
   - 스크린샷 업로드용 폴더 하나 만들고 ID 추출 → config.yaml `screenshot_folder_id`.
   - Shared Drive 안에 둔다면 OAuth 계정이 해당 Shared Drive 멤버여야 함.

5. **GitHub**
   - 위 Secrets 5개 등록 후 Actions 탭에서 workflow_dispatch로 hourly/daily 한 번씩 손으로 실행해서 결과 확인.

---

## 부록 B — 자주 부딪힌 함정들 (반드시 알고 시작하기)

| 함정 | 원인 | 해결 |
|---|---|---|
| `read_day_long`가 0행 반환 | Sheet가 날짜를 로케일 포맷("2026. 5. 1.")으로 저장 | `valueInputOption="RAW"`로 적재 + read 시 ISO/로케일 모두 매칭 |
| 같은 날 리포트가 2~3번 발송됨 | daily cron 4개 안전망이 모두 동작 | `has_day_wide` 멱등성 체크 + `concurrency` 그룹 |
| 같은 시간 Long 탭에 행이 7배 중복 | hourly cron 6개 안전망이 모두 동작 | `has_hour_data` 멱등성 체크 (마지막 200행 스캔) + ts를 :00 슬롯으로 정규화 |
| Drive 업로드 404 NotFound | 폴더가 Shared Drive 안에 있음 | 모든 Drive API 호출에 `supportsAllDrives=True` (+ list에는 `includeItemsFromAllDrives=True`) |
| Slack 이미지가 안 보임 | Drive 공유 권한 누락 | upload 직후 `permissions.create({role:"reader", type:"anyone"})` |
| 스크린샷이 빈 페이지 | 카드 lazy-load 미완료 | goto 후 2.5s 대기 + 0~4000px 스크롤 JS + 모든 `<img>.onload` 대기 |
| 스크린샷이 너무 길어서 Slack에서 안 읽힘 | full_page | `crop_to_rank=12` 로 rank N번째 카드 bottom까지만 clip |
| GitHub Actions에서 token refresh 실패 | refresh_token 만료/회수 | 로컬에서 새 token.json 발급해서 `GOOGLE_OAUTH_TOKEN` Secret 재등록 |
| 다른 라인 상품이 섞임 ("무신사 스탠다드 스포츠" 등) | brand 매칭이 substring | `filter_by_brand(mode="exact")` 기본값 유지 |

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

이 문서를 그대로 Claude/Cursor/Aider에게 Phase 단위로 던지면, 동일한 봇이 같은 구조·같은 안정성 보장으로 재현된다. Phase 5는 한 번에 묶지 말고 4개 커밋 단위로 쪼개도 되지만, 멱등성·RAW·KST는 **반드시 함께** 가야 한다 — 셋 중 하나라도 빠지면 시간/날짜 비교가 깨진다.
