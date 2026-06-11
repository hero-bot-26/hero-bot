"""마스터 시트에 표현용 '보드 탭' 추가 — 화면 3.1 히어로 보드 + 화면 3.4 글로벌 KPI.

원리: 시트 ①·②는 입력·SoT, 보드 탭은 표현 (raw 시트는 사람이 보기 거침).
QUERY 함수로 시트 ①의 데이터를 끌어와 조건부 서식으로 신호 색상 표시.

실행:
  python -m soo.hero_ops._create_board_tab \\
      [--spreadsheet-id <id>]   # 생략 시 config.yaml의 hero_ops.master_sheet_id

요건:
- soo/hero_ops/_create_master_sheets.py로 마스터 시트 생성 완료
- config.yaml에 hero_ops.master_sheet_id 등록됨
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import yaml

from soo.auth import build_services, get_credentials
from soo.hero_ops.schema import SHEET1_COLUMNS


ROOT = Path(__file__).resolve().parents[2]


# ──────────────────────────────────────────────────────
# 시트 ① 컬럼 인덱스 → A1 letter
# ──────────────────────────────────────────────────────

def col_letter(idx_0: int) -> str:
    result = ""
    n = idx_0
    while True:
        result = chr(ord("A") + (n % 26)) + result
        n = n // 26 - 1
        if n < 0:
            break
    return result


_K1 = {c.key: i for i, c in enumerate(SHEET1_COLUMNS)}

L = {k: col_letter(i) for k, i in _K1.items()}

# 검증 — 화면에서 쓸 핵심 컬럼들이 다 있는지
for k in ["hero_id", "name", "category",
          "stage1_status", "stage2_status", "stage3_status",
          "stage4_status", "stage5_status", "stage6_status",
          "stage1_delta_days", "stage2_delta_days", "stage3_delta_days",
          "stage4_delta_days", "stage5_delta_days", "stage6_delta_days",
          "stage3_actual", "global_orderfair_baseline",
          "is_global_exception"]:
    assert k in L, f"누락된 컬럼: {k}"


# ──────────────────────────────────────────────────────
# 보드 탭 셀 빌더
# ──────────────────────────────────────────────────────

BOARD_TITLE = "📊 보드 v0.1"  # 시트 탭명에 이모지 가능
SOURCE = "'① 히어로 보드'"  # QUERY에서 인용


def _board_layout() -> list[list[dict]]:
    """보드 탭의 행별 셀 내용 (userEnteredValue + 포맷).

    구조:
      A1: 제목 (대문자, 큰 글씨)
      A2~G2: KPI 카운터 라벨/값 (전체 / 진행중 / 빨강 / 베이스라인누락)
      A4: "화면 1 — 히어로 보드 (단계 1~6 매트릭스)"
      A5..: QUERY 수식 결과 (헤더 + 데이터)
      A20: "화면 2 — 글로벌 KPI (단계 3 종료 ≤ 글로벌 수주회 - 4주)"
      A21..: QUERY 수식 결과
    """
    rows: list[list[dict]] = []

    def cell(value, *, bold=False, italic=False, size=None, bg=None, fg=None, formula=False, wrap=False):
        c = {}
        if formula:
            c["userEnteredValue"] = {"formulaValue": value}
        elif isinstance(value, (int, float)):
            c["userEnteredValue"] = {"numberValue": value}
        elif value is None:
            return {}
        else:
            c["userEnteredValue"] = {"stringValue": str(value)}
        fmt = {"textFormat": {}}
        if bold:
            fmt["textFormat"]["bold"] = True
        if italic:
            fmt["textFormat"]["italic"] = True
        if size:
            fmt["textFormat"]["fontSize"] = size
        if fg:
            fmt["textFormat"]["foregroundColor"] = fg
        if bg:
            fmt["backgroundColor"] = bg
        if wrap:
            fmt["wrapStrategy"] = "WRAP"
        if fmt["textFormat"] or "backgroundColor" in fmt or "wrapStrategy" in fmt:
            c["userEnteredFormat"] = fmt
        return c

    BLUE = {"red": 0.20, "green": 0.35, "blue": 0.60}
    GRAY_BG = {"red": 0.95, "green": 0.95, "blue": 0.95}
    DARK = {"red": 0.15, "green": 0.15, "blue": 0.15}

    # row 1: 제목
    rows.append([cell("히어로 마스터 — 보드 v0.1", bold=True, size=16, fg=BLUE)])

    # row 2: KPI 카운터 (라벨)
    rows.append([
        cell("전체 히어로", bold=True, bg=GRAY_BG),
        cell("진행중 단계", bold=True, bg=GRAY_BG),
        cell("빨강 신호 (delta>7)", bold=True, bg=GRAY_BG),
        cell("베이스라인 누락", bold=True, bg=GRAY_BG),
    ])

    # row 3: KPI 값 (수식) — robust: 각 status·delta 컬럼별 합산 + ISNUMBER 가드
    status_cols = [L[f"stage{n}_status"] for n in range(1, 7)]
    delta_cols = [L[f"stage{n}_delta_days"] for n in range(1, 7)]
    running_expr = "+".join(f'COUNTIF({SOURCE}!{c}3:{c}102,"진행중")' for c in status_cols)
    red_expr = "+".join(
        f'SUMPRODUCT(--ISNUMBER({SOURCE}!{c}3:{c}102),--({SOURCE}!{c}3:{c}102>7))'
        for c in delta_cols
    )
    # 베이스라인 누락 = hero_id 있는데 stage1_baseline 비어있는 행
    baseline_missing_expr = (
        f'SUMPRODUCT(--({SOURCE}!{L["hero_id"]}3:{L["hero_id"]}102<>""),'
        f'--({SOURCE}!{L["stage1_baseline"]}3:{L["stage1_baseline"]}102=""))'
    )
    rows.append([
        cell(f'=COUNTA({SOURCE}!{L["hero_id"]}3:{L["hero_id"]}102)', formula=True, size=14, bold=True),
        cell(f'={running_expr}', formula=True, size=14, bold=True),
        cell(f'={red_expr}', formula=True, size=14, bold=True,
             fg={"red": 0.8, "green": 0.0, "blue": 0.0}),
        cell(f'={baseline_missing_expr}', formula=True, size=14, bold=True),
    ])

    # row 4: 빈 줄
    rows.append([])

    # row 5: 화면 1 제목
    rows.append([cell("화면 1 — 히어로 보드 (단계 1~6 진척 매트릭스)", bold=True, size=12, fg=DARK, bg=GRAY_BG)])

    # row 6: QUERY 헤더 + 데이터 한 셀에
    # QUERY로 hero_id, name, category, grade, stage1~6 status, stage1~6 delta_days 추출
    select_cols = [
        L["hero_id"], L["name"], L["category"],
        L["stage1_status"], L["stage2_status"], L["stage3_status"],
        L["stage4_status"], L["stage5_status"], L["stage6_status"],
        L["stage1_delta_days"], L["stage2_delta_days"], L["stage3_delta_days"],
        L["stage4_delta_days"], L["stage5_delta_days"], L["stage6_delta_days"],
    ]
    select_str = ", ".join(select_cols)
    query1 = (
        f'=QUERY({SOURCE}!A3:BJ, '
        f'"SELECT {select_str} '
        f'WHERE {L["hero_id"]} IS NOT NULL '
        f'LABEL '
        f'{L["hero_id"]} \'히어로\', {L["name"]} \'이름\', {L["category"]} \'카테고리\', '
        f'{L["stage1_status"]} \'1 킥오프\', {L["stage2_status"]} \'2 매트릭스\', '
        f'{L["stage3_status"]} \'3 품평회/GO·DROP\', {L["stage4_status"]} \'4 Initial PO\', '
        f'{L["stage5_status"]} \'5 QC 완료\', {L["stage6_status"]} \'6 IMC 킥오프\', '
        f'{L["stage1_delta_days"]} \'Δ 킥오프\', {L["stage2_delta_days"]} \'Δ 매트릭스\', '
        f'{L["stage3_delta_days"]} \'Δ 품평회\', {L["stage4_delta_days"]} \'Δ PO\', '
        f'{L["stage5_delta_days"]} \'Δ QC\', {L["stage6_delta_days"]} \'Δ IMC\'", '
        f'0)'
    )
    rows.append([cell(query1, formula=True)])

    # row 7~24: QUERY가 자동으로 채움 (Spilled). 1차에서는 18행 정도 여유 두기

    # row 25: 빈
    for _ in range(18):
        rows.append([])

    # row 25: 화면 2 제목 — 인덱스로 따지면 rows 길이가 6+18=24, 그 다음이 row 25
    rows.append([cell("화면 2 — 글로벌 KPI (단계 3 종료 ≤ 글로벌 수주회 - 4주)", bold=True, size=12, fg=DARK, bg=GRAY_BG)])

    # row 26: 글로벌 KPI 헤더 (직접 셀 수식이 빈 데이터에서도 robust)
    A = L["hero_id"]; B_ = L["name"]; T = L["stage3_actual"]; BD = L["global_orderfair_baseline"]
    header2 = ["히어로", "이름", "단계 3 종료", "글로벌 수주회", "4주전 기준일", "여유(일)"]
    rows.append([cell(h, bold=True, bg=GRAY_BG) for h in header2])

    # row 27~76: 시트 ① row 3~52 참조 (50행). hero_id 비어있으면 빈 셀
    for r in range(3, 53):
        rows.append([
            cell(f'=IF({SOURCE}!{A}{r}="","",{SOURCE}!{A}{r})', formula=True),
            cell(f'=IF({SOURCE}!{A}{r}="","",{SOURCE}!{B_}{r})', formula=True),
            cell(f'=IF({SOURCE}!{A}{r}="","",{SOURCE}!{T}{r})', formula=True),
            cell(f'=IF({SOURCE}!{A}{r}="","",{SOURCE}!{BD}{r})', formula=True),
            cell(f'=IF({SOURCE}!{BD}{r}="","",{SOURCE}!{BD}{r}-28)', formula=True),
            cell(f'=IF(OR({SOURCE}!{BD}{r}="",{SOURCE}!{T}{r}=""),"",{SOURCE}!{BD}{r}-28-{SOURCE}!{T}{r})', formula=True),
        ])

    return rows


# ──────────────────────────────────────────────────────
# 조건부 서식 빌더 (status / delta_days)
# ──────────────────────────────────────────────────────

def _conditional_formats(board_sheet_id: int) -> list[dict]:
    """보드 탭의 status·delta 셀에 색상 규칙 적용.

    화면 1: row 7~25(QUERY 결과 범위) × 컬럼 E~J(status 6개), K~P(delta 6개)
    """
    out = []

    # status: 완료=초록, 진행중=노랑, 미시작=회색
    # grade 컬럼 제거로 한 칸씩 왼쪽으로 이동: status 1~6 = D~I
    status_range = {
        "sheetId": board_sheet_id,
        "startRowIndex": 6,    # row 7 (0-indexed 6)
        "endRowIndex": 24,     # row 24
        "startColumnIndex": 3, # D
        "endColumnIndex": 9,   # I+1=9 (status 1~6 = D~I)
    }
    out.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [status_range],
                "booleanRule": {
                    "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "완료"}]},
                    "format": {"backgroundColor": {"red": 0.78, "green": 0.92, "blue": 0.78}},
                },
            },
            "index": 0,
        }
    })
    out.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [status_range],
                "booleanRule": {
                    "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "진행중"}]},
                    "format": {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.7}},
                },
            },
            "index": 1,
        }
    })
    out.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [status_range],
                "booleanRule": {
                    "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "미시작"}]},
                    "format": {"backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.93}},
                },
            },
            "index": 2,
        }
    })

    # delta_days: §4 신호 규칙
    # ≤ -3 초록 / 0초과~3 노랑 / 3~7 주황 / >7 빨강
    delta_range = {
        "sheetId": board_sheet_id,
        "startRowIndex": 6,
        "endRowIndex": 24,
        "startColumnIndex": 9,   # J
        "endColumnIndex": 15,    # O+1=15 (delta 1~6 = J~O)
    }
    out.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [delta_range],
                "booleanRule": {
                    "condition": {"type": "NUMBER_LESS_THAN_EQ", "values": [{"userEnteredValue": "-3"}]},
                    "format": {"backgroundColor": {"red": 0.70, "green": 0.88, "blue": 0.70}},
                },
            },
            "index": 3,
        }
    })
    out.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [delta_range],
                "booleanRule": {
                    "condition": {"type": "NUMBER_GREATER", "values": [{"userEnteredValue": "7"}]},
                    "format": {
                        "backgroundColor": {"red": 0.95, "green": 0.55, "blue": 0.55},
                        "textFormat": {"bold": True, "foregroundColor": {"red": 0.5, "green": 0, "blue": 0}},
                    },
                },
            },
            "index": 4,
        }
    })
    out.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [delta_range],
                "booleanRule": {
                    "condition": {"type": "NUMBER_BETWEEN",
                                  "values": [{"userEnteredValue": "3"}, {"userEnteredValue": "7"}]},
                    "format": {"backgroundColor": {"red": 1.0, "green": 0.78, "blue": 0.55}},
                },
            },
            "index": 5,
        }
    })
    out.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [delta_range],
                "booleanRule": {
                    "condition": {"type": "NUMBER_BETWEEN",
                                  "values": [{"userEnteredValue": "0.0001"}, {"userEnteredValue": "3"}]},
                    "format": {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.6}},
                },
            },
            "index": 6,
        }
    })

    # 화면 2 글로벌 KPI: 여유(일) 컬럼 F (인덱스 5) — row 27부터
    gap_range = {
        "sheetId": board_sheet_id,
        "startRowIndex": 26,   # row 27
        "endRowIndex": 60,     # 여유 범위
        "startColumnIndex": 5, # F
        "endColumnIndex": 6,
    }
    out.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [gap_range],
                "booleanRule": {
                    "condition": {"type": "NUMBER_LESS", "values": [{"userEnteredValue": "0"}]},
                    "format": {
                        "backgroundColor": {"red": 0.95, "green": 0.55, "blue": 0.55},
                        "textFormat": {"bold": True, "foregroundColor": {"red": 0.5, "green": 0, "blue": 0}},
                    },
                },
            },
            "index": 7,
        }
    })
    out.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [gap_range],
                "booleanRule": {
                    "condition": {"type": "NUMBER_BETWEEN",
                                  "values": [{"userEnteredValue": "0"}, {"userEnteredValue": "14"}]},
                    "format": {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.6}},
                },
            },
            "index": 8,
        }
    })
    out.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [gap_range],
                "booleanRule": {
                    "condition": {"type": "NUMBER_GREATER", "values": [{"userEnteredValue": "14"}]},
                    "format": {"backgroundColor": {"red": 0.70, "green": 0.88, "blue": 0.70}},
                },
            },
            "index": 9,
        }
    })

    return out


# ──────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────

def add_board_tab(spreadsheet_id: str) -> dict:
    creds = get_credentials(ROOT / "credentials.json", ROOT / "token.json")
    sheets = build_services(creds)["sheets"]

    # 1) 보드 탭 추가
    resp = sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={
        "requests": [{
            "addSheet": {
                "properties": {
                    "title": BOARD_TITLE,
                    "index": 0,  # 첫 번째 탭으로
                    "gridProperties": {"rowCount": 100, "columnCount": 20, "frozenRowCount": 6},
                }
            }
        }]
    }).execute()
    board_sheet_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    print(f"[OK] 보드 탭 추가: {BOARD_TITLE} (gid={board_sheet_id})")

    # 2) 셀 내용 채우기
    rows = _board_layout()
    sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={
        "requests": [{
            "updateCells": {
                "rows": [{"values": row} for row in rows],
                "fields": "userEnteredValue,userEnteredFormat",
                "start": {"sheetId": board_sheet_id, "rowIndex": 0, "columnIndex": 0},
            }
        }]
    }).execute()
    print(f"[OK] 레이아웃·수식 {len(rows)}행 적용")

    # 3) 컬럼 폭 조정 — A 넓게(히어로명), 단계 status·delta는 좁게
    width_requests = []
    # status 컬럼 D~I (3~8): 60px
    width_requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": board_sheet_id, "dimension": "COLUMNS",
                      "startIndex": 3, "endIndex": 9},
            "properties": {"pixelSize": 60},
            "fields": "pixelSize",
        }
    })
    # delta 컬럼 J~O (9~14): 50px
    width_requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": board_sheet_id, "dimension": "COLUMNS",
                      "startIndex": 9, "endIndex": 15},
            "properties": {"pixelSize": 50},
            "fields": "pixelSize",
        }
    })
    # A 컬럼 (hero_id): 110px
    width_requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": board_sheet_id, "dimension": "COLUMNS",
                      "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 110},
            "fields": "pixelSize",
        }
    })
    # B 컬럼 (name): 160px
    width_requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": board_sheet_id, "dimension": "COLUMNS",
                      "startIndex": 1, "endIndex": 2},
            "properties": {"pixelSize": 160},
            "fields": "pixelSize",
        }
    })
    sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={
        "requests": width_requests
    }).execute()
    print(f"[OK] 컬럼 폭 조정")

    # 4) 조건부 서식
    cf = _conditional_formats(board_sheet_id)
    sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": cf}).execute()
    print(f"[OK] 조건부 서식 {len(cf)}개 규칙 적용")

    return {"board_sheet_id": board_sheet_id}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="보드 탭 추가")
    p.add_argument("--spreadsheet-id", default=None, help="대상 spreadsheet ID (생략 시 config.yaml)")
    args = p.parse_args(argv)

    ss_id = args.spreadsheet_id
    if not ss_id:
        with (ROOT / "config.yaml").open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        ss_id = cfg.get("hero_ops", {}).get("master_sheet_id")
        if not ss_id:
            print("[FAIL] config.yaml에 hero_ops.master_sheet_id가 없습니다", file=sys.stderr)
            return 1

    try:
        result = add_board_tab(ss_id)
    except Exception as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1

    print()
    print("=" * 60)
    print(f"  board_sheet_id (gid): {result['board_sheet_id']}")
    print(f"  URL: https://docs.google.com/spreadsheets/d/{ss_id}/edit#gid={result['board_sheet_id']}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
