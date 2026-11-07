"""One pending row per client and a read-only whole-client assignment preview."""
CAREFLOW_BUILD = '2026.10.18-v19'
from dataclasses import replace
import hashlib
from datetime import datetime
import pandas as pd
from agency_rules import clean, leave_overlap
from recurring_schedule import visit_pattern


_TOKEN_KEYS = ('來源列','案家ID','時間窗_開始','時間窗_結束','服務歷時(分鐘)')


def pattern_token(task):
    """固定時段的身分（星期、來源列、起迄時間、服務分鐘、服務項目）。

    刻意不含案家資料帶進來的欄位（是否失智、特殊照護…）：同一份任務表「合併案家資料前」與「合併後」算出來必須相同，
    否則表格上指定的負責居服員、調整時間會對不到任務。
    """
    get = task.get
    day = pd.Timestamp(get('日期')).isoweekday()
    codes = sorted((str(k),clean(get(k))) for k in task.keys() if str(k).startswith(('Service_Code_','Units_')))
    keys = tuple((k,clean(get(k))) for k in _TOKEN_KEYS)
    return hashlib.sha256(repr((day,)+keys+tuple(codes)).encode()).hexdigest()[:16]


def pending_client_summary(tasks, assignments, diagnostics=None, owners=None):
    """Count unique clients separately from their actual pending visits."""
    columns = ['案家ID','固定居服員ID','未派服務數','全部服務數','已派服務數',
               '首次未派日期','最後未派日期','每週待處理時段','未派原因']
    valid = assignments.copy()
    if {'任務ID','派單居服員'}.issubset(valid.columns):
        valid = valid.loc[valid['派單居服員'].map(clean).isin(['','未指派','None','nan']).eq(False)]
        assigned = set(valid['任務ID'].astype(str))
    else:
        assigned = set()
    unique = tasks.drop_duplicates('任務ID').copy()
    unique['_pending'] = ~unique['任務ID'].astype(str).isin(assigned)
    diag = diagnostics.to_dict('records') if isinstance(diagnostics,pd.DataFrame) else diagnostics or []
    diag = {str(row.get('任務ID')):row for row in diag}
    rows = []
    for client,frame in unique.groupby('案家ID',sort=True):
        pending = frame.loc[frame['_pending']]
        if pending.empty:
            continue
        slots = sorted({f"{pd.Timestamp(t['日期']).day_name() if not clean(t.get('星期')) else t['星期']} {t['時間窗_開始']}～{t['時間窗_結束']}"
                        for t in pending.to_dict('records')})
        reasons = sorted({clean(diag.get(str(t),{}).get('問題分類')) or
                          clean(diag.get(str(t),{}).get('診斷')) or '待核對目前班表'
                          for t in pending['任務ID']})
        rows.append(dict(zip(columns,[str(client),(owners or {}).get(str(client),''),len(pending),len(frame),
            len(frame)-len(pending),str(pending['日期'].min())[:10],str(pending['日期'].max())[:10],
            '；'.join(slots),'；'.join(reasons)])))
    return pd.DataFrame(rows,columns=columns).sort_values('未派服務數',ascending=False,kind='stable').reset_index(drop=True)


def client_patterns(tasks, client):
    rows = []
    seen = set()
    for task in tasks.loc[tasks['案家ID'].astype(str).eq(str(client))].to_dict('records'):
        token = pattern_token(task)
        if token in seen:
            continue
        seen.add(token)
        rows.append({'模式ID':token,'來源列':task.get('來源列',''),'星期':task.get('星期',''),
                     '開始時間':task['時間窗_開始'],'結束時間':task['時間窗_結束'],
                     '服務分鐘':task['服務歷時(分鐘)']})
    return pd.DataFrame(rows)


def _hm(text):
    t = str(text).strip()
    parts = t.split(':')
    return int(parts[0]) * 60 + int(parts[1])


