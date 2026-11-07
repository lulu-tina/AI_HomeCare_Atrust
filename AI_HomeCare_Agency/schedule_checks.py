"""Shared service-hour and rest checks; durations come from occupied time slots."""

CAREFLOW_BUILD = '2026.10.11-v18'
def workload_error(slots, cap_hours=8, occupied_minutes=0, continuous_limit=240, rest_minutes=30, transfers=None):
    slots=sorted(slots)
    durations=[(b-a).total_seconds()/60 for a,b in slots]
    if any(d<=0 for d in durations):return '服務結束時間必須晚於開始時間。'
    import math
    transfer_values=list((transfers or {}).values())
    if any(not math.isfinite(float(t)) or float(t)<0 for t in transfer_values):return '轉場時間未知或無效，無法確認工時。'
    total=sum(durations)+max(0,float(occupied_minutes))+sum(transfer_values)
    cap=min(12.0,float(cap_hours))
    if total>cap*60+1e-7:return f'每日工時超標：服務加轉場後 {total/60:.2f} 小時，上限 {cap:g} 小時。'
    continuous=0
    for i,((start,end),duration) in enumerate(zip(slots,durations)):
        if i:
            gap=(start-slots[i-1][1]).total_seconds()/60
            if gap<0:return '服務時間重疊，不能同時接兩案。'
            transfer=(transfers or {}).get(i,0)
            if gap-transfer>=rest_minutes:continuous=0
            else:continuous+=transfer
        continuous+=duration
        if continuous>continuous_limit+1e-7:
            return f'休息不足：連續工作（含轉場）將達 {continuous:g} 分鐘，上限 {continuous_limit:g} 分鐘；需保留至少 {rest_minutes:g} 分鐘休息（不含轉場）。'
    return None


def _row_hashes(frame):
    """One 64-bit hash per row (vectorised) so a group signature never needs repr(records)."""
    import pandas as pd
    if frame is None or len(frame)==0:
        return []
    text=frame.astype(str)
    # 新增一列（含空值）會讓其他列的欄位型別改變：整數變浮點（1 → 1.0）、空值變 None；
    # 統一寫法，讓既有列的簽章不受新增列影響，不會整個班表交通重算。
    for column in frame.columns[frame.dtypes.map(lambda d: d.kind in 'fO').to_numpy()]:
        text[column]=text[column].str.replace(r'^(-?\d+)\.0$',r'\1',regex=True).replace({'None':'nan','<NA>':'nan','NaT':'nan'})
    return pd.util.hash_pandas_object(text,index=False).tolist()


def incremental_travel(result, tasks, caregivers, config, cache, calculate, initial_routes=None):
    """Cache each caregiver/day route. Reuse travel fields, never stale assignment data.

    Signatures are built from vectorised row hashes (not repr of every record), and the
    per-group travel frames are merged back in a single pass, so an unchanged rerun is cheap.
    """
    import hashlib
    import pandas as pd
    if result.empty:
        cache.clear()
        return result.copy()
    base=result.reset_index(drop=True)
    first_task=tasks.drop_duplicates('任務ID')
    day_of=dict(zip(first_task['任務ID'],first_task['日期'])) if '日期' in first_task else {}
    default_day=base['排班日期'] if '排班日期' in base else None
    cg_map=caregivers.set_index('居服員ID').to_dict('index')
    seed = initial_routes.set_index('任務ID') if initial_routes is not None and not initial_routes.empty else None
    seed_ids=set(seed.index) if seed is not None else set()
    workers=base['派單居服員'].astype(str).tolist()
    task_ids=base['任務ID'].tolist()
    groups={}
    for index,tid in enumerate(task_ids):
        day=day_of.get(tid)
        if day is None:
            day=default_day.iloc[index] if default_day is not None else '單日'
        groups.setdefault((workers[index],str(day)),[]).append(index)
    # Row-level hashes, computed once for the whole frame instead of once per group.
    task_hash={}
    for tid,h in zip(tasks['任務ID'].tolist(),_row_hashes(tasks)):
        task_hash.setdefault(tid,[]).append(h)
    cfg_text=repr({k:v for k,v in vars(config).items() if k not in ('work_day_minutes','primary_profiles','client_primary_map','client_backup_map','dispatch_cache','hourly_wage','transition_wage','sa10_hourly_price','max_monthly_overtime_hours')})
    cg_hash={}
    def caregiver_hash(worker):
        if worker not in cg_hash:
            cg_hash[worker]=hashlib.sha256(repr(cg_map.get(worker,{})).encode()).hexdigest()
        return cg_hash[worker]
    travel_frames=[]; active={}; order=[]; expected_sig=None
    for key,indices in groups.items():
        order.extend(indices)
        ids=[task_ids[i] for i in indices]
        worker=workers[indices[0]]
        pairs=sorted((str(t),worker) for t in ids)
        hashes=sorted((str(t),tuple(sorted(task_hash.get(t,())))) for t in ids)
        signature=hashlib.sha256(repr((pairs,hashes,caregiver_hash(worker),cfg_text)).encode()).hexdigest()
        old=cache.get(key)
        if old and old[0]==signature:
            travel=old[1]
        else:
            group=base.iloc[indices].copy()
            subset=tasks[tasks['任務ID'].isin(ids)].copy()
            travel=None
            if seed is not None and set(ids).issubset(seed_ids):
                seeded=seed.loc[ids].reset_index()
                if expected_sig is None:
                    # Validate every seeded route in one pass instead of once per caregiver-day.
                    from schedule_output import describe_routes
                    try:
                        whole=describe_routes(base,tasks,caregivers,config.buffer_mins).drop_duplicates('任務ID').set_index('任務ID')
                        expected_sig=whole['交通資料簽章'].to_dict()
                    except Exception:
                        expected_sig={}
                valid=('交通資料簽章' in seeded and all(
                    expected_sig.get(tid) is not None and expected_sig.get(tid)==sig
                    for tid,sig in zip(seeded['任務ID'],seeded['交通資料簽章'])))
                if valid:
                    fields=[c for c in seeded if c not in group or c in ('預估車程(分)','交通估算來源') or c.startswith(('交通','路段','含緩衝','轉場','配對起點'))]
                    travel=seeded[['任務ID']+[c for c in fields if c!='任務ID']].copy()
            if travel is None:
                computed=calculate(group,subset,caregivers,config)
                fields=[c for c in computed if c not in group or c in ('預估車程(分)','交通估算來源') or c.startswith(('交通','路段','含緩衝','轉場','配對起點'))]
                travel=computed[['任務ID']+[c for c in fields if c!='任務ID']].copy()
        active[key]=(signature,travel)
        travel_frames.append(travel)
    cache.clear();cache.update(active)
    travel_all=pd.concat(travel_frames,ignore_index=True)
    ordered=base.iloc[order].reset_index(drop=True)
    ordered=ordered.drop(columns=[c for c in travel_all if c!='任務ID' and c in ordered])
    return ordered.merge(travel_all,on='任務ID',how='left',validate='one_to_one')
