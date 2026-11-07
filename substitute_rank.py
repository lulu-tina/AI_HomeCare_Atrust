"""代班挑選順序（排班時請假代班、固定備援、臨時支援，以及四種臨時異動共用）。

先由各呼叫端排除不能做的人（資格、時間重疊、轉場不足、每日 12 小時與月加班上限），
本模組只負責「都可以做的人」之間的先後：

    0. 跳過：月加班已達上限的 80%（只在沒有其他人選時才使用）
    1. 案家熟悉：服務過這個案家（本次班表服務過、固定備援或臨時支援過）
       —— 但本月工時超過全體平均 1.2 倍者降級，不享有熟悉優先
    2. 交通區段：從「前一案」過來（當天第一案才從家裡出發）
       順路 ≤10 分、可接受 ≤20 分、較遠 >20 分；同區段視為平手
    3. 同區段時，服務過次數多者優先
    4. 本月工時少者優先
    5. 年資深者優先

不讀滿意度（機構資料沒有滿意度欄位）。所有門檻集中在下方常數，方便機構調整。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import math
from collections import Counter

import pandas as pd

TRAVEL_BANDS = ((10.0, '順路'), (20.0, '可接受'), (math.inf, '較遠'))
OVERTIME_SKIP_RATIO = 0.8      # 月加班達上限的 80% → 跳過
OVERLOAD_RATIO = 1.2           # 本月工時 > 全體平均 × 1.2 → 熟悉者降級
EMPTY = ('', '未指派', 'None', 'nan', 'NaT')


def _text(value):
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _float(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _hhmm(value):
    text = _text(value)
    return text[:5] if len(text) >= 5 and text[2] == ':' else text


def _day(value):
    text = '' if value is None else str(value)
    if len(text) >= 10 and text[4] == '-' and text[7] == '-' and text[:4].isdigit() and text[5:7].isdigit() and text[8:10].isdigit():
        return text[:10]          # 'YYYY-MM-DD…' 直接取，不必每筆交給 pandas 猜格式（大量呼叫時很慢）
    stamp = pd.to_datetime(value, errors='coerce')
    return '' if pd.isna(stamp) else stamp.strftime('%Y-%m-%d')


def travel_band(minutes):
    """交通分鐘 → (區段序號, 區段名稱)；無法取得交通時排在最後。"""
    value = _float(minutes)
    if value is None:
        return len(TRAVEL_BANDS), '交通未知'
    for index, (limit, label) in enumerate(TRAVEL_BANDS):
        if value <= limit + 1e-9:
            return index, label
    return len(TRAVEL_BANDS) - 1, TRAVEL_BANDS[-1][1]


# ---------------- 本月工時與加班 ----------------

def month_summary(ledger, month, config=None):
    """一次算好某月每位居服員的工時、加班與全體平均（有排班者），排序時重複使用。

    ledger：{(居服員ID, 'YYYY-MM-DD'): 當日服務＋轉場分鐘}。
    """
    normal = float(getattr(config, 'normal_daily_hours', 8.0)) if config is not None else 8.0
    hours, overtime = Counter(), Counter()
    for (who, date), minutes in (ledger or {}).items():
        if str(date).startswith(month):
            value = max(0.0, _float(minutes, 0.0)) / 60.0
            hours[str(who)] += value
            overtime[str(who)] += max(0.0, value - normal)
    active = [h for h in hours.values() if h > 0]
    return {'month': month, 'hours': hours, 'overtime': overtime, 'normal': normal,
            'avg': sum(active) / len(active) if active else 0.0}


def month_hours(ledger, worker, month):
    return month_summary(ledger, month)['hours'][str(worker)]


def team_average_hours(ledger, month):
    return month_summary(ledger, month)['avg']


# ---------------- 熟悉度 ----------------

def familiarity_counts(assignments=None, task_clients=None, backup_map=None, support_map=None):
    """{(案家ID, 居服員ID): 服務次數}。固定備援、臨時支援過的人至少算 1 次。

    assignments：DataFrame（任務ID、派單居服員）或 [(任務ID, 居服員ID)]；task_clients：{任務ID: 案家ID}。
    """
    counts = Counter()
    pairs = []
    if isinstance(assignments, pd.DataFrame):
        if {'任務ID', '派單居服員'} <= set(assignments.columns):
            pairs = zip(assignments['任務ID'], assignments['派單居服員'])
    elif assignments:
        pairs = assignments
    task_clients = task_clients or {}
    for tid, worker in pairs:
        worker = _text(worker)
        client = _text(task_clients.get(tid, task_clients.get(str(tid))))
        if worker in EMPTY or not client:
            continue
        counts[(client, worker)] += 1
    for client, workers in (backup_map or {}).items():
        for worker in workers or ():
            key = (_text(client), _text(worker))
            counts[key] = max(counts[key], 1)
    for tid, worker in (support_map or {}).items():
        client = _text(task_clients.get(tid, task_clients.get(str(tid))))
        if client and _text(worker):
            key = (client, _text(worker))
            counts[key] = max(counts[key], 1)
    return counts


# ---------------- 交通銜接 ----------------

def previous_visit(task, others):
    """others：該居服員同一天其他服務 [{'start','end','lat','lon','client','task_id'}]；回傳緊接在前的一案或 None。"""
    start = _hhmm(task.get('時間窗_開始'))
    before = [o for o in others if _hhmm(o.get('end')) and _hhmm(o.get('end')) <= start]
    return max(before, key=lambda o: _hhmm(o.get('end'))) if before else None


def incoming_leg(task, person, others, config, travel_fn, known_minutes=None, allow_live_transit=False, estimate_fn=None):
    """回傳 (交通分鐘, 出發點說明, 是否估算)。

    從前一案過來；當天第一案才從家裡出發。同一案家接續服務為 0 分鐘。
    大眾運輸居服員若沒有已查好的實際分鐘，用機車路網 ×1.5＋候車 10 分估算（避免排序時大量查 Google）。
    """
    prev = previous_visit(task, others)
    if prev is not None:
        origin = (prev.get('lat'), prev.get('lon'))
        label = '前一案'
        if _text(prev.get('client')) and _text(prev.get('client')) == _text(task.get('案家ID')):
            return 0.0, '同案家接續', False
    else:
        origin = (person.get('服務起點_緯度(家)'), person.get('服務起點_經度(家)'))
        label = '家裡'
    if known_minutes is not None and _float(known_minutes) is not None:
        return float(known_minutes), label, False
    destination = (task.get('服務地點_緯度'), task.get('服務地點_經度'))
    if any(_float(v) is None for v in (*origin, *destination)):
        return None, label, False
    mode = _text(person.get('常用交通工具')) or '機車'
    try:
        if mode == '大眾運輸' and not allow_live_transit:
            motor = (estimate_fn(*origin, *destination) if estimate_fn is not None
                     else travel_fn(*origin, *destination, config, transport_mode='機車'))
            return (motor * 1.5 + 10.0 if _float(motor) is not None else None), label, True
        minutes = travel_fn(*origin, *destination, config, transport_mode=mode)
    except Exception:
        return None, label, False
    return (_float(minutes)), label, False


# ---------------- 排序 ----------------

def build_profile(worker, task, person, config, *, others=(), familiar=0, ledger=None, team_avg=None,
                  travel_fn=None, known_minutes=None, known_from=None, day_minutes=None, seniority_years=None,
                  summary=None, estimate_fn=None):
    """計算一位候選人的排序資料與白話理由。"""
    day = _day(task.get('日期'))
    month = day[:7]
    ledger = ledger if ledger is not None else getattr(config, 'work_day_minutes', {}) or {}
    if summary is None or summary.get('month') != month:
        summary = month_summary(ledger, month, config)
    hours = summary['hours'][str(worker)]
    avg = team_avg if team_avg is not None else summary['avg']
    limit = float(getattr(config, 'max_monthly_overtime_hours', 46.0) or 0.0)
    normal = summary['normal']
    overtime = max(0.0, _float((person or {}).get('排班期外本月加班時數'), 0.0)) + summary['overtime'][str(worker)]
    if day_minutes is not None and day:
        before = _float(ledger.get((str(worker), day)), 0.0) / 60.0
        overtime += max(0.0, _float(day_minutes, 0.0) / 60.0 - normal) - max(0.0, before - normal)
    skipped = limit > 0 and overtime >= OVERTIME_SKIP_RATIO * limit - 1e-9
    demoted = bool(familiar) and avg > 0 and hours > OVERLOAD_RATIO * avg + 1e-9
    if known_from is not None:
        minutes, origin, estimated = _float(known_minutes), known_from, False
    elif travel_fn is not None:
        minutes, origin, estimated = incoming_leg(task, person, list(others), config, travel_fn, known_minutes,
                                                  estimate_fn=estimate_fn)
    else:
        minutes, origin, estimated = (_float(known_minutes), '前一案' if previous_visit(task, list(others)) else '家裡', False)
    band, band_label = travel_band(minutes)
    if seniority_years is None:
        joined = pd.to_datetime(person.get('到職日'), errors='coerce')
        as_of = pd.to_datetime(day or None, errors='coerce')
        seniority_years = (max(0.0, (as_of - joined).days / 365.2425) if pd.notna(joined) and pd.notna(as_of) else 0.0)
    profile = {
        'worker': str(worker), 'familiar': int(familiar or 0), 'demoted': demoted, 'skipped': skipped,
        'travel_minutes': minutes, 'travel_from': origin, 'travel_estimated': estimated,
        'band': band, 'band_label': band_label, 'month_hours': hours, 'team_avg_hours': avg,
        'overtime_hours': overtime, 'overtime_limit': limit, 'seniority_years': float(seniority_years or 0.0),
    }
    profile['reason'] = explain(profile)
    return profile


def rank_key(profile):
    """越小越優先。"""
    familiar_first = bool(profile['familiar']) and not profile['demoted']
    return (bool(profile['skipped']), not familiar_first, profile['band'],
            -int(profile['familiar']) if familiar_first else 0,
            round(float(profile['month_hours']), 2), -float(profile['seniority_years']), str(profile['worker']))


def explain(profile):
    parts = []
    if profile['familiar']:
        parts.append(f"服務過 {profile['familiar']} 次")
    else:
        parts.append('未服務過此案家')
    minutes = profile['travel_minutes']
    if minutes is None:
        parts.append('交通未知')
    elif profile['travel_from'] == '同案家接續':
        parts.append('同案家接續（順路）')
    else:
        approx = '約 ' if profile['travel_estimated'] else ''
        parts.append(f"{profile['travel_from']}過來 {approx}{minutes:.0f} 分（{profile['band_label']}）")
    parts.append(f"本月 {profile['month_hours']:.0f} 小時")
    parts.append(f"年資 {profile['seniority_years']:.0f} 年")
    note = ''
    if profile['skipped']:
        note = f"｜月加班 {profile['overtime_hours']:.0f}/{profile['overtime_limit']:.0f} 小時已達八成，沒有其他人選才建議"
    elif profile['demoted']:
        note = f"｜本月工時已超過平均 1.2 倍（平均 {profile['team_avg_hours']:.0f} 小時），不優先"
    return '・'.join(parts) + note


def order_workers(profiles):
    """依規則排序，回傳 profile list。"""
    return sorted(profiles, key=rank_key)


RULE_TEXT = ('代班順序：①服務過此案家（本月工時超過平均 1.2 倍者不優先）→ ②從前一案過來的交通'
             '（10 分內順路、20 分內可接受、同區段算平手）→ ③本月工時少 → ④年資深；月加班達上限八成者最後才考慮。')
