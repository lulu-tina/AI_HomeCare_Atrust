"""Who can take this client?  Per-caregiver, per-requirement explanation for whole-client assignment.

Stage 1 (instant): run the engine's own hard-constraint check on every visit of the client for every caregiver and
group the answers (證照 / 排班日 / 時段 / 性別 / 體力 / 環境 / 請假 / 到期 ...).  Same function the dispatcher uses,
so the table can never say "OK" for something the dispatcher would reject.
Stage 2 (on demand): run the full whole-client preview (conflicts with other clients, rest, hours, travel,
monthly overtime) for the best few candidates.
"""
CAREFLOW_BUILD = '2026.10.11-v18'

from collections import Counter, OrderedDict
from dataclasses import replace

import numpy as np
import pandas as pd

import caregiver_engine as engine
from care_policy import DEFAULT_CODE_CERTS, certificate_names, dementia_status, text
from caregiver_engine import SPECIAL_CERT_REQUIREMENTS, WEEKDAY_NAME_TO_NUM, WEEKDAY_NUM_TO_NAME

CATEGORY_RULES = [
    ('證照', ('缺特殊訓練', '缺乏該需求類別之法定專長認證', '長照服務人員證明已到期')),
    ('性別', ('性別不符',)),
    ('體力', ('重度移位',)),
    ('環境', ('排斥',)),
    ('排班日', ('當日不排班', '可排班星期', '星期')),
    ('可服務時段', ('超出每日可服務時段',)),
    ('請假', ('請假', '不排班記錄')),
    ('到職', ('到職日',)),
    ('資料', ('座標', '缺漏', '無效', '無法')),
    ('工時', ('工時', '加班')),
    ('班表衝突', ('重疊', '前一任務', '下一任務', '空檔', '交通', '休息', '連續')),
    ('通勤', ('預估車程', '車程', 'Transit', '大眾運輸')),
]
CATEGORY_ORDER = [c for c, _ in CATEGORY_RULES] + ['其他']
WEEK_SHORT = {1: '一', 2: '二', 3: '三', 4: '四', 5: '五', 6: '六', 7: '日'}


def categorize(reason: str) -> str:
    for name, needles in CATEGORY_RULES:
        if any(n in reason for n in needles):
            return name
    return '其他'


def required_certs(client_tasks: pd.DataFrame) -> OrderedDict:
    """整案需要的證照／訓練與原因：{證照: [來源說明, ...]}。"""
    needs: OrderedDict = OrderedDict()

    def add(cert, why):
        needs.setdefault(cert, [])
        if why not in needs[cert]:
            needs[cert].append(why)

    for _, task in client_tasks.iterrows():
        for cert in [c for c in text(task.get('服務碼必要證照')).split('、') if c]:
            add(cert, '服務碼要求')
        for code, units in engine.iter_service_codes(task):
            if units and float(units) > 0 and code.upper() in DEFAULT_CODE_CERTS:
                add(DEFAULT_CODE_CERTS[code.upper()], f'服務碼 {code.upper()} 要求')
        if dementia_status(task) is True:
            add('失智症照顧專長', '案主為失智個案')
        for cert in SPECIAL_CERT_REQUIREMENTS.get(text(task.get('特殊照護需求')), ()):
            add(cert, f"特殊照護需求「{text(task.get('特殊照護需求'))}」")
    return needs


def client_profile(client_tasks: pd.DataFrame) -> dict:
    first = client_tasks.iloc[0]
    weekdays = sorted({WEEKDAY_NAME_TO_NUM.get(engine.get_weekday_name(d) or text(w))
                       for d, w in zip(client_tasks['日期'], client_tasks.get('星期', [''] * len(client_tasks)))} - {None})
    minutes = []
    for _, t in client_tasks.iterrows():
        a, b = engine.parse_time(f"{t['時間窗_開始']}-{t['時間窗_結束']}")
        if a is not None and b is not None:
            minutes.append((b - a).total_seconds() / 60)
    return {
        'visits': len(client_tasks),
        'hours': round(sum(minutes) / 60, 1),
        'weekdays': weekdays,
        'weekday_text': '、'.join('週' + WEEK_SHORT[d] for d in weekdays) or '—',
        'earliest': min((str(x)[:5] for x in client_tasks['時間窗_開始']), default=''),
        'latest': max((str(x)[:5] for x in client_tasks['時間窗_結束']), default=''),
        'gender': text(first.get('指定居服員性別')),
        'heavy': first.get('需重度移位協助(0/1)') == 1,
        'dementia': any(dementia_status(t) is True for _, t in client_tasks.iterrows()),
        'environment': text(first.get('案家環境特徵')),
        'certs': required_certs(client_tasks),
        'lat': pd.to_numeric(client_tasks['服務地點_緯度'], errors='coerce').median(),
        'lon': pd.to_numeric(client_tasks['服務地點_經度'], errors='coerce').median(),
        'distinct_slots': client_tasks.assign(_s=client_tasks['星期'].astype(str) + client_tasks['時間窗_開始'].astype(str)
                                                  + client_tasks['時間窗_結束'].astype(str))['_s'].nunique(),
    }


