"""Normalize source field aliases without inferring clinical qualifications."""
import math
import pandas as pd


def identifier(value):
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    result = str(value).split('｜', 1)[0].strip()
    if result.endswith('.0') and result[:-2].isdigit():
        result = result[:-2]
    return result


def resolve_worker(value, worker_ids):
    value = identifier(value)
    if not value or value in worker_ids:
        return value
    # Repair a lost leading zero only when the employee master has one match.
    matches = [v for v in worker_ids if v.isdigit() and value.isdigit() and int(v) == int(value)]
    return matches[0] if len(matches) == 1 else value


def normalize_clients(frame, worker_ids=None):
    result = frame.copy()
    if '是否失智' in result:
        values = result['是否失智'].astype(str).str.strip().str.lower()
        result['失智症個案(0/1)'] = values.map({'是':1, '1':1, '1.0':1, 'true':1,
                                                  '否':0, '0':0, '0.0':0, 'false':0})
    if '歷史首選' in result:
        result['歷史首選居服員ID'] = result['歷史首選'].map(identifier)
    if '歷史首選居服員ID' in result and worker_ids is not None:
        result['歷史首選居服員ID'] = result['歷史首選居服員ID'].map(lambda v: resolve_worker(v, worker_ids))
    return result


def source_primary_map(clients, worker_ids=None, fallback=None):
    result = dict(fallback or {})
    clients = normalize_clients(clients, worker_ids)
    for _, row in clients.iterrows():
        owner = identifier(row.get('歷史首選居服員ID'))
        if owner:
            result[identifier(row['案家ID'])] = owner
    return result


_SENIORITY_MEMO = {}


def seniority_score(worker, service_date, config):
    key = (str(worker.get('到職日')), str(service_date), config.seniority_cap_years, config.seniority_bonus_per_year)
    if key in _SENIORITY_MEMO:
        return _SENIORITY_MEMO[key]
    if len(_SENIORITY_MEMO) > 200000:
        _SENIORITY_MEMO.clear()
    _SENIORITY_MEMO[key] = value = _seniority_score(worker, service_date, config)
    return value


def _seniority_score(worker, service_date, config):
    joined = pd.to_datetime(worker.get('到職日'), errors='coerce')
    as_of = pd.to_datetime(service_date, errors='coerce')
    if pd.isna(joined) or pd.isna(as_of):
        return 0.0, 0.0
    years = max(0.0, (as_of.normalize() - joined.normalize()).days / 365.2425)
    bonus = min(years, config.seniority_cap_years) * config.seniority_bonus_per_year
    return round(years, 2), round(bonus, 2)
