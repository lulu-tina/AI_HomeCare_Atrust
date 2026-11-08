"""復原上一步：每次班表有變動，就把「變動前」存一份（最多 10 步），居督按一下就回到上一步。

不必在每個改班按鈕各自加復原：app.py 每次畫面跑完呼叫 track()，比對班表狀態有沒有變，有變才記一步。
涵蓋：人工改派、月曆一鍵調班、整案調整、臨時插單、請假代班、改期、延遲、週計畫異動、以居服員排班。
請假紀錄存在資料庫（WorkflowStore），復原時也一併移除或補回，不然代班會被重新套上。
重新排班（排班條件改變）會清空復原紀錄，不會退回上一次排班的結果。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import copy
from datetime import datetime

import pandas as pd

MAX_STEPS = 10
STATE_KEYS = ('overrides', 'dynamic_insertions', 'dynamic_adjustment_log', 'pending_edits', 'qualification_followups')
RES_KEYS = ('df_tasks', 'df_tasks_all', 'df_matches', 'client_primary_map', 'client_backup_map', 'task_support_map',
            'whole_client_log', 'plan_version_bytes', 'plan_version_log')
PHASE2_KEYS = ('df_result', 'df_matches')


def _copy(value):
    if isinstance(value, pd.DataFrame):
        out = value.copy(deep=True)
        out.attrs = copy.deepcopy(value.attrs)
        return out
    return copy.deepcopy(value)


def snapshot(state, res, blocks=()):
    phase2 = res.get('phase2') or {}
    return {'state': {k: _copy(state[k]) for k in STATE_KEYS if k in state},
            'res': {k: _copy(res[k]) for k in RES_KEYS if k in res},
            'phase2': {k: _copy(phase2[k]) for k in PHASE2_KEYS if k in phase2},
            'blocks': [dict(b) for b in blocks], 'signature': res.get('input_signature')}


def describe(before, state, res, blocks=()):
    """這一步做了什麼（顯示在復原按鈕上）。"""
    old_state, old_res = before['state'], before['res']
    if len(res.get('plan_version_log') or []) > len(old_res.get('plan_version_log') or []):
        return '週計畫異動'
    if len(res.get('whole_client_log') or []) > len(old_res.get('whole_client_log') or []):
        return '整案調整'
    old_log, new_log = old_state.get('dynamic_adjustment_log') or [], state.get('dynamic_adjustment_log') or []
    if len(new_log) > len(old_log):
        kind = str(new_log[-1].get('事件類型', '')).split('（')[0]
        if kind:
            return kind
    old_ids = {b.get('id') for b in before['blocks']}
    if any(b.get('id') not in old_ids for b in blocks):
        return '登錄請假'
    old_ov, new_ov = old_state.get('overrides') or {}, state.get('overrides') or {}
    changed = [k for k in set(old_ov) | set(new_ov) if old_ov.get(k) != new_ov.get(k)]
    if changed:
        reasons = ' '.join(str((new_ov.get(k) or {}).get('reason', '')) for k in changed)
        if '以居服員排班' in reasons:
            return f'以居服員排班（{len(changed)} 次服務）'
        return f'人工改派（{len(changed)} 筆）'
    if (old_state.get('pending_edits') or {}) != (state.get('pending_edits') or {}):
        return '未派案家調整'
    return '班表調整'


def track(state, token, signature, make_snapshot, blocks=()):
    """每次畫面跑完呼叫。token＝班表狀態指紋，signature＝排班條件。
    狀態沒變時什麼都不做（不複製資料）；變了才把上次的狀態存成一步。回傳是否新增了一步。"""
    last = state.get('_undo_last')
    if state.pop('_undo_restored', False) or last is None:
        state['_undo_last'] = (token, make_snapshot())
        state.setdefault('_undo_stack', [])
        return False
    if last[1].get('signature') != signature:               # 重新排班：舊的復原紀錄不再適用
        state['_undo_stack'] = []
        state['_undo_last'] = (token, make_snapshot())
        return False
    if last[0] == token:
        return False
    stack = state.setdefault('_undo_stack', [])
    stack.append({'label': describe(last[1], state, state.get('last_result') or {}, blocks),
                  'at': datetime.now().strftime('%H:%M'), 'snapshot': last[1]})
    del stack[:-MAX_STEPS]
    state['_undo_last'] = (token, make_snapshot())
    return True


def undo(state, store=None):
    """回到上一步；回傳說明文字，沒有可復原的步驟回傳空字串。"""
    stack = state.get('_undo_stack') or []
    if not stack:
        return ''
    step = stack.pop()
    snap = step['snapshot']
    for key in STATE_KEYS:
        if key in snap['state']:
            state[key] = _copy(snap['state'][key])
        elif key in state and key != 'overrides':
            del state[key]
    state.setdefault('overrides', {})
    res = state.get('last_result')
    if isinstance(res, dict):
        for key in RES_KEYS:
            if key in snap['res']:
                res[key] = _copy(snap['res'][key])
            else:
                res.pop(key, None)
        phase2 = res.setdefault('phase2', {})
        for key in PHASE2_KEYS:
            if key in snap['phase2']:
                phase2[key] = _copy(snap['phase2'][key])
    if store is not None:
        before = {b['id']: b for b in snap['blocks']}
        now_ids = set()
        for block in store.blocks():
            now_ids.add(block['id'])
            if block['id'] not in before:
                store.remove_block(block['id'])             # 這一步新增的請假 → 移除
        for bid, block in before.items():
            if bid not in now_ids:                           # 這一步刪掉的請假 → 補回
                start, end = str(block['start']), str(block['end'])
                store.add_blocks(block['cg'], start[:10], [(start[11:16], end[11:16])], block.get('reason') or '請假（復原）')
    state['_undo_restored'] = True
    return f"已復原：{step['label']}（{step['at']} 的調整）"
