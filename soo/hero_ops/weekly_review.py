# -*- coding: utf-8 -*-
"""
히어로 주간 세일즈 리뷰 — 시트 생성기 (월요일 회의용)

26FW 히어로 대시보드의 WEEK 블록을 매주 읽어 _HISTORY 탭에 적재하고,
직전 적재 주차와 비교해 리뷰 시트를 만든다.
→ 전주 스냅샷 사본을 사람이 뜰 필요가 없다(초기 1회 bootstrap 만 하면 됨).

사용:
    python weekly_hero_review.py                 # 주간 실행 (자동화 진입점)
    python weekly_hero_review.py --bootstrap <스냅샷시트ID>
                                                 # 과거 주차를 히스토리에 1회 시딩
    python weekly_hero_review.py --dry           # 히스토리 적재 없이 시트만 다시 그림

산출 탭:
    요약        히어로 15종 주간 헤드라인 + 재고주수 + 데이터 확인필요 자동검출
    STY전체     전 품목 STY별 WoW
    유입        덱 품목의 PDP 경로별 유입(외부유입 포함) — 원천에 주차가 쌓여 스냅샷 불필요
    <덱 품목>   품목총계 / STY별 / 컬러별(온·오프)
    _HISTORY   주차별 플랫 적재 (숨김). 이게 전주 비교의 소스.

★ 대시보드 품목 탭 구조 전제
    row10 브랜드 전체 / row11 히어로 워셔블 비중 / row12 품목 총계, row13~ STY 반복.
    각 STY 는 3벌 반복 —
        [1] 구분=HERO…   Sub Total = STY 총계(On+Off), 하위 컬러행 = 온라인
        [2] 구분=★주연/공란                            하위 컬러행 = 오프라인
        [3] 구분=통합 UID                              온라인 중복 → 버림
    예외: 일부 STY(MWFNP0A05 등)는 개별 uid 한 벌에 온·오프가 함께 들어있다.
    → HERO 벌 컬러행에서 on/off 를 둘 다 받아두고, 오프라인 벌이 비면 그것으로 대체.
    컬러합 vs STY Sub Total 대조로 검증하고 불일치는 시트에 표기한다.

★ 라이브 시트는 09:30 잡이 끝난 뒤에 읽어야 한다(반쯤 쓰인 상태를 읽는 사고 이력 있음).
  wait_live_fresh() 가 기준일이 전일인지 확인하고 아니면 대기한다.
"""
import sys
import time
import datetime as dt

from pathlib import Path

from soo.auth import build_services, get_credentials

_ROOT = Path(__file__).resolve().parent.parent.parent

LIVE_SID = '1-A04_TwKZJNPkFg27USkKAScZRu6CAhbgVeXk9c09nA'   # 26FW 히어로 실적 대시보드(라이브)
DATA_SID = '1iHH2qG8Uj5vmlC3aXkey96usktWODmguDPD_ToT2rfA'   # 히어로 실적(자동) — 유입 원천
REVIEW_SID = '1dHizAmzFBnl6aWBLxPfzU-UtMcnwGVUiXSYoxql44tM'  # 산출 시트

DECK_ITEMS = ['커브드팬츠', '라이트다운']    # 덱·컬러상세·유입 대상 (고정값으로 관리)
N_ITEMS = 15                                 # 대시보드 앞 15탭 = 히어로 품목
MAXROW = 160
HIST = '_HISTORY'
HIST_KEEP_WEEKS = 26

# WEEK 블록 내 오프셋 (CE=0 기준). CR~ 는 같은 블록의 '25년 전년' 구간.
W = {'gmv': 1, 'yoy': 2, 'tgt': 3, 'qty': 4, 'ach': 5, 'pdisc': 10, 'margin': 12,
     'ly_gmv': 14, 'ly_qty': 15}
# ★ 실적은 항상 주간 기준으로 보고, 전년비·전주비·목표비를 가장 먼저 읽는다.
METRICS = ('gmv', 'qty', 'tgt', 'ach', 'margin', 'ly_gmv', 'ly_qty')


def colidx(c):
    n = 0
    for ch in c:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


OFF_ON = colidx('DC') - colidx('CE')
OFF_OFF = colidx('EA') - colidx('CE')


def svc():
    """CI 는 GOOGLE_SA_JSON / GOOGLE_OAUTH_TOKEN, 로컬은 token.json 을 쓴다."""
    creds = get_credentials(_ROOT / 'credentials.json', _ROOT / 'token.json')
    return build_services(creds)['sheets']


def retry(fn, what='', n=4):
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            if i == n - 1:
                raise
            print('  retry %s (%d): %s' % (what, i + 1, e))
            time.sleep(2 * (i + 1))


def num(v):
    return v if isinstance(v, (int, float)) else None


def serial_to_date(v):
    if not isinstance(v, (int, float)):
        return None
    return dt.date(1899, 12, 30) + dt.timedelta(days=int(v))


# ---------------------------------------------------------------- 대시보드 파싱
def fetch(s, sid, tab):
    rng = ["'%s'!A3:B6" % tab, "'%s'!A10:F%d" % (tab, MAXROW),
           "'%s'!CE10:EX%d" % (tab, MAXROW), "'%s'!Q10:Q%d" % (tab, MAXROW),
           "'%s'!AP10:AP%d" % (tab, MAXROW), "'%s'!BO10:BO%d" % (tab, MAXROW)]
    r = retry(lambda: s.spreadsheets().values().batchGet(
        spreadsheetId=sid, ranges=rng, valueRenderOption='UNFORMATTED_VALUE').execute(), tab)
    return [vr.get('values', []) for vr in r['valueRanges']]


