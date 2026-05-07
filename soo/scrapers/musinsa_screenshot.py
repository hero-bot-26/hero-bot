"""무신사 랭킹 페이지 스크린샷.

Playwright(Chromium)로 https://www.musinsa.com/main/musinsa/ranking 페이지를
헤드리스로 띄워서 PNG 캡처. rank ≤ threshold 진입 검출 시에만 호출.

crop_to_rank가 지정되면 해당 순위까지만 보이도록 하단을 잘라서 반환 —
페이지 내 product 카드의 boundingBox를 측정해서 (rank N)번째 카드 bottom Y로 clip.
"""

from __future__ import annotations

from playwright.sync_api import sync_playwright


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

_RANKING_URL_TEMPLATE = (
    "https://www.musinsa.com/main/musinsa/ranking"
    "?storeCode=musinsa&sectionId={section_id}&categoryCode=000&gf=A&ageBand=AGE_BAND_ALL"
)

# lazy-load된 product 카드들이 모두 그려지도록 한 번 끝까지 스크롤 → 위로 → 이미지 onload 대기.
_TRIGGER_LAZY_LOAD_JS = r"""
async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    for (let y = 0; y <= 4000; y += 400) {
        window.scrollTo(0, y);
        await sleep(150);
    }
    window.scrollTo(0, 0);
    await sleep(400);
    await Promise.all(
        Array.from(document.images)
            .filter(img => !img.complete)
            .map(img => new Promise(r => {
                img.onload = img.onerror = () => r();
                setTimeout(r, 2500);
            }))
    );
}
"""

# rank N번째 product 카드 bottom Y를 px로 반환. N개 미만이면 null.
_FIND_RANK_BOTTOM_JS = r"""
(rankLimit) => {
    const seen = new Set();
    const cards = [];
    for (const a of document.querySelectorAll('a[href*="/products/"]')) {
        const href = a.href;
        if (seen.has(href)) continue;
        const r = a.getBoundingClientRect();
        // 의미 있는 크기의 카드만 (ad/icon 등 제외)
        if (r.width < 80 || r.height < 80) continue;
        seen.add(href);
        cards.push({ y: r.top + window.scrollY, height: r.height });
    }
    cards.sort((a, b) => a.y - b.y);
    if (cards.length < rankLimit) return null;
    const last = cards[rankLimit - 1];
    return Math.ceil(last.y + last.height + 8);
}
"""


def screenshot_ranking_full_page(
    section_id: int = 199,
    timeout_ms: int = 30000,
    viewport_width: int = 1280,
    crop_to_rank: int | None = 12,
) -> bytes:
    """무신사 랭킹 페이지 PNG 바이트 반환.

    crop_to_rank: 해당 순위까지만 보이게 하단 자름. None/0이면 풀페이지.
                  카드를 N개 못 찾으면 풀페이지로 fallback.
    """
    url = _RANKING_URL_TEMPLATE.format(section_id=section_id)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                viewport={"width": viewport_width, "height": 900},
                user_agent=_USER_AGENT,
                locale="ko-KR",
            )
            page = context.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            # 동적 로드되는 상품 카드들이 그려질 시간 확보 (networkidle은 광고 등으로 안 끝날 때 있음)
            page.wait_for_timeout(2500)

            if not crop_to_rank or crop_to_rank <= 0:
                return page.screenshot(full_page=True, type="png")

            # lazy-load 카드 + 이미지 onload 안정화
            page.evaluate(_TRIGGER_LAZY_LOAD_JS)

            clip_height = page.evaluate(_FIND_RANK_BOTTOM_JS, int(crop_to_rank))
            if not clip_height:
                return page.screenshot(full_page=True, type="png")

            return page.screenshot(
                full_page=True,
                clip={"x": 0, "y": 0, "width": viewport_width, "height": int(clip_height)},
                type="png",
            )
        finally:
            browser.close()
