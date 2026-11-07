"""Batch search for a temporary (LINE) case: all time slots at once, plus a "one caregiver for everything" plan.

No Streamlit here. The Streamlit page only collects the case, calls these functions and renders.
"""
CAREFLOW_BUILD = '2026.10.18-v19'

from datetime import datetime

import pandas as pd

import caregiver_engine as engine

SLOT_COLUMNS = ['納入', '日期', '開始', '結束', '服務碼1', 'Units1', '服務碼2', 'Units2', '時段備註']


def slots_to_frame(slots: list[dict]) -> pd.DataFrame:
    rows = []
    for s in slots:
        rows.append({
            '納入': True,
            '日期': pd.Timestamp(s['日期']).date(),
            '開始': s['開始時間'], '結束': s['結束時間'],
            '服務碼1': s.get('Service_Code_1') or '', 'Units1': float(s.get('Units_1') or 0.0),
            '服務碼2': s.get('Service_Code_2') or '', 'Units2': float(s.get('Units_2') or 0.0),
            '時段備註': s.get('時段備註', ''),
        })
    return pd.DataFrame(rows, columns=SLOT_COLUMNS)


def _minutes(start: str, end: str) -> float:
    a, b = datetime.strptime(start.strip(), '%H:%M'), datetime.strptime(end.strip(), '%H:%M')
    return (b - a).total_seconds() / 60.0


def _required_certs(row: dict, service_master: pd.DataFrame) -> set[str]:
    """服務碼要求的證照：主檔「必要證照」欄優先，沒有就用系統內建（BA08 足部照護、BA17A/B 抽吸）。"""
    from care_policy import DEFAULT_CODE_CERTS
    table = {}
    if '必要證照' in service_master.columns:
        table = {str(c).strip().upper(): str(v or '') for c, v in zip(service_master['系統代碼'], service_master['必要證照'])}
    needed = set()
    for key, unit in (('Service_Code_1', 'Units_1'), ('Service_Code_2', 'Units_2')):
        code = str(row.get(key) or '').strip().upper()
        if not code or not float(row.get(unit) or 0) > 0:
            continue
        configured = table.get(code, '')
        if configured.strip() and configured.strip().lower() != 'nan':
            needed.update(x.strip() for x in configured.split('、') if x.strip())
        elif code in DEFAULT_CODE_CERTS:
            needed.add(DEFAULT_CODE_CERTS[code])
    return needed


def build_temp_tasks(case: dict, slot_frame: pd.DataFrame, service_master: pd.DataFrame, id_prefix: str, source_label: str):
    """回傳 (tasks DataFrame, errors list)。每個被勾選的時段一列；案家屬性與座標已合併在同一列。"""
    rows, errors = [], []
    included = slot_frame[slot_frame['納入'].fillna(False).astype(bool)]
    for n, (_, s) in enumerate(included.iterrows(), start=1):
        label = f"{pd.Timestamp(s['日期']):%m/%d} {s['開始']}-{s['結束']}"
        try:
            minutes = _minutes(str(s['開始']), str(s['結束']))
        except ValueError:
            errors.append(f'{label}：時間格式請用 HH:MM。')
            continue
        if minutes <= 0:
            errors.append(f'{label}：結束時間必須晚於開始時間。')
            continue
        code1 = str(s['服務碼1']).strip()
        if not code1:
            errors.append(f'{label}：請選擇服務碼。')
            continue
        day = pd.Timestamp(s['日期'])
        row = {
            '任務ID': f'{id_prefix}-{n}', '案家ID': case['client_id'], '日期': day,
            '星期': engine.get_weekday_name(day), '時間窗_開始': str(s['開始']).strip(), '時間窗_結束': str(s['結束']).strip(),
            '任務優先級': '高', '服務歷時(分鐘)': minutes, '資料來源': source_label,
            'Service_Code_1': code1, 'Units_1': float(s['Units1'] or 1.0),
            'Service_Code_2': (str(s['服務碼2']).strip() or None), 'Units_2': (float(s['Units2']) if str(s['服務碼2']).strip() else None),
        }
        known = {str(c).strip().upper() for c in service_master.get('系統代碼', [])}
        bad = [c for c in (code1, row['Service_Code_2']) if c and str(c).strip().upper() not in known]
        if bad:
            errors.append(f'{label}：服務碼 {"、".join(map(str, bad))} 不在 Service_code 主檔。')
            continue
        row['服務碼必要證照'] = '、'.join(sorted(_required_certs(row, service_master)))
        row['服務歷時(分鐘)'] = minutes
        row['服務歷時來源'] = '人工確認起迄時間'
        row.update({
            '指定居服員性別': case.get('gender', '男女不拘'),
            '服務地點_緯度': float(case['lat']), '服務地點_經度': float(case['lon']),
            '特殊照護需求': case.get('need', '一般照護'), '案家環境特徵': case.get('env', '一般居家環境'),
            '需重度移位協助(0/1)': int(bool(case.get('heavy', False))),
            '歷史首選居服員ID': '', '臨時事件備註': case.get('note', ''),
            '失智症個案(0/1)': 1 if case.get('dementia') else 0,
        })
        rows.append(row)
    return pd.DataFrame(rows), errors