def parse(blocks):
    meta, lab, wk, q, ap, bo = blocks

    def L(i, j):
        row = lab[i] if i < len(lab) else []
        return row[j] if j < len(row) else ''

    def wrow(i):
        return wk[i] if i < len(wk) else []

    def wv(i, key, off=0):
        row = wrow(i)
        j = off + W[key]
        return num(row[j]) if j < len(row) else None

    def stock(blk, i):
        return num(blk[i][0]) if i < len(blk) and blk[i] else None

    def metrics(i):
        d = {}
        for ch, off in (('t', 0), ('on', OFF_ON), ('off', OFF_OFF)):
            for k in METRICS + ('pdisc',):
                d['%s_%s' % (ch, k)] = wv(i, k, off)
        d['stock_t'], d['stock_on'], d['stock_off'] = stock(q, i), stock(ap, i), stock(bo, i)
        return d

    out = {'date': serial_to_date(meta[0][1] if meta and len(meta[0]) > 1 else None),
           'week1st': serial_to_date(meta[1][1] if len(meta) > 1 and len(meta[1]) > 1 else None),
           'total': metrics(2), 'sty': []}

    cur, channel = None, None
    for i in range(3, len(lab)):
        code, sty, name, color = L(i, 2), L(i, 1), L(i, 3), L(i, 5)
        gubun = str(L(i, 0)).strip()
        if isinstance(code, str) and code.endswith('SKU'):
            if gubun.startswith('HERO'):
                cur = dict(sty=sty, name=name, pending=gubun.startswith('HERO SU'),
                           on_colors=[], off_colors=[], _hero_off=[], **metrics(i))
                out['sty'].append(cur)
                channel = 'on'
            elif gubun.startswith('통합'):
                channel = None
            else:
                channel = 'off'
            continue
        if cur is None or channel is None or not str(color).strip():
            continue
        m = metrics(i)

        def rec(ch):
            return dict(code=code, color=color, gmv=m['%s_gmv' % ch], qty=m['%s_qty' % ch],
                        margin=m['%s_margin' % ch],
                        stock_on=m['stock_on'], stock_off=m['stock_off'])

        def add(bucket, r):
            if not any(x['code'] == r['code'] and x['color'] == r['color'] for x in bucket):
                bucket.append(r)

        if channel == 'on':
            add(cur['on_colors'], rec('on'))
            add(cur['_hero_off'], rec('off'))
        else:
            add(cur['off_colors'], rec('off'))

    for r in out['sty']:
        if sum(c['qty'] or 0 for c in r['off_colors']) == 0:
            r['off_colors'] = [c for c in r['_hero_off'] if (c['qty'] or c['gmv'])]
        r.pop('_hero_off', None)
    return out


def collect(s, sid, tabs):
    out = {}
    for t in tabs:
        print('  read', t)
        out[t] = parse(fetch(s, sid, t))
    return out


def item_tabs(s, sid):
    m = retry(lambda: s.spreadsheets().get(spreadsheetId=sid,
                                           fields='sheets.properties').execute(), 'tabs')
    return [x['properties']['title'] for x in m['sheets']][:N_ITEMS]


# ---------------------------------------------------------------- 히스토리 (플랫)
HCOLS = (['week_start', 'week_end', 'item', 'level', 'sty', 'name', 'color', 'code', 'channel']
         + list(METRICS) + ['stock_t', 'stock_on', 'stock_off', 'pending'])
MOFF = 9                       # HCOLS 에서 METRICS 가 시작하는 인덱스
SOFF = MOFF + len(METRICS)     # stock_t 인덱스


def run_week(data, ref):
    """런 전체의 주차 키 = 기준 탭의 B4/B3. 탭별로 달라지면 안 된다."""
    d = data[ref]
    return d['week1st'].isoformat(), d['date'].isoformat()


def to_flat(data, deck_items, ws, we):
    """★ 기준 주차와 다른 탭은 버린다.
    시트 복제본은 탭마다 B3/B4 수식이 재계산돼 서로 다른 주차를 가리키는 사고가 있었다.
    그런 탭을 그대로 적재하면 다음 주 WoW 가 조용히 엉뚱한 구간과 비교된다."""
    rows, dropped = [], []
    for item, d in data.items():
        iw = d['week1st'].isoformat() if d['week1st'] else ''
        ie = d['date'].isoformat() if d['date'] else ''
        if (iw, ie) != (ws, we):
            dropped.append('%s(%s~%s)' % (item, iw, ie))
            continue
        t = d['total']
        for ch in ('t', 'on', 'off'):
            rows.append([ws, we, item, 'item', '', '', '', '', ch]
                        + [t.get('%s_%s' % (ch, k)) for k in METRICS]
                        + [t['stock_t'], t['stock_on'], t['stock_off'], ''])
        for r in d['sty']:
            for ch in ('t', 'on', 'off'):
                rows.append([ws, we, item, 'sty', r['sty'], r['name'], '', '', ch]
                            + [r.get('%s_%s' % (ch, k)) for k in METRICS]
                            + [r['stock_t'], r['stock_on'], r['stock_off'],
                               1 if r['pending'] else ''])
            if item not in deck_items:
                continue
            for ch, key in (('on', 'on_colors'), ('off', 'off_colors')):
                for c in r[key]:
                    rows.append([ws, we, item, 'color', r['sty'], r['name'], c['color'], c['code'],
                                 ch]
                                + [c.get(k) for k in METRICS]
                                + ['', c['stock_on'], c['stock_off'], ''])
    if dropped:
        print('  ! 기준 주차(%s~%s)와 달라 제외: %s' % (ws, we, ', '.join(dropped)))
    return rows


