"""mini soo — 무탠다드 인턴봇."""

__version__ = "0.1.0"

# 랭킹 뷰 — 무신사 랭킹 페이지의 성별 필터. (gf, 표기) 튜플.
# Long/Wide/Screenshots 탭의 "뷰" 컬럼에는 표기(전체/남자/여자)가 저장된다.
# 무신사 API gf 파라미터는 A=전체, M=남자, F=여자.
VIEWS: list[tuple[str, str]] = [
    ("A", "전체"),
    ("M", "남자"),
    ("F", "여자"),
]
VIEW_LABELS: list[str] = [label for _, label in VIEWS]
GF_BY_LABEL: dict[str, str] = {label: gf for gf, label in VIEWS}
LABEL_BY_GF: dict[str, str] = {gf: label for gf, label in VIEWS}
DEFAULT_VIEW_LABEL: str = "전체"  # 기존(뷰 컬럼 없던 시기) 데이터 해석 기본값
