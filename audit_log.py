"""存取稽核紀錄：只能附加、每筆帶前一筆的雜湊（hash chain），可驗證有沒有被竄改。

- 存在 output_results/audit/audit_log.sqlite3；資料表有觸發器，UPDATE／DELETE 一律拒絕。
- 只記 ID、事件種類、筆數、檔案雜湊；不記姓名、地址、健康資料（寫入前過濾）。
- verify() 從第一筆重算雜湊，回報第一筆不一致的序號；anchor() 把最新雜湊另存，用來證明之後沒有整檔被換掉。
- 帳號：試用期沒有個人登入，帳號取自環境變數 CAREFLOW_OPERATOR（預設 supervisor）；
  個人帳號上線後改由登入資訊帶入（DATA_GOVERNANCE.md 第二節）。

對應文件：DATA_GOVERNANCE.md 第五節。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

AUDIT_DIR = Path(os.getenv('CAREFLOW_AUDIT_DIR', str(Path(__file__).parent / 'output_results' / 'audit')))
GENESIS = '0' * 64

EVENTS = {
    'import': '匯入機構 Excel',
    'export': '匯出檔案',
    'reveal_names': '開啟顯示遮罩姓名',
    'override': '居督覆寫系統建議',
    'leave': '登錄請假／不可排時間',
    'qualification_recheck': '照顧需求變更／資格重新檢核',
    'retention_purge': '依保存期限刪除資料',
    'settings': '系統設定變更',
}

# 不得寫進稽核紀錄的欄位關鍵字（只用 ID）
_FORBIDDEN = ('姓名', '地址', 'name', 'address', '電話', 'phone', '身分證', '診斷')


def db_path(folder=None):
    folder = Path(folder) if folder else AUDIT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder / 'audit_log.sqlite3'


def _connect(folder=None):
    db = sqlite3.connect(db_path(folder), timeout=15, isolation_level=None)
    db.execute('CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, '
               'account TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL, prev_hash TEXT NOT NULL, '
               'hash TEXT NOT NULL)')
    db.execute("CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit "
               "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END")
    db.execute("CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit "
               "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END")
    return db


def _clean(detail):
    out = {}
    for key, value in (detail or {}).items():
        if any(word in str(key).lower() for word in _FORBIDDEN):
            continue
        if isinstance(value, (list, tuple, set)):
            value = [str(v) for v in value][:50]
        elif not isinstance(value, (int, float, bool)) and value is not None:
            value = str(value)[:200]
        out[str(key)] = value
    return out


def _digest(prev_hash, at, account, event, detail_json):
    payload = json.dumps([prev_hash, at, account, event, detail_json], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def current_account():
    return os.getenv('CAREFLOW_OPERATOR', 'supervisor')


def record(event, account=None, folder=None, **detail):
    """附加一筆稽核事件；稽核失敗不可中斷排班作業，回傳是否成功。"""
    try:
        account = account or current_account()
        detail_json = json.dumps(_clean(detail), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        at = datetime.now().isoformat(timespec='seconds')
        db = _connect(folder)
        try:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
            prev = row[0] if row else GENESIS
            db.execute('INSERT INTO audit(at, account, event, detail, prev_hash, hash) VALUES(?,?,?,?,?,?)',
                       (at, account, event, detail_json, prev, _digest(prev, at, account, event, detail_json)))
            db.execute('COMMIT')
        except sqlite3.Error:
            db.execute('ROLLBACK')
            raise
        finally:
            db.close()
        return True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return False


def entries(limit=None, folder=None):
    db = _connect(folder)
    try:
        sql = 'SELECT seq, at, account, event, detail, prev_hash, hash FROM audit ORDER BY seq'
        rows = db.execute(sql).fetchall()
    finally:
        db.close()
    out = [dict(zip(['seq', 'at', 'account', 'event', 'detail', 'prev_hash', 'hash'], r)) for r in rows]
    return out[-limit:] if limit else out


def verify(folder=None):
    """回傳 {'ok', 'count', 'first_bad_seq', 'last_hash'}。"""
    prev, count = GENESIS, 0
    for item in entries(folder=folder):
        count += 1
        expected = _digest(prev, item['at'], item['account'], item['event'], item['detail'])
        if item['prev_hash'] != prev or item['hash'] != expected:
            return {'ok': False, 'count': count, 'first_bad_seq': item['seq'], 'last_hash': prev}
        prev = item['hash']
    return {'ok': True, 'count': count, 'first_bad_seq': None, 'last_hash': prev}


def anchor(folder=None):
    """把目前最後雜湊附加到 anchors.txt（建議每日一次，並另存一份到機構其他地方）。"""
    state = verify(folder)
    path = db_path(folder).with_name('anchors.txt')
    with open(path, 'a', encoding='utf-8') as fh:
        fh.write(f"{datetime.now().isoformat(timespec='seconds')}\t{state['count']}\t{state['last_hash']}\n")
    return state


def as_table(limit=200, folder=None):
    import pandas as pd
    rows = []
    for item in entries(limit=limit, folder=folder):
        rows.append({'序號': item['seq'], '時間': item['at'], '帳號': item['account'],
                     '事件': EVENTS.get(item['event'], item['event']), '內容': item['detail']})
    return pd.DataFrame(rows, columns=['序號', '時間', '帳號', '事件', '內容'])
