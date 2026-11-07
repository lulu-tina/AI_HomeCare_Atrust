"""派單儀錶板的數據計算（只用 pandas，不碰畫面，方便測試）。

儀錶板要回答督導每天真正會問的問題，而不是顯示演算法內部分數：
  1. 整體健康度：成功率、固定人選比例、代班／替補筆數、平均日工時、接近加班上限的人數。
  2. 哪天、哪個時段最容易派不出去？（每日成功率、星期×時段未派熱度圖）
  3. 為什麼派不出去？該怎麼處理？（未派原因分類＋建議處理）
  4. 誰太累、誰還有空檔？（居服員負荷與月加班使用率）
  5. 交通是不是吃掉太多工時？（轉場時間分布、轉場佔總工時比）
  6. 哪些案家老是換人？（非固定人選服務最多的案家，作為調整歷史首選的依據）
"""
CAREFLOW_BUILD = '2026.10.11-v18'
import pandas as pd

WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']
_BLANK = {'', 'nan', 'none', '未指派'}


def _text(value):
    return '' if value is None or (isinstance(value, float) and pd.isna(value)) else str(value).strip()


def _flag(value):
    return value is True or str(value).strip().lower() in ('true', '1', '是')


def _clock_minutes(value):
    text = _text(value)[:5]
    try:
        hour, minute = text.split(':')
        return int(hour) * 60 + int(minute)
    except ValueError:
        return None


