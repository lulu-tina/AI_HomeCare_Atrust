"""以居服員為主軸排班（仿居督熟悉的舊系統：選居服員 → 看他的案家 → 勾時段排進去）。

這裡只整理資料（可單獨測試）；真正排入時沿用 app.py 的人工改派入口 `_quick_reassign`，
時間衝突、轉場、工時、資格、連續上班等檢查都跟其他改派方式完全相同。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import numpy as np
import pandas as pd

import caregiver_engine as engine

_EMPTY = {'', '未指派', 'None', 'nan', 'NaN'}
_ORDER = {name: i for i, name in enumerate(engine.WEEKDAY_NAME_TO_NUM)}
COLUMNS = ['時段ID', '案家ID', '固定居服員ID', '所屬', '星期', '時段', '頻率', '服務項目', '本期次數',
           '由他服務', '他人服務', '未排', '他有空的天數', '距離(公里)', '未排任務ID', '首次日期']


def _clean(value):
    text = engine._text(value)
    return '' if text in _EMPTY else text


def _minutes(hhmm):
    text = str(hhmm)[:5]
    return int(text[:2]) * 60 + int(text[3:5])


def _slot_id(task):
    """同一個週計畫列（同案家、同星期同時段）＝一個時段；沒有來源列時用星期＋起迄時間。"""
    row = task.get('來源列')
    if row is not None and not pd.isna(row) and str(row).strip():
        return f"{task['案家ID']}|R{int(float(row))}"
    return f"{task['案家ID']}|{task.get('星期')}|{task['時間窗_開始']}|{task['時間窗_結束']}"


def _items(task):
    return ' '.join(f'{code}({units:g})' for code, units in engine.iter_service_codes(task) if np.isfinite(units))


def _home(caregivers, worker):
    if caregivers is None or caregivers.empty or '居服員ID' not in caregivers:
        return None
    rows = caregivers[caregivers['居服員ID'].astype(str) == str(worker)]
    if rows.empty:
        return None
    lat = engine._number(rows.iloc[0].get('服務起點_緯度(家)'))
    lon = engine._number(rows.iloc[0].get('服務起點_經度(家)'))
    return (lat, lon) if np.isfinite(lat) and np.isfinite(lon) else None


def slot_overview(tasks, effective, worker, owners=None, caregivers=None):
    """回傳每個案家時段一列：本期有幾次、幾次由這位居服員服務、幾次還沒排、他那幾天有沒有空、離他家多遠。"""
    worker = str(worker)
    owners = {str(k): str(v) for k, v in (owners or {}).items()}
    if tasks is None or tasks.empty:
        return pd.DataFrame(columns=COLUMNS)
    assigned = {}
    if effective is not None and {'任務ID', '派單居服員'}.issubset(effective.columns):
        for tid, cg in zip(effective['任務ID'].astype(str), effective['派單居服員']):
            assigned[tid] = _clean(cg)
    frame = tasks.drop_duplicates('任務ID', keep='last').copy()
    frame['任務ID'] = frame['任務ID'].astype(str)
    frame['案家ID'] = frame['案家ID'].astype(str)
    frame['日期'] = pd.to_datetime(frame['日期'], errors='coerce').dt.strftime('%Y-%m-%d')
    frame = frame[frame['日期'].notna()]
    frame['_cg'] = frame['任務ID'].map(lambda t: assigned.get(t, ''))

    busy = {}
    for _, task in frame[frame['_cg'] == worker].iterrows():
        busy.setdefault(task['日期'], []).append((_minutes(task['時間窗_開始']), _minutes(task['時間窗_結束'])))

    def free(task):
        start, end = _minutes(task['時間窗_開始']), _minutes(task['時間窗_結束'])
        return not any(start < b and a < end for a, b in busy.get(task['日期'], []))

    home = _home(caregivers, worker)
    rows = []
    frame['_slot'] = [_slot_id(t) for t in frame.to_dict('records')]
    for slot, group in frame.groupby('_slot', sort=False):
        group = group.sort_values(['日期', '時間窗_開始'])
        first = group.iloc[0].to_dict()
        client = first['案家ID']
        pending = group[group['_cg'] == '']
        mine = int((group['_cg'] == worker).sum())
        distance = np.nan
        lat, lon = engine._number(first.get('服務地點_緯度')), engine._number(first.get('服務地點_經度'))
        if home and np.isfinite(lat) and np.isfinite(lon):
            distance = round(engine.calc_distance_km(home[0], home[1], lat, lon), 1)
        weekday = _clean(first.get('星期')) or engine.get_weekday_name(first['日期'])
        rows.append({
            '時段ID': slot, '案家ID': client, '固定居服員ID': owners.get(client, ''),
            '所屬': owners.get(client) == worker or mine > 0,
            '星期': weekday, '時段': f"{first['時間窗_開始']}–{first['時間窗_結束']}",
            '頻率': _clean(first.get('頻率')) or '單次', '服務項目': _items(first),
            '本期次數': len(group), '由他服務': mine, '他人服務': len(group) - mine - len(pending), '未排': len(pending),
            '他有空的天數': int(sum(free(t) for _, t in pending.iterrows())), '距離(公里)': distance,
            '未排任務ID': pending['任務ID'].tolist(), '首次日期': first['日期']})
    out = pd.DataFrame(rows, columns=COLUMNS)
    out['_o'] = out['星期'].map(_ORDER).fillna(9)
    return out.sort_values(['案家ID', '_o', '時段']).drop(columns='_o').reset_index(drop=True)


def owned_slots(overview):
    """他的所屬案家：固定人選是他，或本期至少有一次由他服務。"""
    return overview[overview['所屬']].reset_index(drop=True)


def open_slots(overview, only_owned=False):
    """還有未排次數的時段；他的所屬案家排前面，再依「他有空的天數」多、距離近排序。"""
    rows = overview[overview['未排'] > 0]
    if only_owned:
        rows = rows[rows['所屬']]
    rows = rows.assign(_d=rows['距離(公里)'].fillna(9999))
    return rows.sort_values(['所屬', '他有空的天數', '_d'], ascending=[False, False, True]).drop(columns='_d').reset_index(drop=True)


def status_text(row):
    if row['未排'] == 0 and row['他人服務'] == 0:
        return '✅ 都由他服務'
    parts = []
    if row['他人服務']:
        parts.append(f"{row['他人服務']} 次由別人代班")
    if row['未排']:
        parts.append(f"⚠️ {row['未排']} 次還沒排")
    return '；'.join(parts)
