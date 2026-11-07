"""保存期限：每天第一次開啟系統時，刪除逾期的自動存檔、請假紀錄與路段快取，並寫入稽核紀錄。

| 資料 | 預設期限 | 環境變數 |
|---|---|---|
| 自動存檔（含上傳 Excel 位元組） | 最後儲存後 90 天 | CAREFLOW_AUTOSAVE_DAYS |
| 請假／不可排時段 | 結束日後 90 天 | CAREFLOW_AUTOSAVE_DAYS |
| 路段快取（只有座標與分鐘） | 30 天 | CAREFLOW_ROAD_CACHE_DAYS |

稽核紀錄不在此刪除（至少保存 2 年，刪除需機構負責人核准，見 DATA_GOVERNANCE.md）。
刪除後執行 VACUUM，讓資料真的從檔案中移除，而不是只標記為可覆寫。
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

AUTOSAVE_DB = 'output_results/dispatch_workflow.sqlite3'
MARKER = 'output_results/.retention_last_run'


def _days(name, default):
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def purge_autosave(days=None, path=AUTOSAVE_DB, now=None):
    """回傳 {'snapshots': n, 'blocks': m}。"""
    days = days or _days('CAREFLOW_AUTOSAVE_DAYS', 90)
    now = now or datetime.now()
    cutoff = (now - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    counts = {'snapshots': 0, 'blocks': 0}
    if not Path(path).exists():
        return counts
    db = sqlite3.connect(path, timeout=15)
    try:
        tables = _tables(db)
        if 'work_snapshots' in tables:
            counts['snapshots'] = db.execute('DELETE FROM work_snapshots WHERE saved_at < ?', (cutoff,)).rowcount
        if 'blocks' in tables:
            # 請假時段以 ISO 格式（YYYY-MM-DDTHH:MM:SS）儲存
            iso_cutoff = (now - timedelta(days=days)).isoformat(timespec='seconds')
            counts['blocks'] = db.execute('DELETE FROM blocks WHERE end < ?', (iso_cutoff,)).rowcount
        db.commit()
        if any(counts.values()):
            db.execute('VACUUM')
    finally:
        db.close()
    return counts


def purge_road_cache(days=None, path=None, now=None):
    days = days or _days('CAREFLOW_ROAD_CACHE_DAYS', 30)
    if path is None:
        from persistent_cache import cache_path
        path = cache_path()
    if not Path(path).exists():
        return 0
    cutoff = (now or time.time()) - days * 86400
    db = sqlite3.connect(path, timeout=15)
    try:
        if 'road_legs' not in _tables(db):
            return 0
        count = db.execute('DELETE FROM road_legs WHERE updated < ?', (cutoff,)).rowcount
        db.commit()
        if count:
            db.execute('VACUUM')
    finally:
        db.close()
    return count


def run_daily(force=False, marker=MARKER, autosave_path=AUTOSAVE_DB, cache_db=None):
    """一天最多執行一次；回傳刪除筆數 dict，當天已執行過則回傳 None。失敗不影響排班。"""
    marker = Path(marker)
    try:
        if not force and marker.exists() and time.time() - marker.stat().st_mtime < 86400:
            return None
        result = purge_autosave(path=autosave_path)
        result['road_legs'] = purge_road_cache(path=cache_db)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now().isoformat(timespec='seconds'), encoding='utf-8')
        import audit_log
        audit_log.record('retention_purge', **result)
        audit_log.anchor()
        return result
    except (OSError, sqlite3.Error):
        return None