def _fmt(minutes):
    minutes = int(round(minutes))
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def service_items(task):
    """任務上的 (服務碼, 單位數) 清單。"""
    items = []
    for i in range(1, 13):
        code, units = task.get(f'Service_Code_{i}'), task.get(f'Units_{i}')
        if code is None or pd.isna(code) or not str(code).strip():
            continue
        try:
            units = float(units)
        except (TypeError, ValueError):
            continue
        if units > 0:
            items.append((str(code).strip(), units))
    return items


def unit_minutes_from_master(master):
    """Service_code 表 → {服務碼: 每單位分鐘}。"""
    out = {}
    if master is None or len(master) == 0:
        return out
    for code, minutes in zip(master.get('系統代碼', []), master.get('CareFlow排班分鐘(暫定)', [])):
        try:
            value = float(minutes)
        except (TypeError, ValueError):
            continue
        if value > 0:
            out[str(code).strip().upper()] = value
    return out


def _unit_table(task, unit_minutes):
    items = service_items(task)
    if not items:
        raise ValueError('此服務沒有服務項目，無法拆成兩段。')
    mins = {}
    for code, _ in items:
        value = (unit_minutes or {}).get(code.upper())
        if not value:
            raise ValueError(f'服務碼 {code} 沒有每單位分鐘數，無法拆成兩段。')
        mins[code] = float(value)
    total = float(task['服務歷時(分鐘)'])
    if abs(sum(mins[c] * u for c, u in items) - total) > 1e-6:
        raise ValueError('服務項目的單位分鐘合計與服務總分鐘不同，無法依項目拆成兩段。')
    return items, mins, total


def split_points(task, unit_minutes, minimum=30):
    """第一段可選的分鐘數（必須落在服務單位的邊界上，兩段都不少於 minimum 分鐘）。"""
    from math import gcd
    from functools import reduce
    items, mins, total = _unit_table(task, unit_minutes)
    step = reduce(gcd, [int(round(v)) for v in mins.values()])
    total = int(round(total))
    return [m for m in range(step, total, step) if m >= minimum and total - m >= minimum]


def default_split_minutes(task, unit_minutes):
    points = split_points(task, unit_minutes)
    if not points:
        raise ValueError('這個服務太短，無法拆成兩段。')
    half = float(task['服務歷時(分鐘)']) / 2
    return min(points, key=lambda m: (abs(m - half), m))


def split_items(task, first_minutes, unit_minutes):
    """把服務項目依分鐘切成兩段：先按比例分配，對不齊時改成依序填滿。"""
    import math
    items, mins, total = _unit_table(task, unit_minutes)
    first = float(first_minutes)
    if first not in [float(x) for x in split_points(task, unit_minutes, minimum=1)]:
        raise ValueError('第一段分鐘必須落在服務單位邊界上。')
    ratio = first / total
    alloc = {c: min(u, math.floor(u * ratio + 1e-9)) for c, u in items}
    units = dict(items)
    used = sum(alloc[c] * mins[c] for c in alloc)
    while used < first - 1e-9:
        options = [(units[c] * ratio - alloc[c], c) for c in alloc if alloc[c] < units[c] and mins[c] <= first - used + 1e-9]
        if not options:
            break
        _, pick = max(options)
        alloc[pick] += 1
        used += mins[pick]
    if abs(used - first) > 1e-9:
        alloc, used = {c: 0 for c in units}, 0.0
        for c, u in items:
            take = min(u, math.floor((first - used) / mins[c] + 1e-9))
            alloc[c] = take
            used += take * mins[c]
        if abs(used - first) > 1e-9:
            raise ValueError('第一段分鐘無法對齊服務單位。')
    one = [(c, alloc[c]) for c, _ in items if alloc[c] > 0]
    two = [(c, units[c] - alloc[c]) for c, _ in items if units[c] - alloc[c] > 0]
    return one, two


def _segment_id(task_id):
    import re
    text = str(task_id)
    changed = re.sub(r'^(W\d+)_', r'\1B_', text, count=1)
    return changed if changed != text else text + '_B'


