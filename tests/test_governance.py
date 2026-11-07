import sqlite3
from datetime import datetime, timedelta

import pytest

import audit_log
import data_retention


def test_audit_chain_verifies_and_blocks_edits(tmp_path):
    folder = tmp_path / 'audit'
    assert audit_log.record('import', folder=folder, file_sha256='abc', size_bytes=10)
    assert audit_log.record('override', folder=folder, task_id='T1', reason='案家要求')
    state = audit_log.verify(folder)
    assert state['ok'] and state['count'] == 2
    db = sqlite3.connect(audit_log.db_path(folder))
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE audit SET detail='{}' WHERE seq=1")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute('DELETE FROM audit')
    db.close()


def test_audit_detects_tampering_after_triggers_removed(tmp_path):
    folder = tmp_path / 'audit'
    for i in range(3):
        audit_log.record('export', folder=folder, rows=i)
    db = sqlite3.connect(audit_log.db_path(folder))
    db.execute('DROP TRIGGER audit_no_update')
    db.execute("UPDATE audit SET detail='{\"rows\":99}' WHERE seq=2")
    db.commit(); db.close()
    state = audit_log.verify(folder)
    assert not state['ok'] and state['first_bad_seq'] == 2


def test_audit_never_stores_names_or_addresses(tmp_path):
    folder = tmp_path / 'audit'
    audit_log.record('export', folder=folder, 姓名='王小明', 地址='新北市', task_id='T9')
    detail = audit_log.entries(folder=folder)[-1]['detail']
    assert '王小明' not in detail and '新北市' not in detail and 'T9' in detail


def _autosave_db(path, saved_ats, block_ends):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE work_snapshots(scope TEXT, file TEXT, saved_at TEXT, summary TEXT, source TEXT, '
               'file_name TEXT, file_bytes BLOB, body BLOB, PRIMARY KEY(scope, file))')
    db.execute('CREATE TABLE blocks(id INTEGER PRIMARY KEY, scope TEXT, cg TEXT, start TEXT, end TEXT, reason TEXT)')
    for i, at in enumerate(saved_ats):
        db.execute('INSERT INTO work_snapshots VALUES(?,?,?,?,?,?,?,?)', ('s', f'f{i}', at, '', '', '', b'', b''))
    for end in block_ends:
        db.execute('INSERT INTO blocks(scope,cg,start,end,reason) VALUES(?,?,?,?,?)', ('s', 'A', end, end, '請假'))
    db.commit(); db.close()


def test_autosave_purge_respects_90_days(tmp_path):
    now = datetime(2026, 11, 7, 12, 0, 0)
    old, fresh = now - timedelta(days=91), now - timedelta(days=10)
    path = tmp_path / 'w.sqlite3'
    _autosave_db(path, [old.strftime('%Y-%m-%d %H:%M:%S'), fresh.strftime('%Y-%m-%d %H:%M:%S')],
                 [old.isoformat(timespec='seconds'), fresh.isoformat(timespec='seconds')])
    counts = data_retention.purge_autosave(days=90, path=str(path), now=now)
    assert counts == {'snapshots': 1, 'blocks': 1}
    db = sqlite3.connect(path)
    assert db.execute('SELECT COUNT(*) FROM work_snapshots').fetchone()[0] == 1
    assert db.execute('SELECT COUNT(*) FROM blocks').fetchone()[0] == 1
    db.close()


def test_road_cache_purge(tmp_path):
    path = tmp_path / 'road.sqlite3'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE road_legs (key TEXT PRIMARY KEY, minutes REAL NOT NULL, updated REAL NOT NULL)')
    now = 1_800_000_000.0
    db.executemany('INSERT INTO road_legs VALUES(?,?,?)', [('a', 1, now - 40 * 86400), ('b', 2, now - 5 * 86400)])
    db.commit(); db.close()
    assert data_retention.purge_road_cache(days=30, path=path, now=now) == 1


def test_run_daily_only_once(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, 'AUDIT_DIR', tmp_path / 'audit')
    marker = tmp_path / 'marker'
    first = data_retention.run_daily(marker=marker, autosave_path=str(tmp_path / 'none.sqlite3'),
                                     cache_db=tmp_path / 'none2.sqlite3')
    assert first == {'snapshots': 0, 'blocks': 0, 'road_legs': 0}
    assert data_retention.run_daily(marker=marker) is None
    assert audit_log.entries(folder=tmp_path / 'audit')[-1]['event'] == 'retention_purge'