def _weekday_numbers(cg) -> set[int]:
    return {int(x) for x in text(cg.get('可排班星期')).replace('，', ',').split(',') if x.strip().isdigit()}


def _hours_booked(assignments: pd.DataFrame, tasks: pd.DataFrame, cg_id: str, exclude_ids: set) -> float:
    if assignments is None or assignments.empty:
        return 0.0
    mine = assignments.loc[assignments['派單居服員'].astype(str).eq(str(cg_id)) & ~assignments['任務ID'].isin(exclude_ids), '任務ID']
    sub = tasks.loc[tasks['任務ID'].isin(set(mine))]
    total = 0.0
    for _, t in sub.iterrows():
        a, b = engine.parse_time(f"{t['時間窗_開始']}-{t['時間窗_結束']}")
        if a is not None and b is not None:
            total += (b - a).total_seconds() / 60
    return round(total / 60, 1)


def screen_caregivers(client_tasks: pd.DataFrame, caregivers: pd.DataFrame, config, owner: str = '', backups=(),
                      assignments: pd.DataFrame | None = None, all_tasks: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """回傳 (摘要表, {居服員ID: 完整說明 dict})。client_tasks 須是已與案家資料合併的任務。"""
    prof = client_profile(client_tasks)
    certs_needed = list(prof['certs'])
    client_ids = set(client_tasks['任務ID'])
    client_id = str(client_tasks.iloc[0]['案家ID'])
    rows, details = [], {}
    backups = {str(b) for b in (backups or ())}
    for _, cg in caregivers.iterrows():
        cg_id = str(cg['居服員ID'])
        reasons_by_task = {}
        category_hits: dict[str, Counter] = {}
        # 整案處理是在「換固定居服員」：把此人視為新的固定人選來檢查，才不會被舊固定人選規則擋掉。
        trial = replace(config, client_primary_map={**config.client_primary_map, client_id: cg_id})
        for _, task in client_tasks.iterrows():
            try:
                reason = engine._check_hard_constraints(task, cg, trial, None, cg_id == str(owner))
            except Exception as exc:                      # 資料缺漏時照實顯示，不讓整張表壞掉
                reason = f'資料無法檢查：{exc}'
            if reason:
                reasons_by_task[str(task['任務ID'])] = reason
                category_hits.setdefault(categorize(reason), Counter())[reason] += 1
        total = len(client_tasks)
        ok = total - len(reasons_by_task)

        have = certificate_names(cg)
        missing = [c for c in certs_needed if c not in have]
        if not certs_needed:
            cert_cell = '— 無特殊證照要求'
        elif missing:
            cert_cell = '✘ 缺 ' + '、'.join(missing)
        else:
            cert_cell = '✔ ' + '、'.join(certs_needed)

        allowed = _weekday_numbers(cg)
        off_days = [d for d in prof['weekdays'] if d not in allowed]
        rest = text(cg.get('休息日'))
        if off_days:
            day_cell = f"✘ 週{'、週'.join(WEEK_SHORT[d] for d in off_days)} 不排班" + (f'（休息日 {rest}）' if rest else '')
        else:
            day_cell = '✔ ' + prof['weekday_text']
        window = f"{text(cg.get('每日可服務時段_起'))}～{text(cg.get('每日可服務時段_迄'))}"
        window_hits = category_hits.get('可服務時段')
        time_cell = (f"✘ 有 {sum(window_hits.values())} 次超出 {window}" if window_hits
                     else f"✔ {prof['earliest']}～{prof['latest']} 在 {window} 內")

        other = []
        for cat in ('性別', '體力', '環境'):
            if cat in category_hits:
                other.append(f"✘ {cat}：{next(iter(category_hits[cat]))}")
        if prof['gender'] in ('女', '男', '限女性', '限男性') and '性別' not in category_hits:
            other.append(f"✔ 性別符合（{prof['gender']}）")
        if prof['heavy'] and '體力' not in category_hits:
            other.append('✔ 具重度移位體力')
        other_cell = '；'.join(other) or '— 無額外限制'

        leave = []
        for cat in ('請假', '到職', '資料'):
            if cat in category_hits:
                leave.append(f"✘ {cat}：{next(iter(category_hits[cat]))}（{sum(category_hits[cat].values())} 次）")
        leave_cell = '；'.join(leave) or '✔ 無衝突'

        dist = float('nan')
        try:
            dist = float(engine.calc_distance_km(cg['服務起點_緯度(家)'], cg['服務起點_經度(家)'], prof['lat'], prof['lon']))
        except Exception:
            pass

        before = _hours_booked(assignments, all_tasks, cg_id, client_ids) if assignments is not None and all_tasks is not None else float('nan')
        relation = '固定居服員' if cg_id == str(owner) else '備援' if cg_id in backups else '—'
        verdict = '✅ 資格全符合' if ok == total else ('❌ 全部不符' if ok == 0 else '⚠️ 部分可排')

        blockers = []
        for cat in CATEGORY_ORDER:
            for reason, n in (category_hits.get(cat) or {}).items():
                blockers.append(f'{reason}（{n} 次）')
        rows.append({
            '居服員ID': cg_id, '結論': verdict, '資格可排': f'{ok}/{total}',
            '證照／訓練': cert_cell, '排班日': day_cell, '可服務時段': time_cell,
            '性別／體力／環境': other_cell, '請假／到期': leave_cell,
            '本月已排(時)': before, '加上此案(時)': round(before + prof['hours'], 1) if before == before else float('nan'),
            '住家距離(公里)': round(dist, 1) if dist == dist else float('nan'),
            '與此案關係': relation, '阻擋原因': '；'.join(blockers) or '—',
            '_ok': ok, '_missing': missing,
        })
        details[cg_id] = {'reasons_by_task': reasons_by_task, 'missing_certs': missing, 'ok': ok, 'total': total, 'have': sorted(have)}
    frame = pd.DataFrame(rows)
    order = {'✅ 資格全符合': 0, '⚠️ 部分可排': 1, '❌ 全部不符': 2}
    rel = {'固定居服員': 0, '備援': 1, '—': 2}
    frame['_o'] = frame['結論'].map(order)
    frame['_r'] = frame['與此案關係'].map(rel)
    frame = frame.sort_values(['_o', '_r', '住家距離(公里)'], kind='stable').drop(columns=['_o', '_r']).reset_index(drop=True)
    return frame, details


def month_overtime(ledger: dict, worker: str, month: str, normal_daily_hours: float) -> float:
    total = 0.0
    for (w, d), minutes in ledger.items():
        if str(w) == str(worker) and str(d).startswith(month) and minutes == minutes:
            total += max(0.0, float(minutes) / 60 - normal_daily_hours)
    return round(total, 2)


def explain_slot(task: pd.Series, frame: pd.DataFrame, caregivers: pd.DataFrame, config) -> pd.DataFrame:
    """某個時段「為什麼這些人不能接」：每位居服員一列，附原因分類。"""
    inside = {str(r['居服員ID']): r for _, r in frame.iterrows()} if frame is not None and not frame.empty else {}
    rows = []
    for _, cg in caregivers.iterrows():
        cg_id = str(cg['居服員ID'])
        if cg_id in inside:
            if inside[cg_id]['可直接插入']:
                continue
            reason = str(inside[cg_id]['不可插入原因']) or '無法直接插入'
        else:
            try:
                reason = engine._check_hard_constraints(task, cg, config, None, False)
                if not reason:
                    mode = str(cg.get('常用交通工具', '機車')).strip()
                    travel = engine.calc_travel_minutes(
                        cg['服務起點_緯度(家)'], cg['服務起點_經度(家)'], task['服務地點_緯度'], task['服務地點_經度'], config,
                        transport_mode=mode,
                        arrival_time=(engine._bucket_transit_datetime(engine._task_arrival(task)) if mode == '大眾運輸'
                                      else engine._task_arrival(task)))
                    if mode == '大眾運輸' and not pd.notna(travel):
                        reason = 'Google Transit 無可用路線／班次'
                    else:
                        reason = engine._check_hard_constraints(task, cg, config, travel, False)
                        if not reason and mode == '大眾運輸' and getattr(config, 'transit_candidate_top_k', 0) > 0:
                            reason = f'大眾運輸只估算離案家最近的 {config.transit_candidate_top_k} 位，此人未入選'
                reason = reason or '通過硬性條件，但未進入候選名單'
            except Exception as exc:
                reason = f'資料無法檢查：{exc}'
        rows.append({'居服員ID': cg_id, '類別': categorize(reason), '原因': reason})
    return pd.DataFrame(rows, columns=['居服員ID', '類別', '原因'])


def month_overtime_summary(ledger: dict, before_ledger: dict, worker: str, months, config) -> str:
    """『10月 8.5→14.0／46 時』：接手前後的月加班時數（只計排班期內已知工時）。"""
    parts = []
    for month in months:
        before = month_overtime(before_ledger, worker, month, config.normal_daily_hours)
        after = month_overtime(ledger, worker, month, config.normal_daily_hours)
        parts.append(f"{month[5:]}月 {before:g}→{after:g}／{config.max_monthly_overtime_hours:g} 時")
    return '；'.join(parts)


def simulate_worker(preview: dict, before_ledger: dict, worker: str, months, config) -> dict:
    failed = preview['failed']
    reasons = Counter()
    for r in failed.get('原因', []):
        reasons[str(r).split('；')[0][:70]] += 1
    total = preview['total']
    accepted = len(preview['accepted'])
    return {
        '居服員ID': worker,
        '試算結果': '✅ 全部可排' if accepted == total else ('❌ 全部排不進' if accepted == 0 else f'⚠️ 可排 {accepted}/{total}'),
        '可安排': accepted, '仍未派': len(failed), '總次數': total,
        '月加班(時)': month_overtime_summary(preview.get('ledger', {}), before_ledger, worker, months, config),
        '主要原因': '；'.join(f'{k}（{v} 次）' for k, v in reasons.most_common(3)) or '—',
    }


def worker_status_table(screen: pd.DataFrame, trial: pd.DataFrame | None, total: int) -> pd.DataFrame:
    """③ 結果表與 ④ 選單共用的「單一格式」狀態：每位居服員一列，不管有沒有試算過。

    狀態（由好到差）：✅ 全部可排 n/n → ⚠️ 可排 a/b → ❌ 全部排不進 → 🔘 資格符合・尚未試算 → ⛔ 資格不符
    """
    tried = {}
    if trial is not None and not trial.empty:
        tried = {str(r['居服員ID']): r for _, r in trial.iterrows()}
    rows = []
    for _, s in screen.iterrows():
        worker = str(s['居服員ID'])
        eligible = int(s['_ok']) > 0
        t = tried.get(worker)
        if t is not None:
            accepted = int(t['可安排'])
            if accepted == int(t['總次數']):
                state, rank = f'✅ 全部可排 {accepted}/{accepted}', 0
            elif accepted == 0:
                state, rank = '❌ 全部排不進', 3
            else:
                state, rank = f'⚠️ 可排 {accepted}/{int(t["總次數"])}', 1
            rows.append({'居服員ID': worker, '狀態': state, '可排次數': accepted, '資格': s['資格可排'], '月加班(時)': t['月加班(時)'],
                         '主要原因': t['主要原因'], '_rank': rank, '_tried': True, '_eligible': eligible})
        elif eligible:
            rows.append({'居服員ID': worker, '狀態': '🔘 資格符合・尚未試算', '可排次數': None, '資格': s['資格可排'], '月加班(時)': '—',
                         '主要原因': '按「完整試算」才會檢查時間衝突、休息與工時', '_rank': 4, '_tried': False, '_eligible': True})
        else:
            rows.append({'居服員ID': worker, '狀態': '⛔ 資格不符', '可排次數': 0, '資格': s['資格可排'], '月加班(時)': '—',
                         '主要原因': str(s['阻擋原因'])[:120], '_rank': 5, '_tried': False, '_eligible': False})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame['_n'] = frame['可排次數'].fillna(-1)
    return frame.sort_values(['_rank', '_n'], ascending=[True, False], kind='stable').drop(columns='_n').reset_index(drop=True)