def demand_heat(t, caregivers=None):
    """星期 × 小時的『排班需求量』。需求含已派與未派，服務跨小時就按重疊分鐘拆開。

    demand  ：平均同時需要幾位居服員（＝該時段每週平均服務人時）。
    hours   ：整段排班期間該時段的總服務人時。
    supply  ：該星期、該時段『可上班』的居服員人數（依休息日與每日可服務時段）。
    pressure：demand ÷ supply，越高代表人力越吃緊。
    """
    empty = {'demand': pd.DataFrame(), 'hours': pd.DataFrame(), 'supply': pd.DataFrame(), 'pressure': pd.DataFrame(),
             'unassigned_hours': pd.DataFrame()}
    needed = {'_date', '時間窗_開始', '時間窗_結束'}
    if t is None or t.empty or not needed.issubset(t.columns):
        return empty
    dated = t.dropna(subset=['_date'])
    if dated.empty:
        return empty
    minutes, missing = {}, {}
    for date, start, end, ok in zip(dated['_date'], dated['時間窗_開始'], dated['時間窗_結束'], dated['_assigned']):
        a, b = _clock_minutes(start), _clock_minutes(end)
        if a is None or b is None or b <= a:
            continue
        weekday = date.weekday()
        for hour in range(a // 60, (b - 1) // 60 + 1):
            overlap = min(b, (hour + 1) * 60) - max(a, hour * 60)
            if overlap > 0:
                minutes[(weekday, hour)] = minutes.get((weekday, hour), 0) + overlap
                if not ok:
                    missing[(weekday, hour)] = missing.get((weekday, hour), 0) + overlap
    if not minutes:
        return empty
    first, last = dated['_date'].min(), dated['_date'].max()
    span = pd.date_range(first, last, freq='D')
    occurrences = {wd: max(1, int(sum(1 for d in span if d.weekday() == wd))) for wd in range(7)}
    hours_range = list(range(min(h for _, h in minutes), max(h for _, h in minutes) + 1))
    weekdays = sorted({wd for wd, _ in minutes})
    names = {wd: '週' + WEEKDAYS[wd] for wd in weekdays}
    cols = [f'{h:02d}時' for h in hours_range]

    def table(fn):
        return pd.DataFrame([[fn(wd, h) for h in hours_range] for wd in weekdays],
                            index=[names[w] for w in weekdays], columns=cols, dtype=float)
    hours = table(lambda wd, h: minutes.get((wd, h), 0) / 60)
    unassigned = table(lambda wd, h: missing.get((wd, h), 0) / 60)
    demand = table(lambda wd, h: minutes.get((wd, h), 0) / 60 / occurrences[wd])
    supply = pd.DataFrame()
    if caregivers is not None and not caregivers.empty and '可排班星期' in caregivers:
        people = []
        for _, row in caregivers.iterrows():
            days = {int(x) for x in _text(row.get('可排班星期')).replace('，', ',').split(',') if x.strip().isdigit()}
            a = _clock_minutes(row.get('每日可服務時段_起'))
            b = _clock_minutes(row.get('每日可服務時段_迄'))
            people.append((days, 0 if a is None else a, 24 * 60 if b is None else b))
        if people:
            def count(wd, h):
                return sum(1 for days, a, b in people if (not days or (wd + 1) in days) and a <= h * 60 and (h + 1) * 60 <= b)
            supply = table(count)
    pressure = pd.DataFrame()
    if not supply.empty:
        pressure = (demand / supply.where(supply > 0)).fillna(0.0)
    return {'demand': demand, 'hours': hours, 'supply': supply, 'pressure': pressure, 'unassigned_hours': unassigned}


def peak_slots(heat, supply=None, demand=None, n=3):
    """熱圖中數值最高的 n 個格子，供畫面寫成一句話。"""
    if heat is None or heat.empty:
        return []
    stacked = heat.stack()
    stacked = stacked[stacked > 0].sort_values(ascending=False, kind='stable').head(n)
    out = []
    for (day, hour), value in stacked.items():
        out.append({'星期': day, '時段': hour, '值': float(value),
                    '需求': None if demand is None or demand.empty else float(demand.loc[day, hour]),
                    '可上班': None if supply is None or supply.empty else int(supply.loc[day, hour])})
    return out


def build_dashboard(tasks, assignments, diagnostics=None, task_payroll=None, daily_payroll=None,
                    monthly_payroll=None, routes=None, primary_map=None, load_warning=0.8, caregivers=None):
    """回傳 dict：summary / by_day / heat_unassigned / heat_rate / reasons / workload / travel_bins / travel_stats / nonprimary。"""
    primary_map = {str(k): _text(v) for k, v in (primary_map or {}).items()}
    t = tasks.drop_duplicates('任務ID').copy()
    t['任務ID'] = t['任務ID'].astype(str)
    t['_date'] = pd.to_datetime(t['日期'], errors='coerce').dt.normalize() if '日期' in t else pd.NaT
    start = t['時間窗_開始'].astype(str).str.slice(0, 5) if '時間窗_開始' in t else pd.Series('', index=t.index)
    t['_hour'] = pd.to_numeric(start.str.slice(0, 2), errors='coerce')
    worker_of = {}
    if assignments is not None and not assignments.empty and {'任務ID', '派單居服員'}.issubset(assignments.columns):
        for tid, worker in zip(assignments['任務ID'].astype(str), assignments['派單居服員']):
            if _text(worker).lower() not in _BLANK:
                worker_of[tid] = _text(worker)
    t['_worker'] = t['任務ID'].map(worker_of).fillna('')
    t['_assigned'] = t['_worker'].ne('')
    total, assigned = len(t), int(t['_assigned'].sum())

    # ---------- 固定人選 / 代班 / 替補 ----------
    owner = pd.Series('', index=t.index, dtype=object)
    leave = pd.Series(False, index=t.index)
    if task_payroll is not None and not task_payroll.empty and '任務ID' in task_payroll:
        tp = task_payroll.drop_duplicates('任務ID').set_index(task_payroll.drop_duplicates('任務ID')['任務ID'].astype(str))
        if '固定居服員ID' in tp:
            owner = t['任務ID'].map(tp['固定居服員ID'].map(_text)).fillna('')
        if '是否請假代班' in tp:
            leave = t['任務ID'].map(tp['是否請假代班'].map(_flag)).fillna(False).astype(bool)
    if '案家ID' in t:
        mapped = t['案家ID'].astype(str).map(primary_map).fillna('')
        owner = owner.where(owner.ne(''), mapped)
    t['_owner'] = owner
    has_owner = t['_assigned'] & t['_owner'].ne('')
    is_primary = has_owner & t['_worker'].eq(t['_owner'])
    is_leave = has_owner & ~is_primary & leave
    is_backup = has_owner & ~is_primary & ~leave
    t['_kind'] = ''
    t.loc[is_primary, '_kind'] = '固定人選'
    t.loc[is_leave, '_kind'] = '請假代班'
    t.loc[is_backup, '_kind'] = '備援／調度'
    owned = int(has_owner.sum())

    # ---------- 負荷 ----------
    workload = pd.DataFrame(columns=['居服員', '服務次數', '工作天數', '總工時', '日均工時', '轉場工時', '月加班(含期外)', '月加班上限', '加班使用率', '狀態'])
    near_limit = 0
    avg_day = float('nan')
    if monthly_payroll is not None and not monthly_payroll.empty:
        m = monthly_payroll.copy()
        m['居服員ID'] = m['居服員ID'].astype(str)
        for col in ['總工時', '轉場工時', '合計加班時數', '月加班上限']:
            m[col] = pd.to_numeric(m.get(col), errors='coerce')
        grouped = m.groupby('居服員ID').agg(總工時=('總工時', 'sum'), 轉場工時=('轉場工時', 'sum'),
                                          月加班=('合計加班時數', 'max'), 上限=('月加班上限', 'max'))
        days = pd.Series(dtype=float)
        if daily_payroll is not None and not daily_payroll.empty:
            d = daily_payroll.copy()
            d['居服員ID'] = d['居服員ID'].astype(str)
            days = d.groupby('居服員ID')['日期'].nunique()
            avg_day = pd.to_numeric(d['總工時'], errors='coerce').mean()
        counts = t.loc[t['_assigned']].groupby('_worker').size()
        workload = pd.DataFrame({
            '居服員': grouped.index, '服務次數': counts.reindex(grouped.index).fillna(0).astype(int).values,
            '工作天數': days.reindex(grouped.index).fillna(0).astype(int).values,
            '總工時': grouped['總工時'].round(1).values, '轉場工時': grouped['轉場工時'].round(1).values,
            '月加班(含期外)': grouped['月加班'].round(1).values, '月加班上限': grouped['上限'].values})
        workload['日均工時'] = (workload['總工時'] / workload['工作天數'].where(workload['工作天數'] > 0)).round(1)
        workload['加班使用率'] = (workload['月加班(含期外)'] / workload['月加班上限'].where(workload['月加班上限'] > 0)).fillna(0).clip(lower=0)
        workload['狀態'] = workload['加班使用率'].map(lambda v: '超過上限' if v > 1 + 1e-9 else '接近上限' if v >= load_warning else '正常')
        workload = workload[['居服員', '服務次數', '工作天數', '總工時', '日均工時', '轉場工時', '月加班(含期外)', '月加班上限', '加班使用率', '狀態']]
        workload = workload.sort_values(['加班使用率', '總工時'], ascending=False, kind='stable').reset_index(drop=True)
        near_limit = int((workload['加班使用率'] >= load_warning).sum())

    # ---------- 每日 ----------
    dated = t.dropna(subset=['_date'])
    by_day = (dated.groupby('_date').agg(任務數=('任務ID', 'size'), 已派=('_assigned', 'sum')))
    by_day['未派'] = by_day['任務數'] - by_day['已派']
    by_day['成功率'] = (by_day['已派'] / by_day['任務數'] * 100).round(1)
    by_day.insert(0, '星期', [WEEKDAYS[d.weekday()] for d in by_day.index])
    by_day.index = by_day.index.strftime('%m/%d')
    by_day.index.name = '日期'

    # ---------- 星期 × 時段 ----------
    heat_unassigned = heat_rate = pd.DataFrame()
    hh = dated.dropna(subset=['_hour']).copy()
    if not hh.empty:
        hh['星期'] = hh['_date'].dt.weekday.map(lambda i: '週' + WEEKDAYS[i])
        hh['時段'] = hh['_hour'].astype(int).map(lambda h: f'{h:02d}時')
        order_days = ['週' + w for w in WEEKDAYS]
        order_hours = sorted(hh['時段'].unique())
        all_tasks = hh.pivot_table(index='星期', columns='時段', values='任務ID', aggfunc='size', fill_value=0)
        miss = hh.loc[~hh['_assigned']].pivot_table(index='星期', columns='時段', values='任務ID', aggfunc='size', fill_value=0)
        all_tasks = all_tasks.reindex(index=[d for d in order_days if d in all_tasks.index], columns=order_hours, fill_value=0)
        miss = miss.reindex(index=all_tasks.index, columns=order_hours, fill_value=0)
        heat_unassigned = miss.astype(int)
        heat_rate = ((all_tasks - miss) / all_tasks.where(all_tasks > 0) * 100).round(0)

    # ---------- 未派原因 ----------
    diag = diagnostics.to_dict('records') if isinstance(diagnostics, pd.DataFrame) else list(diagnostics or [])
    diag_map = {str(r.get('任務ID')): r for r in diag}
    pending = t.loc[~t['_assigned']]
    rows = []
    for _, row in pending.iterrows():
        info = diag_map.get(row['任務ID'], {})
        rows.append({'原因': _text(info.get('問題分類')) or _text(info.get('失敗階段')) or '待核對目前班表',
                     '建議處理': _text(info.get('建議處理')) or '到「未派案家任務」查看明細',
                     '案家ID': _text(row.get('案家ID'))})
    reasons = pd.DataFrame(columns=['原因', '未派筆數', '涉及案家', '建議處理'])
    if rows:
        r = pd.DataFrame(rows)
        reasons = (r.groupby('原因').agg(未派筆數=('原因', 'size'), 涉及案家=('案家ID', 'nunique'),
                                         建議處理=('建議處理', lambda s: s.mode().iloc[0]))
                   .reset_index().sort_values('未派筆數', ascending=False, kind='stable').reset_index(drop=True))

    # ---------- 交通 ----------
    travel_stats, travel_bins = {}, pd.DataFrame(columns=['區間', '段數'])
    if routes is not None and not routes.empty and '預估車程(分)' in routes:
        legs = pd.to_numeric(routes['預估車程(分)'], errors='coerce')
        legs = legs[legs > 0].dropna()
        if len(legs):
            edges = [0, 5, 10, 15, 20, 30, 45, 60, float('inf')]
            labels = ['≤5', '5–10', '10–15', '15–20', '20–30', '30–45', '45–60', '>60']
            cut = pd.cut(legs, bins=edges, labels=labels, right=True)
            travel_bins = cut.value_counts().reindex(labels).fillna(0).astype(int).rename_axis('區間').reset_index(name='段數')
            travel_stats = {'段數': int(len(legs)), '平均': float(legs.mean()), '中位數': float(legs.median()),
                            'P90': float(legs.quantile(0.9)), '最長': float(legs.max()), '超過30分鐘': int((legs > 30).sum())}
    travel_share = float('nan')
    if monthly_payroll is not None and not monthly_payroll.empty:
        total_hours = pd.to_numeric(monthly_payroll['總工時'], errors='coerce').sum()
        if total_hours > 0:
            travel_share = float(pd.to_numeric(monthly_payroll['轉場工時'], errors='coerce').sum() / total_hours)

    # ---------- 老是換人的案家 ----------
    nonprimary = pd.DataFrame(columns=['案家ID', '固定居服員', '總次數', '非固定次數', '其中請假代班', '主要替代者'])
    off = t.loc[is_primary | is_leave | is_backup]
    if not off.empty:
        parts = []
        for client, frame in off.groupby('案家ID'):
            other = frame.loc[frame['_kind'].ne('固定人選')]
            if other.empty:
                continue
            parts.append({'案家ID': str(client), '固定居服員': frame['_owner'].iloc[0], '總次數': len(frame),
                          '非固定次數': len(other), '其中請假代班': int((other['_kind'] == '請假代班').sum()),
                          '主要替代者': other['_worker'].mode().iloc[0]})
        if parts:
            nonprimary = (pd.DataFrame(parts).sort_values(['非固定次數', '總次數'], ascending=False, kind='stable')
                          .reset_index(drop=True))

    summary = {
        'total': total, 'assigned': assigned, 'unassigned': total - assigned,
        'rate': (assigned / total * 100) if total else 0.0,
        'owned': owned, 'primary': int(is_primary.sum()), 'leave': int(is_leave.sum()), 'backup': int(is_backup.sum()),
        'primary_share': (int(is_primary.sum()) / owned * 100) if owned else float('nan'),
        'avg_day_hours': avg_day, 'near_limit_people': near_limit, 'people': int(len(workload)),
        'travel_share': travel_share,
    }
    heat = demand_heat(t, caregivers)
    return {'summary': summary, 'by_day': by_day, 'heat_unassigned': heat_unassigned, 'heat_rate': heat_rate,
            'heat_demand': heat['demand'], 'heat_hours': heat['hours'], 'heat_supply': heat['supply'],
            'heat_pressure': heat['pressure'], 'heat_unassigned_hours': heat['unassigned_hours'],
            'reasons': reasons, 'workload': workload, 'travel_bins': travel_bins, 'travel_stats': travel_stats,
            'nonprimary': nonprimary}