def from_flat(rows):
    """히스토리 플랫 행 -> parse() 와 같은 모양"""
    out = {}
    for r in rows:
        r = list(r) + [''] * (len(HCOLS) - len(r))
        ws, we, item, level, sty, name, color, code, ch = r[:MOFF]
        vals = {k: num(r[MOFF + i]) for i, k in enumerate(METRICS)}
        st, so, sf, pend = num(r[SOFF]), num(r[SOFF + 1]), num(r[SOFF + 2]), r[SOFF + 3]
        d = out.setdefault(item, {'week1st': ws or None, 'date': we or None,
                                  'total': {}, 'sty': [], '_idx': {}})
        if level == 'item':
            for k, v in vals.items():
                d['total']['%s_%s' % (ch, k)] = v
            d['total'].update(stock_t=st, stock_on=so, stock_off=sf)
        elif level == 'sty':
            rec = d['_idx'].get(sty)
            if rec is None:
                rec = dict(sty=sty, name=name, pending=bool(pend), on_colors=[], off_colors=[],
                           stock_t=st, stock_on=so, stock_off=sf)
                d['_idx'][sty] = rec
                d['sty'].append(rec)
            for k, v in vals.items():
                rec['%s_%s' % (ch, k)] = v
        else:
            rec = d['_idx'].get(sty)
            if rec is None:
                continue
            c = dict(code=code, color=color, stock_on=so, stock_off=sf)
            c.update(vals)
            rec['%s_colors' % ch].append(c)
    for d in out.values():
        d.pop('_idx', None)
        for r in d['sty']:
            for ch in ('t', 'on', 'off'):
                for k in METRICS + ('pdisc',):
                    r.setdefault('%s_%s' % (ch, k), None)
    return out


def ensure_tabs(s, sid, titles):
    m = retry(lambda: s.spreadsheets().get(spreadsheetId=sid,
                                           fields='sheets.properties').execute(), 'meta')
    have = {x['properties']['title']: x['properties'] for x in m['sheets']}
    reqs = [{'addSheet': {'properties': {'title': t}}} for t in titles if t not in have]
    if reqs:
        retry(lambda: s.spreadsheets().batchUpdate(spreadsheetId=sid,
                                                   body={'requests': reqs}).execute(), 'addSheet')
        m = retry(lambda: s.spreadsheets().get(spreadsheetId=sid,
                                               fields='sheets.properties').execute(), 'meta2')
        have = {x['properties']['title']: x['properties'] for x in m['sheets']}
    return {t: p['sheetId'] for t, p in have.items()}


def load_history(s, sid):
    try:
        r = retry(lambda: s.spreadsheets().values().get(
            spreadsheetId=sid, range="'%s'!A2:Q200000" % HIST,
            valueRenderOption='UNFORMATTED_VALUE').execute(), HIST)
        return r.get('values', [])
    except Exception:
        return []


def save_history(s, sid, hist_rows, new_rows, cur_week):
    keep = [r for r in hist_rows if r and r[0] != cur_week]
    weeks = sorted({r[0] for r in keep if r and r[0]})[-HIST_KEEP_WEEKS:]
    keep = [r for r in keep if r[0] in weeks]
    allrows = keep + new_rows
    retry(lambda: s.spreadsheets().values().clear(spreadsheetId=sid,
                                                  range="'%s'" % HIST).execute(), 'clearHist')
    retry(lambda: s.spreadsheets().values().update(
        spreadsheetId=sid, range="'%s'!A1" % HIST, valueInputOption='RAW',
        body={'values': [HCOLS] + [[('' if v is None else v) for v in r] for r in allrows]}
    ).execute(), 'writeHist')
    return len(allrows)


# ---------------------------------------------------------------- 계산 헬퍼
def wow(a, b):
    if a in (None, 0) or b is None:
        return None
    return b / a - 1


def key_sty(r):
    return r['sty']


def checks(r):
    out = []
    for ch, lbl in (('on', 'ON'), ('off', 'OFF')):
        sc = sum(c['qty'] or 0 for c in r.get('%s_colors' % ch, []))
        st = r.get('%s_qty' % ch) or 0
        if r.get('%s_colors' % ch) and sc != st:
            out.append('%s 컬러합 %s vs 총계 %s' % (lbl, sc, st))
    return out


