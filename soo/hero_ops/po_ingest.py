"""PO 수량(발주수량) → 스타일/컬러별 정규화 (타겟시즌 필터, 4채널).

소스: 소싱/MD 실무 시트 'MD투입' 탭. 실무자 사용본. (추후 PLM 직접 연동 가능.)

⚠️ 이 시트는 Connected/수식 기반이라:
  - Drive API(파일 export)로는 #REF! → Sheets API values.get 로 읽어야 함.
  - **컬럼이 수시로 이동**한다(예: '최종품번'이 B→A로 옮겨가고 옛 위치는 빔).
    → 위치 고정 금지. **R7 헤더명으로 컬럼을 매 실행 resolve**한다.
  - 와이드 직사각형 읽기는 수식열(A/B) 때문에 정렬이 틀어짐 → **컬럼별 narrow batchGet**.

매칭/구조 (사용자 확정 2026-06-17):
  - 스타일 매칭 = **O열 '품번'** (26FW 행은 9자리 = 앱 STY와 동일). '최종품번'은 비어서 안 씀.
  - 컬러별 = **N열 '스타일컬러코드'** (예 MMFPC3A15-BK)
  - 판매시즌 구분 = **AI열 '타겟시즌'** (26FW → '2026FW'). 같은 품번이 타시즌에도 있어 필터 필수.
  - 4채널 = 내수 온라인 / 내수 오프라인 / 차이나 온라인 / 차이나 오프라인.

반환: { style(품번): {
          po:     {dom_on, dom_off, chn_on, chn_off, t},   # 스타일 합계(해당 시즌)
          colors: { 스타일컬러코드: {dom_on, dom_off, chn_on, chn_off, t} } } }
"""
from __future__ import annotations

import re

PO_SHEET_ID = "13R4gcJ7cDlReY-vwjXZf0kMZ7tC4kr2-S7PC9uziVUQ"
PO_TAB = "MD투입"
_HEADER_ROW = 7          # R7 = 헤더, R8~ = 데이터
_DATA_START = _HEADER_ROW + 1

# 내부 키 → R7 헤더명 후보(우선순위 순). ★실무 시트라 컬럼명도 바뀐다
#   (2026-07-30: '품번'→'대표품번', '스타일컬러코드'→'품번-컬러'로 개명돼 파싱이 죽었다).
#   위치는 물론이고 이름도 고정 금지 → 별칭 목록으로 resolve하고, 개명은 로그로 남긴다.
_HEAD_ALIASES = {
    "style": ("품번", "대표품번", "최종품번"),
    "color": ("스타일컬러코드", "품번-컬러", "품번컬러", "스타일 컬러코드"),
    "season": ("타겟시즌",),
    "dom_on": ("내수 온라인",),
    "dom_off": ("내수 오프라인",),
    "chn_on": ("차이나 온라인",),
    "chn_off": ("차이나 오프라인",),
}
# 선택 컬럼 — 없으면 그냥 건너뛴다(필수 아님).
#   ★'PLM PO' = PLM에서 실제로 PO가 발행돼 번호가 찍힌 행. 오더시트 수량은 '계획'이고
#     이 번호가 있는 행만 '발행분'이다(사용자 지적 2026-08-01: "PO 수량은 PLM 기준이 정확").
_OPT_ALIASES = {
    "plm_po": ("PLM PO", "PLM PO NO", "PLM_PO"),
}
CHANNELS = ("dom_on", "dom_off", "chn_on", "chn_off")
STYLE_RE = re.compile(r"^M[A-Z0-9]{8}$")


def _num(v):
    if v in (None, ""):
        return 0
    try:
        return int(round(float(str(v).replace(",", "").strip())))
    except (TypeError, ValueError):
        return 0


def _cn(n):   # 1-indexed → A1 컬럼 문자
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _zero():
    d = {c: 0 for c in CHANNELS}
    d["t"] = 0
    return d


