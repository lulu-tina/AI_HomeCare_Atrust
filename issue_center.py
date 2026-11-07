"""衝突與異常處理：把排班結果中的問題整理成居督看得懂的分類。

只做資料整理（pandas），不呼叫排班演算法、不查交通、不使用 Streamlit；
供 app.py「③ 衝突與異常處理」模組與「④ 排班結果與覆核」的警示案件共用。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import pandas as pd

# 問題類型（畫面顯示順序）：(類型, 說明, 建議處理)
ISSUE_GROUPS = [
    ('班次重疊', '同一位居服員的服務時段互相重疊', '改派其中一筆給其他人，或與案家協調時段'),
    ('轉場不足', '前後兩案之間交通加緩衝時間不夠，或交通資料無法計算', '調整前後案時段、改派順路的居服員，或核對座標'),
    ('條件不符', '證照／訓練、資格期限、可服務星期等條件不合', '改派資格符合者，或核對員工資料'),
    ('工時與休息', '每日工時、休息或連續上班天數超過限制', '分散給其他人，或調整服務時段'),
    ('請假與待處理', '請假後缺代班，或居督標記待處理', '安排代班或人工改派'),
    ('其他未成功派班', '資料或其他限制', '查看逐人原因並核對服務計畫'),
]
GROUP_NAMES = [g[0] for g in ISSUE_GROUPS]

# monthly_scheduler.failure_kind／team_scheduler 的「問題分類」→ 問題類型
CATEGORY_TO_GROUP = {
    '時段重疊': '班次重疊',
    '轉場空檔不足': '轉場不足', '交通資料': '轉場不足',
    '必要證照': '條件不符', '資格日期': '條件不符', '可服務星期': '條件不符',
    '休息不足': '工時與休息', '工時上限': '工時與休息', '連續工作天數': '工時與休息',
    '請假代班缺人': '請假與待處理', '人工待處理': '請假與待處理',
}

_EMPTY_VALUES = ('', '未指派', 'None', 'nan', 'NaT')


def issue_group(category):
    """把排班引擎的「問題分類」歸到居督使用的問題類型。"""
    return CATEGORY_TO_GROUP.get(str(category or '').strip(), '其他未成功派班')


def _text(value):
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _minutes(value):
    """'HH:MM'、'HH:MM:SS'、time 或 Timestamp → 當日分鐘數；無法辨識回傳 None。"""
    text = _text(value)
    if not text:
        return None
    if hasattr(value, 'hour') and hasattr(value, 'minute'):
        return int(value.hour) * 60 + int(value.minute)
    parts = text.split(' ')[-1].split(':')
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return None


def _day(value):
    text = '' if value is None else str(value)
    if len(text) >= 10 and text[4] == '-' and text[7] == '-' and text[:4].isdigit() and text[5:7].isdigit() and text[8:10].isdigit():
        return text[:10]          # 'YYYY-MM-DD…' 直接取，不必每筆交給 pandas 猜格式（大量呼叫時很慢）
    stamp = pd.to_datetime(value, errors='coerce')
    return '' if pd.isna(stamp) else stamp.strftime('%Y-%m-%d')


def _valid_assignments(assignments):
    if assignments is None or assignments.empty or not {'任務ID', '派單居服員'} <= set(assignments.columns):
        return pd.DataFrame(columns=['任務ID', '派單居服員'])
    frame = assignments[['任務ID', '派單居服員']].copy()
    frame['任務ID'] = frame['任務ID'].astype(str)
    frame['派單居服員'] = frame['派單居服員'].map(_text)
    frame = frame.loc[~frame['派單居服員'].isin(_EMPTY_VALUES)]
    return frame.drop_duplicates('任務ID', keep='last')


def unassigned_issues(pending_tasks, diagnostics):
    """未成功派班的服務：每筆一列，附問題類型、原因與建議處理。"""
    columns = ['問題類型', '原因分類', '日期', '時段', '案家ID', '任務ID', '原因', '建議處理']
    if pending_tasks is None or pending_tasks.empty or '任務ID' not in pending_tasks.columns:
        return pd.DataFrame(columns=columns)
    diag_map = {}
    for item in diagnostics or []:
        diag_map[str(item.get('任務ID'))] = item          # 與畫面其他地方相同：同任務以最後一筆為準
    rows = []
    for task in pending_tasks.drop_duplicates('任務ID').to_dict('records'):
        info = diag_map.get(str(task['任務ID']), {})
        category = _text(info.get('問題分類')) or '待核對'
        group = issue_group(category)
        advice = _text(info.get('建議處理')) or dict((g, a) for g, _, a in ISSUE_GROUPS)[group]
        rows.append({'問題類型': group, '原因分類': category, '日期': _day(task.get('日期')),
                     '時段': f"{_text(task.get('時間窗_開始'))}–{_text(task.get('時間窗_結束'))}",
                     '案家ID': _text(task.get('案家ID')), '任務ID': str(task['任務ID']),
                     '原因': _text(info.get('剔除原因')) or '待核對可用人選', '建議處理': advice})
    frame = pd.DataFrame(rows, columns=columns)
    return frame.sort_values(['日期', '時段', '案家ID'], kind='stable').reset_index(drop=True)


def assigned_overlaps(tasks, assignments):
    """已派服務中，同一位居服員同一天時段互相重疊者（通常來自人工改派或臨時插單）。"""
    columns = ['日期', '居服員', '任務ID', '案家ID', '時段', '重疊任務ID', '重疊案家ID', '重疊時段']
    valid = _valid_assignments(assignments)
    needed = {'任務ID', '日期', '時間窗_開始', '時間窗_結束'}
    if valid.empty or tasks is None or tasks.empty or not needed <= set(tasks.columns):
        return pd.DataFrame(columns=columns)
    keep = [c for c in ['任務ID', '案家ID', '日期', '時間窗_開始', '時間窗_結束'] if c in tasks.columns]
    info = tasks[keep].copy()
    info['任務ID'] = info['任務ID'].astype(str)
    info = info.drop_duplicates('任務ID', keep='last')
    merged = valid.merge(info, on='任務ID', how='inner')
    merged['_day'] = merged['日期'].map(_day)
    merged['_start'] = merged['時間窗_開始'].map(_minutes)
    merged['_end'] = merged['時間窗_結束'].map(_minutes)
    merged = merged.dropna(subset=['_start', '_end'])
    merged = merged.loc[merged['_day'].ne('')]
    rows = []
    for (worker, day), group in merged.groupby(['派單居服員', '_day'], sort=True):
        if len(group) < 2:
            continue
        ordered = group.sort_values(['_start', '_end']).to_dict('records')
        latest = ordered[0]
        for current in ordered[1:]:
            if current['_start'] < latest['_end']:
                rows.append({'日期': day, '居服員': worker, '任務ID': current['任務ID'],
                             '案家ID': _text(current.get('案家ID')),
                             '時段': f"{_text(current['時間窗_開始'])}–{_text(current['時間窗_結束'])}",
                             '重疊任務ID': latest['任務ID'], '重疊案家ID': _text(latest.get('案家ID')),
                             '重疊時段': f"{_text(latest['時間窗_開始'])}–{_text(latest['時間窗_結束'])}"})
            if current['_end'] > latest['_end']:
                latest = current
    return pd.DataFrame(rows, columns=columns)


def transition_issues(routes):
    """已派服務的逐段交通中，轉場時間不足或交通無法計算者。"""
    columns = ['問題', '交通日期', '派單居服員', '任務ID', '交通路段', '預估車程(分)', '含緩衝交通時間(分)', '說明']
    if routes is None or routes.empty or '任務ID' not in routes.columns:
        return pd.DataFrame(columns=columns)
    frame = routes.copy()
    check = frame['轉場時段檢查'].map(_text) if '轉場時段檢查' in frame else pd.Series('', index=frame.index)
    status = frame['交通計算狀態'].map(_text) if '交通計算狀態' in frame else pd.Series('', index=frame.index)
    short = check.str.contains('不足', regex=False)
    broken = status.str.contains('無法|需人工確認|缺', regex=True) & ~short
    picked = frame.loc[short | broken].copy()
    if picked.empty:
        return pd.DataFrame(columns=columns)
    picked['問題'] = ['轉場時間不足' if s else '交通資料待確認' for s in short.loc[picked.index]]
    picked['說明'] = [c if s else t for s, c, t in zip(short.loc[picked.index], check.loc[picked.index], status.loc[picked.index])]
    picked = picked.reindex(columns=columns)
    return picked.sort_values(['問題', '交通日期', '派單居服員'], kind='stable').reset_index(drop=True)


def issue_summary(unassigned, overlaps, transitions):
    """每種問題類型：已派需覆核筆數、未派筆數、涉及案家數與建議處理。"""
    unassigned = unassigned if unassigned is not None else pd.DataFrame(columns=['問題類型', '案家ID'])
    rows = []
    for name, meaning, advice in ISSUE_GROUPS:
        pending = unassigned.loc[unassigned['問題類型'].eq(name)] if not unassigned.empty else unassigned
        review = 0
        if name == '班次重疊' and overlaps is not None:
            review = len(overlaps)
        elif name == '轉場不足' and transitions is not None:
            review = len(transitions)
        rows.append({'問題類型': name, '說明': meaning, '已派需覆核': int(review), '未派服務': int(len(pending)),
                     '涉及案家': int(pending['案家ID'].nunique()) if len(pending) else 0, '建議處理': advice})
    return pd.DataFrame(rows)