# ---------------------------------------------------------------- 시트 본문
def build_summary(items, prev, last, period):
    rows = [['히어로 주간 세일즈 리뷰'],
            ['비교 주차', period],
            ['생성 시각', dt.datetime.now().strftime('%Y-%m-%d %H:%M')],
            ['라이브 대시보드', 'https://docs.google.com/spreadsheets/d/%s/edit' % LIVE_SID],
            [],
            ['★ 주간 기준. 전년비 · 전주비 · 목표비 순으로 먼저 볼 것'],
            ['품목', '지난주 GMV', '전년비', '전주비', '목표비(달성율)',
             '목표 판매량', '판매수량', '전년 판매수량',
             '온라인 전주비', '오프라인 전주비', '지난주 ON GMV', '지난주 OFF GMV', '온라인 비중',
             '매총율(전)', '매총율(후)', '재고 ON', '재고 OFF', '재고주수 ON', '재고주수 OFF']]
    pct, wos = [], []
    body = []
    for item in items:
        l = last.get(item, {}).get('total', {})
        p = prev.get(item, {}).get('total', {})
        if not l:
            continue
        on_g, off_g = l.get('on_gmv') or 0, l.get('off_gmv') or 0
        ly = l.get('t_ly_gmv')
        body.append([item, l.get('t_gmv'),
                     wow(ly, l.get('t_gmv')) if ly else None,
                     wow(p.get('t_gmv'), l.get('t_gmv')),
                     l.get('t_ach'), l.get('t_tgt'), l.get('t_qty'), l.get('t_ly_qty'),
                     wow(p.get('on_gmv'), l.get('on_gmv')), wow(p.get('off_gmv'), l.get('off_gmv')),
                     on_g, off_g, (on_g / (on_g + off_g)) if (on_g + off_g) else None,
                     p.get('t_margin'), l.get('t_margin'), l.get('stock_on'), l.get('stock_off'),
                     (l['stock_on'] / l['on_qty']) if l.get('on_qty') and l.get('stock_on') else None,
                     (l['stock_off'] / l['off_qty']) if l.get('off_qty') and l.get('stock_off') else None])
    body.sort(key=lambda x: -(x[1] or 0))
    for r in body:
        rows.append(r)
        pct += [(len(rows) - 1, c) for c in (2, 3, 4, 8, 9, 12, 13, 14)]
        wos += [(len(rows) - 1, c) for c in (17, 18)]

    rows += [[], ['■ 데이터 확인 필요'], ['항목', '내용']]
    for item in items:
        l = last.get(item, {})
        t = l.get('total', {})
        st, so, sf = t.get('stock_t') or 0, t.get('stock_on') or 0, t.get('stock_off') or 0
        if st and abs(st - (so + sf)) > 1:
            rows.append(['%s 재고 합계 불일치' % item,
                         '총 %s ≠ ON %s + OFF %s (차 %s) — 재고 이관 판단 전 확인'
                         % (f'{st:,.0f}', f'{so:,.0f}', f'{sf:,.0f}', f'{st-(so+sf):,.0f}')])
        if so and sf and abs(so - sf) / max(so, sf) < 0.01:
            rows.append(['%s 온/오프 재고 동일값' % item,
                         'ON %s ≒ OFF %s — 중복 계상 의심' % (f'{so:,.0f}', f'{sf:,.0f}')])
        if t.get('t_gmv') and not t.get('t_tgt'):
            rows.append(['%s 주간 목표 미세팅' % item, '목표=0 이라 달성율 산출 불가'])
        for r in l.get('sty', []):
            for c in checks(r):
                rows.append(['%s %s 컬러합 불일치' % (item, r['sty']), c])
    if rows[-1][0] == '항목':
        rows.append(['없음', '자동 검출된 이상 없음'])
    return rows, pct, wos


def build_sty_all(items, prev, last):
    rows = [['전 품목 STY별 주간 실적'],
            ['품목', '스타일', '상품명', '상태', '전주 GMV', '지난주 GMV', 'WoW',
             '지난주 수량', 'ON 수량', 'OFF 수량', 'ON 비중', '재고 ON', '재고 OFF', '매총율']]
    pct = []
    body = []
    for item in items:
        pmap = {key_sty(x): x for x in prev.get(item, {}).get('sty', [])}
        for r in last.get(item, {}).get('sty', []):
            if not any([r.get('t_gmv'), r.get('t_qty'), r.get('stock_on'), r.get('stock_off')]):
                continue
            p = pmap.get(key_sty(r), {})
            onq, offq = r.get('on_qty') or 0, r.get('off_qty') or 0
            body.append([item, r['sty'], r['name'], '미발매' if r['pending'] else '판매중',
                         p.get('t_gmv'), r.get('t_gmv'), wow(p.get('t_gmv'), r.get('t_gmv')),
                         r.get('t_qty'), onq, offq,
                         (onq / (onq + offq)) if (onq + offq) else None,
                         r.get('stock_on'), r.get('stock_off'), r.get('t_margin')])
    body.sort(key=lambda x: -(x[5] or 0))
    for r in body:
        rows.append(r)
        pct += [(len(rows) - 1, c) for c in (6, 10, 13)]
    return rows, pct