def split_task(task, first_minutes, unit_minutes, edit_one, edit_two):
    """依兩列時間表把一筆服務拆成兩筆（第一段沿用原任務ID，第二段加 B）。"""
    one, two = split_items(task, first_minutes, unit_minutes)
    total = float(task['服務歷時(分鐘)'])
    rows = []
    for suffix, items, minutes, edit in (('', one, float(first_minutes), edit_one), ('B', two, total - float(first_minutes), edit_two)):
        row = dict(task)
        for i in range(1, 13):
            row.pop(f'Service_Code_{i}', None)
            row.pop(f'Units_{i}', None)
        for i, (code, units) in enumerate(items, start=1):
            row[f'Service_Code_{i}'], row[f'Units_{i}'] = code, float(units)
        start, end = str(edit['開始時間']).strip(), str(edit['結束時間']).strip()
        if _hm(end) - _hm(start) != int(round(minutes)):
            raise ValueError(f'拆段後每段的結束時間減開始時間必須等於該段服務分鐘（{int(round(minutes))} 分）。')
        row['服務歷時(分鐘)'] = minutes
        row['時間窗_開始'], row['時間窗_結束'] = _fmt(_hm(start)), _fmt(_hm(end))
        for col, value in (('服務分鐘', minutes), ('服務時數', minutes / 60), ('開始時間', _fmt(_hm(start)) + ':00'), ('結束時間', _fmt(_hm(end)) + ':00')):
            if col in row:
                row[col] = value
        if '服務項目' in row:
            row['服務項目'] = ' '.join(f'{c}({int(u) if float(u).is_integer() else u})' for c, u in items)
        if suffix:
            row['任務ID'] = _segment_id(task['任務ID'])
        rows.append(row)
    return rows[0], rows[1]


def pending_week_label(task):
    """『每週三 12:40～17:40』這種固定時段名稱（task 可為 dict 或 Series）。"""
    day = clean(task.get('星期')) or '星期' + '一二三四五六日'[pd.Timestamp(task['日期']).weekday()]
    return f"每{str(day).replace('星期', '週')} {task['時間窗_開始']}～{task['時間窗_結束']}"


def preview_with_edits(tasks, assignments, caregivers, clients, config, client, worker, edits, pattern_workers=None, task_workers=None,
                       unit_minutes=None):
    """依「調整清單」（後移／拆段／換天／換人）組出時間表後做整案預覽。"""
    edits = edits or {}
    table = edited_pattern_table(tasks, client, edits)
    workers = {}
    for row in table.to_dict('records'):
        pid = row['模式ID']
        base = pid.split('#')[0]
        owner = (edits.get('pattern_workers') or {}).get(pid) or (pattern_workers or {}).get(pid)
        if not owner and '#' in pid:
            split = (edits.get('splits') or {}).get(base, {})
            owner = split.get('second_worker') if pid.endswith('#2') else split.get('first_worker')
            owner = owner or (pattern_workers or {}).get(base)
        workers[pid] = owner or worker
    return preview_client_assignment(tasks, assignments, caregivers, clients, config, client, worker, table, workers,
                                     {**(task_workers or {}), **(edits.get('workers') or {})},
                                     splits=edits.get('splits'), moves=edits.get('moves'), unit_minutes=unit_minutes)


def edited_pattern_table(tasks, client, edits=None):
    """每個固定時段一列；套用「後移／拆成兩段」的調整，供整案預覽與表格編輯使用。"""
    edits = edits or {}
    shifts, splits = edits.get('shifts') or {}, edits.get('splits') or {}
    rows = []
    for row in client_patterns(tasks, client).to_dict('records'):
        pid = row['模式ID']
        start = _hm(row['開始時間']) + int(shifts.get(pid, 0) or 0)
        duration = int(round(float(row['服務分鐘'])))
        split = splits.get(pid)
        if split:
            first, gap = int(split['first']), int(split.get('gap', 0) or 0)
            second = duration - first
            rows.append({**row, '模式ID': pid + '#1', '開始時間': _fmt(start), '結束時間': _fmt(start + first), '服務分鐘': first, '段': '第一段'})
            rows.append({**row, '模式ID': pid + '#2', '開始時間': _fmt(start + first + gap), '結束時間': _fmt(start + first + gap + second),
                         '服務分鐘': second, '段': '第二段'})
        else:
            rows.append({**row, '開始時間': _fmt(start), '結束時間': _fmt(start + duration), '段': ''})
    return pd.DataFrame(rows)


