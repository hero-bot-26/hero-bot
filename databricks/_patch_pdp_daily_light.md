# PDP일별 셀 경량화 — 적용 완료 (2026-08-07)

노트북 `/Users/sooyoung.moon@musinsa.com/히어로 마스터 앱_실적` 셀 18 `(4) 일별 유입`.
백업 = `_nb_backup_20260807_pre_pdpd.py` · 적용본 = `_nb_patched_20260807.py`.

## 왜

`PDP일별` 탭이 **156,142행 × 4열**. 2위 `PMKT경로기간`(30,268)의 5배다.
이 셀이 노트북 마지막이라 늘 제일 늦게 끝났고, 그 탓에 앱 갱신 CI가 **매일** 4시간 대기를
꽉 채우고 타임아웃 → 실적·PMKT가 아침 런이 아니라 저녁 폴백 런에나 반영됐다
(8/3·8/4·8/5 로그: `아직 1개 탭이 미갱신(PDP일별)` → `타임아웃`).

CI 쪽은 별도 조치함(`wait_sheet_fresh.WAIT_SKIP_TABS` + 생성기 `_FRESH_PDPD` 분리, `0a66d3f`).
이 문서는 **원인 자체**를 줄인 노트북 패치 기록.

## 무엇을 바꿨나 — goods 단위 → base 품번 단위 집계

★처음엔 `m.style_no` 로 묶으면 될 줄 알았는데 **틀렸다.** `m.style_no` 는 `MM0TS4Z05-NB` 처럼
**품번-컬러(SKU)** 라 goods 와 거의 1:1 이었다(goods 1,007 vs style_no 1,005 → 절감 0).

소비처 둘 다 실제로는 **base 품번**만 쓴다:
- 생성기 `_hero_of_fw`: `_fw2h_sty.get(style.split("-")[0])`
- `weekly_review.py:748`: `str(x[2]).split('-')[0] in stys`

→ `SPLIT(m.style_no,'-')[0]` 로 접으면 **164종**. 실측 156,142행 → **25,207행(84%↓)**.

```python
_pdpd_key = "COALESCE(NULLIF(SPLIT(m.style_no,'-')[0],''), CAST(p.goods_no AS STRING))"
pdp_daily_query = f"""
SELECT TO_DATE(p.dt,'yyyyMMdd') AS date,
       ANY_VALUE(CAST(p.goods_no AS STRING)) AS goods_no,
       {_pdpd_key} AS style_no,
       SUM(p.pdp_uv_cnt) AS pdp_uv
{_PMKT_FROM.rstrip()}
    AND p.dt BETWEEN '{_pdpd_start}' AND '{date}'
GROUP BY TO_DATE(p.dt,'yyyyMMdd'), {_pdpd_key}
HAVING SUM(p.pdp_uv_cnt) > 0
ORDER BY date, style_no
"""
```

## 지킨 제약

- **컬럼 순서 `date, goods_no, style_no, pdp_uv` 고정** — `weekly_review` 가 위치(`x[2]`·`x[3]`)로
  읽는다. 이름으로 읽는 생성기와 달리 순서를 바꾸면 조용히 깨진다.
- **창(시즌 2/1~) 유지** — 2026-07-31 사용자 요청으로 90일에서 확장한 것. 줄이지 않았다.
- `style_no` 가 비면 `goods_no` 를 키로 → 생성기의 goods 폴백 경로 유지.
- 라벨 끝 `{date}`(yyyyMMdd) 유지 — `check_freshness` 가 마지막 8자리로 기준일을 읽는다.

## 검증 (전부 실측)

| 항목 | 결과 |
|---|---|
| 패치 전후 셀별 `ast.parse` 오류 집합 | 동일(`{4: unexpected indent}` = 원본부터 있던 것) |
| import 후 **되읽어** 재파싱 | 통과 · 19셀 |
| **UV 총량 보존** (8/1~8/5, 실 goods 1,007) | **346,883 = 346,883 완전 일치** |
| 행수 (같은 5일 창) | 4,528 → 778 (1/5.8) |

★`ast.parse` 검사 기준은 "오류 0"이 아니라 **"패치 전후 오류 집합이 같은가"** — 셀 4는 들여쓰기가
섞여 있어 `textwrap.dedent` 로 못 벗기는 원본 오탐이다. 여기서 멈추면 안 된다.

## 남은 것 (2단계, 별건)

증분 append — 어제 하루치만 붙이고 시즌 시작 이전만 잘라내면 쓰기량이 다시 1/186.
다만 `insert_query_result` 가 전 탭 공용(clear+전량쓰기)이라 전용 경로가 필요하고,
원천이 뒤늦게 보정되는 날의 과거분 반영이 빠진다. 84% 줄인 뒤 실제 소요를 보고 판단할 것.

## 다음 실행 때 볼 것

1. `[OK] PDP일별: N rows x 4 cols` 의 N 이 **3만 미만**인지.
2. 생성기 로그 `PDP일별 주입: N일 · 히어로 N` 의 히어로 수가 줄지 않았는지.
3. 앱 성과탭 'PDP 일별 유입 트렌드' 곡선 모양이 이전과 같은지(총량 불변이어야 함).
