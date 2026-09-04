# -*- coding: utf-8 -*-
"""소스 레지스트리 — 생성기가 하드코딩 ID 대신 앱의 `_소스설정` 탭을 읽어 동적 로드.

담당자가 앱 IMC "소스" 탭에서 원천 스프레드시트 링크만 갈아끼우면(권한·헤더 검증 후 저장)
다음 자동 갱신부터 그 시트를 기준으로 데이터를 읽는다. 스펙: hero-master-app/docs/source-registry.md

핵심 안전 규칙:
  1. 레지스트리 탭이 없거나(=아직 아무도 저장 안 함) 읽기 실패해도 절대 예외를 던지지 않음 → {} 반환.
  2. resolve()는 레지스트리에 항목이 없으면 DEFAULTS(현재 하드코딩 값)로 폴백 → 마이그레이션 중 무변화.
  3. 스키마 불일치/접근 실패 시 '그 소스만' 스킵하고 이전 값 유지(호출부 가드가 담당). 절대 0/빈값으로 덮지 않음.

즉 레지스트리가 비어 있는 현재는 출력이 기존과 동일하며, 링크가 채워지는 순간부터 그 소스만 바뀐다.
"""
from __future__ import annotations
import re

APP_SHEET_ID = "1_tZDl-heZyWT4VQYIAT3ZHFeMoQlK2FSOpEMyZjqvm0"  # 앱 시트(레지스트리 호스트)
REG_TAB = "_소스설정"

# 소스키(불변) → 현재 하드코딩 기본값. 레지스트리에 항목이 없을 때의 안전망.
# id 는 _gen_26fw_heroes.py / imc_triggers.py / sales_rollup.py / baseline_ingest.py 의 현재 상수와 일치해야 함.
DEFAULTS = {
    "imc_calendar": {  # IMC 캘린더 액션 (SNS/CRM 통합 관리 시트의 2)일정·5)온사이트·6)PR·4)IG광고)
        "id": "11f6JTGvms3uVcuVJW-M9Wa9-Lt4x3Tjn5IFJ2m8jifE", "tab": "2)일정", "range": "", "expected": []},
    # ★sns_perf / crm_perf 는 2026-08-15 제거 — SNS/CRM 채널 성과가 앱 화면에 렌더된 적이 없어 수집 폐지.
    "budget": {        # 월 예산 (PMKT/CRM 예산)
        "id": "11f6JTGvms3uVcuVJW-M9Wa9-Lt4x3Tjn5IFJ2m8jifE", "tab": "PMKT/CRM 예산", "range": "", "expected": []},
    "dashboard": {     # 실적 대시보드 (Databricks 잡이 채우는 전용 시트, raw/PMKT 탭)
        "id": "1iHH2qG8Uj5vmlC3aXkey96usktWODmguDPD_ToT2rfA", "tab": "", "range": "", "expected": []},
    "pdp_daily": {     # PDP 일별 유입 — 원천이 웨어하우스 뷰라 시트 ID 없음(#2에서 별도 처리)
        "id": "", "tab": "", "range": "", "expected": []},
    "mstrd": {         # 상품MAP 발매 (★MSTRD_26FW 상품MAP: HERO STY·SKU·발매스케줄)
        "id": "1tvtbz6u3xob_SkZQBH79xX6J8dRpsHAa1-nn-KMeY-g", "tab": "HERO STY", "range": "", "expected": []},
    "target_26fw": {   # 26FW 히어로 일자별 목표(.xlsx, `일자별 목표 셋팅` 탭) — 달성율·소진율 소스
        "id": "1CB10ouLsOZplJuPoSOkkAXhvzwgtR0zD", "tab": "일자별 목표 셋팅", "range": "", "expected": []},
    "plm_milestone": { # ★ PLM 마일스톤 실적일 (DBX 잡 출력 시트의 `데이터` 탭) — 26FW 단계 진척
        # ★2026-09-04 레지스트리화. 27SS 를 담는 새 시트로 갈아탈 때 **코드 배포 없이** 행만 바꾸면 된다.
        #   ※ 같은 책의 `HERO_STY` 탭(MSTRD 미러)과 `_소스신선도` 탭은 **여기서 안 옮긴다** —
        #     용도가 달라 plm_ingest.DBX_SHEET_ID 에 고정돼 있다(아래 _gen 주석 참조).
        "id": "1_tZDl-heZyWT4VQYIAT3ZHFeMoQlK2FSOpEMyZjqvm0", "tab": "데이터", "range": "",
        "expected": ["시즌", "style_no", "style_status", "스타일생성", "컬러확정", "입고"]},
    "plm_milestone_extra": {  # ★ PLM 원본 붙여넣기 시트 — 주 원천에 **없는 시즌**을 보충(현재 27SS)
        # ★2026-09-04 신설. 주 원천(DBX 경유본)은 26FW 만 담고 있어 27SS 단계가 통째로 비어 있었다.
        #   ⚠보충일 뿐 대체가 아니다 — 이 시트엔 **담당자 4열이 전부 비어 있고 입고 실적(A:)이 0건**
        #   (PLM 은 WMS 입고를 모른다). 겹치는 스타일은 **주 원천이 이긴다**.
        #   비우면(id 공란) 보충 없이 종전 동작.
        "id": "1Cv-upIFHYIkUom__1bf9VGdNJ-CblZ0ZvvK1CJv2zx4", "tab": "시트1", "range": "",
        "expected": ["시즌", "스타일 코드", "스타일 생성", "컬러 확정"]},
    "plm_27ss": {      # 27SS 기획 관리판 (#.상세일정) — 홈 카드 SEASON_27SS_PROGRESS
        "id": "10guWc_5t06nu9QryPymTIl2oogQfV4qOEO81iXSgenI", "tab": "#.상세일정", "range": "", "expected": []},
    "plm_27ss_req": {  # ★ MS_27SS_작업의뢰 '기획시트' — 27SS 보드 단계 진척·타겟일(STY_SCHED_27SS)
        # ★2026-08-26 파일 교체 — 담당자가 새 파일("★ MS_27SS_작업의뢰(링크변경)")로 갈아탔는데
        #   여기가 옛 파일을 물고 있어 27SS 히어로가 35개에서 멈춰 있었다(신본 85개).
        "id": "1IvQHP-93wxK_WMBEevVcnwyEa-QM529VoB544ojpxD0", "tab": "기획시트", "range": "",
        "expected": ["신품번", "판매 시즌", "컬러 확정", "원단 확정", "PO 발행 (작지투입)",
                     "테크팩 확정 (APP)", "입고 완료", "MD입고 목표일", "APP 목표"]},
}