def preview_client_assignment(tasks, assignments, caregivers, clients, config, client, worker, patterns=None, pattern_workers=None, task_workers=None,
                              splits=None, moves=None, unit_minutes=None):
    """Check every date atomically; caller decides whether to apply the preview.

    The selected worker becomes the client owner across all weekdays. Only
    overlapping actual leave permits retaining/selecting a qualified backup.
    pattern_workers（模式ID → 居服員）可把不同星期／時段交給不同人：被指定的人與主責一起成為
    該案的固定人選團隊（主責＋備援），每一筆仍逐項檢查資格、交通、休息與工時。
    splits（模式ID → {'first': 第一段分鐘}）把該固定時段每一次都拆成兩段（模式ID#1／#2 各自在 patterns 有一列時間與負責人）；
    moves（任務ID → {'date','start'}）只把個別日期改到別天／別的開始時間（服務分鐘不變）。
    """
    import caregiver_engine as engine
    client,worker = str(client),str(worker)
    pattern_workers = {str(k):str(v) for k,v in (pattern_workers or {}).items() if str(v) and str(v)!='nan'}
    splits = {str(k):dict(v) for k,v in (splits or {}).items()}
    moves = {str(k):dict(v) for k,v in (moves or {}).items()}
    proposed = engine.merge_task_client_tables(tasks,clients).copy()
    proposed['日期'] = pd.to_datetime(proposed['日期']).dt.strftime('%Y-%m-%d')
    people = {str(p['居服員ID']):p for p in caregivers.to_dict('records')}
    task_workers = {str(k):str(v) for k,v in (task_workers or {}).items() if str(v) and str(v)!='nan'}
    team = [worker] + sorted({w for w in [*pattern_workers.values(),*task_workers.values()] if w!=worker})
    if any(w not in people for w in team):
        raise ValueError('找不到選取的居服員。')
    mask = proposed['案家ID'].astype(str).eq(client)
    if not mask.any():
        raise ValueError('找不到案家服務。')
    edits = {str(r['模式ID']):r for r in patterns.to_dict('records')} if patterns is not None else {}
    # 固定時段的身分要在改時間「之前」記下來；之後查負責人都用這份（不然改了時間就找不到人）。
    owner_key = {str(t['任務ID']):pattern_token(t) for _,t in proposed.loc[mask].iterrows()}
    extra = []
    for index,task in proposed.loc[mask].iterrows():
        pid = owner_key[str(task['任務ID'])]
        if pid in splits:
            one,two = edits.get(pid+'#1'),edits.get(pid+'#2')
            if not one or not two:
                raise ValueError('拆段資料不完整。')
            first,second = split_task(task,float(splits[pid]['first']),unit_minutes,one,two)
            for col in [c for c in proposed.columns if str(c).startswith(('Service_Code_','Units_'))]:
                proposed.at[index,col] = float('nan')
            for key,value in first.items():
                if key in proposed.columns:
                    proposed.at[index,key] = value
            extra.append(second)
            owner_key[str(task['任務ID'])] = pid+'#1'
            owner_key[str(second['任務ID'])] = pid+'#2'
            continue
        edit = edits.get(pid)
        if not edit:
            continue
        try:
            start,end = [datetime.strptime(str(edit[k]).strip(),'%H:%M') for k in ('開始時間','結束時間')]
        except ValueError as exc:
            raise ValueError('請使用 HH:MM 時間格式。') from exc
        if end<=start or abs((end-start).total_seconds()/60-float(task['服務歷時(分鐘)']))>1e-6:
            raise ValueError('整案調整須維持每筆原服務分鐘，且結束時間晚於開始。')
        proposed.loc[index,['時間窗_開始','時間窗_結束']] = [start.strftime('%H:%M'),end.strftime('%H:%M')]
    if extra:
        proposed = pd.concat([proposed,pd.DataFrame(extra).reindex(columns=proposed.columns)],ignore_index=True)
        mask = proposed['案家ID'].astype(str).eq(client)
    weekday_text = '一二三四五六日'
    for tid,move in moves.items():
        hit = proposed.index[proposed['任務ID'].astype(str).eq(tid)]
        if not len(hit):
            continue
        index = hit[0]
        minutes = float(proposed.at[index,'服務歷時(分鐘)'])
        date = pd.Timestamp(move['date'])
        start = _hm(move.get('start') or proposed.at[index,'時間窗_開始'])
        proposed.at[index,'日期'] = date.strftime('%Y-%m-%d')
        proposed.at[index,'時間窗_開始'] = _fmt(start)
        proposed.at[index,'時間窗_結束'] = _fmt(start+minutes)
        if '星期' in proposed.columns:
            proposed.at[index,'星期'] = '星期'+weekday_text[date.weekday()]
    selected = proposed.loc[mask].sort_values(['日期','時間窗_開始','任務ID'])
    case_ids = set(selected['任務ID'])
    remaining = assignments.loc[~assignments['任務ID'].isin(case_ids)].copy()
    if '派單居服員' in remaining:
        remaining = remaining.loc[~remaining['派單居服員'].map(clean).isin(['','未指派','None','nan'])]
    trial = replace(config,client_primary_map={**config.client_primary_map,client:worker},
                    client_backup_map={**getattr(config,'client_backup_map',{}),client:tuple(team[1:])},
                    primary_profiles=people,work_day_minutes=dict(config.work_day_minutes),dispatch_cache={})
    days = {day:frame.copy() for day,frame in proposed.groupby('日期',sort=True)}
    assigned = {day:remaining.loc[remaining['任務ID'].isin(frame['任務ID'])].copy() for day,frame in days.items()}
    trial.dispatch_cache={'caregivers':caregivers,'people':people,'busy':engine._build_cg_busy_blocks(caregivers),
        'days':{id(f):{'frame':f,'ids':set(f['任務ID']),'lookup':f.set_index('任務ID').to_dict('index')} for f in days.values()}}
    prepared = set()
    accepted,failed,leaves = [],[],[]
    def prepare(candidate,date):
        key = candidate,date[:7]
        if key not in prepared:
            current = pd.concat([remaining,pd.DataFrame(accepted,columns=['任務ID','派單居服員'])],ignore_index=True)
            trial.work_day_minutes = engine._effective_month_ledger('',candidate,pd.Timestamp(date),
                proposed,current,caregivers,trial)
            prepared.add(key)
    def evaluate(task,candidate):
        prepare(candidate,task['日期'])
        return engine._evaluate_reassignment(task['任務ID'],candidate,days[task['日期']],
                                            assigned[task['日期']],caregivers,trial)
    def add(task,candidate,checked):
        row={'任務ID':task['任務ID'],'派單居服員':candidate}
        accepted.append(row)
        assigned[task['日期']] = pd.concat([assigned[task['日期']],pd.DataFrame([row])],ignore_index=True)
        trial.work_day_minutes[(candidate,task['日期'])]=checked['working_minutes']
    for _,task in selected.iterrows():
        owner = task_workers.get(str(task['任務ID'])) or pattern_workers.get(owner_key.get(str(task['任務ID'])),worker)
        if leave_overlap(task,people[owner]):
            leaves.append((task,owner))
            continue
        checked = evaluate(task,owner)
        if checked['available']:
            add(task,owner,checked)
        else:
            failed.append({'任務ID':task['任務ID'],'日期':task['日期'],'原因':checked['detail']})
    old = dict(zip(assignments.get('任務ID',[]),assignments.get('派單居服員',[])))
    for task,owner in leaves:
        previous = clean(old.get(task['任務ID']))
        candidates = ([previous] if previous in people and previous!=owner else [])
        candidates += [p for p in people if p!=owner and p not in candidates]
        reasons=[]
        for candidate in candidates:
            checked=evaluate(task,candidate)
            if checked['available']:
                add(task,candidate,checked)
                break
            reasons.append(f'{candidate}：{checked["detail"]}')
        else:
            failed.append({'任務ID':task['任務ID'],'日期':task['日期'],'原因':'請假代班缺人；'+'；'.join(reasons)})

    def evaluate_row(row,candidate,extra_assigned=(),live_transit=False):
        """以目前（預覽結束時）的班表，檢查「某一筆（可以是改過時間／日期、或全新的拆段）服務」能不能由某人接。
        提供給處理建議使用；不會更動預覽結果。"""
        # 分析階段不替「搭大眾運輸」的居服員逐一查 Google（每個假設時段都是一次付費查詢、每次 1～2 秒）；
        # 他們仍可在「我想自己調整」或整案預覽時被指定，那時才會真的查。
        if not live_transit and str(people.get(str(candidate), {}).get('常用交通工具') or '機車').strip() == '大眾運輸':
            return {'cg_id': candidate, 'available': False, 'reason': 'transit_skipped',
                    'detail': '搭大眾運輸的居服員不在自動建議內（需逐時段查 Google）；要指定請用「我想自己調整」'}
        row = dict(row)
        date = str(row['日期'])[:10]
        row['日期'] = date
        base = proposed.loc[proposed['日期'].eq(date)&proposed['任務ID'].astype(str).ne(str(row['任務ID']))]
        frame = pd.concat([base,pd.DataFrame([row]).reindex(columns=proposed.columns)],ignore_index=True)
        current = pd.concat([remaining,pd.DataFrame(accepted,columns=['任務ID','派單居服員']),
                             pd.DataFrame(list(extra_assigned),columns=['任務ID','派單居服員'])],ignore_index=True)
        day_assigned = current.loc[current['任務ID'].isin(frame['任務ID'])&current['任務ID'].astype(str).ne(str(row['任務ID']))]
        prepare(candidate,date)
        # 固定人選之外的人：暫時當成這個案家的備援來檢查（建議「換人」時才會真的加入團隊）。
        saved = trial.client_backup_map
        if candidate!=worker and candidate not in team:
            trial.client_backup_map = {**saved,client:tuple(team[1:])+(candidate,)}
        # 當天其他任務的查詢表沿用預覽時已建好的，只補上這一筆（省掉每次把整天資料轉成字典）。
        registered = None
        base_entry = trial.dispatch_cache['days'].get(id(days.get(date))) if date in days else None
        if base_entry is not None:
            one = pd.DataFrame([row]).reindex(columns=proposed.columns).set_index('任務ID').to_dict('index')
            lookup = {k: v for k, v in base_entry['lookup'].items() if k != row['任務ID']}
            lookup.update(one)
            registered = id(frame)
            trial.dispatch_cache['days'][registered] = {'frame': frame, 'ids': set(frame['任務ID']), 'lookup': lookup}
        try:
            return engine._evaluate_reassignment(row['任務ID'],candidate,frame,day_assigned,caregivers,trial)
        finally:
            trial.client_backup_map = saved
            if registered is not None:
                trial.dispatch_cache['days'].pop(registered, None)
    return {'tasks':proposed,'accepted':pd.DataFrame(accepted,columns=['任務ID','派單居服員']),
            'failed':pd.DataFrame(failed,columns=['任務ID','日期','原因']),
            'client':client,'worker':worker,'team':tuple(team[1:]),'total':len(selected),'primary_map':trial.client_primary_map,
            'pattern_workers':pattern_workers,'task_workers':task_workers,'pattern_of':owner_key,
            'splits':splits,'moves':moves,'evaluate_row':evaluate_row,
            'ledger':dict(trial.work_day_minutes)}


