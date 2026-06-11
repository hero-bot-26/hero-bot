"""시트 ① 히어로 보드 (62컬럼) + 시트 ② 발매분 보드 (23컬럼) 스키마.

IA v0.2 §2, §3 정의를 코드로 옮긴 단일 소스.
다른 모듈(baseline_ingest, triggers, delta_calc)이 이 스키마를 참조.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# enum 값 — IA v0.2 §2~§3
STAGE_STATUS = ["미시작", "진행중", "완료"]
STAGE_STATUS_RUNNING = ["진행중", "완료"]  # 단계 8/IMC execution용
USP_STATUS = ["미작성", "작성", "배포"]
MDP_STATUS = ["정상", "지연", "위험"]
ASSET_STATUS = ["미작성", "제작중", "완료"]
RELEASE_NAME = ["봄", "여름", "한여름", "한겨울", "FW이어가기"]
REORDER_DECISION = ["미정", "리오더 진행", "리오더 안함"]
TRACK = ["봄", "여름", "가을", "겨울", "공통"]  # 시즌 안 트랙 분기 — SS=봄/여름, FW=가을/겨울


@dataclass
class Column:
    """한 컬럼의 스키마.

    - key: 컬럼 키 (헤더 행 1에 들어가는 값, 코드에서 식별자)
    - dtype: "str" | "date" | "int" | "float" | "bool" | "enum" | "text"
    - protected: True면 Protected ranges 대상 (전략팀만 수정)
    - enum_values: dtype="enum"일 때 허용값 리스트
    - note: 헤더 행 2(설명행)에 들어갈 한 줄 설명
    - formula: 둘째 데이터행부터 채울 R1C1 수식 (없으면 None)
    """

    key: str
    dtype: str
    protected: bool = False
    enum_values: list[str] | None = None
    note: str = ""
    formula: str | None = None


# ──────────────────────────────────────────────────────
# 시트 ① 히어로 보드 — 62 컬럼
# ──────────────────────────────────────────────────────

def _stage_block(n: int) -> list[Column]:
    """단계 N의 5컬럼 블록 (IA v0.2 §2.B)."""
    return [
        Column(f"stage{n}_baseline", "date", protected=True, note=f"단계 {n} 종료 baseline (전략팀만)"),
        Column(f"stage{n}_actual", "date", note=f"단계 {n} 종료 actual"),
        Column(
            f"stage{n}_delta_days",
            "int",
            note="actual - baseline (+면 지연)",
            formula=f"=IF(AND(ISNUMBER(INDIRECT(\"R[0]C[-1]\",FALSE)),ISNUMBER(INDIRECT(\"R[0]C[-2]\",FALSE))),INDIRECT(\"R[0]C[-1]\",FALSE)-INDIRECT(\"R[0]C[-2]\",FALSE),\"\")",
        ),
        Column(f"stage{n}_status", "enum", enum_values=STAGE_STATUS, note="미시작 / 진행중 / 완료"),
        Column(f"stage{n}_close_condition_met", "bool", note="종료 조건 충족"),
    ]


SHEET1_COLUMNS: list[Column] = [
    # A. 메타 (9)
    Column("hero_id", "str", note="PK — 예: 27SS_001"),
    Column("season", "str", note="예: 27SS"),
    Column("name", "str", note="히어로 명 (예: 커브드 팬츠)"),
    Column("category", "str", note="카테고리 (예: 팬츠)"),
    Column("owner_md", "str", note="기획MD 담당자"),
    Column("owner_designer", "str", note="디자이너 담당자"),
    Column("is_global_exception", "bool", note="디폴트 N — 한국 히어로 = 글로벌 디폴트"),
    Column("track", "enum", enum_values=TRACK, note="시즌 안 트랙 — 단계 1~4 baseline 분기 (봄/여름/공통)"),

    # B. 단계 1~6 진척 (각 5컬럼 × 6 = 30)
    *_stage_block(1),
    *_stage_block(2),
    *_stage_block(3),
    *_stage_block(4),
    *_stage_block(5),
    *_stage_block(6),

    # C. 단계 2 부서 동의 (7)
    Column("agree_planning_md", "bool", note="기획MD 동의"),
    Column("agree_sourcing", "bool", note="소싱 동의"),
    Column("agree_marketing_content", "bool", note="마케팅·콘텐츠 동의"),
    Column("agree_online", "bool", note="온라인 동의"),
    Column("agree_offline", "bool", note="오프라인 동의"),
    Column("agree_vmd", "bool", note="VMD 동의"),
    Column("agree_global", "bool", note="글로벌 동의"),

    # D. USP (단계 3~4) (5)
    Column("usp_text", "text", note="USP 표준 템플릿 작성본"),
    Column("usp_deploy_baseline", "date", protected=True, note="USP 배포 baseline (전략팀만)"),
    Column("usp_deploy_actual", "date", note="USP 배포 actual"),
    Column(
        "usp_deploy_delta_days",
        "int",
        note="usp_deploy actual - baseline",
        formula="=IF(AND(ISNUMBER(INDIRECT(\"R[0]C[-1]\",FALSE)),ISNUMBER(INDIRECT(\"R[0]C[-2]\",FALSE))),INDIRECT(\"R[0]C[-1]\",FALSE)-INDIRECT(\"R[0]C[-2]\",FALSE),\"\")",
    ),
    Column("usp_global_summary_status", "enum", enum_values=USP_STATUS, note="미작성/작성/배포"),

    # E. MDP·입고 (단계 4~5) (2)
    Column("mdp_status", "enum", enum_values=MDP_STATUS, note="정상/지연/위험"),
    Column("arrival_rate_pct", "float", note="입고율 % (70=VMD 가능, 60=IMC 집중)"),

    # F. 글로벌 (단계 4) (6)
    Column(
        "global_pick",
        "bool",
        note="자동 = NOT is_global_exception",
        formula="=NOT(INDIRECT(\"R[0]C8\",FALSE))",  # 8번째 컬럼 = is_global_exception
    ),
    Column("global_simul_delivery_agreed", "bool", note="동등납기 합의"),
    Column("global_local_asset_status", "enum", enum_values=ASSET_STATUS, note="미작성/제작중/완료"),
    Column("global_orderfair_baseline", "date", protected=True, note="글로벌 수주회 baseline (봄 7/3, 여름 8/5 등)"),
    Column("global_orderfair_actual", "date", note="글로벌 수주회 actual"),
    Column(
        "global_orderfair_delta_days",
        "int",
        note="orderfair actual - baseline",
        formula="=IF(AND(ISNUMBER(INDIRECT(\"R[0]C[-1]\",FALSE)),ISNUMBER(INDIRECT(\"R[0]C[-2]\",FALSE))),INDIRECT(\"R[0]C[-1]\",FALSE)-INDIRECT(\"R[0]C[-2]\",FALSE),\"\")",
    ),

    # G. 단계 9 회고 (4)
    Column("feedback_status", "enum", enum_values=STAGE_STATUS, note="미시작/진행중/완료"),
    Column("pain_case_link", "str", note="v5 페인 ID 멀티 (예: A-1, F-2)"),
    Column("retro_memo", "text", note="시즌 마감 회고 메모"),
    Column("handoff_next_season", "text", note="다음 시즌 인계 항목"),
]


# ──────────────────────────────────────────────────────
# 시트 ② 발매분 보드 — 23 컬럼
# ──────────────────────────────────────────────────────

SHEET2_COLUMNS: list[Column] = [
    # A. 메타 + 발매 (7)
    Column("release_id", "str", note="PK — 예: 27SS_001_봄"),
    Column("hero_id", "str", note="FK → 시트 ① hero_id"),
    Column("release_name", "enum", enum_values=RELEASE_NAME, note="봄/여름/한여름/한겨울/FW이어가기"),
    Column("release_start_baseline", "date", protected=True, note="발매 시작 baseline (핵심 기준점)"),
    Column("release_start_actual", "date", note="발매 시작 actual"),
    Column(
        "release_start_delta_days",
        "int",
        note="release_start actual - baseline",
        formula="=IF(AND(ISNUMBER(INDIRECT(\"R[0]C[-1]\",FALSE)),ISNUMBER(INDIRECT(\"R[0]C[-2]\",FALSE))),INDIRECT(\"R[0]C[-1]\",FALSE)-INDIRECT(\"R[0]C[-2]\",FALSE),\"\")",
    ),
    Column("planned_volume", "int", note="계획 물량 (선택)"),

    # B. 단계 7 IMC 확정 (5)
    Column(
        "stage7_trigger_baseline",
        "date",
        protected=True,
        note="자동 = release_start_baseline - 90일",
        formula="=IF(ISNUMBER(INDIRECT(\"R[0]C4\",FALSE)),INDIRECT(\"R[0]C4\",FALSE)-90,\"\")",  # 4번째 컬럼 = release_start_baseline
    ),
    Column("stage7_actual", "date", note="IMC 확정 입력 완료 actual"),
    Column(
        "stage7_delta_days",
        "int",
        note="stage7 actual - trigger_baseline",
        formula="=IF(AND(ISNUMBER(INDIRECT(\"R[0]C[-1]\",FALSE)),ISNUMBER(INDIRECT(\"R[0]C[-2]\",FALSE))),INDIRECT(\"R[0]C[-1]\",FALSE)-INDIRECT(\"R[0]C[-2]\",FALSE),\"\")",
    ),
    Column("stage7_status", "enum", enum_values=STAGE_STATUS, note="미시작/진행중/완료"),
    Column("stage7_sales_action_text", "text", note="세부 세일즈 액션"),

    # C. 단계 8 판매실행 (7)
    Column("stage8_status", "enum", enum_values=STAGE_STATUS_RUNNING, note="진행중/완료"),
    Column("stage8_close_actual", "date", note="시즌 운영 종료일"),
    Column("vmd_trigger_arrival_70", "bool", note="자동 (시트 ① arrival_rate_pct ≥ 70)"),
    Column("imc_focus_trigger_arrival_60", "bool", note="자동 (시트 ① arrival_rate_pct ≤ 60)"),
    Column("imc_execution_status", "enum", enum_values=STAGE_STATUS_RUNNING, note="진행중/완료"),
    Column("reorder_decision", "enum", enum_values=REORDER_DECISION, note="미정/리오더 진행/리오더 안함"),
    Column("reorder_decision_date", "date", note="리오더 결정 일자"),

    # D. 매출 (QuickSight 연동) (4)
    Column("daily_sales_link", "str", note="QuickSight 대시보드 직링크"),
    Column("cum_sales", "int", note="누적 매출 (QuickSight 동기화)"),
    Column("sellthrough_pct", "float", note="소진율 %"),
    Column("reorder_trigger_flag", "bool", note="자동 (리오더 트리거 발화 여부)"),
]


# 검증: IA v0.2 §2, §3에 명시된 컬럼 수와 일치
assert len(SHEET1_COLUMNS) == 62, f"시트 ① 컬럼 {len(SHEET1_COLUMNS)} ≠ 62"
assert len(SHEET2_COLUMNS) == 23, f"시트 ② 컬럼 {len(SHEET2_COLUMNS)} ≠ 23"


@dataclass
class SheetDef:
    """시트 한 장 정의."""

    title: str
    columns: list[Column]
    description: str = ""
    frozen_rows: int = 2  # 헤더 1행 + 설명 1행
    frozen_cols: int = 1  # 첫 컬럼 (PK) 고정


SHEET1 = SheetDef(
    title="① 히어로 보드",
    columns=SHEET1_COLUMNS,
    description="행 = 한 히어로 (시즌당 N행). NULL 허용 (Progressive 입력).",
)

SHEET2 = SheetDef(
    title="② 발매분 보드",
    columns=SHEET2_COLUMNS,
    description="행 = 한 발매분 (한 히어로 = 1..M행).",
    frozen_cols=2,  # release_id, hero_id 고정
)
