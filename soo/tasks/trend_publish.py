"""트렌드 레이더 — 서술 생성(템플릿) + Slack 블록 빌더(썸네일 포함).

LLM 없이 뜨는 키워드에서 "오늘 한 줄"을 템플릿으로 생성. 클라우드 Actions에서 결정적 동작.
상품 썸네일은 payload의 이미지 CDN URL(공개)을 Slack image block으로 첨부.
"""

from __future__ import annotations

from datetime import date

_WEEKDAY = ["월", "화", "수", "목", "금", "토", "일"]


def _kw_join(trends, n=2) -> str:
    return "·".join(t.canonical for t in trends[:n])


def build_narrative(kw_by_view: dict[str, list]) -> str:
    """뜨는 키워드에서 '오늘 한 줄' 서술을 템플릿 생성.

    kw_by_view: {뷰표기: [KeywordTrend]} (전체/남자/여자).
    """
    allk = kw_by_view.get("전체", [])
    if not allk:
        return "오늘은 뚜렷한 키워드 급부상 없이 스테디셀러 중심. (급상승 상품은 아래 참고)"

    head = f"오늘 무신사 랭킹에서 *{_kw_join(allk, 2)}* 키워드가 빠르게 올라오는 중."
    m = kw_by_view.get("남자", [])
    f = kw_by_view.get("여자", [])
    mk = m[0].canonical if m else None
    fk = f[0].canonical if f else None
    if mk and fk and mk != fk:
        head += f" 남성은 '{mk}', 여성은 '{fk}' 강세."
    elif mk and mk == fk:
        head += f" 남녀 공통으로 '{mk}'."
    return head


def _riser_line(m) -> str:
    if m.is_new:
        return f"🆕 신규 → *{m.rank}위*   {m.name[:38]}  _{m.brand}_"
    return f"🔥 {m.prev_rank}위 → *{m.rank}위* ▲{m.jump}   {m.name[:38]}  _{m.brand}_"


def _kw_line(t) -> str:
    ex = t.examples[0].name[:22] if t.examples else ""
    return f"• *{t.canonical}* ({t.category}) — {t.product_count}개  _예: {ex}_"


def build_blocks(
    target_day: date,
    narrative: str,
    risers: list,
    new_entries: list,
    keywords: list,
    thumb_movers: list,
    sheet_url: str | None = None,
    max_thumbs: int = 6,
) -> tuple[str, list[dict]]:
    """일일 트렌드 메시지의 (fallback text, Slack blocks) 생성.

    risers/new_entries/keywords = '전체' 뷰 기준. thumb_movers = 썸네일로 붙일 Mover들.
    """
    wd = _WEEKDAY[target_day.weekday()]
    title = f"📡 무신사 트렌드 레이더 · {target_day.month}/{target_day.day}({wd})"

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"🔑 *오늘 한 줄*\n{narrative}"}},
    ]

    top_movers = (risers[:4] + new_entries[:3])[:5]
    if top_movers:
        lines = "\n".join(_riser_line(m) for m in top_movers)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*🔥 급상승 (어제 대비)*\n{lines}"}})

    if keywords:
        klines = "\n".join(_kw_line(t) for t in keywords)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*🏷 지금 뜨는 키워드*\n{klines}"}})

    # 썸네일 — 얘기한 상품들의 실제 이미지
    for m in thumb_movers[:max_thumbs]:
        if getattr(m, "img", ""):
            blocks.append({
                "type": "image",
                "image_url": m.img,
                "alt_text": m.name[:60] or "product",
            })

    ctx = "전 브랜드 Top500 기준 · 어제 대비 변화"
    if sheet_url:
        ctx = f"<{sheet_url}|무신사 랭킹 바로가기> · {ctx}"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": ctx}]})

    fallback = f"{title} — {narrative}"
    return fallback, blocks