def _parse_id(url: str) -> str:
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url or "")
    return m.group(1) if m else (url or "")


def load_registry(sheets) -> dict:
    """`_소스설정` → { 소스키: {id, tab, range, expected[]} }. 마지막(최신) 행이 유효.

    탭이 없거나 읽기 실패하면 {} 반환(절대 예외 안 던짐). sheets = googleapiclient sheets service.
    """
    try:
        vals = sheets.spreadsheets().values().get(
            spreadsheetId=APP_SHEET_ID, range=f"'{REG_TAB}'!A2:J"
        ).execute().get("values", [])
    except Exception:
        return {}
    reg = {}
    for r in vals:
        if not r or not r[0]:
            continue
        key = str(r[0]).strip()
        _get = lambda i: (r[i] if len(r) > i and r[i] is not None else "")
        sid = str(_get(3)).strip() or _parse_id(str(_get(2)))  # D열 파싱ID 우선, 없으면 C열 링크 재파싱
        reg[key] = {  # append 방식: 같은 키가 여러 행이면 마지막이 최종
            "id": sid,
            "tab": str(_get(4)).strip(),
            "range": str(_get(5)).strip(),
            "expected": [h.strip() for h in str(_get(6)).split(",") if h.strip()],
        }
    return reg


def resolve(key: str, reg: dict) -> dict:
    """레지스트리 우선, 없으면 DEFAULTS. 레지스트리 행에 id가 비면(담당자 실수) DEFAULTS로 폴백."""
    r = (reg or {}).get(key)
    if r and r.get("id"):
        return r
    return DEFAULTS.get(key, {"id": "", "tab": "", "range": "", "expected": []})


def source_id(key: str, reg: dict) -> str:
    return resolve(key, reg).get("id") or ""


def describe(reg: dict) -> list[str]:
    """각 소스키가 레지스트리(담당자 설정)에서 왔는지 DEFAULT인지 로그용 요약. 안전규칙 #3(로그 남기기)."""
    out = []
    for k in DEFAULTS:
        r = (reg or {}).get(k)
        src = "레지스트리" if (r and r.get("id")) else "기본값"
        out.append(f"{k}={source_id(k, reg) or '(없음)'} [{src}]")
    return out
