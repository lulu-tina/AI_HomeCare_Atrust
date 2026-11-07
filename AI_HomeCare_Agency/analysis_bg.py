"""未派案家的「原因＋方案」分析：可在背景執行、結果存起來，畫面不必等。

流程：排班完成（或存檔）後，app 把每個未派案家的分析丟進背景執行緒（一次一個，避免同時大量查路網）；
畫面照常顯示，卡片先顯示「分析中」，算好就自動換成原因與建議。結果依「班表簽章」存在記憶體，
簽章不變（只是開關視窗、換案家）就直接取用。
"""
CAREFLOW_BUILD = '2026.10.11-v18'
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

_LOCK = threading.RLock()
_REG = {}          # key -> slot
_MEMOS = {}        # (session, sig, case, worker) -> memo（建議與預覽的重用表）
_EXEC = None
_MAX_SLOTS = 80


def _sync():
    return os.environ.get('CAREFLOW_SYNC_ANALYSIS') == '1'


def _executor():
    global _EXEC
    if _EXEC is None:
        _EXEC = ThreadPoolExecutor(max_workers=1, thread_name_prefix='careflow-analysis')
    return _EXEC


def memo_for(session, sig, case, worker):
    with _LOCK:
        return _MEMOS.setdefault((session, sig, str(case), str(worker)), {})


def prefetch_case(tasks, caregivers, clients, config, case):
    """把「這個案家的位置 ↔ 全部服務位置／居服員住家」的機車路網一次抓回來（批次、已有的不重抓）。失敗不影響分析。"""
    try:
        import caregiver_engine as engine
        merged = engine.merge_task_client_tables(tasks, clients)
        lat_col, lon_col = '服務地點_緯度', '服務地點_經度'
        if lat_col not in merged or lon_col not in merged:
            return
        def points(frame, a, b):
            return [(float(x), float(y)) for x, y in zip(pd.to_numeric(frame[a], errors='coerce'), pd.to_numeric(frame[b], errors='coerce'))
                    if np.isfinite(x) and np.isfinite(y)]
        mine = points(merged.loc[merged['案家ID'].astype(str).eq(str(case))], lat_col, lon_col)
        everyone = points(merged, lat_col, lon_col)
        if '服務起點_緯度(家)' in caregivers:
            everyone += points(caregivers, '服務起點_緯度(家)', '服務起點_經度(家)')
        if not mine:
            return
        engine.prefetch_osrm_travel_times(mine, everyone, config.travel_min_per_km)
        engine.prefetch_osrm_travel_times(everyone, mine, config.travel_min_per_km)
    except Exception:
        pass


def compute_entry(ctx, case, worker, edits_obj, quick, memo, existing=None):
    """整案預覽＋白話診斷＋已驗證方案。quick＝只算最優先的一個方案；existing 是先前的快速結果（只補方案）。"""
    from pending_advice import build_plans, diagnose
    from pending_clients import preview_with_edits

    def run(edit_obj):
        key = repr(edit_obj)
        previews = memo.setdefault('preview', {})
        if key not in previews:
            previews[key] = preview_with_edits(ctx['tasks'], ctx['assignments'], ctx['caregivers'], ctx['clients'], ctx['config'],
                                               str(case), worker, edit_obj, {}, {}, ctx['unit_minutes'])
        return previews[key]
    if existing is None:
        prefetch_case(ctx['tasks'], ctx['caregivers'], ctx['clients'], ctx['config'], case)
        preview = run(edits_obj)
        entry = {'preview': preview, 'diag': diagnose(preview, ctx['unit_minutes'], ctx['limit']), 'plans': [], 'quick': True}
        if preview['failed'].empty:
            entry['quick'] = False
            return entry
    else:
        entry = existing
        preview = entry['preview']
    entry['plans'] = build_plans(preview, ctx['caregivers'], ctx['config'], run, edits_obj, ctx['unit_minutes'],
                                 max_plans=1 if quick else 3, budget_seconds=40.0, memo=memo)
    entry['quick'] = bool(quick)
    return entry


def get(key):
    return _REG.get(key)


def submit(key, fn):
    """排進背景（同一個 key 只排一次）。fn() 回傳結果；執行中的狀態可由 status 查。"""
    with _LOCK:
        slot = _REG.get(key)
        if slot is not None:
            return slot
        slot = _REG[key] = {'state': 'queued', 'entry': None, 'error': '', 't0': time.time(), 'key': key}
        if len(_REG) > _MAX_SLOTS:
            for old in sorted(_REG, key=lambda k: _REG[k]['t0'])[:len(_REG) - _MAX_SLOTS]:
                if _REG[old]['state'] in ('done', 'error'):
                    _REG.pop(old, None)

    try:   # 讓背景執行緒也看得到這次連線的 session（交通來源設定等），否則會退回預設值
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
        script_ctx = get_script_run_ctx()
    except Exception:
        add_script_run_ctx = script_ctx = None

    def work():
        if script_ctx is not None:
            try:
                add_script_run_ctx(threading.current_thread(), script_ctx)
            except Exception:
                pass
        slot['state'] = 'running'
        try:
            slot['entry'] = fn()
            slot['state'] = 'done'
        except Exception as exc:  # noqa: BLE001
            slot['error'] = str(exc)[:200]
            slot['state'] = 'error'
    if os.environ.get('CAREFLOW_NO_BG') == '1':   # 只供量測：不執行分析
        return slot
    if _sync():
        work()
    else:
        _executor().submit(work)
    return slot


def busy(session, sig):
    """這份班表底下是否還有分析在排隊或執行。"""
    with _LOCK:
        return any(k[0] == session and k[1] == sig and s['state'] in ('queued', 'running') for k, s in _REG.items())


def forget_other_signatures(session, sig):
    """班表已變動：舊簽章的結果與重用表不會再用到。"""
    with _LOCK:
        for k in [k for k in _REG if k[0] == session and k[1] != sig and _REG[k]['state'] in ('done', 'error')]:
            _REG.pop(k, None)
        for k in [k for k in _MEMOS if k[0] == session and k[1] != sig]:
            _MEMOS.pop(k, None)
