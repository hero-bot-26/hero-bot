"""히어로 상품 UID 리스트를 Google Sheet(시즌 대시보드)에서 동적으로 로드.

라인별 탭들 (커브드팬츠, 라이트다운, ...) 의 A열에서 6~10자리 숫자만 추출 → 합집합.
숨김(그룹화) 행 포함 — Sheets API는 hide 상태 무관하게 전체 데이터 반환.

★ 탭 목록은 **하드코딩하지 않는다**. 시즌이 바뀌면 라인업이 통째로 바뀌는데
   (26SS 워셔블수피마·쿨탠다드… → 26FW 커브드·라이트다운·헤비다운…) 상수로 박아두면
   시즌 전환 때마다 조용히 옛 리스트로 감시하게 된다(실제로 밟음). 대신 대시보드 구조에서
   자동 판별한다:
     - 보이는(hidden=False) 탭만  → 지난 시즌 탭은 숨김 처리돼 있다
     - A열에 'HERO' 구분 마커가 2개 이상 → 라인 탭. 집계/유틸 탭(YTD·MTD·잔여재고…)은 0~1개.
   config.yaml 의 ranking.hero_line_tabs 로 강제 지정도 가능(자동 판별 우회).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_UID_RE = re.compile(r"^\d{6,10}$")
# A열 구분 마커: 'HERO' / 'HERO SUB' (★주연·통합 UID 벌은 마커가 다르지만 UID는 같이 긁는다)
_HERO_MARK_RE = re.compile(r"^HERO\b")
# 라인 탭으로 인정할 최소 HERO 마커 수 — '준비물량(SKU)' 같은 유틸 탭이 헤더로 1개 갖고 있다
_MIN_HERO_MARKS = 2


@dataclass
class HeroEntry:
    uid: str
    line: str  # 어느 탭에서 왔는지


def _visible_tabs(sheets_service, sheet_id: str) -> list[str]:
    meta = sheets_service.spreadsheets().get(
        spreadsheetId=sheet_id,
        fields="sheets(properties(title,hidden))",
    ).execute()
    return [
        s["properties"]["title"]
        for s in meta.get("sheets", [])
        if not s["properties"].get("hidden")
    ]


def load_hero_list(
    sheets_service,
    sheet_id: str,
    line_tabs: Iterable[str] | None = None,
    a_range: str = "A1:A400",
    log=None,
) -> dict[str, HeroEntry]:
    """라인 탭 A열에서 UID 추출. {uid: HeroEntry} (라인 충돌 시 첫 등장 유지).

    line_tabs=None 이면 시트 구조에서 라인 탭을 자동 판별한다.
    A열 읽기는 batchGet 1회 — 탭별 get 은 429(rate limit) 를 부른다.
    """
    tabs = list(line_tabs) if line_tabs else None
    if tabs is None:
        try:
            tabs = _visible_tabs(sheets_service, sheet_id)
        except Exception as e:
            if log:
                log.error(f"히어로 시트 탭 목록 조회 실패: {e}")
            return {}
        autodetect = True
    else:
        autodetect = False

    if not tabs:
        return {}

    try:
        resp = sheets_service.spreadsheets().values().batchGet(
            spreadsheetId=sheet_id,
            ranges=[f"'{t}'!{a_range}" for t in tabs],
        ).execute()
    except Exception as e:
        if log:
            log.error(f"히어로 시트 A열 조회 실패: {e}")
        return {}

    out: dict[str, HeroEntry] = {}
    used: list[str] = []
    for tab, vr in zip(tabs, resp.get("valueRanges", [])):
        col = [(row[0] or "").strip() if row else "" for row in vr.get("values", [])]
        if autodetect:
            marks = sum(1 for v in col if _HERO_MARK_RE.match(v))
            if marks < _MIN_HERO_MARKS:
                continue  # 집계/유틸 탭
        used.append(tab)
        for v in col:
            if _UID_RE.match(v) and v not in out:
                out[v] = HeroEntry(uid=v, line=tab)

    if log:
        log.info(f"히어로 라인 탭 {len(used)}개 — {', '.join(used)}")
    return out
