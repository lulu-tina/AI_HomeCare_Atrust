"""自動存檔（第一階段）：把整份排班作業進度存進本機資料庫，重新整理或隔天打開可以還原。

存在 output_results/dispatch_workflow.sqlite3（與請假、固定人選同一個檔案）的 snapshots 資料表。
以「機構＋來源 Excel 內容＋排班期間」辨識，同一份檔案與期間只保留最新一份進度。

限制（試用期）：沒有登入，同一台主機上用同一份 Excel 與期間的人會看到同一份進度；
多人同時修改請等第二階段（資料庫主機＋登入）。資料只存在這台主機，不會上傳。
"""
CAREFLOW_BUILD = '2026.10.18-v19'
import hashlib
import pickle
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = 'output_results/dispatch_workflow.sqlite3'
KEEP_PER_SCOPE = 5

# 畫面暫存（可重算）的欄位不存，避免檔案變大；交通路段與每日路線快取要保留，還原後才不必重算交通。
_RESULT_SKIP = {'_plan_cache', '_screen_cache', '_trial_cache', '_team_cache', '_excel_cache',
                '_final_table_cache', '_payroll_cache', '_bg_id', '_advice_cache'}

# 要一起存的作業狀態與設定（設定也存，還原後排班條件一致，班表才不會被判定「參數已修改」而清除）。
STATE_KEYS = ['last_result', 'overrides', 'dynamic_insertions', 'dynamic_adjustment_log',
              'edit_cg', 'edit_cl', 'edit_tasks', 'edit_service_code', 'data_hist', 'tasks_sheet_name',
              'pending_edits', 'pending_client_summary', 'qualification_followups']
SETTING_KEYS = ['cfg_buffer_mins', 'cfg_travel_penalty_weight', 'google_query_limit', 'transit_fallback_enabled',
                'osrm_source_choice', 'agency_period', 'agency_range_start', 'agency_range_end', '_period_signature',
                'show_identity_names', 'export_identity_mode']


def _connect(path=DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.execute('CREATE TABLE IF NOT EXISTS work_snapshots(scope TEXT, file TEXT, saved_at TEXT, summary TEXT, source TEXT, '
               'file_name TEXT, file_bytes BLOB, body BLOB, PRIMARY KEY(scope, file))')
    return db


def file_key(data):
    """來源 Excel 內容的指紋（同一份檔案重新上傳也認得）。"""
    return hashlib.sha256(data).hexdigest()


def _prune_result(result):
    if not isinstance(result, dict):
        return result
    pruned = {k: v for k, v in result.items() if k not in _RESULT_SKIP}
    phase2 = pruned.get('phase2')
    if isinstance(phase2, dict):
        frame = phase2.get('df_matches')
        if frame is None:
            frame = pruned.get('df_matches')
        if frame is not None and hasattr(frame, 'attrs'):
            # DataFrame.attrs（未派原因診斷）在部分 pandas 版本 pickle 不保留，另外存一份。
            pruned['_saved_matching_diagnostics'] = list(frame.attrs.get('matching_diagnostics', []))
    return pruned


def restore_result(result):
    """還原 last_result：把另外存的診斷接回 df_matches.attrs。"""
    if not isinstance(result, dict):
        return result
    diagnostics = result.pop('_saved_matching_diagnostics', None)
    if diagnostics is not None:
        for frame in (result.get('df_matches'), (result.get('phase2') or {}).get('df_matches')):
            if frame is not None and hasattr(frame, 'attrs') and not frame.attrs.get('matching_diagnostics'):
                frame.attrs['matching_diagnostics'] = diagnostics
    return result


def build_snapshot(session_state):
    state = {}
    for key in STATE_KEYS + SETTING_KEYS:
        if key in session_state:
            value = session_state[key]
            state[key] = _prune_result(value) if key == 'last_result' else value
    return state


def save(scope, file, source, session_state, file_name='', file_bytes=b'', summary='', path=DB_PATH):
    """同一份 Excel 只保留最新進度；Excel 本身也存一份，重新整理後不必重新上傳。"""
    body = pickle.dumps(build_snapshot(session_state), protocol=pickle.HIGHEST_PROTOCOL)
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _connect(path) as db:
        db.execute('INSERT INTO work_snapshots VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(scope, file) DO UPDATE SET '
                   'saved_at=excluded.saved_at, summary=excluded.summary, source=excluded.source, body=excluded.body, '
                   'file_name=excluded.file_name',
                   (str(scope), str(file), stamp, summary, str(source), str(file_name), sqlite3.Binary(file_bytes or b''), body))
        old = db.execute('SELECT file FROM work_snapshots WHERE scope=? ORDER BY saved_at DESC LIMIT -1 OFFSET ?',
                         (str(scope), KEEP_PER_SCOPE)).fetchall()
        for (key,) in old:
            db.execute('DELETE FROM work_snapshots WHERE scope=? AND file=?', (str(scope), key))
    return stamp, len(body)


def info(scope, file=None, path=DB_PATH):
    """回傳存檔摘要（不載入內容）：指定 file 時找那份 Excel，否則找最近一次。沒有則 None。"""
    if not Path(path).exists():
        return None
    with _connect(path) as db:
        if file is None:
            row = db.execute('SELECT file, saved_at, summary, source, file_name FROM work_snapshots WHERE scope=? '
                             'ORDER BY saved_at DESC LIMIT 1', (str(scope),)).fetchone()
        else:
            row = db.execute('SELECT file, saved_at, summary, source, file_name FROM work_snapshots WHERE scope=? AND file=?',
                             (str(scope), str(file))).fetchone()
    if not row:
        return None
    return {'file': row[0], 'saved_at': row[1], 'summary': row[2], 'source': row[3], 'file_name': row[4]}


def load(scope, file, path=DB_PATH):
    """回傳 (作業狀態, Excel 檔名, Excel 內容, 來源鍵)；沒有則 None。"""
    with _connect(path) as db:
        row = db.execute('SELECT body, file_name, file_bytes, source FROM work_snapshots WHERE scope=? AND file=?',
                         (str(scope), str(file))).fetchone()
    if not row:
        return None
    # 只讀取本機程式自己寫入的存檔（同一個資料庫檔），不載入外部來源的資料。
    state = pickle.loads(row[0])
    if 'last_result' in state:
        state['last_result'] = restore_result(state['last_result'])
    return state, row[1], bytes(row[2] or b''), row[3]


def discard(scope, file, path=DB_PATH):
    if not Path(path).exists():
        return
    with _connect(path) as db:
        db.execute('DELETE FROM work_snapshots WHERE scope=? AND file=?', (str(scope), str(file)))


def change_token(parts):
    """作業內容的指紋：沒有變動就不重寫存檔。"""
    return hashlib.sha256(repr(parts).encode()).hexdigest()