def build_item(item, p, l):
    rows, pct = [], []
    t0, t1 = p.get('total', {}), l.get('total', {})
    rows.append(['[%s] 품목 총계' % item])
    rows.append(['구분', '전주 GMV', '지난주 GMV', 'WoW', '전주 수량', '지난주 수량', 'WoW',
                 '매총율(전)', '매총율(후)'])
    for lbl, k in (('합계', 't'), ('온라인', 'on'), ('오프라인', 'off')):
        rows.append([lbl, t0.get('%s_gmv' % k), t1.get('%s_gmv' % k),
                     wow(t0.get('%s_gmv' % k), t1.get('%s_gmv' % k)),
                     t0.get('%s_qty' % k), t1.get('%s_qty' % k),
                     wow(t0.get('%s_qty' % k), t1.get('%s_qty' % k)),
                     t0.get('%s_margin' % k), t1.get('%s_margin' % k)])
        pct += [(len(rows) - 1, c) for c in (3, 6, 7, 8)]
    rows.append([])

    pmap = {key_sty(x): x for x in p.get('sty', [])}
    rows.append(['[%s] STY별' % item])
    rows.append(['스타일', '상품명', '상태', '전주 GMV', '지난주 GMV', 'WoW', '지난주 수량',
                 'ON 수량', 'OFF 수량', 'ON 비중', '재고 ON', '재고 OFF', '매총율', '검증'])
    for r in l.get('sty', []):
        if not any([r.get('t_gmv'), r.get('t_qty'), r.get('stock_on'), r.get('stock_off')]):
            continue
        pr = pmap.get(key_sty(r), {})
        onq, offq = r.get('on_qty') or 0, r.get('off_qty') or 0
        c = checks(r)
        rows.append([r['sty'], r['name'], '미발매' if r['pending'] else '판매중',
                     pr.get('t_gmv'), r.get('t_gmv'), wow(pr.get('t_gmv'), r.get('t_gmv')),
                     r.get('t_qty'), onq, offq, (onq / (onq + offq)) if (onq + offq) else None,
                     r.get('stock_on'), r.get('stock_off'), r.get('t_margin'),
                     ' / '.join(c) if c else 'OK'])
        pct += [(len(rows) - 1, x) for x in (5, 9, 12)]
    rows.append([])

    for ch, lbl in (('on', '온라인'), ('off', '오프라인')):
        rows.append(['[%s] 컬러별 · %s' % (item, lbl)])
        rows.append(['스타일', '상품명', '컬러', '전주 GMV', '지난주 GMV', 'WoW',
                     '전주 수량', '지난주 수량', '증감', '재고 ON', '재고 OFF'])
        body = []
        for r in l.get('sty', []):
            pr = pmap.get(key_sty(r))
            pc = {(c['code'], c['color']): c for c in pr['%s_colors' % ch]} if pr else {}
            for c in r.get('%s_colors' % ch, []):
                o = pc.get((c['code'], c['color'])) or {}
                if not (c['gmv'] or c['qty'] or o.get('gmv') or o.get('qty')):
                    continue
                body.append([r['sty'], r['name'], c['color'], o.get('gmv'), c['gmv'],
                             wow(o.get('gmv'), c['gmv']), o.get('qty'), c['qty'],
                             (c['qty'] or 0) - (o.get('qty') or 0), c['stock_on'], c['stock_off']])
        body.sort(key=lambda x: -(x[4] or 0))
        pct += [(len(rows) + i, 5) for i in range(len(body))]
        rows.extend(body)
        rows.append([])
    return rows, pct


def build_brief(items, prev, last, period, deck_items):
    """회의 서두에 읽을 브리핑 — 주간 기준, 전년비·전주비·목표비 순.
    사실 서술만 자동 생성한다(해석·액션은 사람이 붙인다)."""
    def eok(v):
        if v is None:
            return '-'
        return '%.2f억' % (v / 1e8) if abs(v) >= 1e8 else format(round(v / 1e4), ',') + '만'

    def pc(v):
        return '-' if v is None else '%+.1f%%' % (v * 100)

    tot_l = sum((last.get(i, {}).get('total', {}).get('t_gmv') or 0) for i in items)
    tot_p = sum((prev.get(i, {}).get('total', {}).get('t_gmv') or 0)
                for i in items if i in prev)
    cmp_l = sum((last.get(i, {}).get('total', {}).get('t_gmv') or 0)
                for i in items if i in prev)
    rows = [['히어로 주간 브리핑'], ['대상 주차', period],
            ['원칙', '실적은 주간 기준. 전년비 → 전주비 → 목표비 순으로 읽는다'], []]
    rows.append(['■ 전체'])
    rows.append(['히어로 합계 GMV', eok(tot_l)])
    if tot_p:
        rows.append(['전주비(전주 데이터 있는 품목만)',
                     '%s → %s  %s' % (eok(tot_p), eok(cmp_l), pc(wow(tot_p, cmp_l)))])
    rows.append([])

    rows.append(['■ 품목별 한 줄'])
    rows.append(['품목', '지난주 GMV', '전년비', '전주비', '목표비', '한 줄'])
    body = []
    for item in items:
        l = last.get(item, {}).get('total', {})
        p = prev.get(item, {}).get('total', {})
        if not l.get('t_gmv'):
            continue
        ly = l.get('t_ly_gmv')
        w = wow(p.get('t_gmv'), l.get('t_gmv'))
        onw = wow(p.get('on_gmv'), l.get('on_gmv'))
        offw = wow(p.get('off_gmv'), l.get('off_gmv'))
        note = []
        if onw is not None and offw is not None:
            note.append('온라인 %s / 오프라인 %s' % (pc(onw), pc(offw)))
        if l.get('t_margin') and p.get('t_margin'):
            note.append('매총 %.1f%%→%.1f%%' % (p['t_margin'] * 100, l['t_margin'] * 100))
        so, sq = l.get('stock_on'), l.get('on_qty')
        if so and sq:
            wk = so / sq
            if wk < 6:
                note.append('★온라인 재고 %.1f주치로 부족' % wk)
        body.append([item, l.get('t_gmv'), wow(ly, l.get('t_gmv')) if ly else None, w,
                     l.get('t_ach'), ' · '.join(note)])
    body.sort(key=lambda x: -(x[1] or 0))
    pct = [(len(rows) + i, c) for i in range(len(body)) for c in (2, 3, 4)]
    rows += body
    rows.append([])

    rows.append(['■ 덱 품목 상세'])
    for item in deck_items:
        l, p = last.get(item, {}), prev.get(item, {})
        t, tp = l.get('total', {}), p.get('total', {})
        if not t.get('t_gmv'):
            continue
        rows.append(['[%s]' % item, '%s (전주비 %s)' % (eok(t.get('t_gmv')),
                                                     pc(wow(tp.get('t_gmv'), t.get('t_gmv'))))])
        pmap = {x['sty']: x for x in p.get('sty', [])}
        sty = [x for x in l.get('sty', []) if x.get('t_gmv')]
        sty.sort(key=lambda x: -(x.get('t_gmv') or 0))
        for r in sty[:6]:
            pr = pmap.get(r['sty'], {})
            rows.append(['', '%s  %s  전주비 %s' % (r['name'][:34], eok(r.get('t_gmv')),
                                                pc(wow(pr.get('t_gmv'), r.get('t_gmv'))))])
        rows.append([])
    return rows, pct


