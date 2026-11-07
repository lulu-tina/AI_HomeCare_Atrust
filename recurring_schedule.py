"""Recurring visit identities and representative calendar days.

Expand only the visits supplied by the loader. A week template never creates
visits outside effective dates or changes the biweekly anchor.
"""
import pandas as pd
from agency_rules import clean


def visit_pattern(task):
    day = pd.Timestamp(task['日期'])
    keys = ['來源列', '案家ID', '時間窗_開始', '時間窗_結束',
            '服務歷時(分鐘)', '服務地點_緯度', '服務地點_經度',
            '是否失智', '失智症個案(0/1)', '特殊照護需求', '服務碼必要證照',
            '指定居服員性別', '需重度移位協助(0/1)', '案家環境特徵',
            '任務資料異常', '任務待確認事項', '資料來源']
    keys += sorted(k for k in task.keys() if k.startswith(('Service_Code_', 'Units_')))
    return (day.isoweekday(),) + tuple((k, clean(task.get(k))) for k in keys)


def representative_days(tasks, caregivers):
    """One complete day per weekday/task-set/qualification-date band.

    Weekly plans normally need seven days; alternating weeks and later plan
    changes introduce additional shapes. Prefer a date without temporary leave
    for the regular template, then check actual leave during extension.
    """
    dates = []
    blocks = []
    for profile in caregivers.to_dict('records'):
        start = pd.to_datetime(profile.get('到職日'), errors='coerce')
        end = pd.to_datetime(profile.get('長照服務人員到期日'), errors='coerce')
        dates.append((start.date() if pd.notna(start) else None,
                      end.date() if pd.notna(end) else None))
        leave = profile.get('請假紀錄')
        if isinstance(leave, (list, tuple)) and len(leave) == 2:
            a, b = pd.to_datetime(leave[0], errors='coerce'), pd.to_datetime(leave[1], errors='coerce')
            if pd.notna(a) and pd.notna(b):
                blocks.append((a.date(), b.date()))
        for value in clean(profile.get('請假日期')).replace('、', ',').split(','):
            parsed = pd.to_datetime(value, errors='coerce')
            if pd.notna(parsed):
                blocks.append((parsed.date(), parsed.date()))
    selected = {}
    for day, frame in tasks.groupby('日期', sort=True):
        date = pd.Timestamp(day).date()
        band = tuple((a is not None and date < a, b is not None and date > b) for a, b in dates)
        shape = tuple(sorted(visit_pattern(t) for t in frame.to_dict('records')))
        key = band, shape
        score = sum(a <= date <= b for a, b in blocks)
        if key not in selected:
            selected[key] = score, day, 1
        else:
            old_score,old_day,count = selected[key]
            selected[key] = (score,day,count+1) if score<old_score else (old_score,old_day,count+1)
    chosen = {value[1] for value in selected.values()}
    result = tasks.loc[tasks['日期'].isin(chosen)].copy()
    weights = {value[1]:value[2] for value in selected.values()}
    result['_template_occurrences'] = result['日期'].map(weights)
    return result