def suggest_team(tasks, accepted_by_worker, max_team=3, preferred=None):
    """沒有人能一人接下整案時，依「每個固定時段（模式）整組交給同一人」做貪婪組合。

    tasks：該案全部服務（含 任務ID 與可算 pattern_token 的欄位）。
    accepted_by_worker：{居服員: 單獨試算時可排進的任務ID集合}。
    回傳 {'pattern_workers': {模式ID: 居服員}, 'main': 主責, 'covered': 可排次數, 'total': 總次數, 'team': [...]}；
    組合只是建議，實際可行與否要再用 preview_client_assignment 驗證。
    """
    rows = [(str(t['任務ID']),pattern_token(t)) for t in tasks.to_dict('records')]
    patterns = {}
    for tid,token in rows:
        patterns.setdefault(token,[]).append(tid)
    remaining = dict(patterns)
    chosen,team = {},[]
    while remaining and len(team) < max_team:
        best,best_key = None,(0,0)
        for worker,ids in accepted_by_worker.items():
            if worker in team:
                continue
            ids = {str(i) for i in ids}
            whole = [tok for tok,tids in remaining.items() if all(t in ids for t in tids)]
            visits = sum(len(remaining[tok]) for tok in whole)
            partial = sum(len(ids.intersection(tids)) for tids in remaining.values())
            key = (visits,partial) if visits else (0,partial)
            if worker == preferred:
                key = (key[0],key[1]+0.5)
            if key > best_key:
                best,best_key = worker,key
        if best is None:
            break
        ids = {str(i) for i in accepted_by_worker[best]}
        take = [tok for tok,tids in remaining.items() if all(t in ids for t in tids)]
        if not take:   # 沒有完整涵蓋的固定時段：把能排最多的那幾個時段先給他
            take = [tok for tok,tids in remaining.items() if ids.intersection(tids)]
        for tok in take:
            chosen[tok] = best
            remaining.pop(tok)
        team.append(best)
    covered = sum(len({str(i) for i in accepted_by_worker[chosen[tok]]}.intersection(patterns[tok])) for tok in chosen)
    load = {}
    for tok,worker in chosen.items():
        load[worker] = load.get(worker,0) + len(patterns[tok])
    main = max(load,key=load.get) if load else (preferred or '')
    return {'pattern_workers':chosen,'main':main,'covered':covered,'total':len(rows),'team':team,
            'uncovered_patterns':list(remaining)}


