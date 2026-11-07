"""Institution rules shared by automatic dispatch, manual changes and payroll.

Payroll is a planning estimate for ordinary working days, not a payroll settlement.
Home commuting is excluded. Inter-client travel and the working buffer count as work.
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import json
import math

import pandas as pd


def clean(value):
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (ValueError, TypeError):
        pass
    return str(value).strip()


def finite(value, default=float('nan')):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def leave_overlap(task, caregiver):
    """Only explicit leave opens substitution; rest days/conflicts do not."""
    if caregiver is None:
        return ''
    from dispatch_workflow import overlap
    leave = caregiver.get('請假紀錄')
    if isinstance(leave, (tuple, list)) and len(leave) == 2:
        if overlap(task, {'start': leave[0], 'end': leave[1]}):
            return '來源員工資料請假'
    day = clean(task.get('日期'))[:10]
    # The legacy "請假或不排班日期" mixes two different concepts. It blocks
    # availability in the engine, but cannot prove leave for substitution.
    explicit_dates = clean(caregiver.get('請假日期')).replace('、', ',').split(',')
    if day and day in [v.strip() for v in explicit_dates]:
        return '已登錄全天請假'
    blocks = caregiver.get('臨時不可排時段', [])
    if isinstance(blocks, str):
        try:
            blocks = json.loads(blocks)
        except ValueError:
            return ''
    for block in blocks if isinstance(blocks, list) else []:
        if str(block.get('reason', '')).startswith('請假') and overlap(task, block):
            return f"請假 {block['start']}～{block['end']}"
    return ''


def primary_guard(task, caregiver, config):
    client = clean(task.get('案家ID'))
    owner = clean(config.client_primary_map.get(client))
    if not owner:
        owner = clean(task.get('歷史首選居服員ID'))
    if not owner or clean(caregiver.get('居服員ID')) == owner:
        return None
    # 該案已指定的固定備援居服員：與主責同屬「固定人選」，不受代班限制。
    backups = getattr(config, 'client_backup_map', {}).get(client) or ()
    if isinstance(backups, str):
        backups = (backups,)
    if clean(caregiver.get('居服員ID')) in {clean(b) for b in backups}:
        return None
    # 固定人選都排不進時，系統為「這一次服務」指定的臨時支援，不擴大為整案備援。
    support = getattr(config, 'task_support_map', {}).get(clean(task.get('任務ID')))
    if support and clean(caregiver.get('居服員ID')) == clean(support):
        return None
    # 處長規則：以「1 主責＋最多 2 備援」為原則；固定人選都排不進這一次服務時，可以再選其他合格居服員。
    # 呼叫端先確認固定人選都不行，才暫時把這筆服務放進 open_support_tasks（不擴大為整案備援）。
    if clean(task.get('任務ID')) in {clean(t) for t in (getattr(config, 'open_support_tasks', None) or ())}:
        return None
    profile = config.primary_profiles.get(owner)
    if leave_overlap(task, profile):
        return None
    return f'案家固定居服員為 {owner}；只有固定人選已登錄且重疊本次服務的請假，才能代班。'


def daily_pay(service_minutes, transfer_minutes, transitions, config):
    """46/transition replaces time-priced travel pay; overtime is incremental."""
    working = service_minutes + transfer_minutes
    overtime = max(0.0, working / 60 - config.normal_daily_hours)
    first = min(overtime, 2.0)
    second = min(max(0.0, overtime - 2.0), 2.0)
    base = service_minutes / 60 * config.hourly_wage
    transfer = transitions * config.transition_wage
    extra = config.hourly_wage * (
        first * (config.overtime_first_multiplier - 1)
        + second * (config.overtime_second_multiplier - 1)
    )
    return dict(服務工時=service_minutes / 60, 轉場工時=transfer_minutes / 60,
                總工時=working / 60, 加班時數=overtime,
                前兩小時加班=first, 後兩小時加班=second,
                轉場次數=transitions, 服務基本工資=round(base, 2),
                轉場工資=round(transfer, 2), 加班加給=round(extra, 2),
                預估居服員薪資=round(base + transfer + extra, 2))


def monthly_overtime_error(cg_id, day, working_minutes, config, caregiver=None):
    day = pd.to_datetime(day, errors='coerce')
    if pd.isna(day):
        return None
    month = day.strftime('%Y-%m')
    other = 0.0
    for (worker, date), minutes in config.work_day_minutes.items():
        if str(worker) == str(cg_id) and str(date).startswith(month) and str(date)[:10] != day.strftime('%Y-%m-%d'):
            if not math.isfinite(float(minutes)):
                return '同月既有工時含未知轉場時間，尚不能確認加班上限。'
            other += max(0.0, float(minutes) / 60 - config.normal_daily_hours)
    baseline = max(0.0, finite((caregiver or {}).get('排班期外本月加班時數'), 0.0))
    total = baseline + other + max(0.0, working_minutes / 60 - config.normal_daily_hours)
    if total > config.max_monthly_overtime_hours + 1e-7:
        return f'每月加班工時超標：{month} 加入後 {total:.2f} 小時，上限 {config.max_monthly_overtime_hours:g} 小時。'
    return None


def consecutive_run_before(workdays, day):
    """回傳 day（'YYYY-MM-DD'）之前、不中斷的已上班天數。workdays 為該居服員已知上班日期字串集合
    （跨月持久種子＋本次已排），day 當天本身不計入。"""
    day = pd.to_datetime(day, errors='coerce')
    if pd.isna(day):
        return 0
    cursor = day - pd.Timedelta(days=1)
    streak = 0
    while cursor.strftime('%Y-%m-%d') in workdays:
        streak += 1
        cursor -= pd.Timedelta(days=1)
    return streak


def consecutive_workdays_error(worker, day, config):
    """連續上班天數規則：若排入 day 會讓居服員連續上班達 config.max_consecutive_work_days+1 天，
    回傳說明文字；否則回傳 None。

    呼叫端決定如何處理非 None 的回傳值：AI 批次排班（monthly_scheduler.check_visit）視為硬性
    擋下，一律不指派；人工插單／人工改派只拿它顯示提醒，不阻擋操作——規則判斷邏輯只有這一處，
    兩邊共用，訊息文案與門檻值不會跑掉。

    config.worker_workdays：{居服員ID: {日期字串,...}}，由呼叫端維護並持續寫入（本函式不修改）。
    """
    limit = int(getattr(config, 'max_consecutive_work_days', 6))
    workdays = getattr(config, 'worker_workdays', {}).get(str(worker), set())
    streak = consecutive_run_before(workdays, day)
    if streak < limit:
        return None
    day_ts = pd.to_datetime(day, errors='coerce')
    first = (day_ts - pd.Timedelta(days=streak)).strftime('%Y-%m-%d')
    last = (day_ts - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    return (f'已連續上班 {streak} 天（{first}～{last}），若 {day_ts.strftime("%Y-%m-%d")} 再排班，'
            f'將達連續 {streak + 1} 天，超過上限 {limit} 天，不得連續上班逾 {limit} 天。')


def payroll_tables(tasks, assignments, caregivers, routes, config):
    """Rebuild money from effective assignments and current signed route legs.

    Per-task overtime is attributed chronologically so its sum equals each day's
    incremental overtime. Unknown inter-client travel keeps the daily total unknown.
    """
    from caregiver_engine import calculate_task_revenue_and_salary
    from schedule_output import describe_routes
    financial = calculate_task_revenue_and_salary(tasks, config)
    task_map = financial.drop_duplicates('任務ID').set_index('任務ID').to_dict('index')
    profiles = caregivers.set_index('居服員ID').to_dict('index')
    sequence = describe_routes(assignments, tasks, caregivers, config.buffer_mins)
    route_map = routes.drop_duplicates('任務ID').set_index('任務ID').to_dict('index') if not routes.empty else {}
    details, days = [], []
    if sequence.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {}
    ledger = {}
    for (worker, date), legs in sequence.groupby(['派單居服員', '交通日期'], sort=True):
        service, travel, transitions = 0.0, 0.0, 0
        known = True
        cumulative = 0.0
        previous_extra = 0.0
        day_details = []
        for _, leg in legs.iterrows():
            tid = leg['任務ID']
            task = task_map[tid]
            duration = finite(task.get('服務歷時(分鐘)'))
            start = pd.to_datetime(clean(task.get('時間窗_開始')), format='%H:%M', errors='coerce')
            end = pd.to_datetime(clean(task.get('時間窗_結束')), format='%H:%M', errors='coerce')
            if pd.notna(start) and pd.notna(end):
                duration = (end - start).total_seconds() / 60
            current_client = clean(task.get('案家ID'))
            transition = bool(leg['前一任務ID']) and clean(leg['起點案號']) != current_client
            leg_minutes = 0.0
            if transition:
                transitions += 1
                route = route_map.get(tid, {})
                valid = (clean(route.get('派單居服員')) == str(worker) and
                         clean(route.get('交通資料簽章')) == leg['交通資料簽章'])
                raw = finite(route.get('預估車程(分)')) if valid else float('nan')
                leg_minutes = raw + finite(leg['路段緩衝(分)'], 0)
            known = known and math.isfinite(duration) and duration > 0 and math.isfinite(leg_minutes)
            service += duration
            travel += leg_minutes
            cumulative += duration + leg_minutes
            extra = daily_pay(cumulative, 0, 0, config)['加班加給'] if known else float('nan')
            attributed_extra = extra - previous_extra
            previous_extra = extra
            base = round(duration / 60 * config.hourly_wage, 2)
            transfer_wage = config.transition_wage if transition else 0.0
            owner = clean(config.client_primary_map.get(current_client)) or clean(task.get('歷史首選居服員ID'))
            absence = leave_overlap(task, profiles.get(owner)) if owner != str(worker) else ''
            row = {'任務ID': tid, '派單居服員': worker, '日期': date,
                   **{k: task.get(k) for k in ['預估服務營收', '預估自費營收', '預估BA服務營收', '財務估算狀態', 'SA10計費提醒', '費用類型']},
                   '固定居服員ID': owner, '是否請假代班': bool(owner and owner != str(worker) and absence),
                   '代班原因': absence, '服務基本工資': base,
                   '轉場次數': int(transition), '轉場工資': transfer_wage,
                   '加班加給': round(attributed_extra, 2),
                   '預估居服員薪資': round(base + transfer_wage + attributed_extra, 2)}
            day_details.append(row)
        stats = daily_pay(service, travel, transitions, config)
        if not known:
            for k in ['轉場工時', '總工時', '加班時數', '前兩小時加班', '後兩小時加班', '加班加給', '預估居服員薪資']:
                stats[k] = float('nan')
            for row in day_details:
                row['加班加給'] = row['預估居服員薪資'] = float('nan')
        ledger[(str(worker), str(date))] = stats['總工時'] * 60
        revenue_values = [finite(r['預估服務營收']) for r in day_details]
        revenue = sum(revenue_values) if all(math.isfinite(v) for v in revenue_values) else float('nan')
        status = '一般工作日機構估算' if known else '轉場或服務工時未知，薪資總計待確認'
        if known and stats['總工時'] > min(config.max_daily_hours, 12) + 1e-7:
            status = '超過每日工時上限，須調班'
        if known and stats['加班時數'] > 4 + 1e-7:
            stats['預估居服員薪資'] = float('nan')
            status = '超過一般工作日加班模型範圍，須調班'
        days.append({'居服員ID': worker, '日期': date, '月份': str(date)[:7],
                     **stats, '預估服務營收': revenue, '薪資估算狀態': status})
        details.extend(day_details)
    daily = pd.DataFrame(days)
    money = ['服務工時', '轉場工時', '總工時', '加班時數', '轉場次數', '服務基本工資', '轉場工資', '加班加給', '預估居服員薪資', '預估服務營收']
    monthly = daily.groupby(['居服員ID', '月份'], as_index=False)[money].agg(lambda s: s.sum() if s.notna().all() else float('nan'))
    monthly['月加班上限'] = config.max_monthly_overtime_hours
    monthly['排班期外本月加班時數'] = monthly['居服員ID'].map(lambda cg: finite(profiles.get(str(cg), {}).get('排班期外本月加班時數'), 0))
    monthly['合計加班時數'] = monthly['加班時數'] + monthly['排班期外本月加班時數']
    monthly['月工時檢查'] = monthly['合計加班時數'].map(lambda h: '資料待確認' if pd.isna(h) else '超過月加班上限' if h > config.max_monthly_overtime_hours + 1e-7 else '本次已知工時通過')
    return pd.DataFrame(details), daily, monthly, ledger
