"""무신사 랭킹 페이지 스크래퍼.

API 엔드포인트로 직접 호출 (페이지 HTML 파싱 X) — 안정적이고 가벼움.
- page 1:  /api/home/web/v5/pans/ranking?storeCode=musinsa&sectionId=200&...
- page 2+: /api/home/web/v5/pans/ranking/sections/200?...&page=N&offset=...&startRank=...

응답에서 Top 300 추출 + 지정 브랜드(무탠/우먼/키즈)로 필터.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import requests


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)
_API_PAGE1 = "https://client.musinsa.com/api/home/web/v5/pans/ranking"
_API_PAGEN = "https://client.musinsa.com/api/home/web/v5/pans/ranking/sections/{section}"


@dataclass
class RankItem:
    rank: int
    goods_no: str
    brand: str
    product_name: str
    url: str
    # 트렌드 레이더용 확장 필드 (랭킹봇 경로에선 기본값으로 무시됨)
    image_url: str = ""
    price: int | None = None
    discount_rate: int | None = None
    review_count: int | None = None
    label: str = ""


def _params_page1(section_id: int, sub_pan: str | None = None, gf: str = "A") -> dict:
    p = {
        "storeCode": "musinsa",
        "sectionId": str(section_id),
        "contentsId": "",
        "categoryCode": "000",
        "ageBand": "AGE_BAND_ALL",
        "gf": gf,
    }
    if sub_pan:
        p["subPan"] = sub_pan
    return p


def _params_pagen(page: int, offset: int, start_rank: int, gf: str = "A") -> dict:
    return {
        "storeCode": "musinsa",
        "gf": gf,
        "ageBand": "AGE_BAND_ALL",
        "period": "REALTIME",
        "eventPeriod": "BASIC_REALTIME",
        "categoryCode": "000",
        "contentsId": "",
        "page": str(page),
        "offset": str(offset),
        "startRank": str(start_rank),
        "variantValue": "",
    }


def _to_int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _event_payloads(image: dict) -> tuple[dict, dict]:
    """image.onClickLike.eventLog 아래 ga4 / amplitude payload 추출 (가격·리뷰 소스)."""
    log = ((image.get("onClickLike") or {}).get("eventLog") or {})
    ga4 = ((log.get("ga4") or {}).get("payload") or {})
    amp = ((log.get("amplitude") or {}).get("payload") or {})
    return ga4, amp


def _extract_items(payload: dict) -> list[RankItem]:
    """API 응답 dict에서 RankItem 리스트 추출."""
    out: list[RankItem] = []
    for module in payload.get("data", {}).get("modules", []):
        if module.get("type") != "MULTICOLUMN":
            continue
        for it in module.get("items", []):
            if it.get("type") != "PRODUCT_COLUMN":
                continue
            image = it.get("image") or {}
            rank = image.get("rank")
            if not rank:  # 광고 슬롯 등 None
                continue
            info = it.get("info") or {}
            goods_no = str(it.get("id") or "")
            brand = info.get("brandName") or ""
            name = info.get("productName") or ""
            url = (it.get("onClick") or {}).get("url") or f"https://www.musinsa.com/products/{goods_no}"

            ga4, amp = _event_payloads(image)
            image_url = image.get("url") or ""
            price = _to_int(ga4.get("best_price") or ga4.get("price"))
            discount_rate = _to_int(ga4.get("discount_rate"))
            review_count = _to_int(amp.get("reviewCount"))
            labels = image.get("labels") or []
            label = (labels[0].get("text") if labels and isinstance(labels[0], dict) else "") or ""

            out.append(RankItem(
                rank=int(rank), goods_no=goods_no,
                brand=brand, product_name=name, url=url,
                image_url=image_url, price=price, discount_rate=discount_rate,
                review_count=review_count, label=label,
            ))
    return out


def fetch_top(
    n: int = 300,
    section_id: int = 199,
    sub_pan: str | None = "product",
    timeout: float = 10.0,
    sleep_between: float = 0.5,
    gf: str = "A",
) -> list[RankItem]:
    """무신사 랭킹 Top N 가져오기 (페이지 단위 fetch, 합쳐서 반환).

    sectionId=199 + subPan=product = 사용자가 보는 [전체] 탭 기본값.
    gf = "A"(전체) / "M"(남자) / "F"(여자) — 랭킹 페이지 우측 성별 필터.
    """
    import time

    sess = requests.Session()
    sess.headers.update({"User-Agent": _USER_AGENT, "Accept": "application/json"})

    all_items: list[RankItem] = []

    # Page 1 — 별도 엔드포인트 사용
    resp = sess.get(_API_PAGE1, params=_params_page1(section_id, sub_pan, gf=gf), timeout=timeout)
    resp.raise_for_status()
    items = _extract_items(resp.json())
    all_items.extend(items)

    # Page 2+
    page = 2
    while len(all_items) < n:
        last_rank = max((it.rank for it in all_items), default=0)
        offset = len(all_items)
        params = _params_pagen(page=page, offset=offset, start_rank=last_rank + 1, gf=gf)
        time.sleep(sleep_between)
        resp = sess.get(_API_PAGEN.format(section=section_id), params=params, timeout=timeout)
        resp.raise_for_status()
        new_items = _extract_items(resp.json())
        if not new_items:
            break
        all_items.extend(new_items)
        page += 1
        if page > 10:  # 안전장치
            break

    # rank 정렬 + Top N으로 자르기
    all_items.sort(key=lambda x: x.rank)
    return [it for it in all_items if it.rank <= n]


def filter_by_brand(
    items: Iterable[RankItem],
    brand_keywords: Iterable[str],
    mode: str = "exact",
) -> list[RankItem]:
    """브랜드명 매칭 필터.

    mode="exact"   — brand == keyword 인 것만 (기본). "무신사 스탠다드 스포츠" 같은 다른 브랜드 제외.
    mode="contains"— brand 안에 keyword가 포함되면 통과 (substring).
    """
    keys = [k.strip() for k in brand_keywords if k.strip()]
    out = []
    for it in items:
        brand = (it.brand or "").strip()
        if mode == "exact":
            if brand in keys:
                out.append(it)
        else:  # contains
            for k in keys:
                if k in brand:
                    out.append(it)
                    break
    return out