# ---------------------------------------------------------------- 유입
def build_inflow(s, last, deck_items, sink=None):
    """PMKT경로주차(경로별) + PDP일별(요일 맞춘 총량). 원천에 주차가 쌓여 스냅샷 불필요."""
    wk = retry(lambda: s.spreadsheets().values().get(
        spreadsheetId=DATA_SID, range="'PMKT경로주차'!A3:J40000",
        valueRenderOption='UNFORMATTED_VALUE').execute(), '경로주차').get('values', [])
    dl = retry(lambda: s.spreadsheets().values().get(
        spreadsheetId=DATA_SID, range="'PDP일별'!A3:D200000",
        valueRenderOption='UNFORMATTED_VALUE').execute(), 'PDP일별').get('values', [])

    rows, pct = [], []
    sink = {} if sink is None else sink
    rows.append(['커브드/라이트다운 유입 (히어로 마스터앱 PDP 경로, path_type=direct · 온라인)'])
    rows.append(['★ 유입 원천은 실적보다 늦게 적재된다. 아래 기간 표기를 반드시 확인할 것'])
    for item in deck_items:
        stys = {r['sty'] for r in last.get(item, {}).get('sty', []) if r.get('sty')}
        norm = item.replace(' ', '')
        cand = [x for x in wk if len(x) > 9 and str(x[0]).replace(' ', '') == norm]
        if not cand:
            continue
        # ★ 원천에는 (7/28~7/31) 같은 파편 행이 섞여 있다. 월요일 시작 주차만 진짜 주차다.
        def is_week(a):
            try:
                return dt.date.fromisoformat(a).weekday() == 0
            except Exception:
                return False

        # ★ 시즌은 사전순으로 고르면 26SS 가 잡힌다. 진행 시즌(FW)을 우선하고 나머지는 참고로.
        seasons = sorted({str(x[1]) for x in cand})
        seasons.sort(key=lambda x: (0 if x.endswith('FW') else 1, x))
        for si, season in enumerate(seasons):
            sc = [x for x in cand if str(x[1]) == season]
            weeks = sorted({(str(x[5]), str(x[6])) for x in sc if is_week(str(x[5])) and x[6]})
            if len(weeks) < 2:
                continue
            cw, pw = weeks[-1], weeks[-2]

            def agg(w, sc=sc):
                d = {}
                for x in sc:
                    if (str(x[5]), str(x[6])) == w:
                        a = d.setdefault(str(x[2]), [0, 0, 0])
                        a[0] += int(x[7] or 0)
                        a[1] += int(x[8] or 0)
                        a[2] += int(float(x[9] or 0))
                return d

            A, B = agg(pw), agg(cw)

            def days(w):
                return (dt.date.fromisoformat(w[1]) - dt.date.fromisoformat(w[0])).days + 1

            da, db = days(pw), days(cw)
            tag = '' if si == 0 else '  (참고)'
            rows += [[], ['■ %s · %s%s — 전주 %s~%s (%d일)  vs  지난주 %s~%s (%d일) · 일평균 비교'
                          % (item, season, tag, pw[0], pw[1], da, cw[0], cw[1], db)],
                     ['경로', '전주 UV', '지난주 UV', '전주 일평균', '지난주 일평균', '일평균 WoW',
                      '전주 비중', '지난주 비중', '지난주 전환율', '지난주 GMV 일평균']]
            ta, tb = sum(v[0] for v in A.values()), sum(v[0] for v in B.values())
            for pth in sorted(set(A) | set(B), key=lambda k: -(B.get(k, [0])[0])):
                a, b = A.get(pth, [0, 0, 0]), B.get(pth, [0, 0, 0])
                rows.append([pth, a[0], b[0], a[0] / da, b[0] / db,
                             wow(a[0] / da, b[0] / db), a[0] / ta if ta else None,
                             b[0] / tb if tb else None, b[1] / b[0] if b[0] else None, b[2] / db])
                pct += [(len(rows) - 1, c) for c in (5, 6, 7, 8)]
            rows.append(['합계', ta, tb, ta / da, tb / db, wow(ta / da, tb / db), 1, 1, None, None])
            pct += [(len(rows) - 1, c) for c in (5, 6, 7)]
            if si == 0:
                sink.setdefault(item, {}).update(
                    season=season, prev_week=pw, cur_week=cw, prev_days=da, cur_days=db,
                    total=(ta, tb),
                    paths=[dict(path=p_, a=A.get(p_, [0, 0, 0]), b=B.get(p_, [0, 0, 0]))
                           for p_ in sorted(set(A) | set(B), key=lambda k: -(B.get(k, [0])[0]))])

        if stys:
            by = {}
            for x in dl:
                if len(x) > 3 and str(x[2]).split('-')[0] in stys:
                    by[str(x[0])] = by.get(str(x[0]), 0) + int(x[3] or 0)
            if by:
                end = max(by)
                e = dt.date.fromisoformat(end)
                mon = e - dt.timedelta(days=e.weekday())

                def s_(a, b):
                    t, d = 0, a
                    while d <= b:
                        t += by.get(d.isoformat(), 0)
                        d += dt.timedelta(days=1)
                    return t

                nd = (e - mon).days + 1
                rows += [[], ['□ %s 요일 맞춘 총 유입 (원천 최종 적재 %s 기준, 월~%s %d일 구간)'
                              % (item, end, e.strftime('%a'), nd)],
                         ['구간', '기간', 'UV', '일평균', 'WoW']]
                seg = []
                for k in (2, 1, 0):
                    a = mon - dt.timedelta(days=7 * k)
                    b = a + dt.timedelta(days=nd - 1)
                    seg.append((a, b, s_(a, b)))
                for i, (a, b, v) in enumerate(seg):
                    rows.append(['%d주 전' % (2 - i) if i < 2 else '지난주',
                                 '%s~%s' % (a, b), v, v / nd,
                                 wow(seg[i - 1][2], v) if i else None])
                    if i:
                        pct.append((len(rows) - 1, 4))
                sink.setdefault(item, {}).update(
                    matched=[(str(a), str(b), v, nd) for a, b, v in seg], last_load=end,
                    daily=[(d, by[d]) for d in sorted(by)[-12:]])
    return rows, pct


