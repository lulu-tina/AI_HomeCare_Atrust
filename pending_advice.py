"""未派單處理建議：時間挪動（後移／前移）→ 換天 → 換人 → 拆成兩段。

每一項建議都用「預覽結束時的班表」＋同一套檢查（資格、時段、交通、休息、連續工作、月工時）逐項驗證；
最前面的幾項還會整案重跑一次預覽，顯示「套用後整案還剩幾次排不進」。
建議只是參考，居督按「套用」之後仍要再預覽、確認。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import copy
import pandas as pd
from pending_clients import _fmt, _hm, split_points, default_split_minutes, split_task, pending_week_label

SHIFT_ORDER = (30, -30, 60, -60, 90, -90, 120, -120)
DAY_ORDER = (1, -1, 2, -2, 3, -3)
KIND_ORDER = {'時間挪動': 0, '換天': 1, '換人': 2, '拆成兩段': 3}


def empty_edits():
    return {'shifts': {}, 'splits': {}, 'moves': {}, 'workers': {}, 'log': []}


def apply_suggestion(edits, apply):
    """把一項建議的 apply 內容疊加到調整清單上，回傳新的清單（不改原本的）。"""
    out = copy.deepcopy(edits) if edits else empty_edits()
    for key in ('shifts', 'splits', 'moves', 'workers', 'log'):
        out.setdefault(key, {} if key != 'log' else [])
    kind = apply['type']
    if kind == 'shift':
        pid = apply['pid'].split('#')[0]
        out['shifts'][pid] = int(out['shifts'].get(pid, 0)) + int(apply['minutes'])
    elif kind == 'move':
        out['moves'][apply['tid']] = {'date': apply['date'], 'start': apply['start']}
    elif kind == 'pattern_worker':
        out.setdefault('pattern_workers', {})[apply['pid']] = apply['worker']
    elif kind == 'worker':
        out['workers'][apply['tid']] = apply['worker']
    elif kind == 'split':
        out['splits'][apply['pid'].split('#')[0]] = {'first': int(apply['first']), 'gap': int(apply.get('gap', 0)),
                                                     'first_worker': apply.get('first_worker'), 'second_worker': apply.get('second_worker')}
    else:
        raise ValueError(f'未知的調整類型：{kind}')
    out['log'].append({'類型': kind, **{k: v for k, v in apply.items() if k != 'type'}})
    return out


def edits_summary(edits, labels=None):
    """目前累積的調整，給人看的文字列表（labels：固定時段ID → 『每週三 12:40～17:40』）。"""
    labels = labels or {}
    name = lambda pid: labels.get(pid) or labels.get(str(pid).split('#')[0]) or f'固定時段 {str(pid)[:6]}'
    lines = []
    for pid, minutes in (edits.get('shifts') or {}).items():
        lines.append(f"{name(pid)}：{'後移' if minutes > 0 else '前移'} {abs(minutes)} 分鐘（服務分鐘不變）")
    for pid, sp in (edits.get('splits') or {}).items():
        who = f"（第一段 {sp.get('first_worker') or '原負責人'}、第二段 {sp.get('second_worker') or '原負責人'}）"
        lines.append(f"{name(pid)}：每次拆成兩段，第一段 {sp['first']} 分" + (f"，中間休息 {sp['gap']} 分" if sp.get('gap') else '，兩段各自指派') + who)
    for tid, mv in (edits.get('moves') or {}).items():
        lines.append(f"{tid}：只這一次改到 {mv['date']} {mv['start']}")
    for tid, w in (edits.get('workers') or {}).items():
        lines.append(f"{tid}：只這一次改由 {w} 服務")
    for pid, w in (edits.get('pattern_workers') or {}).items():
        lines.append(f"{name(pid)}：整個固定時段改由 {w} 服務")
    return lines


def _row(preview, tid):
    frame = preview['tasks']
    hit = frame.loc[frame['任務ID'].astype(str).eq(str(tid))]
    return hit.iloc[0].to_dict()


def _with_time(row, date, start, minutes):
    out = dict(row)
    out['日期'] = date
    out['時間窗_開始'] = _fmt(start)
    out['時間窗_結束'] = _fmt(start + minutes)
    if '星期' in out:
        out['星期'] = '星期' + '一二三四五六日'[pd.Timestamp(date).weekday()]
    return out


def advise(preview, caregivers, config, run_preview=None, unit_minutes=None, max_groups=4, max_verify=4, per_kind=2, window=(360, 1260)):
    """針對預覽中仍排不進的服務產生處理建議。

    run_preview(apply) → 套用該建議後的整案預覽（可省略；省略就沒有「套用後還剩幾次」）。
    回傳 list[dict]；每個 dict：群組(固定時段)、日期、種類、標題、說明、apply、套用後仍未派（可能為 None）。
    """
    failed = preview['failed']
    if failed.empty:
        return []
    evaluate = preview['evaluate_row']
    pattern_of = preview.get('pattern_of', {})
    members = [str(preview['worker'])] + [str(w) for w in preview.get('team', ())]
    everyone = [str(x) for x in caregivers['居服員ID']]
    limit = float(getattr(config, 'continuous_work_limit_mins', 240.0))
    groups = {}
    for tid in failed['任務ID'].astype(str):
        groups.setdefault(pattern_of.get(tid, tid), []).append(tid)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:max_groups]
    occurrences = {}
    for _tid in preview['tasks'].loc[preview['tasks']['案家ID'].astype(str).eq(str(preview['client'])), '任務ID'].astype(str):
        occurrences[pattern_of.get(_tid, _tid)] = occurrences.get(pattern_of.get(_tid, _tid), 0) + 1
    client_dates = set(preview['tasks'].loc[preview['tasks']['案家ID'].astype(str).eq(str(preview['client'])), '日期'].astype(str))
    suggestions = []
    for pid, tids in ordered:
        rep = _row(preview, tids[0])
        date = str(rep['日期'])[:10]
        minutes = int(round(float(rep['服務歷時(分鐘)'])))
        start = _hm(rep['時間窗_開始'])
        owner = str(preview['task_workers'].get(tids[0]) or preview['pattern_workers'].get(pid) or preview['worker'])
        who = [owner] + [w for w in members if w != owner]
        base_pid = pid.split('#')[0]
        week_label = pending_week_label(rep)
        found = {k: [] for k in KIND_ORDER}

        # 1 時間挪動：整個固定時段一起前移／後移，優先由原本的人（固定人選團隊）接手
        if '#' not in pid:
            for delta in SHIFT_ORDER:
                if len(found['時間挪動']) >= per_kind:
                    break
                ns = start + delta
                if ns < window[0] or ns + minutes > window[1]:
                    continue
                trial = _with_time(rep, date, ns, minutes)
                for cand in who:
                    if evaluate(trial, cand)['available']:
                        word = '後移' if delta > 0 else '前移'
                        found['時間挪動'].append({
                            '標題': f"{week_label}整個固定時段{word} {abs(delta)} 分鐘（{_fmt(start)}～{_fmt(start + minutes)} → {_fmt(ns)}～{_fmt(ns + minutes)}）",
                            '說明': f"服務分鐘不變；{date} 驗證可由 {cand} 接手" + ('' if cand == owner else '（原負責人仍排不進，改由團隊成員）'),
                            'apply': {'type': 'shift', 'pid': base_pid, 'minutes': delta}, '人選': [cand]})
                        if occurrences.get(pid, len(tids)) > len(tids) or len(tids) == 1:
                            found['時間挪動'].append({
                                '標題': f"只把 {date} 這一次{word} {abs(delta)} 分鐘（{_fmt(start)}～{_fmt(start + minutes)} → {_fmt(ns)}～{_fmt(ns + minutes)}）",
                                '說明': f"其他日期照舊；服務分鐘不變；驗證可由 {cand} 接手；需先和案家確認這一天的新時段",
                                'apply': {'type': 'move', 'tid': tids[0], 'date': date, 'start': _fmt(ns)}, '人選': [cand]})
                        break
        # 2 換天：只動這一天
        for dd in DAY_ORDER:
            if len(found['換天']) >= per_kind:
                break
            nd = (pd.Timestamp(date) + pd.Timedelta(days=dd)).strftime('%Y-%m-%d')
            if nd in client_dates:
                continue
            trial = _with_time(rep, nd, start, minutes)
            for cand in who:
                if evaluate(trial, cand)['available']:
                    found['換天'].append({
                        '標題': f"{date} 這一次改到 {nd}（{'後' if dd > 0 else '前'} {abs(dd)} 天，同樣 {_fmt(start)}～{_fmt(start + minutes)}）",
                        '說明': f"其他日期不動；{nd} 驗證可由 {cand} 接手；需先和案家確認",
                        'apply': {'type': 'move', 'tid': tids[0], 'date': nd, 'start': _fmt(start)}, '人選': [cand]})
                    break
        # 3 換人：同一天同一時間，找別人（先看團隊成員，再看全體）
        for cand in [w for w in who if w != owner] + [w for w in everyone if w not in who]:
            if len(found['換人']) >= per_kind + 1:
                break
            if evaluate(rep, cand)['available']:
                inside = cand in members
                found['換人'].append({
                    '標題': f"{date} 這一次改由 {cand} 服務" + ('' if inside else '（需加入固定人選團隊）'),
                    '說明': ('已在團隊內，只改這一天' if inside else f'會成為第 {len(members) + 1} 位固定人選，請先與對方確認'),
                    'apply': {'type': 'worker', 'tid': tids[0], 'worker': cand}, '人選': [cand]})
        # 3b 整個固定時段改交給同一個人（失敗的每一天都要驗證）
        if len(tids) > 1:
            for cand in [w for w in who if w != owner] + [w for w in everyone if w not in who]:
                if sum(1 for x in found['換人'] if x['apply']['type'] == 'pattern_worker') >= 1:
                    break
                if all(evaluate(_row(preview, t), cand)['available'] for t in tids):
                    found['換人'].insert(0, {
                        '標題': f"{week_label}（{len(tids)} 次未派）整個固定時段改由 {cand} 服務" + ('' if cand in members else '（需加入固定人選團隊）'),
                        '說明': '失敗的每一天都逐日驗證通過',
                        'apply': {'type': 'pattern_worker', 'pid': pid, 'worker': cand}, '人選': [cand]})
        # 4 拆成兩段（兩位各做一段，或同一人中間休息）
        try:
            points = split_points(pd.Series(rep), unit_minutes)
            first = default_split_minutes(pd.Series(rep), unit_minutes) if points else None
        except ValueError:
            first = None
        if first:
            second = minutes - first
            seg1 = _with_time(rep, date, start, first)
            done = False
            pool = who + [w for w in everyone if w not in who]
            for c1 in pool:
                if done or not evaluate(seg1, c1)['available']:
                    continue
                seg2 = _with_time({**rep, '任務ID': str(rep['任務ID']) + 'B'}, date, start + first, second)
                for c2 in [w for w in pool if w != c1]:
                    if evaluate(seg2, c2, [(rep['任務ID'], c1)])['available']:
                        found['拆成兩段'].append({
                            '標題': f"每次拆成兩段：{_fmt(start)}～{_fmt(start + first)} 由 {c1}，{_fmt(start + first)}～{_fmt(start + minutes)} 由 {c2}",
                            '說明': f"兩位各做一段（{first}＋{second} 分鐘），中間不必休息；{date} 驗證通過",
                            'apply': {'type': 'split', 'pid': base_pid, 'first': first, 'gap': 0, 'first_worker': c1, 'second_worker': c2}, '人選': [c1, c2]})
                        done = True
                        break
                if not done:
                    seg2b = _with_time({**rep, '任務ID': str(rep['任務ID']) + 'B'}, date, start + first + 30, second)
                    if evaluate(seg2b, c1, [(rep['任務ID'], c1)])['available']:
                        found['拆成兩段'].append({
                            '標題': f"每次拆成兩段，同一人 {c1} 做完整個服務、中間休息 30 分（結束時間延後 30 分）",
                            '說明': f"{_fmt(start)}～{_fmt(start + first)}＋{_fmt(start + first + 30)}～{_fmt(start + minutes + 30)}；{date} 驗證通過",
                            'apply': {'type': 'split', 'pid': base_pid, 'first': first, 'gap': 30, 'first_worker': c1, 'second_worker': c1}, '人選': [c1]})
                        done = True
        required_split = minutes > limit
        for kind, items in found.items():
            for rank, item in enumerate(items):
                suggestions.append({'群組': pid, '日期': tids if len(tids) > 1 else tids[:1], '次數': len(tids), '種類': kind,
                                    '必要': kind == '拆成兩段' and required_split, '順位': rank, '代表日期': date, '週別': week_label, **item})
        if not any(found.values()):
            suggestions.append({'群組': pid, '日期': tids[:1], '次數': len(tids), '種類': '無解', '必要': False, '順位': 0, '代表日期': date, '週別': week_label,
                                '標題': f"{week_label}（{date} 等 {len(tids)} 次）目前找不到可行的調整",
                                '說明': '時間前後 2 小時、前後 3 天、所有居服員、拆成兩段都試過了；請到「05 人工調整班表」挪動衝突的既有班，或增加人力。',
                                'apply': None, '人選': []})
    suggestions.sort(key=lambda s: (0 if s['必要'] else 1, KIND_ORDER.get(s['種類'], 9), s['代表日期'], s['順位']))
    # 前幾項整案重跑預覽，算出套用後還剩幾次
    checked = 0
    before = len(failed)
    if run_preview is not None:
        seen = set()
        for s in suggestions:
            if s['apply'] is None or checked >= max_verify or (s['群組'], s['種類']) in seen:
                continue
            seen.add((s['群組'], s['種類']))
            try:
                after = run_preview(s['apply'])
                s['套用後仍未派'] = int(len(after['failed']))
                s['套用前仍未派'] = before
            except Exception as exc:  # 預覽失敗就不顯示預估
                s['預估失敗'] = str(exc)[:80]
            checked += 1
    return suggestions


def change_log_rows(case, edits, base_patterns, worker, team=(), reason=''):
    """把這次整案調整寫成「Excel 異動明細」格式（工作表／案號／異動／原值／新值／原因／需確認）。"""
    rows = []
    lookup = {str(r['模式ID']): r for r in base_patterns.to_dict('records')}

    def span(start, minutes):
        return f'{_fmt(start)}～{_fmt(start + minutes)}'
    for pid, row in lookup.items():
        shift = int((edits.get('shifts') or {}).get(pid, 0) or 0)
        start, minutes = _hm(row['開始時間']), int(round(float(row['服務分鐘'])))
        label = f"{row['星期']} {span(_hm(row['開始時間']), minutes)}（{minutes}分）"
        split = (edits.get('splits') or {}).get(pid)
        if shift and not split:
            rows.append({'工作表': '週服務計畫', '案號': case, '異動': f"服務時間{'後移' if shift > 0 else '前移'} {abs(shift)} 分鐘（服務分鐘不變）",
                         '原值': label, '新值': f"{row['星期']} {span(start + shift, minutes)}", '原因': reason, '需確認': '與案家確認新時段'})
        if split:
            first, gap = int(split['first']), int(split.get('gap', 0) or 0)
            second = minutes - first
            s0 = start + shift
            rows.append({'工作表': '週服務計畫', '案號': case, '異動': f"拆成兩段（{'中間休息 %d 分' % gap if gap else '兩位各做一段，不需休息'}）",
                         '原值': label,
                         '新值': f"{span(s0, first)}（{split.get('first_worker') or worker}）＋ {span(s0 + first + gap, second)}（{split.get('second_worker') or worker}）",
                         '原因': reason, '需確認': '與案家確認分段、核對兩段的服務項目與計費'})
        owner = (edits.get('pattern_workers') or {}).get(pid)
        if owner:
            rows.append({'工作表': '個案資料', '案號': case, '異動': '這個固定時段改由他人服務', '原值': label, '新值': owner,
                         '原因': reason, '需確認': '通知該居服員與案家'})
    for tid, mv in (edits.get('moves') or {}).items():
        rows.append({'工作表': '個別日期', '案號': case, '異動': '只改這一次的日期／時間', '原值': tid, '新值': f"{mv['date']} {mv['start']}",
                     '原因': reason, '需確認': '與案家確認這一天的新時段'})
    for tid, w in (edits.get('workers') or {}).items():
        rows.append({'工作表': '個別日期', '案號': case, '異動': '只改這一次的服務居服員', '原值': tid, '新值': w,
                     '原因': reason, '需確認': '通知該居服員'})
    rows.append({'工作表': '個案資料', '案號': case, '異動': '固定人選（主責＋備援）', '原值': '（調整前）', '新值': '、'.join([str(worker), *map(str, team)]),
                 '原因': reason, '需確認': '通知新主責、備援與案家'})
    return rows



# ───────────────────────── 白話原因與「方案」 ─────────────────────────
import re
import time

_ORDERS = (('時間挪動', '換人', '拆成兩段', '換天'),
           ('換人', '時間挪動', '拆成兩段', '換天'),
           ('拆成兩段', '時間挪動', '換人', '換天'))


def plain_reason(text):
    """把引擎的檢查結果改寫成居督看得懂的一句話，並附上可行方向。回傳 (白話原因, 方向, 類別)。"""
    t = str(text or '')
    m = re.search(r'只有\s*([\d.]+)\s*分鐘空檔，交通加緩衝需\s*([\d.]+)\s*分鐘', t)
    if m:
        gap, need = float(m.group(1)), float(m.group(2))
        return (f'前一個服務結束後只剩 {gap:g} 分鐘，趕到這裡要 {need:g} 分鐘（差 {max(need - gap, 0):g} 分鐘）',
                '把這個時段往後（或往前）挪一點就能解', '時間')
    if '連續工作' in t:
        m = re.search(r'將達\s*(\d+)\s*分鐘，上限\s*(\d+)', t)
        return (f"連續工作會到 {m.group(1)} 分鐘（上限 {m.group(2)}），中間排不出 30 分鐘休息" if m else '連續工作超過上限，中間排不出休息',
                '把服務拆成兩段，或兩位居服員分著做', '連續工作')
    if '重疊' in t:
        return ('和這位居服員已排的其他案家時間重疊', '改時間（前後挪）或改由別人', '時間')
    if '工時超標' in t:
        return ('這位居服員當天總工時（含轉場）會超過上限', '改由別人，或把部分時段移到別天', '工時')
    if '月加班' in t or '加班' in t:
        return ('這位居服員本月加班會超過上限', '改由別人', '工時')
    if '缺特殊訓練' in t or '資格' in t or '證照' in t or '限女' in t or '限男' in t:
        return (t.replace('缺特殊訓練：', '沒有這項訓練／資格：'), '改由有資格的居服員', '資格')
    if '請假' in t:
        return ('居服員當天請假，找不到代班', '改由別人或換天', '請假')
    if '休息日' in t or '不排班' in t:
        return ('這位居服員這天不排班', '改由別人或換天', '排班日')
    return (t[:80] or '原因待確認', '請看右邊的建議方案', '其他')


def diagnose(preview, unit_minutes=None, limit=240):
    """未派服務依「每週固定時段」分組，附白話原因。"""
    failed = preview['failed']
    if failed.empty:
        return []
    pattern_of = preview.get('pattern_of', {})
    tasks = preview['tasks'].set_index(preview['tasks']['任務ID'].astype(str))
    groups = {}
    for row in failed.to_dict('records'):
        tid = str(row['任務ID'])
        groups.setdefault(pattern_of.get(tid, tid), []).append(row)
    out = []
    for pid, rows in groups.items():
        first = tasks.loc[str(rows[0]['任務ID'])]
        minutes = int(round(float(first['服務歷時(分鐘)'])))
        why, hint, kind = plain_reason(rows[0]['原因'])
        if minutes > limit and kind != '資格':
            why = f'單次連續服務 {minutes} 分鐘，超過連續工作上限 {int(limit)} 分鐘，沒有人能不違規一次做完'
            hint, kind = '一定要拆成兩段（中間休息，或兩位居服員各做一段）', '連續工作'
        dates = sorted({f"{int(str(r['日期'])[5:7])}/{int(str(r['日期'])[8:10])}" for r in rows}, key=lambda x: tuple(int(p) for p in x.split('/')))
        out.append({'群組': pid, '時段': pending_week_label(first), '次數': len(rows), '日期': dates,
                    '原因': why, '方向': hint, '類別': kind, '原文': str(rows[0]['原因'])})
    out.sort(key=lambda g: (-g['次數'], g['時段']))
    return out


def _plan_notes(steps):
    notes = []
    kinds = {s['種類'] for s in steps}
    if kinds & {'時間挪動', '換天', '拆成兩段'}:
        notes.append('要先和案家確認新的服務時間／分段')
    if kinds & {'換人', '拆成兩段'}:
        notes.append('要通知新負責的居服員（會加入該案固定人選團隊）')
    return notes


def build_plans(base, caregivers, config, run_edits, edits, unit_minutes=None, max_plans=3, budget_seconds=45.0, memo=None):
    """從目前狀態出發，一步一步「試、驗證、套用」，產生能把整案排進去（或盡量排進去）的方案。

    run_edits(edits_obj) → 整案預覽。每個方案用不同的偏好順序（先挪時間／先換人／先拆段），
    每一步都用完整預覽驗證，只有「真的減少未派」才採用，所以顯示的『套用後剩幾次』是實際算過的。
    回傳 list[dict]，已排序：全部排進 → 剩餘少 → 步驟少。
    """
    if base['failed'].empty:
        return []
    deadline = time.time() + budget_seconds
    plans, seen = [], set()
    # memo：同一案、同一份班表底下，相同的調整只算一次（三種偏好順序、卡片與完整方案共用）。
    memo = memo if memo is not None else {}
    advice_memo, preview_memo = memo.setdefault('advice', {}), memo.setdefault('preview', {})

    def cached_run(trial_edits):
        key = repr(trial_edits)
        if key not in preview_memo:
            preview_memo[key] = run_edits(trial_edits)
        return preview_memo[key]
    for order in _ORDERS:
        if len(plans) >= max_plans or time.time() > deadline:
            break
        cur_edits, preview, steps = copy.deepcopy(edits) if edits else empty_edits(), base, []
        for _ in range(4):
            if preview['failed'].empty or time.time() > deadline:
                break
            _akey = repr(cur_edits)
            if _akey not in advice_memo:
                advice_memo[_akey] = [c for c in advise(preview, caregivers, config, None, unit_minutes, max_groups=1, per_kind=2) if c.get('apply')]
            cands = list(advice_memo[_akey])
            if any(c['必要'] for c in cands):
                cands = [c for c in cands if c['必要']]
            before, pick = len(preview['failed']), None
            for kind in order:
                best = None
                for cand in [c for c in cands if c['種類'] == kind][:3]:
                    try:
                        trial_edits = apply_suggestion(cur_edits, cand['apply'])
                        after = cached_run(trial_edits)
                    except Exception:
                        continue
                    n = len(after['failed'])
                    if best is None or n < best[0]:
                        best = (n, cand, after, trial_edits)
                    if n == 0:
                        break
                if best and best[0] < before:
                    pick = best
                    break
            if pick is None:
                break
            steps.append(pick[1])
            cur_edits, preview = pick[3], pick[2]
        if not steps:
            continue
        key = tuple(sorted(repr(s['apply']) for s in steps))
        if key in seen:
            continue
        seen.add(key)
        left = len(preview['failed'])
        plans.append({'標題': '；'.join(('' if len(steps) == 1 else f'{i}. ') + s['標題'] for i, s in enumerate(steps, 1)),
                      '步驟': steps, 'applies': [s['apply'] for s in steps], '仍未派': left, '總次數': preview['total'],
                      '種類': sorted({s['種類'] for s in steps}), '需確認': _plan_notes(steps), '人選': sorted({w for s in steps for w in s.get('人選', [])})})
    plans.sort(key=lambda p: (p['仍未派'], len(p['步驟']), KIND_ORDER.get(p['種類'][0], 9)))
    return plans
