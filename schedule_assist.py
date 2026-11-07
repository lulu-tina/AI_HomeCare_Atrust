"""把排班結果整理成可寫回來源 Excel 的資料：新案歷史首選、固定備援、整列時段調整。

只做資料整理與寫回，不做任何排班判斷；排班規則全部在 monthly_scheduler.py。
"""
import re

import pandas as pd

from agency_rules import clean
from source_fields import identifier, resolve_worker
from source_workbook import update_source_workbook


def _split_ids(value, worker_ids=None):
    ids = [identifier(v) for v in re.split(r'[、,，;；\s]+', clean(value)) if v]
    if worker_ids is not None:
        ids = [resolve_worker(v, worker_ids) for v in ids]
    return tuple(dict.fromkeys(i for i in ids if i))


def new_primary_owners(result, clients):
    """來源 Excel「歷史首選」仍空白、本次由引擎選出的新案固定人選。"""
    frame = clients.copy()
    if '歷史首選' in frame:
        existing = {clean(r['案家ID']) for _, r in frame.iterrows() if identifier(r.get('歷史首選'))}
    elif '歷史首選居服員ID' in frame:
        existing = {clean(r['案家ID']) for _, r in frame.iterrows() if identifier(r.get('歷史首選居服員ID'))}
    else:
        existing = set()
    return {str(c): w for c, w in result['client_primary_map'].items() if str(c) not in existing}


def merged_backups(result, clients, worker_ids=None):
    """既有「歷史備援」在前，本次新增備援接在後（去重），避免覆蓋人工維護的名單。"""
    seeds = {}
    if '歷史備援' in clients:
        for _, row in clients.iterrows():
            ids = _split_ids(row.get('歷史備援'), worker_ids)
            if ids:
                seeds[clean(row['案家ID'])] = ids
    out = {}
    for client, workers in (result.get('client_backup_map') or {}).items():
        merged = tuple(dict.fromkeys(seeds.get(str(client), ()) + tuple(workers)))
        if merged:
            out[str(client)] = '、'.join(merged)
    return out


def weekly_changes_from_shifts(time_shifts, tasks):
    """整列一致的時段調整才可寫回「週服務計畫」。

    某來源列只要有任何一次服務沒被挪動（或各次位移不同），改整列時間會連帶改變
    那些服務，結果無法保證，因此只列入「需居督確認」，不自動寫回。
    """
    if time_shifts is None or time_shifts.empty:
        return {}, pd.DataFrame(columns=['來源列', '原因'])
    totals = tasks.drop_duplicates('任務ID').groupby('來源列').size()
    changes, skipped = {}, []
    for row, group in time_shifts.groupby('來源列'):
        row = int(row)
        starts, ends = set(group['新開始']), set(group['新結束'])
        if len(group) != int(totals.get(row, -1)):
            skipped.append({'來源列': row, '原因': f'同一計畫列有 {int(totals.get(row, 0)) - len(group)} 次服務維持原時段，改整列會連帶更動'})
        elif len(starts) != 1 or len(ends) != 1:
            skipped.append({'來源列': row, '原因': '各次挪動的位移不一致，需居督決定固定時段'})
        else:
            changes[row] = {'開始時間': next(iter(starts)), '結束時間': next(iter(ends))}
    return changes, pd.DataFrame(skipped, columns=['來源列', '原因'])


def build_history_workbook(source_bytes, result, clients, caregivers=None,
                           include_backups=True, apply_shifts=False, tasks=None):
    """回傳 (更新後 xlsx bytes, 摘要 DataFrame, 未寫回的時段調整 DataFrame)。"""
    worker_ids = set(caregivers['居服員ID'].astype(str)) if caregivers is not None else None
    owners = new_primary_owners(result, clients)
    backups = merged_backups(result, clients, worker_ids) if include_backups else None
    changes, skipped = ({}, pd.DataFrame(columns=['來源列', '原因']))
    if apply_shifts:
        changes, skipped = weekly_changes_from_shifts(result.get('time_shifts'), tasks if tasks is not None else result['tasks_adjusted'])
    data = update_source_workbook(source_bytes, owners, changes or None, backups or None)
    df = result['df_result']
    served = df.groupby(['案家ID', '派單居服員']).size().rename('承接服務次數').reset_index()
    total = (tasks if tasks is not None else result['tasks_adjusted']).drop_duplicates('任務ID').groupby('案家ID').size()
    rows = []
    for client, worker in owners.items():
        n = int(served[(served['案家ID'] == client) & (served['派單居服員'] == worker)]['承接服務次數'].sum())
        rows.append({'案家ID': client, '新歷史首選': worker, '首選承接次數': n, '本期服務次數': int(total.get(client, 0)),
                     '歷史備援': (backups or {}).get(client, '')})
    summary = pd.DataFrame(rows, columns=['案家ID', '新歷史首選', '首選承接次數', '本期服務次數', '歷史備援'])
    return data, summary, skipped
