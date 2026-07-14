"""트렌드 레이더 — 무신사 Top500 시장 트렌드 캡처 + 델타 계산.

히어로 랭킹봇(무탠만 추적)과 별개 라인. 목적: 전사가 "요즘 뭐가 잘 팔리나"
감을 매일 자동으로 잡게 한다. 무탠 필터 없이 전 브랜드 Top500을 본다.

파이프라인:
  capture()      — Top500 × 3뷰(전체/남자/여자) fetch → 스냅샷 저장 → prune
  compute_movers — 어제(또는 지난주) 스냅샷 대비 급상승/신규 진입 산출
  (키워드 태깅·서술·Slack 발행은 상위 태스크에서 LLM 붙여 처리)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from soo import VIEWS, persona
from soo.scrapers.musinsa_ranking import fetch_top
from soo.storage import trend_snapshot


@dataclass
class Mover:
    """급상승 또는 신규 진입 상품."""
    goods_no: str
    brand: str
    name: str
    rank: int            # 오늘 순위
    prev_rank: int | None  # 이전 순위 (None = 신규 진입)
    img: str = ""
    price: int | None = None

    @property
    def is_new(self) -> bool:
        return self.prev_rank is None

    @property
    def jump(self) -> int:
        """상승 폭(양수 = 오름). 신규는 진입 순위 기반 근사."""
        if self.prev_rank is None:
            return 501 - self.rank
        return self.prev_rank - self.rank


def capture(
    *,
    top_n: int,
    section_id: int,
    sub_pan: str | None,
    log: logging.Logger,
) -> dict[str, list]:
    """Top N × 3뷰 fetch. {뷰표기: [RankItem]} 반환 (무탠 필터 없음)."""
    views: dict[str, list] = {}
    for gf, label in VIEWS:
        items = fetch_top(n=top_n, section_id=section_id, sub_pan=sub_pan, gf=gf)
        views[label] = items
        log.info(persona.step(f"[{label}] Top{top_n} fetch — {len(items)}개"))
    return views


def compute_movers(
    today_items: list,
    baseline_map: dict[str, int],
    *,
    jump_threshold: int = 30,
    top_focus: int = 300,
    max_new: int = 10,
    max_risers: int = 10,
) -> tuple[list[Mover], list[Mover]]:
    """오늘 vs 베이스라인 → (신규 진입, 급상승).

    - 신규: 베이스라인에 없던 goods_no 중 오늘 top_focus 안에 든 것.
    - 급상승: 둘 다 있고 (prev_rank - rank) >= jump_threshold.
    베이스라인이 비어있으면(cold start) 둘 다 빈 리스트.
    """
    if not baseline_map:
        return [], []

    new_entries: list[Mover] = []
    risers: list[Mover] = []
    for it in today_items:
        prev = baseline_map.get(it.goods_no)
        m = Mover(
            goods_no=it.goods_no, brand=it.brand, name=it.product_name,
            rank=it.rank, prev_rank=prev,
            img=getattr(it, "image_url", "") or "",
            price=getattr(it, "price", None),
        )
        if prev is None:
            if it.rank <= top_focus:
                new_entries.append(m)
        elif (prev - it.rank) >= jump_threshold:
            risers.append(m)

    new_entries.sort(key=lambda m: m.rank)
    risers.sort(key=lambda m: -m.jump)
    return new_entries[:max_new], risers[:max_risers]


def save_snapshot(
    *,
    root: Path,
    target_day: date,
    views: dict[str, list],
    captured_at: str,
    keep_days: int,
    log: logging.Logger,
) -> Path:
    path = trend_snapshot.save(root, target_day, views, captured_at)
    pruned = trend_snapshot.prune(root, keep_days=keep_days)
    log.info(persona.step(f"스냅샷 저장 — {path.name} (오래된 {pruned}개 정리)"))
    return path
