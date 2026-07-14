"""트렌드 레이더 — 일일 Top500 스냅샷 저장/로드 (봇 내부 기억).

스프레드시트가 아니라 깃 저장소 안의 작은 JSON. "뜨는/급상승"은 어제·지난주
스냅샷과의 델타로 계산되므로 최소한의 과거 기억이 필요하다.
스냅샷 1개 ≈ 500행 × 3뷰 ≈ 50KB, 최근 N일치만 유지(자동 prune).

파일 구조: {root}/{YYYY-MM-DD}.json
{
  "date": "2026-07-14",
  "captured_at": "2026-07-14T09:00:03+09:00",
  "views": {
    "전체": [{"rank":1,"goods_no":"...","brand":"...","name":"...",
              "img":"...","price":89000,"disc":10,"rev":954,"label":"..."}, ...],
    "남자": [...], "여자": [...]
  }
}
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def _item_to_dict(it: Any) -> dict:
    return {
        "rank": it.rank,
        "goods_no": it.goods_no,
        "brand": it.brand,
        "name": it.product_name,
        "img": getattr(it, "image_url", "") or "",
        "price": getattr(it, "price", None),
        "disc": getattr(it, "discount_rate", None),
        "rev": getattr(it, "review_count", None),
        "label": getattr(it, "label", "") or "",
    }


def save(
    root: Path,
    target_day: date,
    views: dict[str, list],
    captured_at: str,
) -> Path:
    """뷰별 RankItem 리스트를 하루치 스냅샷 JSON으로 저장."""
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": target_day.isoformat(),
        "captured_at": captured_at,
        "views": {v: [_item_to_dict(it) for it in items] for v, items in views.items()},
    }
    path = root / f"{target_day.isoformat()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load(root: Path, target_day: date) -> dict | None:
    """해당 날짜 스냅샷 로드. 없으면 None."""
    path = root / f"{target_day.isoformat()}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def rank_map(snapshot: dict | None, view: str) -> dict[str, int]:
    """스냅샷의 특정 뷰 → {goods_no: rank}. 델타 계산용."""
    if not snapshot:
        return {}
    return {r["goods_no"]: r["rank"] for r in snapshot.get("views", {}).get(view, [])}


def prune(root: Path, keep_days: int = 14) -> int:
    """최근 keep_days개 스냅샷만 남기고 삭제. 삭제 건수 반환."""
    if not root.exists():
        return 0
    files = sorted(root.glob("????-??-??.json"))
    stale = files[:-keep_days] if len(files) > keep_days else []
    for f in stale:
        try:
            f.unlink()
        except OSError:
            pass
    return len(stale)
