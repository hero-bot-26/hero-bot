"""히어로 상품 UID 리스트를 Google Sheet에서 동적으로 로드.

라인별 탭들 (워셔블수피마, 커브드팬츠, ...) 의 A열에서 6~10자리 숫자만 추출 → 합집합.
숨김(그룹화) 행 포함 — Sheets API는 hide 상태 무관하게 전체 데이터 반환.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# 라인별 탭 — 집계/유틸 탭은 제외
DEFAULT_LINE_TABS = [
    "워셔블수피마",
    "커브드팬츠",
    "윈드브레이커",
    "심리스브라",
    "NEW 티셔츠",
    "쿨탠다드티셔츠",
    "쿨탠다드팬츠",
]

_UID_RE = re.compile(r"^\d{6,10}$")


@dataclass
class HeroEntry:
    uid: str
    line: str  # 어느 탭에서 왔는지


def load_hero_list(
    sheets_service,
    sheet_id: str,
    line_tabs: Iterable[str] = DEFAULT_LINE_TABS,
    a_range: str = "A1:A200",
) -> dict[str, HeroEntry]:
    """라인별 탭 A열에서 UID 추출. {uid: HeroEntry} (라인 충돌 시 첫 등장 유지).

    a_range 는 각 탭에서 A열 어디까지 읽을지. 200행이면 충분.
    """
    out: dict[str, HeroEntry] = {}
    for tab in line_tabs:
        try:
            resp = sheets_service.spreadsheets().values().get(
                spreadsheetId=sheet_id,
                range=f"'{tab}'!{a_range}",
            ).execute()
        except Exception:
            continue
        for row in resp.get("values", []):
            if not row:
                continue
            v = (row[0] or "").strip()
            if _UID_RE.match(v) and v not in out:
                out[v] = HeroEntry(uid=v, line=tab)
    return out
