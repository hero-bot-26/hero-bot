"""트렌드 레이더 — 키워드 태깅 + 순위가중 델타 (LLM 없이, 사전 매칭).

사전(data/trend_keywords.json)은 claude -p로 1회 오프라인 시드 → 깃 커밋.
런타임(클라우드 Actions)은 이 사전만 써서 결정적으로 동작 → API 크레딧 0.

"지금 뜨는 키워드" = cold start 3번 방식:
  전체 500개가 아니라 "어제 대비 오른 상품(급상승+신규)"만 태깅 →
  순위가중 점수 합으로 오른 키워드 랭킹. 급상승 블록과 데이터가 겹쳐 논리적으로 이어짐.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class KeywordDict:
    # surface form(정규화) -> (category, canonical)
    lookup: dict[str, tuple[str, str]] = field(default_factory=dict)
    stopwords: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Path) -> "KeywordDict":
        d = json.loads(path.read_text(encoding="utf-8"))
        stop = {_norm(w) for w in d.get("stopword", [])}
        lookup: dict[str, tuple[str, str]] = {}
        for cat, entries in d.items():
            if cat == "stopword":
                continue
            for canon, forms in entries.items():
                # stopword가 사전을 이긴다: 초대분류(자켓/셔츠/티셔츠 등)로 등록된 canonical은 제외
                if _norm(canon) in stop:
                    continue
                for f in forms:
                    key = _norm(f)
                    if key and key not in lookup and key not in stop:
                        lookup[key] = (cat, canon)
        return cls(lookup=lookup, stopwords=stop)


def _norm(s: str) -> str:
    """소문자화 + 공백/하이픈 제거 (표면형 매칭 안정화)."""
    return re.sub(r"[\s\-_]+", "", (s or "").lower())


def tag(name: str, kd: KeywordDict) -> list[tuple[str, str]]:
    """상품명에서 (category, canonical) 태그 추출. 사전 표면형 substring 매칭.

    긴 표면형 우선(예: '세미와이드'가 '와이드'보다 먼저) — 중복 canonical은 1회만.
    """
    norm = _norm(name)
    hits: list[tuple[str, str]] = []
    seen: set[str] = set()
    for form in sorted(kd.lookup, key=len, reverse=True):
        if form and form in norm:
            cat, canon = kd.lookup[form]
            if canon not in seen:
                seen.add(canon)
                hits.append((cat, canon))
    return hits


@dataclass
class KeywordTrend:
    canonical: str
    category: str
    score: float        # 순위가중 점수 합 (오른 상품 기준)
    product_count: int  # 이 키워드를 가진 오른 상품 수
    examples: list      # 대표 Mover 몇 개 (썸네일·이름용)


def rising_keywords(
    movers: list,
    kd: KeywordDict,
    *,
    top_n: int = 500,
    min_products: int = 2,
    max_keywords: int = 6,
) -> list[KeywordTrend]:
    """오른 상품(Mover 리스트)에서 뜨는 키워드 산출.

    순위가중 점수 = Σ (top_n + 1 - rank). 상위권 상품일수록 큰 가중.
    min_products 미만 등장 키워드는 노이즈로 컷.
    """
    agg: dict[str, dict] = {}
    for m in movers:
        weight = top_n + 1 - m.rank
        for cat, canon in tag(m.name, kd):
            a = agg.setdefault(canon, {"cat": cat, "score": 0.0, "cnt": 0, "ex": []})
            a["score"] += weight
            a["cnt"] += 1
            if len(a["ex"]) < 3:
                a["ex"].append(m)

    trends = [
        KeywordTrend(canonical=c, category=a["cat"], score=a["score"],
                     product_count=a["cnt"], examples=a["ex"])
        for c, a in agg.items()
        if a["cnt"] >= min_products
    ]
    trends.sort(key=lambda t: -t.score)
    return trends[:max_keywords]