def search_slots(temp_tasks: pd.DataFrame, tasks_now: pd.DataFrame, effective_now: pd.DataFrame,
                 df_cg: pd.DataFrame, config) -> list[dict]:
    """每個時段各自找全部候選（含不可插入者與原因）。同一人連續接多個時段的衝突在確認時再逐筆複查。"""
    out = []
    for _, task in temp_tasks.iterrows():
        frame = engine.find_insertion_candidates(task, tasks_now, effective_now, df_cg, config,
                                                  top_n=max(len(df_cg), 3), date_column='日期', include_failed=True)
        out.append({'task': task.to_dict(), 'frame': frame})
    return out


def _col(frame, *names):
    return next((n for n in names if n in frame.columns), None)


def single_caregiver_plan(results: list[dict]) -> pd.DataFrame:
    """所有時段都「可直接插入」的居服員，依代班挑選順序（各時段排名平均）排列，附第一個時段的挑選理由。"""
    if not results:
        return pd.DataFrame()
    per_slot = []
    for r in results:
        f = r['frame']
        if f.empty:
            return pd.DataFrame()
        ok = f[f['可直接插入'] == True].copy()  # noqa: E712
        ok['_位置'] = range(len(ok))   # find_insertion_candidates 已依代班挑選順序排好
        score_col = _col(ok, '適配度分數', '適配分數')
        keep = [c for c in (score_col, '擾動成本', '前段交通時間(分)', '代班排序理由', '_位置') if c]
        per_slot.append(ok.set_index('居服員ID')[keep])
    common = set(per_slot[0].index)
    for p in per_slot[1:]:
        common &= set(p.index)
    rows = []
    for cg in common:
        scores = [float(p.at[cg, p.columns[0]]) for p in per_slot]
        cost = sum(float(p.at[cg, '擾動成本']) if '擾動成本' in p else 0.0 for p in per_slot)
        travel = [float(p.at[cg, '前段交通時間(分)']) for p in per_slot if '前段交通時間(分)' in p and pd.notna(p.at[cg, '前段交通時間(分)'])]
        rank = sum(float(p.at[cg, '_位置']) for p in per_slot) / len(per_slot)
        reason = per_slot[0].at[cg, '代班排序理由'] if '代班排序理由' in per_slot[0] else ''
        rows.append({'居服員ID': str(cg), '平均適配分數': sum(scores) / len(scores), '擾動成本合計': cost,
                     '平均前段交通(分)': (sum(travel) / len(travel)) if travel else float('nan'), '可涵蓋時段數': len(per_slot),
                     '平均排序': rank, '挑選理由': reason})
    plan = pd.DataFrame(rows, columns=['居服員ID', '平均適配分數', '擾動成本合計', '平均前段交通(分)', '可涵蓋時段數', '平均排序', '挑選理由'])
    return plan.sort_values(['擾動成本合計', '平均排序', '居服員ID'], kind='stable').reset_index(drop=True)