# ---------------------------------------------------------------- 시트 쓰기
def push(s, sid, title, rows):
    n = max((len(r) for r in rows), default=1)
    grid = [[('' if v is None else v) for v in r] + [''] * (n - len(r)) for r in rows]
    retry(lambda: s.spreadsheets().values().update(
        spreadsheetId=sid, range="'%s'!A1" % title, valueInputOption='RAW',
        body={'values': grid}).execute(), 'push ' + title)
    return len(grid), n


def fmt_reqs(gid, nrow, ncol, rows, pct, wos=(), pattern='0.0%'):
    reqs = [
        {'repeatCell': {'range': {'sheetId': gid, 'startRowIndex': 0, 'endRowIndex': nrow},
                        'cell': {'userEnteredFormat': {
                            'textFormat': {'fontFamily': 'Arial', 'fontSize': 10}}},
                        'fields': 'userEnteredFormat.textFormat'}},
        {'updateSheetProperties': {
            'properties': {'sheetId': gid, 'gridProperties': {'frozenRowCount': 1}},
            'fields': 'gridProperties.frozenRowCount'}},
        {'autoResizeDimensions': {'dimensions': {'sheetId': gid, 'dimension': 'COLUMNS',
                                                 'startIndex': 0, 'endIndex': ncol}}},
        {'repeatCell': {'range': {'sheetId': gid, 'startRowIndex': 1, 'endRowIndex': nrow,
                                  'startColumnIndex': 1, 'endColumnIndex': ncol},
                        'cell': {'userEnteredFormat': {
                            'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0'}}},
                        'fields': 'userEnteredFormat.numberFormat'}},
    ]
    for i, row in enumerate(rows):
        first = str(row[0]) if row else ''
        if not first:
            continue
        sec = first.startswith('[') or first.startswith('■') or first.startswith('□') or i == 0
        hdr = first in ('구분', '스타일', '품목', '항목', '경로', '구간')
        if not (sec or hdr):
            continue
        reqs.append({'repeatCell': {
            'range': {'sheetId': gid, 'startRowIndex': i, 'endRowIndex': i + 1,
                      'startColumnIndex': 0, 'endColumnIndex': ncol},
            'cell': {'userEnteredFormat': {
                'backgroundColor': {'red': .13, 'green': .13, 'blue': .13} if sec
                else {'red': .92, 'green': .92, 'blue': .92},
                'textFormat': {'bold': True, 'foregroundColor':
                               {'red': 1, 'green': 1, 'blue': 1} if sec
                               else {'red': 0, 'green': 0, 'blue': 0}}}},
            'fields': 'userEnteredFormat.backgroundColor,userEnteredFormat.textFormat'}})

    def ranges(cells, pat):
        by = {}
        for r, c in cells:
            by.setdefault(c, []).append(r)
        for c, rs in by.items():
            rs.sort()
            st = pv = rs[0]
            for r in rs[1:] + [None]:
                if r == pv + 1:
                    pv = r
                    continue
                reqs.append({'repeatCell': {
                    'range': {'sheetId': gid, 'startRowIndex': st, 'endRowIndex': pv + 1,
                              'startColumnIndex': c, 'endColumnIndex': c + 1},
                    'cell': {'userEnteredFormat': {'numberFormat': pat}},
                    'fields': 'userEnteredFormat.numberFormat'}})
                if r is None:
                    break
                st = pv = r

    ranges(pct, {'type': 'PERCENT', 'pattern': pattern})
    ranges(wos, {'type': 'NUMBER', 'pattern': '0.0"주"'})
    return reqs