def plan_team(tasks, assignments, caregivers, clients, config, client, patterns, accepted_by_worker, preferred=None,
              max_team=3, max_extra=1):
    """建議並驗證「多人分派」：先把固定時段整組分給最適合的人，剩下排不進的個別日期再逐筆補人。

    回傳 {'main','pattern_workers','task_workers','team','accepted','failed','total','preview'}；
    preview 是最後一次真實驗證（逐筆檢查資格、交通、休息、工時）的結果。
    """
    client = str(client)
    frame = tasks.loc[tasks['案家ID'].astype(str).eq(client)]
    plan = suggest_team(frame, accepted_by_worker, max_team=max_team, preferred=preferred)
    main = plan['main'] or (preferred or '')
    if not plan['pattern_workers']:
        return {**plan,'main':main,'task_workers':{},'accepted':0,'failed':len(frame),'total':len(frame),'preview':None}
    task_workers,preview = {},None
    for _ in range(3):
        preview = preview_client_assignment(tasks,assignments,caregivers,clients,config,client,main,patterns,
                                            plan['pattern_workers'],task_workers)
        failed = [str(t) for t in preview['failed']['任務ID']]
        if not failed:
            break
        members = set(plan['pattern_workers'].values()) | set(task_workers.values()) | {main}
        changed = False
        for tid in failed:
            if task_workers.get(tid) and tid not in set(preview['failed']['任務ID'].astype(str)):
                continue
            options = [(w in members, len(ids)) for w,ids in accepted_by_worker.items() if tid in {str(i) for i in ids}]
            ranked = sorted(((w in members, len(ids), w) for w,ids in accepted_by_worker.items()
                             if tid in {str(i) for i in ids} and w != task_workers.get(tid)), reverse=True)
            if not ranked:
                continue
            new_members = members | {ranked[0][2]}
            if len(new_members) > max_team + max_extra and ranked[0][2] not in members:
                inside = [r for r in ranked if r[0]]
                if not inside:
                    continue
                ranked = inside
            task_workers[tid] = ranked[0][2]
            members.add(ranked[0][2])
            changed = True
        if not changed:
            break
    return {**plan,'main':main,'task_workers':task_workers,'accepted':len(preview['accepted']),
            'failed':len(preview['failed']),'total':preview['total'],'preview':preview}