def parse_po_qty(sheets, season, sheet_id=PO_SHEET_ID) -> dict:
    """season 예: '2026FW' (앱 '26FW' → '20'+'26FW')."""
    # 1) R7 헤더 → 컬럼 인덱스 resolve (위치 이동 대응)
    hdr_row = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id, range=f"'{PO_TAB}'!{_HEADER_ROW}:{_HEADER_ROW}",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [[]])
    header = hdr_row[0] if hdr_row else []
    names = [str(h).replace(chr(10), ' ').strip() for h in header]
    pos: dict[str, list[int]] = {}
    for i, nm in enumerate(names):
        if nm:
            pos.setdefault(nm, []).append(i)
    idx: dict[str, int] = {}
    used: dict[str, str] = {}
    for key, aliases in _HEAD_ALIASES.items():
        for a in aliases:                    # 별칭 우선순위대로, 같은 이름 중복이면 첫 등장
            if a in pos:
                idx[key], used[key] = pos[a][0], a
                break
    missing = [k for k in _HEAD_ALIASES if k not in idx]
    if missing:
        raise RuntimeError(
            f"MD투입 헤더 못 찾음: {missing} — 후보 {[_HEAD_ALIASES[k] for k in missing]} / "
            f"R{_HEADER_ROW} 실제 헤더 {[n for n in names if n][:40]}")
    for key, aliases in _OPT_ALIASES.items():          # 선택 컬럼(없어도 진행)
        for a in aliases:
            if a in pos:
                idx[key] = pos[a][0]
                break
    _renamed = {k: v for k, v in used.items() if v != _HEAD_ALIASES[k][0]}
    if _renamed:
        print(f"[po_ingest] 헤더 개명 감지 — {_renamed} (별칭으로 처리)")

    # 2) 필요한 컬럼만 narrow batchGet (와이드 읽기는 수식열 때문에 정렬 깨짐)
    keys = list(idx.keys())
    ranges = [f"'{PO_TAB}'!{_cn(idx[k] + 1)}{_DATA_START}:{_cn(idx[k] + 1)}" for k in keys]
    vr = sheets.spreadsheets().values().batchGet(
        spreadsheetId=sheet_id, ranges=ranges,
        valueRenderOption="UNFORMATTED_VALUE").execute()["valueRanges"]
    cols = {k: [(row[0] if row else "") for row in vr[i].get("values", [])]
            for i, k in enumerate(keys)}
    n = max((len(v) for v in cols.values()), default=0)

    def cell(key, r):
        col = cols[key]
        return col[r] if r < len(col) else ""

    out: dict[str, dict] = {}
    for r in range(n):
        style = str(cell("style", r)).strip()
        if not STYLE_RE.match(style):
            continue
        if str(cell("season", r)).strip() != season:
            continue
        vals = {c: _num(cell(c, r)) for c in CHANNELS}
        t = sum(vals.values())
        if not t:
            continue
        sl = out.setdefault(style, {"po": _zero(), "plm": _zero(), "colors": {}})
        for c in CHANNELS:
            sl["po"][c] += vals[c]
        sl["po"]["t"] += t
        if "plm_po" in idx and str(cell("plm_po", r)).strip():   # PLM PO 번호가 찍힌 행 = 발행분
            for c in CHANNELS:
                sl["plm"][c] += vals[c]
            sl["plm"]["t"] += t
        color = str(cell("color", r)).strip()
        if color:
            cc = sl["colors"].setdefault(color, _zero())
            for c in CHANNELS:
                cc[c] += vals[c]
            cc["t"] += t
    return out


if __name__ == "__main__":   # 단독 실행 = 파싱 + 검증
    import sys
    from pathlib import Path
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from soo.auth import get_credentials, build_services
    ROOT = Path(__file__).resolve().parents[2]
    sheets = build_services(get_credentials(ROOT / "credentials.json", ROOT / "token.json"))["sheets"]
    season = sys.argv[1] if len(sys.argv) > 1 else "2026FW"
    po = parse_po_qty(sheets, season)
    print(f"[{season}] PO수량 보유 스타일 {len(po)}개")
    print(f"{'품번':12} {'합계':>9} {'내수온':>8} {'내수오프':>8} {'차온':>7} {'차오프':>7}  컬러수")
    for k, v in sorted(po.items())[:15]:
        p = v["po"]
        print(f"{k:12} {p['t']:9,} {p['dom_on']:8,} {p['dom_off']:8,} {p['chn_on']:7,} {p['chn_off']:7,}  {len(v['colors'])}")
