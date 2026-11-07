"""試辦 KPI：每項指標都明確寫出分子、分母、測量期間、基準值與資料來源。

設計原則
- 只讀排班結果，不重新排班、不改班表。
- 分子與分母都回傳原始數字，評審與機構可以自行重算。
- 基準值（人工排班的現況）由機構提供；沒有基準值時顯示「待量測」，不自行假設。
- 系統量測不到的指標（排班耗時、居服員滿意度）由居督輸入，並標明來源是「人工登錄」。

對應文件：PILOT_KPI.md
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

import pandas as pd

KPI_COLUMNS = ['代碼', '指標', '分子', '分母', '數值', '單位', '方向', '基準值', '目標值',
               '與基準差異', '是否達標', '測量期間', '定義', '資料來源']

BASELINE_PATH = os.path.join('output_results', 'pilot_baseline.json')


@dataclass(frozen=True)
class KpiSpec:
    code: str
    name: str
    unit: str
    better: str          # 'higher' 或 'lower'
    numerator: str
    denominator: str
    source: str
    target_note: str = ''


# 試辦 KPI 定義（PILOT_KPI.md 與畫面共用這份表，避免文件和程式說法不一致）
KPI_SPECS = [
    KpiSpec('K1', '派單成功率', '%', 'higher',
            '期間內已派出的服務次數', '期間內應提供的服務次數（週服務計畫展開後）',
            '系統班表'),
    KpiSpec('K2', '排班作業耗時', '分鐘', 'lower',
            '居督完成本期班表（匯入到最終匯出）的實際作業分鐘', '1 個排班期間',
            '人工登錄（居督計時）'),
    KpiSpec('K3', '平均轉場時間', '分鐘／段', 'lower',
            '不同案家之間的交通分鐘總和（不含首案出門、同案接續）', '不同案家之間的轉場段數',
            '系統逐段交通（OSRM／Google）'),
    KpiSpec('K4', '照護連續性', '%', 'higher',
            '由該案家主責居服員本人服務的次數', '已派出且該案家有主責居服員的服務次數',
            '系統班表＋主責名單'),
    KpiSpec('K5', '居督覆寫率', '%', 'lower',
            '居督改掉系統建議的服務次數（覆寫紀錄）', '期間內系統建議並派出的服務次數',
            '覆寫紀錄 supervisor_override_log.csv'),
    KpiSpec('K6', '排班錯誤率', '%', 'lower',
            '已派服務中有班次重疊、轉場時間不足或交通待確認的次數', '已派出的服務次數',
            '系統衝突檢查（03 衝突與異常處理）'),
    KpiSpec('K7', '居服員工時差距（變異係數）', '%', 'lower',
            '有排班居服員月總工時的標準差', '有排班居服員月總工時的平均',
            '系統工時估算'),
    KpiSpec('K8', '居服員滿意度', '分（1–5）', 'higher',
            '問卷分數總和', '回覆問卷的居服員人數',
            '人工登錄（試辦前後問卷）'),
]
SPEC_BY_CODE = {spec.code: spec for spec in KPI_SPECS}


def _text(value):
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float('nan')
    return out


def _assigned_map(assignments):
    """任務ID → 派單居服員（只算真的有派人）。"""
    out = {}
    if assignments is None or assignments.empty or not {'任務ID', '派單居服員'} <= set(assignments.columns):
        return out
    for tid, worker in zip(assignments['任務ID'].astype(str), assignments['派單居服員']):
        w = _text(worker)
        if w and w.lower() not in {'nan', 'none', '未指派', '未派'}:
            out[tid] = w
    return out


def period_label(tasks):
    if tasks is None or tasks.empty or '日期' not in tasks:
        return '本次排班期間'
    days = pd.to_datetime(tasks['日期'], errors='coerce').dropna()
    if days.empty:
        return '本次排班期間'
    return f"{days.min():%Y-%m-%d}～{days.max():%Y-%m-%d}"


# ---------------- 各指標的分子／分母 ----------------

def dispatch_rate_parts(tasks, assignments):
    ids = set(tasks['任務ID'].astype(str)) if tasks is not None and '任務ID' in tasks else set()
    assigned = _assigned_map(assignments)
    return sum(1 for tid in ids if tid in assigned), len(ids)


def transition_parts(routes):
    """回傳（轉場分鐘總和, 轉場段數）。只算不同案家之間的轉場。"""
    if routes is None or routes.empty or '預估車程(分)' not in routes:
        return 0.0, 0
    frame = routes.copy()
    minutes = pd.to_numeric(frame['預估車程(分)'], errors='coerce')
    check = frame['轉場時段檢查'].map(_text) if '轉場時段檢查' in frame else pd.Series('', index=frame.index)
    buffer = pd.to_numeric(frame.get('路段緩衝(分)', pd.Series(0.0, index=frame.index)), errors='coerce').fillna(0)
    first = check.str.startswith('首案')
    same_client = buffer.le(0) & minutes.fillna(0).le(0)
    picked = minutes[~first & ~same_client].dropna()
    return float(picked.sum()), int(len(picked))


def continuity_parts(tasks, assignments, primary_map):
    primary_map = {str(k): _text(v) for k, v in (primary_map or {}).items() if _text(v)}
    if tasks is None or tasks.empty or '案家ID' not in tasks:
        return 0, 0
    assigned = _assigned_map(assignments)
    t = tasks.drop_duplicates('任務ID')
    hit = total = 0
    for tid, client in zip(t['任務ID'].astype(str), t['案家ID'].astype(str)):
        worker, owner = assigned.get(tid), primary_map.get(client)
        if worker and owner:
            total += 1
            hit += int(worker == owner)
    return hit, total


def override_parts(tasks, assignments, override_log):
    """覆寫紀錄只計入本期任務；同一任務被改多次只算一次。"""
    _, denominator = dispatch_rate_parts(tasks, assignments)
    assigned = _assigned_map(assignments)
    denominator = len(assigned) if assigned else denominator
    if override_log is None or override_log.empty or '任務ID' not in override_log:
        return 0, denominator
    ids = set(tasks['任務ID'].astype(str)) if tasks is not None and '任務ID' in tasks else set()
    log = override_log.copy()
    log['任務ID'] = log['任務ID'].astype(str)
    changed = log.loc[log['任務ID'].isin(ids)]
    if {'AI推薦居服員ID', '居督指定居服員ID'} <= set(changed.columns):
        changed = changed.loc[changed['AI推薦居服員ID'].map(_text) != changed['居督指定居服員ID'].map(_text)]
    return int(changed['任務ID'].nunique()), denominator


def error_parts(tasks, assignments, routes):
    from issue_center import assigned_overlaps, transition_issues
    assigned = _assigned_map(assignments)
    bad = set()
    overlaps = assigned_overlaps(tasks, assignments)
    if not overlaps.empty:
        bad |= set(overlaps['任務ID'].astype(str)) | set(overlaps['重疊任務ID'].astype(str))
    transitions = transition_issues(routes)
    if not transitions.empty:
        bad |= set(transitions['任務ID'].astype(str))
    bad &= set(assigned)
    return len(bad), len(assigned)


def workload_spread_parts(monthly_payroll):
    """回傳（標準差, 平均, 人數）；只算有排班的人。"""
    if monthly_payroll is None or monthly_payroll.empty or '總工時' not in monthly_payroll:
        return float('nan'), float('nan'), 0
    m = monthly_payroll.copy()
    m['總工時'] = pd.to_numeric(m['總工時'], errors='coerce')
    per = m.groupby(m['居服員ID'].astype(str))['總工時'].sum() if '居服員ID' in m else m['總工時']
    per = per[per > 0].dropna()
    if len(per) == 0:
        return float('nan'), float('nan'), 0
    std = float(per.std(ddof=0)) if len(per) > 1 else 0.0
    return std, float(per.mean()), int(len(per))


# ---------------- 基準值 ----------------

def load_baseline(path=BASELINE_PATH):
    """基準值檔：{"K1": {"value": 85.0, "target": 92.0, "period": "2026-09 人工排班", "note": "..."}, ...}"""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_baseline(baseline, path=BASELINE_PATH):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(baseline, fh, ensure_ascii=False, indent=2)


# ---------------- 組合 ----------------

def _ratio(numerator, denominator, scale=100.0):
    if not denominator or (isinstance(denominator, float) and math.isnan(denominator)):
        return float('nan')
    return numerator / denominator * scale


def _fmt(value, digits=1):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ''
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.{digits}f}"


def compute_pilot_kpis(tasks, assignments, routes=None, monthly_payroll=None, primary_map=None,
                       override_log=None, baseline=None, manual=None, period=None):
    """回傳 KPI 表（DataFrame，欄位見 KPI_COLUMNS）。

    manual：人工登錄值，例如 {"K2": {"numerator": 240}, "K8": {"numerator": 38, "denominator": 10}}
    baseline：load_baseline() 的格式
    """
    baseline = baseline or {}
    manual = manual or {}
    period = period or period_label(tasks)
    values = {}

    n, d = dispatch_rate_parts(tasks, assignments)
    values['K1'] = (n, d, _ratio(n, d))
    k2 = manual.get('K2') or {}
    minutes = _num(k2.get('numerator'))
    values['K2'] = (minutes, 1, minutes)
    total, legs = transition_parts(routes)
    values['K3'] = (total, legs, _ratio(total, legs, 1.0))
    n, d = continuity_parts(tasks, assignments, primary_map)
    values['K4'] = (n, d, _ratio(n, d))
    n, d = override_parts(tasks, assignments, override_log)
    values['K5'] = (n, d, _ratio(n, d))
    n, d = error_parts(tasks, assignments, routes)
    values['K6'] = (n, d, _ratio(n, d))
    std, mean, _people = workload_spread_parts(monthly_payroll)
    values['K7'] = (std, mean, _ratio(std, mean))
    k8 = manual.get('K8') or {}
    n, d = _num(k8.get('numerator')), _num(k8.get('denominator'))
    values['K8'] = (n, d, _ratio(n, d, 1.0) if d and not math.isnan(d) else float('nan'))

    rows = []
    for spec in KPI_SPECS:
        numerator, denominator, value = values[spec.code]
        base = baseline.get(spec.code) or {}
        base_value, target = _num(base.get('value')), _num(base.get('target'))
        diff = value - base_value if not (math.isnan(value) or math.isnan(base_value)) else float('nan')
        met = ''
        if not (math.isnan(value) or math.isnan(target)):
            met = '是' if (value >= target if spec.better == 'higher' else value <= target) else '否'
        rows.append({
            '代碼': spec.code, '指標': spec.name,
            '分子': _fmt(numerator), '分母': _fmt(denominator),
            '數值': None if math.isnan(value) else round(value, 1),
            '單位': spec.unit, '方向': '越高越好' if spec.better == 'higher' else '越低越好',
            '基準值': None if math.isnan(base_value) else round(base_value, 1),
            '目標值': None if math.isnan(target) else round(target, 1),
            '與基準差異': None if math.isnan(diff) else round(diff, 1),
            '是否達標': met or ('待量測' if math.isnan(value) else '未設目標'),
            '測量期間': period,
            '定義': f"{spec.numerator} ÷ {spec.denominator}",
            '資料來源': spec.source + (f"；基準：{base.get('period')}" if base.get('period') else ''),
        })
    return pd.DataFrame(rows, columns=KPI_COLUMNS)
