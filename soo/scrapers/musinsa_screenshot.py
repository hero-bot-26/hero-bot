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
    "?storeCode=musinsa&sectionId={section_id}&categoryCode=000&gf={gf}&ageBand=AGE_BAND_ALL"
    # skip_bf=Y: 무진장(블랙프라이데이) 행사 기간엔 랭킹 URL이 mujinjangIndex 인트로
    # 스플래쉬로 리다이렉트되어 랭킹 대신 행사 배너가 캡처됨. 이 파라미터는 무신사가
    # 스플래쉬 '닫기'에 쓰는 자체 우회 플래그라 행사 종료 후엔 무해하게 무시됨 (2026-06-18).
    "&skip_bf=Y"
)

# 혹시 skip_bf가 안 먹혀 스플래쉬가 그래도 뜨면, 무신사 자체 닫기 버튼을 눌러 제거하는 방어막.
_DISMISS_SPLASH_JS = r"""
() => {
    const splash = document.querySelector('[class*="mujinjangIndex-page__Container"]');
    if (!splash) return false;
    const close = document.querySelector('[class*="LocalAppBar__CloseButton"]');
    if (close) { close.click(); return true; }
    return false;
}
"""

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

# rank N번째까지의 클립 height(px) 반환. 다음 행(N+1번째 카드)의 top Y 직전까지 자름 →
# N번째 카드 아래의 상품명·가격 텍스트가 자연스럽게 포함됨. N+1이 없으면 마지막 카드
# bottom + 여유 패딩(상품명 영역) 사용.
_FIND_RANK_BOTTOM_JS = r"""
(rankLimit) => {
    const seen = new Set();
    const cards = [];
    for (const a of document.querySelectorAll('a[href*="/products/"]')) {
        const href = a.href;
        if (seen.has(href)) continue;
        const r = a.getBoundingClientRect();
        if (r.width < 80 || r.height < 80) continue;
        seen.add(href);
        cards.push({ y: r.top + window.scrollY, height: r.height });
    }
    cards.sort((a, b) => a.y - b.y);
    if (cards.length < rankLimit) return null;
    if (cards.length > rankLimit) {
        return Math.ceil(cards[rankLimit].y - 4);
    }
    const last = cards[rankLimit - 1];
    return Math.ceil(last.y + last.height + 160);
}
"""


def screenshot_ranking_full_page(
    section_id: int = 199,
    timeout_ms: int = 30000,
    viewport_width: int = 1280,
    crop_to_rank: int | None = 12,
    gf: str = "A",
) -> bytes:
    """무신사 랭킹 페이지 PNG 바이트 반환.

    crop_to_rank: 해당 순위까지만 보이게 하단 자름. None/0이면 풀페이지.
                  카드를 N개 못 찾으면 풀페이지로 fallback.
    gf: "A"(전체) / "M"(남자) / "F"(여자). URL의 gf 파라미터로 들어감.
    """
    url = _RANKING_URL_TEMPLATE.format(section_id=section_id, gf=gf)
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

            # 행사 스플래쉬가 (skip_bf 우회 실패로) 떠 있으면 닫고 랭킹 페이지 재안정화
            try:
                if page.evaluate(_DISMISS_SPLASH_JS):
                    page.wait_for_timeout(2000)
            except Exception:
                pass

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
