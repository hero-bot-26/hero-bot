"""무신사 랭킹 페이지 풀페이지 스크린샷.

Playwright(Chromium)로 https://www.musinsa.com/main/musinsa/ranking 페이지를
헤드리스로 띄워서 풀페이지 PNG 캡처. rank ≤ 10 진입 검출 시에만 호출.
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


def screenshot_ranking_full_page(
    section_id: int = 199,
    timeout_ms: int = 30000,
    viewport_width: int = 1280,
) -> bytes:
    """무신사 랭킹 페이지(전체 탭) 풀페이지 PNG 바이트 반환."""
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
            return page.screenshot(full_page=True, type="png")
        finally:
            browser.close()
