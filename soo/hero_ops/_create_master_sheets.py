"""IA v0.2 시트 ①·② 신규 Google Spreadsheet 생성.

실행:
  python -m soo.hero_ops._create_master_sheets \\
      --title "히어로 마스터 시트 v0.1" \\
      [--folder-id <drive_folder_id>]

생성 후 출력되는 spreadsheetId를 `config.yaml`의 `hero_ops.master_sheet_id`로 추가.

요건:
- 프로젝트 루트(hero_bot)에 `credentials.json` 또는 `token.json` 존재
- `soo/auth.py`의 SCOPES에 spreadsheets + drive 포함 (이미 충족)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows 콘솔(cp949) 한글·이모지 안전 출력
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from soo.auth import build_services, get_credentials
from soo.hero_ops.schema import (
    SHEET1,
    SHEET2,
    Column,
    SheetDef,
)


ROOT = Path(__file__).resolve().parents[2]  # hero_bot/


# ──────────────────────────────────────────────────────
# A1 유틸
# ──────────────────────────────────────────────────────

def col_letter(idx_0: int) -> str:
    """0-indexed 컬럼 → A1 letter (0→A, 25→Z, 26→AA)."""
    result = ""
    n = idx_0
    while True:
        result = chr(ord("A") + (n % 26)) + result
        n = n // 26 - 1
        if n < 0:
            break
    return result


# ──────────────────────────────────────────────────────
# 자동 수식 (key → (row 3..N 수식 생성기))
# ──────────────────────────────────────────────────────

def _formula_for(sheet: SheetDef, col_key: str, row: int) -> str | None:
    """sheet에서 col_key가 자동 수식 대상이면 row번에 들어갈 수식 반환, 아니면 None."""
    keys = [c.key for c in sheet.columns]
    idx = {k: i for i, k in enumerate(keys)}

    # 단계 N delta_days (시트 ①: stage1~6, 시트 ②: stage7)
    if col_key.startswith("stage") and col_key.endswith("_delta_days"):
        prefix = col_key.removesuffix("_delta_days")  # 예: "stage1"
        baseline_key = f"{prefix}_baseline"
        actual_key = f"{prefix}_actual"
        # 시트 ②의 stage7은 trigger_baseline 사용
        if baseline_key not in idx and f"{prefix}_trigger_baseline" in idx:
            baseline_key = f"{prefix}_trigger_baseline"
        if baseline_key in idx and actual_key in idx:
            b = f"{col_letter(idx[baseline_key])}{row}"
            a = f"{col_letter(idx[actual_key])}{row}"
            return f'=IF(AND(ISNUMBER({a}),ISNUMBER({b})),{a}-{b},"")'

    # usp_deploy_delta_days
    if col_key == "usp_deploy_delta_days":
        b = f"{col_letter(idx['usp_deploy_baseline'])}{row}"
        a = f"{col_letter(idx['usp_deploy_actual'])}{row}"
        return f'=IF(AND(ISNUMBER({a}),ISNUMBER({b})),{a}-{b},"")'

    # global_orderfair_delta_days
    if col_key == "global_orderfair_delta_days":
        b = f"{col_letter(idx['global_orderfair_baseline'])}{row}"
        a = f"{col_letter(idx['global_orderfair_actual'])}{row}"
        return f'=IF(AND(ISNUMBER({a}),ISNUMBER({b})),{a}-{b},"")'

    # release_start_delta_days
    if col_key == "release_start_delta_days":
        b = f"{col_letter(idx['release_start_baseline'])}{row}"
        a = f"{col_letter(idx['release_start_actual'])}{row}"
        return f'=IF(AND(ISNUMBER({a}),ISNUMBER({b})),{a}-{b},"")'

    # stage7_trigger_baseline = release_start_baseline - 90
    if col_key == "stage7_trigger_baseline":
        rs = f"{col_letter(idx['release_start_baseline'])}{row}"
        return f'=IF(ISNUMBER({rs}),{rs}-90,"")'

    # global_pick = NOT(is_global_exception)
    if col_key == "global_pick":
        ex = f"{col_letter(idx['is_global_exception'])}{row}"
        return f"=NOT({ex})"

    return None


# ──────────────────────────────────────────────────────
# Sheets API request 빌더
# ──────────────────────────────────────────────────────

DATA_ROWS = 100  # row 3 ~ 102 (헤더 1 + 설명 2 + 데이터 100)


def _header_request(sheet_id: int, sheet: SheetDef) -> dict:
    """row 1 = 컬럼 키 / row 2 = 설명 한 줄."""
    rows = [
        {"values": [{"userEnteredValue": {"stringValue": c.key},
                     "userEnteredFormat": {"textFormat": {"bold": True},
                                           "backgroundColor": {"red": 0.85, "green": 0.92, "blue": 0.83}}}
                    for c in sheet.columns]},
        {"values": [{"userEnteredValue": {"stringValue": c.note},
                     "userEnteredFormat": {"textFormat": {"italic": True, "fontSize": 9},
                                           "backgroundColor": {"red": 0.96, "green": 0.96, "blue": 0.96},
                                           "wrapStrategy": "WRAP"}}
                    for c in sheet.columns]},
    ]
    return {
        "updateCells": {
            "rows": rows,
            "fields": "userEnteredValue,userEnteredFormat.textFormat,userEnteredFormat.backgroundColor,userEnteredFormat.wrapStrategy",
            "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0},
        }
    }


def _formulas_request(sheet_id: int, sheet: SheetDef) -> dict | None:
    """row 3 ~ 102의 자동 수식 컬럼들 채우기."""
    rows = []
    for r in range(3, 3 + DATA_ROWS):
        cells = []
        for c in sheet.columns:
            f = _formula_for(sheet, c.key, r)
            if f:
                cells.append({"userEnteredValue": {"formulaValue": f}})
            else:
                cells.append({})  # 빈 셀
        rows.append({"values": cells})
    return {
        "updateCells": {
            "rows": rows,
            "fields": "userEnteredValue",
            "start": {"sheetId": sheet_id, "rowIndex": 2, "columnIndex": 0},
        }
    }


def _data_validation_requests(sheet_id: int, sheet: SheetDef) -> list[dict]:
    """enum / bool / date 컬럼에 데이터 유효성 적용 (row 3 ~ 102)."""
    out = []
    for i, c in enumerate(sheet.columns):
        rng = {
            "sheetId": sheet_id,
            "startRowIndex": 2,
            "endRowIndex": 2 + DATA_ROWS,
            "startColumnIndex": i,
            "endColumnIndex": i + 1,
        }
        if c.dtype == "enum" and c.enum_values:
            out.append({
                "setDataValidation": {
                    "range": rng,
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": v} for v in c.enum_values],
                        },
                        "showCustomUi": True,
                        "strict": False,
                    },
                }
            })
        elif c.dtype == "bool":
            out.append({
                "setDataValidation": {
                    "range": rng,
                    "rule": {"condition": {"type": "BOOLEAN"}, "strict": False},
                }
            })
        elif c.dtype == "date":
            out.append({
                "setDataValidation": {
                    "range": rng,
                    "rule": {
                        "condition": {"type": "DATE_IS_VALID"},
                        "showCustomUi": True,
                        "strict": False,
                    },
                }
            })
    return out


def _protected_range_requests(sheet_id: int, sheet: SheetDef) -> list[dict]:
    """Protected 플래그가 켜진 컬럼에 보호 영역 적용 (warningOnly)."""
    out = []
    for i, c in enumerate(sheet.columns):
        if not c.protected:
            continue
        out.append({
            "addProtectedRange": {
                "protectedRange": {
                    "range": {
                        "sheetId": sheet_id,
                        "startColumnIndex": i,
                        "endColumnIndex": i + 1,
                    },
                    "description": f"{c.key}: 전략팀만 수정 (baseline)",
                    "warningOnly": True,
                }
            }
        })
    return out


def _grid_properties_request(sheet_id: int, sheet: SheetDef) -> dict:
    """frozen rows/cols + 컬럼 수 충분히 (62 또는 23 + 여유)."""
    return {
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {
                    "frozenRowCount": sheet.frozen_rows,
                    "frozenColumnCount": sheet.frozen_cols,
                    "rowCount": 2 + DATA_ROWS + 50,  # 헤더 + 데이터 + 여유
                    "columnCount": len(sheet.columns),
                },
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount,gridProperties.rowCount,gridProperties.columnCount",
        }
    }


def _rename_first_sheet_request(sheet_id: int, new_title: str) -> dict:
    return {
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "title": new_title},
            "fields": "title",
        }
    }


def _add_sheet_request(title: str) -> dict:
    return {"addSheet": {"properties": {"title": title}}}


# ──────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────

def create_master_sheets(title: str, folder_id: str | None = None) -> dict:
    """신규 Spreadsheet 생성 + 두 시트 셋업.

    반환: {"spreadsheet_id": ..., "url": ..., "sheet1_id": ..., "sheet2_id": ...}
    """
    creds = get_credentials(ROOT / "credentials.json", ROOT / "token.json")
    services = build_services(creds)
    sheets = services["sheets"]
    drive = services["drive"]

    # 1) 신규 Spreadsheet (기본 시트 1장 자동 생성됨)
    created = sheets.spreadsheets().create(body={
        "properties": {"title": title, "locale": "ko_KR", "timeZone": "Asia/Seoul"},
    }).execute()
    ss_id = created["spreadsheetId"]
    default_sheet_id = created["sheets"][0]["properties"]["sheetId"]
    url = created["spreadsheetUrl"]
    print(f"[OK] Spreadsheet 생성: {url}")

    # 2) Drive 폴더 이동 (옵션)
    if folder_id:
        file = drive.files().get(fileId=ss_id, fields="parents").execute()
        prev = ",".join(file.get("parents", []))
        drive.files().update(fileId=ss_id, addParents=folder_id, removeParents=prev,
                             fields="id, parents").execute()
        print(f"[OK] Drive 폴더 이동: {folder_id}")

    # 3) 시트 ① rename + 시트 ② 추가
    rename_resp = sheets.spreadsheets().batchUpdate(spreadsheetId=ss_id, body={
        "requests": [
            _rename_first_sheet_request(default_sheet_id, SHEET1.title),
            _add_sheet_request(SHEET2.title),
        ]
    }).execute()
    sheet1_id = default_sheet_id
    sheet2_id = rename_resp["replies"][1]["addSheet"]["properties"]["sheetId"]
    print(f"[OK] 시트 ① rename ({sheet1_id}), 시트 ② 추가 ({sheet2_id})")

    # 4) 두 시트 각각 셋업
    for sid, sdef in [(sheet1_id, SHEET1), (sheet2_id, SHEET2)]:
        requests = []
        requests.append(_grid_properties_request(sid, sdef))
        requests.append(_header_request(sid, sdef))
        formulas = _formulas_request(sid, sdef)
        if formulas:
            requests.append(formulas)
        requests.extend(_data_validation_requests(sid, sdef))
        requests.extend(_protected_range_requests(sid, sdef))
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=ss_id, body={"requests": requests}
        ).execute()
        print(f"[OK] {sdef.title} 셋업 완료 ({len(sdef.columns)}컬럼, 수식·유효성·보호 적용)")

    return {
        "spreadsheet_id": ss_id,
        "url": url,
        "sheet1_id": sheet1_id,
        "sheet2_id": sheet2_id,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="히어로 마스터 시트 ①·② 신규 생성")
    p.add_argument("--title", default="히어로 마스터 시트 v0.1", help="Spreadsheet 제목")
    p.add_argument("--folder-id", default=None, help="이동할 Drive 폴더 ID (생략 시 My Drive)")
    args = p.parse_args(argv)

    try:
        result = create_master_sheets(args.title, args.folder_id)
    except Exception as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1

    print()
    print("=" * 60)
    print(f"  spreadsheet_id: {result['spreadsheet_id']}")
    print(f"  URL: {result['url']}")
    print(f"  sheet1 (① 히어로 보드): gid={result['sheet1_id']}")
    print(f"  sheet2 (② 발매분 보드): gid={result['sheet2_id']}")
    print("=" * 60)
    print()
    print("다음 단계: config.yaml에 아래 추가")
    print()
    print("hero_ops:")
    print(f"  master_sheet_id: \"{result['spreadsheet_id']}\"")
    print(f"  sheet1_gid: {result['sheet1_id']}  # ① 히어로 보드")
    print(f"  sheet2_gid: {result['sheet2_id']}  # ② 발매분 보드")
    return 0


if __name__ == "__main__":
    sys.exit(main())