# ---------------------------------------------------------------- 신선도
def wait_live_fresh(s, tries=20, gap=60):
    """라이브 대시보드 기준일이 전일이 될 때까지 대기(09:30 잡 완료 확인)."""
    want = dt.date.today() - dt.timedelta(days=1)
    for i in range(tries):
        r = retry(lambda: s.spreadsheets().values().get(
            spreadsheetId=LIVE_SID, range="'%s'!B3" % DECK_ITEMS[0],
            valueRenderOption='UNFORMATTED_VALUE').execute(), 'fresh')
        d = serial_to_date((r.get('values') or [[None]])[0][0])
        if d and d >= want:
            print('라이브 기준일', d, 'OK')
            return d
        print('라이브 기준일 %s (기대 %s) — 대기 %ds' % (d, want, gap))
        time.sleep(gap)
    raise SystemExit('라이브 대시보드가 갱신되지 않았습니다. 중단합니다.')


# ---------------------------------------------------------------- main
def main():
    argv = sys.argv[1:]
    dry = '--dry' in argv
    boot = argv[argv.index('--bootstrap') + 1] if '--bootstrap' in argv else None
    s = svc()
    tabs = item_tabs(s, LIVE_SID)
    ensure_tabs(s, REVIEW_SID, [HIST])
    hist = load_history(s, REVIEW_SID)

    if boot:
        print('bootstrap from', boot)
        data = collect(s, boot, tabs)
        ws, we = run_week(data, DECK_ITEMS[0])
        rows = to_flat(data, DECK_ITEMS, ws, we)
        n = save_history(s, REVIEW_SID, hist, rows, ws)
        print('히스토리에 %s~%s 주차 %d행 시딩 (총 %d행)' % (ws, we, len(rows), n))
        return

    if '--no-wait' not in argv:
        wait_live_fresh(s)

    print('라이브 읽는 중')
    data = collect(s, LIVE_SID, tabs)
    cur_week, cur_end = run_week(data, DECK_ITEMS[0])

    weeks = sorted({r[0] for r in hist if r and r[0] and r[0] < cur_week})
    if not weeks:
        raise SystemExit('비교할 전주 히스토리가 없습니다. --bootstrap <전주스냅샷ID> 를 먼저 실행하세요.')
    pw = weeks[-1]
    prows = [r for r in hist if r and r[0] == pw]
    prev = from_flat(prows)
    last = data
    period = '%s~%s  vs  %s~%s' % (pw, prows[0][1], cur_week, cur_end)
    print('비교:', period)
    missing = [t for t in tabs if t not in prev]
    if missing:
        print('  ! 전주 데이터 없는 품목(WoW 공란 처리):', ', '.join(missing))

    titles = ['브리핑', '요약', 'STY전체', '유입'] + DECK_ITEMS
    gid = ensure_tabs(s, REVIEW_SID, titles + [HIST])
    for t in titles:
        retry(lambda t=t: s.spreadsheets().values().clear(
            spreadsheetId=REVIEW_SID, range="'%s'" % t).execute(), 'clear ' + t)

    payload = {}
    r, p = build_brief(tabs, prev, last, period, DECK_ITEMS)
    payload['브리핑'] = (r, p, ())
    r, p, w = build_summary(tabs, prev, last, period)
    payload['요약'] = (r, p, w)
    r, p = build_sty_all(tabs, prev, last)
    payload['STY전체'] = (r, p, ())
    inflow = {}
    r, p = build_inflow(s, last, DECK_ITEMS, inflow)
    payload['유입'] = (r, p, ())
    for item in DECK_ITEMS:
        r, p = build_item(item, prev.get(item, {}), last.get(item, {}))
        payload[item] = (r, p, ())

    reqs = []
    for t, (rows, pc, ws) in payload.items():
        nrow, ncol = push(s, REVIEW_SID, t, rows)
        reqs += fmt_reqs(gid[t], nrow, ncol, rows, pc, ws)
    reqs.append({'updateSheetProperties': {
        'properties': {'sheetId': gid[HIST], 'hidden': True}, 'fields': 'hidden'}})
    for i in range(0, len(reqs), 60):
        retry(lambda i=i: s.spreadsheets().batchUpdate(
            spreadsheetId=REVIEW_SID, body={'requests': reqs[i:i + 60]}).execute(), 'fmt')

    if not dry:
        n = save_history(s, REVIEW_SID, hist,
                         to_flat(data, DECK_ITEMS, cur_week, cur_end), cur_week)
        print('히스토리 %s 주차 적재 (총 %d행)' % (cur_week, n))

    url = 'https://docs.google.com/spreadsheets/d/%s/edit' % REVIEW_SID
    print('DONE', url)
    return dict(url=url, period=period, week_start=cur_week, week_end=cur_end,
                prev_start=pw, prev_end=prows[0][1], last=data, prev=prev, tabs=tabs,
                inflow=inflow, deck_items=DECK_ITEMS)


if __name__ == '__main__':
    main()
