# PDP일별 셀 경량화 패치 (2026-08-07)

## 왜

`PDP일별` 탭이 **156,144행 × 4열**. 두 번째로 큰 탭(`PMKT경로기간` 30,268행)의 5배다.
매일 90일 창을 통째로 다시 조회하고 `ws.clear()` 후 전량 재기록한다. 실제로 바뀌는 건 하루치뿐인데.

이 셀이 노트북 마지막이라 항상 제일 늦게 끝나고, 그 탓에 앱 갱신 CI가 **매일** 4시간 대기를
꽉 채우고 타임아웃했다(8/3·8/4·8/5 로그: `아직 1개 탭이 미갱신(PDP일별)` → `타임아웃`).
결과적으로 실적·PMKT가 아침 런이 아니라 저녁 폴백 런에나 반영됐다.

CI 쪽은 이미 조치했다(`wait_sheet_fresh.WAIT_SKIP_TABS` + 생성기 `_FRESH_PDPD` 분리).
아래는 **원인 자체**를 줄이는 노트북 패치다.

## 무엇을 바꾸나

생성기는 이 데이터를 받자마자 **히어로 단위로 롤업**한다(`_gen_26fw_heroes.py` 의 `_pdp_by`).
goods 단위 입도는 히어로 매핑을 해석할 때만 쓰이고, 그마저 `_hero_of_fw(style, goods)` 가
**style 우선 / goods 폴백**이다. 즉 goods x date 로 보낼 이유가 없다.

→ `GROUP BY dt, style_no` 로 올린다. style_no 가 비는 행만 goods_no 로 떨어지게 `COALESCE` 로
   키를 잡아, 매핑 폴백 경로도 그대로 산다.

예상 행수: 90일 × 스타일 수(≈180) ≈ **16,000행 (약 1/10)**.
쓰기량이 1/10이 되므로 gspread `update` 시간도 그만큼 줄고, 429 위험도 함께 준다.

## 패치 (노트북 `(4) 일별 유입` 셀)

### Before

```python
pdp_daily_query = f"""
SELECT TO_DATE(p.dt,'yyyyMMdd') AS date, CAST(p.goods_no AS STRING) AS goods_no,
       ANY_VALUE(m.style_no) AS style_no, SUM(p.pdp_uv_cnt) AS pdp_uv
{_PMKT_FROM.rstrip()}
    AND p.dt BETWEEN '{_pdpd_start}' AND '{date}'
GROUP BY p.dt, CAST(p.goods_no AS STRING)
HAVING SUM(p.pdp_uv_cnt) > 0
ORDER BY date, goods_no
"""
```

### After

```python
# ★2026-08-07 경량화: goods x date → **style x date** 집계.
#   생성기가 어차피 히어로로 롤업하고(goods 단위를 쓰지 않는다), 히어로 해석은
#   _hero_of_fw(style, goods) 가 style 우선·goods 폴백이라 style 키로 충분하다.
#   style_no 가 비는 행은 goods_no 를 키로 써서 폴백 경로를 그대로 살린다.
#   156,144행 → 약 16,000행(1/10). 이 셀이 노트북에서 제일 무겁고 마지막이라
#   CI 대기(4h 타임아웃)의 단독 원인이었다.
pdp_daily_query = f"""
SELECT TO_DATE(p.dt,'yyyyMMdd') AS date,
       COALESCE(NULLIF(TRIM(m.style_no),''), CAST(p.goods_no AS STRING)) AS style_no,
       ANY_VALUE(CAST(p.goods_no AS STRING)) AS goods_no,
       SUM(p.pdp_uv_cnt) AS pdp_uv
{_PMKT_FROM.rstrip()}
    AND p.dt BETWEEN '{_pdpd_start}' AND '{date}'
GROUP BY TO_DATE(p.dt,'yyyyMMdd'),
         COALESCE(NULLIF(TRIM(m.style_no),''), CAST(p.goods_no AS STRING))
HAVING SUM(p.pdp_uv_cnt) > 0
ORDER BY date, style_no
"""
```

컬럼 이름·개수는 그대로(`date, style_no, goods_no, pdp_uv`)라 **생성기는 무수정으로 동작한다**.
컬럼 순서만 바뀌는데 생성기는 헤더명으로 읽으므로 무관하다.

## 적용 시 확인할 것

1. **셀 주입 후 export 를 되읽어 셀별 `ast.parse`** — 2026-08-04 에 셀 하나 문법오류로
   잡이 통째로 죽고 뒤 셀이 전부 안 돌았다(그날 사고 재발 방지).
2. 실행 후 `[OK] PDP일별: N rows x 4 cols` 의 N 이 **2만 미만**인지.
3. 앱 성과탭 'PDP 일별 유입 트렌드' 곡선이 이전과 같은 모양인지(히어로별 합계는 불변이어야 함).
   생성기 로그 `PDP일별 주입: 90일 · 히어로 N` 의 히어로 수가 줄지 않았는지.

## 더 줄이려면 (2단계, 별건)

증분 적재 — 어제 하루치만 append 하고 90일 넘은 앞부분만 잘라내면 쓰기량이 다시 1/90이 된다.
다만 `insert_query_result` 가 전 탭 공용(clear+전량쓰기)이라 PDP일별 전용 경로를 따로 만들어야 하고,
탭이 한 번 깨지면 복구가 번거롭다. 1단계로 10배를 먼저 확보하고, 그래도 부족하면 착수 권장.
